"""
Import logic: files finished slskd downloads into the Navidrome library.

Flow:
  1. Scan DOWNLOADS_DIR recursively for audio files.
  2. Only consider files that are "stable" (not changed for MIN_AGE_SECONDS) -
     so half-finished downloads are not filed in.
  3. Build the target path Music/<Artist>/<Album>/<Track>.<ext> from the tags.
  4. Copy the file (COPY_MODE=1) or move it (COPY_MODE=0).
     When copying, the original stays in the downloads folder so slskd can keep
     sharing it for a while (seeding).
  5. Already-present targets are not overwritten (idempotent).
"""

import json
import logging
import os
import re
import shutil
import time
import unicodedata

from mutagen import File as MutagenFile

from . import config, cover, quality, replaygain, resolve, tags, yearmap, mbyear
from .i18n import t

logger = logging.getLogger(__name__)

# Shared year map (artist|title -> year), written by charts-flashback AND
# charts-energy (library mode); we read it here to set the correct release year
# as a tag WITHOUT ever having it in the file/folder name, and charts-import
# writes its own generic pending markers into it too. Path: in the shared charts
# folder. Owned schema helpers live in app/yearmap.py.
_YEAR_MAP_FILE = yearmap.YEAR_MAP_FILE

# Import log (JSON Lines) for the daily summary (charts-digest). One line per
# actually-filed song {ts, source, artist, title}. Append-only; charts-digest
# reads it and filters to the time window. Sits in the shared charts folder
# that charts-digest also sees.
_IMPORT_LOG_FILE = os.path.join(config.DOWNLOADS_DIR, ".charts-imported-log.jsonl")


