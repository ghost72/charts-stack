"""
Search index + normalization helpers for the Manual Tag Editor (copied subset of
charts-songrequest/app/library.py plus a new in-memory search index).

Copied verbatim from charts-songrequest so the folding keys stay identical
across services: AUDIO_EXTENSIONS, _clean_component, _title_key, _artist_key,
_extract_title_from_filename. The build_search_index / search / classify
functions below are NEW to this copy.

Design trade-offs (documented per the design):
  * Lazy TTL-cached index. A keystroke must not re-walk thousands of folders, so
    the index is built once by a single os.walk and reused until
    config.TAGEDITOR_INDEX_TTL expires; then the next search rebuilds it. Cost:
    one full walk every <= TTL seconds and a few MB of RAM; benefit: every
    search is an in-memory O(n) substring scan over cached strings.
  * Path-derived display. During indexing NO per-file mutagen read happens (that
    is the expensive part, deferred to editor.load_song on selection). The
    display artist/album/title come from the PATH (artist = parent-of-parent
    dir, album = parent dir, title = _extract_title_from_filename(name)). This
    can differ from the real tags; it is acceptable because the index is only
    for finding candidates and load_song re-reads the authoritative tag values.
"""

import os
import re
import shutil
import threading
import time
import unicodedata

from . import config, tags