def _append_import_log(source: str, artist: str, title: str) -> None:
    """Appends ONE import entry to the JSONL log. Errors are not fatal."""
    try:
        rec = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "source": source or "",
            "artist": artist or "",
            "title": title or "",
        }
        with open(_IMPORT_LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.debug(t("import.log.write_failed", path=_IMPORT_LOG_FILE, error=e))


def _hint_year(artist: str, title: str) -> "tuple[str, bool]":
    """
    Year hint from the shared year map (key = lowercase 'artist|title'), written
    by charts-flashback (always a real year) AND charts-energy library mode (a
    real in-decade MB year OR a decade midpoint).

    Returns: (year, is_pending_fallback). `year` is the current best year on the
    map value ("" if none). `is_pending_fallback` is True when the map value is a
    pending object (a charts-energy midpoint still to be sharpened by the
    backfill); False for a confirmed bare-string year (flashback real year, or an
    in-decade MB year from energy) and for a missing entry.
    """
    key = f"{(artist or '').lower()}|{(title or '').lower()}"
    val = yearmap.load().get(key)
    return yearmap.year_of(val), yearmap.is_pending(val)


def _import_source(m: "dict | None") -> str:
    """
    The import-source label passed to tags.write_tags for the durable
    provenance marker. Returns "youtube" iff the manifest entry's source(s)
    include the YouTube label (reusing tags.manifest_sources, which handles the
    new "sources" list and the old single "source" string), else "". Only
    "youtube" is marked today; any other source stamps nothing.
    """
    return "youtube" if "youtube" in tags.manifest_sources(m or {}) else ""


def _ensure_pending_marker(artist: str, title: str, src: str,
                           decade: str | None = None) -> None:
    """
    The ONLY place charts-import creates a GENERIC pending marker. Runs the
    give-up guard first so a given-up song is never re-marked:

      - if the key is already present as an object (a charts-energy decade
        marker OR our own import-generic marker) -> already pending, return;
      - if present as a bare string -> a confirmed year exists, return;
      - if the lowercase 'artist|title' is a cached MB miss -> MB already gave
        up on this song, return.

    The object branch deliberately returns for BOTH src values (no src check),
    preserving the no-re-mark property. Never blocks the import (OSError -> debug
    log); does not change import_downloads's return arity.
    """
    key = f"{(artist or '').lower()}|{(title or '').lower()}"
    try:
        data = yearmap.load()
        val = data.get(key)
        if isinstance(val, dict):
            return  # already pending (decade marker OR our own)
        if isinstance(val, str) and val:
            return  # a confirmed year exists
        if mbyear.is_cached_miss(artist, title):
            return  # MB already gave up on this song
        data[key] = yearmap.make_pending(year="", src=src, decade=decade)
        yearmap.rewrite(data)
    except OSError:
        # Fail-open: a missed marker only means the song is picked up by a later
        # re-mark (subject to the cached-miss guard) or stays unmarked. No new
        # i18n string for this rare debug case.
        logger.debug("year marker write failed for %s - %s", artist, title)


def _iter_audio_files(root: str):
    """
    Yields all audio files below root (absolute paths).
    The quarantine folder (REJECTED_DIR) is excluded so rejected files are not
    checked over and over.
    """
    rejected_abs = os.path.abspath(config.REJECTED_DIR)
    for dirpath, dirnames, filenames in os.walk(root):
        # do not enter _rejected
        if os.path.abspath(dirpath).startswith(rejected_abs):
            dirnames[:] = []
            continue
        for name in filenames:
            ext = os.path.splitext(name)[1].lower()
            if ext in config.AUDIO_EXTENSIONS:
                yield os.path.join(dirpath, name)


def _rejection_reason(src: str, artist: str) -> str | None:
    """
    Checks whether a file should be rejected. Returns the reason (for logging)
    or None if it is fine.
    """
    if not config.REJECT_ENABLED:
        return None

    name = os.path.basename(src.replace("\\", "/")).lower()
    # Parens/chars to spaces so "(lyrics)" matches as "lyrics".
    cleaned = f" {re.sub(r'[^a-z0-9]+', ' ', name)} "
    for kw in config.REJECT_KEYWORDS:
        if f" {kw.lower().strip()} " in cleaned:
            return t("import.reject.keyword", keyword=kw.strip())

    if config.REJECT_UNKNOWN_ARTIST:
        a = (artist or "").strip().lower()
        if not a or a == "unknown artist":
            return t("import.reject.no_artist")

    # Too-short files = aborted/incomplete downloads (e.g. a 24s fragment).
    # Chart singles are practically always longer. Rejected and (via the
    # rejected list) released in the charts-download state so the song is
    # searched again until a complete version arrives.
    if config.MIN_DURATION_SECONDS > 0:
        dur = _duration_seconds(src)
        if dur is not None and dur < config.MIN_DURATION_SECONDS:
            # Do not use '<': the Pushover message is sent as HTML, a '<' would
            # be interpreted as the start of a tag and swallow the rest of the
            # line. Hence "min" instead of "<".
            return t("import.reject.too_short", duration=f"{dur:.0f}",
                     min=config.MIN_DURATION_SECONDS)

    # Upsampling/fake-bitrate check (frequency cutoff). Conservative: only kicks
    # in with a high CLAIMED bitrate and a clearly too-low real cutoff. Not
    # measurable -> no reject (quality.is_upsampled then returns False).
    # Skipped ONLY when the manifest entry carries accept_anyway (a user
    # confirmed a poor-quality manual upload "add it anyway" in
    # charts-songrequest). The keyword / unknown-artist / too-short rejections
    # above stay unconditional - this gate bypasses the upsampling branch alone.
    # The manifest lookup sits INSIDE the REJECT_UPSAMPLED block so no extra read
    # happens when the check is disabled.
    if config.REJECT_UPSAMPLED:
        _m = tags.manifest_lookup(src)
        if not (_m and _m.get("accept_anyway")):
            claimed = _read_bitrate(src)
            upsampled, measured, expected = quality.is_upsampled(
                src, claimed,
                tolerance_khz=config.CUTOFF_TOLERANCE_KHZ,
                min_check_bitrate=config.REJECT_UPSAMPLED_MIN_BITRATE,
            )
            if upsampled:
                # No '<'/'>' in the reason (Pushover HTML). measured/expected in kHz.
                return t("import.reject.upsampled", claimed=claimed,
                         measured=f"{measured:.1f}", expected=f"{expected:.0f}")

    return None


def _duration_seconds(path: str) -> float | None:
    """Length of the audio file in seconds, or None if not readable."""
    try:
        audio = MutagenFile(path)
        if audio is not None and audio.info is not None:
            return float(audio.info.length)
    except Exception:
        pass
    return None


def _quarantine(src: str) -> None:
    """Moves a rejected file to REJECTED_DIR (flat, file name only)."""
    try:
        os.makedirs(config.REJECTED_DIR, exist_ok=True)
        target = _unique_target(os.path.join(config.REJECTED_DIR, os.path.basename(src)))
        shutil.move(src, target)
    except OSError as e:
        logger.error(t("import.quarantine.move_failed", path=src, error=e))


def _is_stable(path: str, min_age_seconds: int) -> bool:
    """True if the file has not changed for min_age_seconds."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return False
    return (time.time() - mtime) >= min_age_seconds


def _read_bitrate(path: str) -> int:
    """Bitrate (kbps) of an audio file, 0 if not readable. For the upgrade comparison."""
    try:
        audio = MutagenFile(path)
        if audio is not None and audio.info is not None:
            return int((getattr(audio.info, "bitrate", 0) or 0) / 1000)
    except Exception:
        pass
    return 0


def _trash(path: str) -> bool:
    """
    Moves a replaced (worse) file into a dated trash under
    MUSIC_DIR/.charts-trash/<YYYYMMDD>/ instead of hard-deleting it.
    Reversible - the trash can be emptied manually later.
    Returns True on success.
    """
    try:
        day = time.strftime("%Y%m%d")
        trash_dir = os.path.join(config.MUSIC_DIR, ".charts-trash", day)
        os.makedirs(trash_dir, exist_ok=True)
        dst = _unique_target(os.path.join(trash_dir, os.path.basename(path)))
        shutil.move(path, dst)
        logger.info(t("import.trash.moved", path=os.path.relpath(dst, config.MUSIC_DIR)))
        return True
    except OSError as e:
        logger.error(t("import.trash.move_failed", path=path, error=e))
        return False


def _unique_target(target: str) -> str:
    """
    If the target already exists, checks whether it is the same file.
    If it is a different file with the same name, a counter suffix is appended.
    """
    if not os.path.exists(target):
        return target
    base, ext = os.path.splitext(target)
    counter = 2
    while os.path.exists(f"{base} ({counter}){ext}"):
        counter += 1
    return f"{base} ({counter}){ext}"


def _same_file(a: str, b: str) -> bool:
    """Rough equality check via size (cheap, good enough for dedup)."""
    try:
        return os.path.getsize(a) == os.path.getsize(b)
    except OSError:
        return False


def _track_key(filename: str) -> str:
    """
    Normalized track name for the duplicate comparison.
    Removes extension, (n) duplicate suffixes and slskd-typical numeric
    suffixes (_639241038209671901 = snowflake ID) so the same song is detected
    regardless of the source file name. Examples:
      'Golden (2).mp3'                       -> 'golden'
      'Stargazing_639241038209671901.mp3'    -> 'stargazing'
      'Myles Smith - Stargazing.flac'        -> 'myles smith - stargazing'
    """
    base = os.path.splitext(os.path.basename(filename))[0]
    base = re.sub(r"\s*\(\d+\)\s*$", "", base)   # (2), (3) ...
    base = re.sub(r"_\d{6,}$", "", base)          # _<long number> (slskd suffix)
    return base.strip().lower()


def _song_already_present(target: str) -> bool:
    """
    True if the target album folder already has a file with the same track
    name (regardless of (n) suffix and extension). Prevents multiple imports of
    the same song from different providers INTO THE SAME folder. For the
    cross-album check see _song_present_for_artist.
    """
    target_dir = os.path.dirname(target)
    if not os.path.isdir(target_dir):
        return False
    want = _track_key(target)
    try:
        for name in os.listdir(target_dir):
            ext = os.path.splitext(name)[1].lower()
            if ext in config.AUDIO_EXTENSIONS and _track_key(name) == want:
                return True
    except OSError:
        pass
    return False


# Keywords (case-insensitive) that mark a trailing (...)/[...] group as mere
# bitrate/format/version decoration rather than part of the real song title.
# Only groups matching one of these are stripped from the END of the title, so
# a meaningful parenthetical like "(Part 2)" survives and stays distinct.
#   - bitrate/format: "320 Kbps", "[320]", "320kbps", "320k", "flac", "wav"
#   - version/mix:    "Extended Mix", "Original Mix", "Radio Edit", "Remix",
#                     "... Mix", "... Version", "... Edit", "remaster(ed)"
#   - channel noise:  "Official ...", "Lyric(s) ...", "Audio", "Video",
#                     "Visualizer", "feat ...", "ft ...", "with ..."
# NOTE: stripping "(Remix)"/"(... Mix)" is a DELIBERATE behaviour change for the
# MATCH key only: a true remix upload of an existing base title now folds to the
# base key and is reported "already present". This does NOT change how the title
# is written to disk or tagged - callers keep the real submitted title.
_TITLE_DECORATION = re.compile(
    r"^\s*\d{2,4}\s*$"                                 # bare number group, e.g. [320]
    r"|\d{2,4}\s*k(?:bps|bit|b)?"                      # 320kbps / 320k
    r"|flac|wav|m4a|aac|ogg|opus"                      # bare format tags
    r"|extended|original|radio|remix|mix|version|edit"  # version/mix words
    r"|remaster(?:ed)?|official|audio|video|visual[sz]er|lyric[s]?"
    r"|feat|ft|with",
    re.IGNORECASE,
)

# Trailing UNBALANCED/open opener-tail: a "(word..." or "[word..." at the very
# end of the title with NO closing bracket, whose first word is a known
# decoration keyword (e.g. "... (original", "... (live", "... (feat. X",
# "... (radio edit", "... (extended mix", "... (official video"). This closes
# the gap left by _TITLE_DECORATION, which only matches a BALANCED "(...)"/"[...]"
# group. CONSERVATIVE: "part"/"pt"/digits are deliberately absent from the
# allowlist, so a genuine open tail like "Song (Part 2" stays distinct from
# "Song"; only a trailing open tail whose first word is a decoration keyword is
# removed. MATCH key only - never touches on-disk names or tags.
_OPEN_TAIL_DECORATION = re.compile(
    r"\s*[\(\[]\s*"
    r"(?:extended|original|radio|remix|mix|version|edit|remaster(?:ed)?|"
    r"official|audio|video|visuali[sz]er|lyric[s]?|feat\.?|ft\.?|with|live|"
    r"acoustic|instrumental|demo|bonus|single)"
    r"[^\(\)\[\]]*$",   # rest of the open tail up to end, no closing bracket
    re.IGNORECASE,
)


def _strip_title_decoration(title: str) -> str:
    """
    Strip a trailing RUN of bitrate/format/version decoration from a title,
    for the MATCH key only (never for on-disk names or tags).

    Repeatedly removes, from the END of the string:
      - a trailing dash-suffixed bitrate tail ("- 320 Kbps", "- 320k"), and
      - a trailing parenthetical/bracketed group "(...)"/"[...]" whose content
        matches a known decoration keyword (_TITLE_DECORATION).
    Chained tails collapse, e.g.
      "Das Isch mini Schwiiz (Melodic Techno Version) - (320 Kbps)" -> base.
    Mid-title decoration is left alone - only a trailing run is removed. A
    non-decoration group like "(Part 2)" is kept, so "Song (Part 2)" stays
    distinct from "Song".
    """
    t = title or ""
    changed = True
    while changed:
        changed = False
        # Trailing "- <bitrate>" tail (no brackets needed).
        new = re.sub(r"\s*[-\u2013]\s*\d{2,4}\s*k(?:bps|bit|b)?\s*$", "", t,
                     flags=re.IGNORECASE)
        if new != t:
            t, changed = new.rstrip(), True
            continue
        # Trailing BARE bitrate token "320kbps"/"320k" (no brackets, no dash).
        # Requires the explicit "k" suffix so a plain trailing number that is
        # part of the title (e.g. "Area 51") is NOT stripped.
        new = re.sub(r"\s+\d{2,4}\s*k(?:bps|bit|b)?\s*$", "", t, flags=re.IGNORECASE)
        if new != t:
            t, changed = new.rstrip(), True
            continue
        # Trailing (...) / [...] group - strip only if it reads as decoration.
        m = re.search(r"\s*[\(\[]([^\(\)\[\]]*)[\)\]]\s*$", t)
        if m and _TITLE_DECORATION.search(m.group(1)):
            t, changed = t[:m.start()].rstrip(), True
            continue
        # Trailing UNBALANCED opener-tail (open "(" / "[" with no closing
        # bracket). Only strip when the tail's first word is a decoration
        # keyword so a genuine open tail like "Song (Part 2" (numeric/part, not
        # in the allowlist) is kept distinct from "Song".
        if re.search(r"[\(\[][^\(\)\[\]]*$", t):   # there IS an unclosed opener
            new = _OPEN_TAIL_DECORATION.sub("", t).rstrip()
            if new and new != t:
                t, changed = new, True
    return t.strip()


def _title_key(title: str) -> str:
    """Normalized song title for the comparison (identical to charts-songrequest)."""
    t = (title or "").lower()
    t = re.sub(r"\((?:feat|ft|with)\.?[^)]*\)", " ", t)
    t = re.sub(r"\[(?:feat|ft|with)\.?[^\]]*\]", " ", t)
    t = re.sub(r"\((?:remaster|remastered|.*version|.*edit|official|audio|video|lyric[s]?)[^)]*\)", " ", t)
    # Strip a trailing run of bitrate/format/version decoration so a decorated
    # title ("Into You (320 Kbps)") and the clean library title ("Into You")
    # fold to the SAME match key. MATCH key only - the real title is still used
    # for disk/tag writes.
    t = _strip_title_decoration(t)
    # Transliterate accents (ä -> a, é -> e) BEFORE the [a-z0-9] fold so an
    # umlaut is folded, not dropped. Mirrors the charts-sync side
    # (library.normalize / deezer._normalize) so both subsystems agree on the
    # same key for 'liäbi' == 'liabi'. MATCH key only - never touches what is
    # written to disk or tags.
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", t)


def _extract_title_from_filename(filename: str) -> str:
    """
    Gets the song title from a file name. Handles both schemes:
      'Into You.mp3'                                  -> 'Into You'
      'Artist - Album - 01 - Into You.mp3'            -> 'Into You'
      '01 - Into You.mp3'                             -> 'Into You'
    For ' - '-separated names takes the LAST part (with Lidarr naming the title
    is at the end), otherwise the cleaned whole name.
    """
    base = os.path.splitext(os.path.basename(filename))[0]
    base = re.sub(r"\s*\(\d+\)\s*$", "", base)   # (2), (3)
    base = re.sub(r"_\d{6,}$", "", base)          # slskd suffix
    if " - " in base:
        base = base.rsplit(" - ", 1)[-1]
    base = re.sub(r"^\s*\d{1,3}\s*[-.\)]\s*", "", base)  # leading track no.
    return base.strip()


def _artist_key(artist: str) -> str:
    """
    Normalized artist key for MATCHING only (never for on-disk folder names).
    Folds dot/underscore/whitespace runs plus case AND transliterates accents
    (NFKD fold), identical to charts-songrequest / presence.py, so 'DJ.ILHAM' ==
    'DJ ILHAM' -> 'djilham' and 'Motörhead' == 'Motorhead' -> 'motorhead'.
    Accent-folds like _title_key so the two axes agree. MATCH key only.
    """
    a = unicodedata.normalize("NFKD", (artist or "").lower())
    a = "".join(c for c in a if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", a)


def _find_artist_dir(music_dir: str, artist: str) -> str | None:
    """
    Finds the artist folder under music_dir - folding dot/underscore/whitespace
    and case via _artist_key (identical to charts-songrequest / presence.py). On
    ext4 folder names are case-sensitive, so we compare on the normalized match
    key. An empty artist resolves to None (no "Unknown Artist" bucket probe),
    matching the presence-side empty fallback. Fail-open: OSError -> None.
    """
    from .tags import _clean_component  # local, to avoid a circular import
    safe_artist = _clean_component(artist, "")
    if not safe_artist:
        return None
    # 1. Fast path: the exact (filesystem-literal) folder exists.
    exact = os.path.join(music_dir, safe_artist)
    if os.path.isdir(exact):
        return exact
    # 2. Match-key comparison across all artist folders (folds dot/space/case).
    target = _artist_key(artist)
    if not target:
        return None
    try:
        for name in os.listdir(music_dir):
            if _artist_key(name) == target and os.path.isdir(os.path.join(music_dir, name)):
                return os.path.join(music_dir, name)
    except OSError:
        pass
    return None


def _song_present_for_artist(music_dir: str, artist: str, title: str) -> str | None:
    """
    True (as a path) if the same song already sits SOMEWHERE under
    Music/<Artist>/ - no matter which album folder. Covers the case where Lidarr
    keeps the song as an album track (e.g. 'Into You (2016)/Artist - Into You -
    01 - Into You.mp3') while charts-download loads it as a single ('Into
    You/Into You.mp3'). Without this check two album folders (duplicate) would
    arise.

    Compares via the normalized SONG TITLE (_title_key) so the different
    file/album namings do not matter. The artist folder is resolved via
    _find_artist_dir (exact, else _artist_key fold), so a folder that differs
    only by case/punctuation/accent (e.g. 'DJ ILHAM' vs 'DJ.ILHAM',
    'Motorhead' vs 'Motörhead') is still recognized - identical to the
    charts-songrequest / presence.py artist resolution.
    Returns: path of the already-present file, otherwise None.
    """
    artist_dir = _find_artist_dir(music_dir, artist)
    if not artist_dir:
        return None
    want = _title_key(title)
    if not want:
        return None
    try:
        for album in os.listdir(artist_dir):
            album_path = os.path.join(artist_dir, album)
            if not os.path.isdir(album_path):
                continue
            for name in os.listdir(album_path):
                ext = os.path.splitext(name)[1].lower()
                if ext not in config.AUDIO_EXTENSIONS:
                    continue
                if _title_key(_extract_title_from_filename(name)) == want:
                    return os.path.join(album_path, name)
    except OSError:
        pass
    return None


def import_downloads() -> "tuple[list[tuple[str, str, str, str, str, str]], list[dict], list[tuple[str, str]]]":
    """
    Runs an import pass.
    Returns: (imported songs as tuples (target_path, source, artist, title,
    album, notify_device), rejected entries as dicts with
    path/artist/title/reason). "source" is the playlist label from the manifest
    (e.g. "charts", "mundart"), empty if unknown. "notify_device" is only set
    for songs manually requested via charts-songrequest (the user's Pushover
    device), otherwise empty.
    """
    if not os.path.isdir(config.DOWNLOADS_DIR):
        logger.warning(t("import.scan.no_downloads_dir", path=config.DOWNLOADS_DIR))
        return [], []

    # Load the manifest fresh at the start of every run (charts-download updates
    # it between runs).
    tags.reload_manifest()
    # Reset the per-pass live-lookup budget for the import-time metadata resolver
    # (Deezer/MB caps). Cache hits never consume budget.
    resolve.reset_run_budget()

    imported: list[tuple[str, str]] = []
    rejected: list[dict] = []
    flagged_upper: list[tuple[str, str]] = []   # UPPERCASE, unresolvable by Deezer
    skipped_no_manifest = 0
    for src in _iter_audio_files(config.DOWNLOADS_DIR):
        if not _is_stable(src, config.MIN_AGE_SECONDS):
            logger.debug(t("import.scan.not_stable", path=src))
            continue

        target, meta = tags.build_target_path(config.MUSIC_DIR, src)
        artist, title, album, from_manifest, unresolved_upper = meta

        # Upgrade request? charts-songrequest sets "replace": true in the
        # manifest when a better version of an already-present file was loaded.
        _m = tags.manifest_lookup(src)
        want_replace = bool(_m and _m.get("replace"))
        is_upgrade = False   # becomes True when an existing file is replaced
        upgrade_old_times = None  # (atime, mtime) of the replaced file on upgrade
        old_year = ""        # year tag of the replaced file (upgrade floor)
        old_album = ""       # album tag of the replaced file (upgrade floor)

        # Only import songs specifically requested by charts-download
        # (REQUIRE_MANIFEST). The downloads folder often contains whole sampler/
        # album folders that came along with the song download from Soulseek.
        # Without this lock charts-import would file in arbitrary tracks from
        # them (e.g. compilation versions) - and on EVERY run again, as long as
        # the source files sit in the (shared) downloads folder. With the lock
        # only what is in the manifest counts (= loaded by charts-download).
        if config.REQUIRE_MANIFEST and not from_manifest:
            skipped_no_manifest += 1
            logger.debug(t("import.scan.no_manifest_entry", path=src))
            continue

        # Gatekeeper: do not file in suspicious/unusable files, move them to
        # quarantine instead.
        reason = _rejection_reason(src, artist)
        if reason:
            logger.info(t("import.scan.rejected", reason=reason, path=src))
            _quarantine(src)
            # Pass along the chart metadata (from the manifest) so the song is
            # released in the charts-download state and loaded again (better).
            m = tags.manifest_lookup(src)
            rejected.append({
                "path": src,
                "artist": (m or {}).get("artist", artist),
                "title": (m or {}).get("title", title),
                "reason": reason,
            })
            continue

        # Already in the library?
        # Important against (2)/(3)/(4) duplicates: the same song is often
        # loaded from several providers with DIFFERENT file names but would end
        # up in the same target album folder. We check whether a file with the
        # same track name is already there (ignores (n) suffixes and extension)
        # and skip then - no matter what the source file is called.
        # Cross-album check: does the same song already sit somewhere for the
        # artist (single OR Lidarr album track)? That file is the "existing"
        # one - a candidate for an upgrade or a reason to skip.
        existing = _song_present_for_artist(config.MUSIC_DIR, artist, title)
        # If the TARGET folder itself already has the same one, take that path.
        if not existing and _song_already_present(target):
            existing = target  # marks "present" (path only symbolic)

        if existing and os.path.isfile(existing):
            old_br = _read_bitrate(existing)
            new_br = _read_bitrate(src)
            # Quality comparison via the REAL frequency cutoff (not just the
            # possibly faked bitrate): quality_key lets the measured cutoff
            # dominate, the bitrate is only a tie-breaker. This way an upsampled
            # "320" is NOT rated better than an honest file - and a real upgrade
            # (higher cutoff) is detected even if both claim the same bitrate.
            old_q = quality.quality_key(existing, old_br)
            new_q = quality.quality_key(src, new_br)
            if want_replace and new_q > old_q:
                # QUALITY UPGRADE: old version to trash, new one in ITS place
                # (same folder/name) so no duplicate arises.
                logger.info(t("import.upgrade.replacing", artist=artist, title=title,
                              old_br=old_br, old_q=f"{old_q:.0f}", new_br=new_br,
                              new_q=f"{new_q:.0f}",
                              path=os.path.relpath(existing, config.MUSIC_DIR)))
                target = existing            # write to the same location
                # Capture the OLD file's (atime, mtime) BEFORE it is trashed, so
                # the in-place upgrade can inherit it. An upgrade is the SAME
                # song at a better bitrate, not a new addition - it must not
                # jump to the top of Navidrome's "recently added". This is the
                # one place a new file deliberately keeps an OLD mtime.
                if config.PRESERVE_MTIME:
                    try:
                        _st = os.stat(existing)
                        upgrade_old_times = (_st.st_atime, _st.st_mtime)
                    except OSError:
                        upgrade_old_times = None
                # Capture the OLD file's year/album BEFORE it is trashed so the
                # re-correction floor below never leaves the replacement worse
                # than before (Decision 5). Fail-open to ("","").
                old_year, old_album = tags.read_existing_year_album(existing)
                _trash(existing)             # move the old version aside
                is_upgrade = True            # skip _unique_target
                # the new file is copied to `target` further below
            else:
                if want_replace:
                    logger.info(t("import.upgrade.not_better", artist=artist,
                                  title=title, old_q=f"{old_q:.0f}",
                                  new_q=f"{new_q:.0f}"))
                else:
                    logger.info(t("import.upgrade.already_present", artist=artist,
                                  title=title,
                                  path=os.path.relpath(existing, config.MUSIC_DIR)))
                continue
        elif _song_already_present(target):
            logger.debug(t("import.upgrade.already_in_library", path=src))
            continue

        # On an upgrade, write to the SAME location (do not append "(2)").
        if not is_upgrade:
            target = _unique_target(target)
        os.makedirs(os.path.dirname(target), exist_ok=True)

        try:
            if config.COPY_MODE:
                shutil.copy2(src, target)
                logger.info(t("import.file.copied", src=src, target=target))
            else:
                shutil.move(src, target)
                logger.info(t("import.file.moved", src=src, target=target))
            # On a manifest hit: write clean chart tags into the target file so
            # Navidrome/charts-sync see correct values.
            if from_manifest:
                # Resolve-once -> compute -> write-once (Decision 3). A FREE year
                # charts-import already has (year-map hint or Lidarr "(YYYY)"
                # folder) keeps precedence; the import-time metadata resolver
                # (Deezer->MB) enriches year + a real album + track on every
                # tier. A download file's own embedded date is NEVER trusted
                # (the resolver reads Deezer/MB, not the file; a stale date is
                # only ever cleared by write_tags, never adopted).
                #
                # Year precedence:
                #   (a) confirmed bare-string year-map hint -> wins;
                #   (b) PENDING midpoint hint vs. existing Lidarr "(YYYY)"
                #       folder -> folder year wins (confirm), else the midpoint
                #       (stays pending);
                #   (c) existing Lidarr "(YYYY)" folder, no hint -> wins;
                #   (d) resolver meta.year (fallback meta.album_year) -> wins;
                #   (e) nothing -> year "", pending marker.
                meta = resolve.resolve_metadata(artist, title)
                hint_year, hint_pending = _hint_year(artist, title)
                folder_year = yearmap._album_folder_year(existing or "")

                final_year = ""
                confirm_folder = False   # (b) folder-wins side effect
                mark_pending = False     # (e) generic pending marker
                if hint_pending:
                    if folder_year:
                        final_year = folder_year
                        confirm_folder = True
                    else:
                        # Leave the charts-energy object in the map as-is (the
                        # backfill sharpens it); just tag the midpoint on disk.
                        final_year = hint_year
                elif hint_year:
                    final_year = hint_year
                elif folder_year:
                    final_year = folder_year
                elif meta.year:
                    final_year = meta.year
                elif meta.album_year:
                    # album_year is a FALLBACK source for date only (Decision 6).
                    final_year = meta.album_year
                else:
                    final_year = ""
                    mark_pending = True

                # Album/track from the resolver, independent of the year tier: a
                # real non-compilation album replaces the default album=title.
                final_album = album
                if meta.album and not resolve.is_compilation_title(meta.album):
                    final_album = meta.album
                track = meta.track

                # charts-upgrade "never worse" floor (Decision 5): a missed
                # fresh lookup must not drop the old file's year/album.
                if is_upgrade:
                    if not final_year and old_year:
                        final_year = old_year
                        mark_pending = False  # a real year is now known
                    if (final_album == album and old_album
                            and old_album.strip().lower() != "unknown album"
                            and not resolve.is_compilation_title(old_album)
                            and old_album.strip().lower() != title.strip().lower()):
                        final_album = old_album

                # Single write. Pass the import source so a YouTube-sourced
                # file gets the durable TXXX:CHARTS_SOURCE=youtube marker, which
                # lets the metadata backfill later replace its (untrusted)
                # YouTube year with the real release year. _m is the manifest
                # entry already looked up above (reused, not re-fetched).
                tags.write_tags(target, artist, title, final_album,
                                year=final_year, album_year=meta.album_year,
                                track=track, source=_import_source(_m))
                album = final_album
                # Side effects by branch (unchanged semantics).
                if confirm_folder:
                    data = yearmap.load()
                    yearmap.confirm(
                        data, f"{artist.lower()}|{title.lower()}", folder_year)
                    yearmap.rewrite(data)
                elif mark_pending:
                    _ensure_pending_marker(artist, title, src="import-generic")
                logger.info(t("import.file.tags_set", artist=artist, title=title,
                              album=album))
                # Find a matching cover via COVER_SOURCES (iTunes->Deezer) and
                # embed it. For chart songs (manifest) prefer the single cover
                # and replace a possibly present compilation/sampler cover
                # (controllable via OVERWRITE_COVER).
                if cover.embed_cover(target, artist, title,
                                     overwrite=config.OVERWRITE_COVER):
                    logger.info(t("import.file.cover_embedded", artist=artist, title=title))
                # Loudness normalization: compute and embed the ReplayGain track
                # tag (lossless; an RG-capable player controls playback). Only
                # if REPLAYGAIN is active and ffmpeg is present.
                if config.REPLAYGAIN and replaygain.apply_track_gain(target):
                    logger.info(t("import.file.replaygain_set", artist=artist, title=title))
            # Playlist origin + optional Pushover target device from the
            # manifest. notify_device is only set for songs manually requested
            # via charts-songrequest -> then main.py reports availability to
            # exactly that device.
            m = tags.manifest_lookup(src)
            # Combined label of EVERY playlist the song belongs to, e.g.
            # "charts+hitparade" (Option B: one line per song, all sources).
            # manifest_sources handles old single-"source" entries too.
            source = "+".join(tags.manifest_sources(m or {}))
            notify_device = (m or {}).get("notify_device", "") if m else ""
            imported.append((target, source, artist, title, album, notify_device))
            # Trustworthy import log for charts-digest: one line per song that
            # was ACTUALLY filed in (time + source + artist/title). This way the
            # daily summary does not have to guess via mtime/title - charts-import
            # knows exactly here what came in and from which source.
            _append_import_log(source, artist, title)
            # UPPERCASE name that Deezer could not resolve reliably (alias
            # artist / title misnamed by FLASHBACK): imported anyway (no gap),
            # but flagged for manual review.
            if unresolved_upper:
                flagged_upper.append((artist, title))
                logger.info(t("import.file.flagged_upper", artist=artist, title=title))
            # On an in-place upgrade: stamp the replacement with the OLD file's
            # mtime, LAST of all - after copy + tag/cover/ReplayGain writes - so
            # nothing re-bumps it. The upgraded song keeps its original "date
            # added" instead of looking freshly imported. (First-time imports
            # have upgrade_old_times=None and keep their fresh copy mtime.)
            if is_upgrade and upgrade_old_times is not None:
                tags.restore_mtime(target, upgrade_old_times)
        except Exception as e:
            logger.error(t("import.file.failed", path=src, error=e))

    if skipped_no_manifest:
        logger.info(t("import.scan.skipped_no_manifest", count=skipped_no_manifest))

    return imported, rejected, flagged_upper