# Extensions recognized as audio (identical to charts-songrequest/charts-import).
AUDIO_EXTENSIONS = {".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".wma"}

# Invalid characters for folder names (like tags._clean_component elsewhere).
_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _clean_component(value: str, fallback: str) -> str:
    """Sanitize a path component - copied from charts-songrequest."""
    value = (value or "").strip()
    if not value:
        value = fallback
    value = _INVALID_CHARS.sub("_", value)
    value = re.sub(r"\s+", " ", value).strip()
    value = value.rstrip(". ")
    return value or fallback


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
    """Normalized song title for the comparison (identical to charts-songrequest).

    Lowercase, strip (feat/ft/with) and version parentheticals, strip a trailing
    run of bitrate/format/version decoration (_strip_title_decoration),
    transliterate accents (NFKD fold), then keep only [a-z0-9]. MATCH key only.
    """
    t = (title or "").lower()
    t = re.sub(r"\((?:feat|ft|with)\.?[^)]*\)", " ", t)
    t = re.sub(r"\[(?:feat|ft|with)\.?[^\]]*\]", " ", t)
    t = re.sub(r"\((?:remaster|remastered|.*version|.*edit|official|audio|video|lyric[s]?)[^)]*\)", " ", t)
    # Strip a trailing run of bitrate/format/version decoration so a decorated
    # title ("Into You (320 Kbps)") and the clean library title ("Into You")
    # fold to the SAME match key. MATCH key only - on-disk names/tags use the
    # raw title via _clean_component.
    t = _strip_title_decoration(t)
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", t)


def _artist_key(artist: str) -> str:
    """Normalized artist key for matching (behaviourally identical to the
    charts-songrequest / presence.py copies).

    Folds dot/underscore/whitespace/case to one key (e.g. 'DJ.ILHAM' ==
    'DJ ILHAM' -> 'djilham') AND transliterates accents (NFKD fold), so
    'Motörhead' == 'Motorhead' -> 'motorhead'. Accent-folds like _title_key so
    the two axes agree. MATCH key only - never used for on-disk folder names.
    """
    a = unicodedata.normalize("NFKD", (artist or "").lower())
    a = "".join(c for c in a if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", a)


def _extract_title_from_filename(filename: str) -> str:
    """Get the song title from a file name (identical to charts-songrequest)."""
    base = os.path.splitext(os.path.basename(filename))[0]
    base = re.sub(r"\s*\(\d+\)\s*$", "", base)   # (2), (3)
    base = re.sub(r"_\d{6,}$", "", base)          # slskd suffix
    if " - " in base:
        base = base.rsplit(" - ", 1)[-1]
    base = re.sub(r"^\s*\d{1,3}\s*[-.\)]\s*", "", base)  # leading track no.
    return base.strip()


# --- Audio info reader (read-only file/bitrate field + play/download gating) --
# Lossless formats a browser's <audio> generally cannot (reliably) decode and
# for which a "kbps" number is meaningless. FLAC is the one that matters for this
# library (leftover non-MP3 files); the rest are listed for correctness.
_LOSSLESS_EXTS = {".flac", ".wav", ".ape", ".aiff", ".aif"}


def read_audio_info(path: str) -> dict:
    """
    Read display-only audio info for the tag editor's read-only field and the
    play/download gating. Pure + fail-open: it NEVER raises; an unreadable file
    yields a best-effort dict derived from the extension alone (bitrate_kbps 0).

    Returns:
      {
        "ext": ".mp3",            # lowercased extension
        "is_mp3": True,           # only MP3 plays natively in-browser
        "is_lossless": False,     # FLAC/WAV/... (the frontend shows a marker)
        "playable": True,         # == is_mp3 (browsers may not decode FLAC)
        "bitrate_kbps": 320,      # kbps (mutagen bit/s -> kbps); 0 if unknown
      }

    The human-readable label (e.g. "320 kbps" / "FLAC (lossless)") is composed
    on the frontend via i18n so no German/English is baked into a Python string;
    this reader only returns structured, language-neutral fields.
    """
    ext = os.path.splitext(path or "")[1].lower()
    is_mp3 = ext == ".mp3"
    is_lossless = ext in _LOSSLESS_EXTS
    bitrate_kbps = 0
    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(path)
        if audio is not None and audio.info is not None:
            br = getattr(audio.info, "bitrate", 0) or 0
            bitrate_kbps = int(br / 1000)  # mutagen returns bit/s -> kbps
    except Exception:
        # Fail-open: keep the extension-derived fields, leave bitrate at 0.
        pass
    return {
        "ext": ext,
        "is_mp3": is_mp3,
        "is_lossless": is_lossless,
        "playable": is_mp3,
        "bitrate_kbps": bitrate_kbps,
    }


# --- "Folder follows the artist tag" move primitives ------------------------
# When a tag-editor save changes the Artist, the edited file is moved on disk
# into the resolved artist folder so a path-derived search no longer surfaces
# the old folder name. These pure filesystem helpers (no FastAPI, no network)
# are unit-testable with tmp dirs; editor.save_song is the thin orchestrator.
#
# The one-folder guarantee lives in _find_artist_dir: the destination artist
# folder is resolved against EXISTING folders via this module's accent-folding
# _artist_key, so key(P!nk) == key(Pink) -> the file moves INTO the established
# Pink/ folder and a second P!nk/ is never created. Only a stack-single
# <Artist>/<Title>/<Title>.mp3 is ever moved; Lidarr (Year)/NN - Title albums
# and curated compilations are left in place.

# Ignorable sidecar entries: a folder holding only these counts as empty.
_CLEANUP_IGNORE = {"@eaDir", "#recycle", ".DS_Store"}

# Compilation / Various-Artists markers (NEW local constant; charts-config has
# no compilation helper). Mirrors - not imports - the path heuristic documented
# in charts-import/charts-sync. Folded artist-folder names and lowercased album
# basenames that hit a marker are never moved (they are hand-managed samplers).
_COMPILATION_MARKERS = {"various", "variousartists", "va"}
_COMPILATION_ALBUM_MARKERS = (
    "top 100", "bravo hits", "kuschelrock", "sampler", "best of", "mastermix",
)

# Lidarr '<Album> (YYYY)' album-folder suffix and the leading 'NN - Title'
# track-name pattern - either marks an album-managed file (not a stack single).
_YEAR_ALBUM_RE = re.compile(r"\(\d{4}\)\s*$")
_NN_TITLE_RE = re.compile(r"^\s*\d{1,3}\s*-\s+")


# --- Loose "could be the same artist" stylization table ---------------------
# loose_key folds a small FIXED set of leet/stylization substitutions (so
# 'P!nk' -> 'pink', 'Ke$ha' -> 'kesha') BEFORE the strict [a-z0-9] fold. It is
# used ONLY to DETECT an ambiguous candidate worth asking the user about - it is
# NEVER used to auto-merge, never for on-disk folder names, and never for the
# search index. No edit-distance, no fuzzy/semantic matching: names that do not
# match under strict_key OR this fixed table are different artists, full stop.
_LOOSE_SUBSTITUTIONS = str.maketrans({
    "$": "s", "!": "i", "0": "o", "1": "i", "3": "e",
    "4": "a", "@": "a", "5": "s", "7": "t",
})


def _loose_key(artist: str) -> str:
    """
    Looser artist key for AMBIGUITY DETECTION ONLY (never auto-merge / folder
    names / index). lowercase -> NFKD accent-fold -> fixed stylization table
    (single pass) -> drop all non-[a-z0-9]. 'P!nk' -> 'pink', 'Ke$ha' -> 'kesha'.
    """
    a = unicodedata.normalize("NFKD", (artist or "").lower())
    a = "".join(c for c in a if not unicodedata.combining(c))
    a = a.translate(_LOOSE_SUBSTITUTIONS)
    return re.sub(r"[^a-z0-9]+", "", a)


def _find_artist_dir(music_dir: str, artist: str) -> str | None:
    """
    Resolve the on-disk artist folder under music_dir for an AUTO merge: literal
    folder-name fast path, then a STRICT _artist_key fold across all folders. So
    'rod stewart'/'Rod Stewart', 'DJ.ILHAM'/'DJ ILHAM' and 'Motörhead'/'Motorhead'
    resolve to the same existing folder. Returns the folder path or None.
    Fail-open (OSError -> None).

    NOTE: this uses the STRICT _artist_key only. The looser stylization match
    (_loose_key) is deliberately NOT used here - a loose-only match is ambiguous
    and routed to the decision dialog, never auto-merged. Copies the resolver
    STRUCTURE from charts-songrequest/app/library.py but binds to the local
    accent-folding _artist_key, so it is not a byte-for-byte copy.
    """
    safe_artist = _clean_component(artist, "")
    if not safe_artist:
        return None
    exact = os.path.join(music_dir, safe_artist)
    if os.path.isdir(exact):
        return exact
    target = _artist_key(artist)
    if not target:
        return None
    try:
        for name in os.listdir(music_dir):
            if _artist_key(name) == target and os.path.isdir(
                    os.path.join(music_dir, name)):
                return os.path.join(music_dir, name)
    except OSError:
        pass
    return None


def _find_loose_artist_dir(music_dir: str, artist: str, exclude: str) -> str | None:
    """
    Find an existing artist folder that matches `artist` under _loose_key but
    NOT under the strict _artist_key - i.e. a stylized near-spelling worth a
    decision dialog (e.g. existing 'Pink/' when the user typed 'P!nk'). Skips
    the folder named `exclude` (the current source artist folder). Returns the
    folder path or None. Fail-open.

    Only a loose-but-not-strict match qualifies: a strict match is handled by
    _find_artist_dir (auto), so this never shadows the auto path.
    """
    target_loose = _loose_key(artist)
    target_strict = _artist_key(artist)
    if not target_loose:
        return None
    exclude_base = os.path.basename(exclude.rstrip(os.sep)) if exclude else ""
    try:
        for name in os.listdir(music_dir):
            if name == exclude_base:
                continue
            path = os.path.join(music_dir, name)
            if not os.path.isdir(path):
                continue
            if _artist_key(name) == target_strict:
                continue  # strict match -> auto path, not a dialog candidate
            if _loose_key(name) == target_loose:
                return path
    except OSError:
        pass
    return None


def _count_audio_files(folder: str, exclude_path: str = "") -> int:
    """
    Count audio files anywhere under `folder` (recursively), skipping the
    @eaDir/#recycle sidecar trees and optionally one specific file (exclude_path,
    the edited file). Used to decide 'does this folder hold OTHER songs' and
    'would the old folder empty out'. Fail-open -> 0 on error.
    """
    excl = os.path.realpath(exclude_path) if exclude_path else ""
    count = 0
    try:
        for root, dirs, files in os.walk(folder):
            dirs[:] = [d for d in dirs if d not in _CLEANUP_IGNORE]
            for name in files:
                ext = os.path.splitext(name)[1].lower()
                if ext not in AUDIO_EXTENSIONS:
                    continue
                if excl and os.path.realpath(os.path.join(root, name)) == excl:
                    continue
                count += 1
    except OSError:
        return count
    return count


def _build_move_target(music_dir: str, cur_path: str, artist: str,
                       title: str, album: str) -> str:
    """
    Build the move target Music/<Artist>/<Album>/<Title>.<ext>.

    - artist folder: _find_artist_dir resolved (established spelling wins when a
      key-matching folder exists); else _clean_component(artist, ...).
    - album component: _clean_component(album, "Unknown Album"). For a stack
      single album == title, so a title change repoints the inner folder too.
    - track name: _clean_component(title, "track") + ext (ext from cur_path).

    Mirrors charts-import.build_target_path, differing only in resolving the
    artist folder through the accent-folding _artist_key. '!' survives
    _clean_component unchanged (not in _INVALID_CHARS), so 'P!nk'/'Snap!' are
    byte-stable; folder comparison is on the key fold, never the raw string.
    """
    resolved = _find_artist_dir(music_dir, artist)
    if resolved:
        safe_artist = os.path.basename(resolved)
    else:
        safe_artist = _clean_component(artist, "Unknown Artist")
    safe_album = _clean_component(album, "Unknown Album")
    ext = os.path.splitext(cur_path)[1].lower()
    track_name = _clean_component(title, "track") + ext
    return os.path.join(music_dir, safe_artist, safe_album, track_name)


def _is_movable_single(music_dir: str, cur_path: str) -> bool:
    """
    True only for a stack-single <Artist>/<Title>/<Title>.mp3 layout.

    False (=> do not move, write tags in place) when:
      - the album folder matches the Lidarr '<Album> (YYYY)' pattern,
      - the basename matches a leading 'NN - Title' track pattern, or
      - the artist folder is a compilation/Various folder, or the album basename
        carries a compilation marker (Bravo Hits, sampler, ...).
    Fail-safe: any read error is treated as NOT movable (prefer in-place).
    """
    try:
        album_dir = os.path.dirname(cur_path)
        artist_dir = os.path.dirname(album_dir)
        album_base = os.path.basename(album_dir)
        artist_base = os.path.basename(artist_dir)
        name_no_ext = os.path.splitext(os.path.basename(cur_path))[0]
    except Exception:
        return False
    # Lidarr year-suffixed album folder.
    if _YEAR_ALBUM_RE.search(album_base):
        return False
    # Lidarr 'NN - Title' track name.
    if _NN_TITLE_RE.match(name_no_ext):
        return False
    # Compilation / Various-Artists folder.
    if _artist_key(artist_base) in _COMPILATION_MARKERS:
        return False
    album_low = album_base.lower()
    if any(marker in album_low for marker in _COMPILATION_ALBUM_MARKERS):
        return False
    return True


def _target_collision(target: str, title: str) -> str | None:
    """
    Return a collision reason ("target_exists") or None.

    Collision if the exact target file already exists, OR a same-_title_key but
    different-filename audio file already sits in the target's album folder
    (Lidarr 'NN - Title' vs single 'Title'). Never overwrite, never write a
    parallel '(2)'. Fail-safe: an OSError scanning the destination is treated as
    a collision (refuse) - the safest default.
    """
    if os.path.exists(target):
        return "target_exists"
    album_dir = os.path.dirname(target)
    if not os.path.isdir(album_dir):
        return None
    want = _title_key(title)
    if not want:
        return None
    target_base = os.path.basename(target)
    try:
        for name in os.listdir(album_dir):
            if name == target_base:
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext not in AUDIO_EXTENSIONS:
                continue
            if _title_key(_extract_title_from_filename(name)) == want:
                return "target_exists"
    except OSError:
        return "target_exists"
    return None


def _move_file(src: str, dst: str) -> None:
    """
    Move src -> dst with os.rename, preserving mtime. Creates the destination
    album folder first (directory creation is cross-device-safe; only the file
    rename can raise EXDEV). Snapshots (atime, mtime) of src BEFORE the rename
    and restores them on dst AFTER (the path-bound preserve_mtime cannot be used
    because src is gone after the rename). On one ext4 mount os.rename already
    preserves mtime; this is belt-and-suspenders and re-stamps correctly if the
    platform ever differs.

    Raises OSError (the caller maps EXDEV -> cross_device, any other ->
    move_failed). NEVER falls back to shutil.move/copy.
    """
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    times = None
    if config.PRESERVE_MTIME:
        try:
            st = os.stat(src)
            times = (st.st_atime, st.st_mtime)
        except OSError:
            times = None
    os.rename(src, dst)
    if times is not None:
        tags.restore_mtime(dst, times)


def _rename_artist_folder(old_dir: str, new_name: str) -> str:
    """
    Rename an artist folder old_dir to its sibling `new_name` (used by rule 3:
    the old folder is about to hold only the edited file, so renaming the whole
    folder keeps one folder and moves every inner subfolder/file with it).
    Returns the NEW folder path. Raises OSError (caller maps EXDEV/other).
    NEVER falls back to copy. The destination must not already exist (the caller
    only picks this path when no strict/loose folder matches).
    """
    new_dir = os.path.join(os.path.dirname(old_dir), new_name)
    os.rename(old_dir, new_dir)
    return new_dir


def _cleanup_empty_dirs(*dirs: str) -> None:
    """
    For each dir (in the given order - pass deepest first), remove it if,
    ignoring @eaDir/#recycle/.DS_Store, it holds no real entries. The ignorable
    sidecars are removed first (os.remove a file, shutil.rmtree an @eaDir
    directory) ONLY when nothing else remains, then os.rmdir the now-empty dir.
    Fail-open: never raises; a dir that still holds real audio is left alone and
    never recursively force-deleted.
    """
    for d in dirs:
        try:
            if not d or not os.path.isdir(d):
                continue
            entries = list(os.scandir(d))
            if any(e.name not in _CLEANUP_IGNORE for e in entries):
                continue  # holds real content -> keep
            # Only ignorable sidecars remain: remove them, then the dir.
            for e in entries:
                try:
                    if e.is_dir():
                        shutil.rmtree(e.path)
                    else:
                        os.remove(e.path)
                except OSError:
                    pass
            os.rmdir(d)
        except OSError:
            pass


# --- Lazily-built, TTL-cached in-memory search index ------------------------
# A module-level list of per-file records, guarded by a lock. Rebuilt on the
# next search after the TTL expires. Each record: path, folded artist-key,
# folded title-key, a path-derived display (artist/album/title) and the
# lowercased basename (for the advisory manifest fast-path lookup in classify).

_INDEX_LOCK = threading.Lock()
_index: list[dict] | None = None
_index_dir: str | None = None
_index_ts: float = 0.0


def _folded_query(query: str) -> str:
    """Accent-fold a query to [a-z0-9] the same way the keys are folded."""
    q = unicodedata.normalize("NFKD", (query or "").lower())
    q = "".join(c for c in q if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", q)


def _build_records(music_dir: str) -> list[dict]:
    """
    One bounded os.walk over music_dir collecting a record per audio file. NO
    per-file mutagen read (display values are path-derived). Symlink loops are
    avoided via followlinks=False. Fail-open: an unreadable tree yields [].
    """
    records: list[dict] = []
    if not music_dir or not os.path.isdir(music_dir):
        return records
    try:
        for root, dirs, files in os.walk(music_dir, followlinks=False):
            # Never descend into sidecar/trash/hidden folders: the Synology
            # @eaDir/#recycle trees, the stack's own .charts-trash (deleted-file
            # bin from the upgrade/replace path), and any dot-folder. Editing a
            # file inside .charts-trash from the tag editor would be wrong - it
            # is scheduled for cleanup. Dirs are pruned in place so os.walk skips
            # their whole subtree.
            dirs[:] = [d for d in dirs
                       if d not in _CLEANUP_IGNORE and not d.startswith(".")]
            for name in files:
                ext = os.path.splitext(name)[1].lower()
                if ext not in AUDIO_EXTENSIONS:
                    continue
                path = os.path.join(root, name)
                # Path-derived display: artist = parent-of-parent dir,
                # album = parent dir, title from the file name.
                album_dir = os.path.basename(root)
                artist_dir = os.path.basename(os.path.dirname(root))
                disp_title = _extract_title_from_filename(name)
                records.append({
                    "path": path,
                    "artist_key": _artist_key(artist_dir),
                    "title_key": _title_key(disp_title),
                    "artist": artist_dir,
                    "album": album_dir,
                    "title": disp_title,
                    "basename": name.lower(),
                })
    except OSError:
        return records
    return records


def build_search_index(music_dir: str) -> list[dict]:
    """
    Return the cached search index for music_dir, rebuilding it when it is
    missing, older than config.TAGEDITOR_INDEX_TTL, or built for a different
    directory. Thread-safe (guarded by a module lock).
    """
    global _index, _index_dir, _index_ts
    now = time.monotonic()
    with _INDEX_LOCK:
        fresh = (_index is not None and _index_dir == music_dir
                 and (now - _index_ts) < config.TAGEDITOR_INDEX_TTL)
        if not fresh:
            _index = _build_records(music_dir)
            _index_dir = music_dir
            _index_ts = now
        return _index


def update_index_entry(path: str, artist: str, title: str) -> None:
    """
    Refresh the cached index record for one file after a successful save so a
    subsequent search reflects the just-written tags immediately.

    The index display + match keys are path-derived (see module docstring), so a
    tag-only edit would otherwise keep surfacing the file under its OLD name
    until the TTL expires and a full re-walk picks up any folder rename. This
    overlays the authoritative saved artist/title onto the matching record (and
    its folded keys) in place. It does NOT touch the TTL / rebuild schedule, so
    the normal lazy-cache behavior for every other file is unchanged.

    No-op when there is no built index yet (the next search builds a fresh one
    anyway) or the path is not present in it. Thread-safe.
    """
    with _INDEX_LOCK:
        if _index is None:
            return
        for rec in _index:
            if rec["path"] == path:
                if artist:
                    rec["artist"] = artist
                    rec["artist_key"] = _artist_key(artist)
                if title:
                    rec["title"] = title
                    rec["title_key"] = _title_key(title)
                break


def move_index_entry(old_path: str, new_path: str, artist: str,
                     title: str) -> None:
    """
    After a file move: drop the cached record for old_path and upsert a record
    for new_path so a search right after the save is consistent (the old path is
    no longer returned, the file is found at its new path/name).

    The upserted record is built the same way _build_records does: a
    path-derived display from the NEW path, with the authoritative saved
    artist/title (and their folded keys) overlaid. No-op when no index is built
    (the next search builds a fresh one anyway). Thread-safe.
    """
    with _INDEX_LOCK:
        if _index is None:
            return
        # Remove any stale record for the old path.
        for i in range(len(_index) - 1, -1, -1):
            if _index[i]["path"] == old_path:
                del _index[i]
        # Path-derived display for the new path (parent-of-parent = artist dir,
        # parent = album dir, title from the file name), then overlay the saved
        # artist/title + their keys.
        root = os.path.dirname(new_path)
        album_dir = os.path.basename(root)
        artist_dir = os.path.basename(os.path.dirname(root))
        disp_title = _extract_title_from_filename(os.path.basename(new_path))
        rec = {
            "path": new_path,
            "artist_key": _artist_key(artist or artist_dir),
            "title_key": _title_key(title or disp_title),
            "artist": artist or artist_dir,
            "album": album_dir,
            "title": title or disp_title,
            "basename": os.path.basename(new_path).lower(),
        }
        # Upsert: replace an existing new_path record if one is somehow present.
        for existing in _index:
            if existing["path"] == new_path:
                existing.update(rec)
                break
        else:
            _index.append(rec)


def search(query: str, limit: int = 20) -> list[dict]:
    """
    Case-insensitive, accent-folded SUBSTRING search over the index.

    A query shorter than 2 (trimmed) chars returns []. limit is clamped to
    [1, 50]. Only the first 200 chars of the query are folded (defensive). A
    result matches when the folded query is a substring of its folded artist-key
    OR title-key OR the folded 'artist title'. Returns up to `limit` records
    (path + path-derived display); origin classification is done by the caller
    only for the returned paths.
    """
    q = (query or "").strip()
    if len(q) < 2:
        return []
    limit = max(1, min(50, limit))
    folded = _folded_query(q[:200])
    if not folded:
        return []
    index = build_search_index(config.MUSIC_DIR)
    out: list[dict] = []
    for rec in index:
        combined = rec["artist_key"] + rec["title_key"]
        if (folded in rec["artist_key"] or folded in rec["title_key"]
                or folded in combined):
            out.append(rec)
            if len(out) >= limit:
                break
    return out


def classify(path: str, manifest_entries: dict) -> str:
    """
    Classify a file's origin as 'stack' or 'curated'.

    'stack' when the file carries a TXXX:CHARTS_SOURCE or TXXX:CHARTS_ALBUM_SRC
    marker (AUTHORITATIVE - durable across Lidarr renames) OR when a manifest
    entry matches its lowercased basename (ADVISORY fast path, slskd-suffix
    tolerant like charts-import's tags._manifest_lookup). Otherwise 'curated'.
    Called only for the <= limit returned paths, so the per-result marker reads
    (up to ~2 ID3 opens each) stay bounded.
    """
    # Authoritative: a durable TXXX marker written into the file bytes.
    if tags.read_charts_source(path) or tags.read_charts_album_src(path):
        return "stack"
    # Advisory: a manifest entry keyed by the (lowercased) basename, tolerating
    # an slskd numeric suffix before the extension ('name_639....mp3' -> 'name.mp3').
    base = os.path.basename(path.replace("\\", "/")).lower()
    if base in manifest_entries:
        return "stack"
    stripped = re.sub(r"_\d{6,}(\.[a-z0-9]+)$", r"\1", base)
    if stripped != base and stripped in manifest_entries:
        return "stack"
    return "curated"
