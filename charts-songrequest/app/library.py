"""
Check whether a song already exists in the Navidrome library.

Before the slskd search it is checked whether the requested song already
exists under MUSIC_DIR/<Artist>/ - regardless of which album folder and
regardless of whether it is a single or a Lidarr album track. This way no
duplicate is downloaded and the UI can immediately report "already present".

The logic is deliberately identical to charts-import (_song_present_for_artist /
_title_key / _extract_title_from_filename) so both services recognize the same
song as "present".
"""

import os
import re

# Extensions recognized as audio (like charts-import).
AUDIO_EXTENSIONS = {".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".wma"}

# Invalid characters for folder names (like tags._clean_component in charts-import).
_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _clean_component(value: str, fallback: str) -> str:
    """Sanitizes a path component (artist folder name) - like charts-import."""
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
    return t.strip()


def _title_key(title: str) -> str:
    """Normalized song title for the comparison (identical to charts-import)."""
    t = (title or "").lower()
    t = re.sub(r"\((?:feat|ft|with)\.?[^)]*\)", " ", t)
    t = re.sub(r"\[(?:feat|ft|with)\.?[^\]]*\]", " ", t)
    t = re.sub(r"\((?:remaster|remastered|.*version|.*edit|official|audio|video|lyric[s]?)[^)]*\)", " ", t)
    # Strip a trailing run of bitrate/format/version decoration so a decorated
    # upload title ("Inside My Soul (320 Kbps)") and the clean library title
    # ("Inside My Soul") fold to the SAME match key. MATCH key only - the real
    # submitted title is still used for disk/tag writes.
    t = _strip_title_decoration(t)
    return re.sub(r"[^a-z0-9]+", "", t)


def _artist_key(artist: str) -> str:
    """
    Normalized artist key for MATCHING only (never for on-disk folder names).

    Mirrors _title_key: lowercase, then strip to [a-z0-9]. This folds the
    deterministic spelling variants of the SAME artist - dot, underscore and
    whitespace runs plus case - to one key, so e.g.
    'DJ.ILHAM' == 'DJ ILHAM' == 'dj  ilham' == 'DJ_ILHAM' -> 'djilham'. It only
    folds punctuation/whitespace/case; it never collapses genuinely different
    artists ('DJ ILHAM' != 'DJ ADONI'). No fuzzy/typo matching.

    Do NOT use this to build folder names - _clean_component stays the literal
    on-disk vehicle so existing folders are never renamed.
    """
    return re.sub(r"[^a-z0-9]+", "", (artist or "").lower())


def _extract_title_from_filename(filename: str) -> str:
    """Gets the song title from a file name (identical to charts-import)."""
    base = os.path.splitext(os.path.basename(filename))[0]
    base = re.sub(r"\s*\(\d+\)\s*$", "", base)   # (2), (3)
    base = re.sub(r"_\d{6,}$", "", base)          # slskd suffix
    if " - " in base:
        base = base.rsplit(" - ", 1)[-1]
    base = re.sub(r"^\s*\d{1,3}\s*[-.\)]\s*", "", base)  # leading track no.
    return base.strip()


def _find_artist_dir(music_dir: str, artist: str) -> str | None:
    """
    Finds the artist folder under music_dir - folding dot/underscore/whitespace
    and case via _artist_key. On ext4 (Linux) folder names are case-sensitive,
    i.e. 'dermot kennedy' would otherwise miss the folder 'Dermot Kennedy'; and
    'DJ ILHAM' would miss an existing 'DJ.ILHAM' folder. We therefore compare on
    the normalized match key so punctuation/whitespace/case in the input do not
    matter. Matching only - the on-disk literal is still tried first.
    """
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


def read_bitrate(path: str) -> int:
    """
    Reads the bitrate (kbps) of an audio file. 0 if not readable.
    For the quality comparison existing file vs. slskd hit.
    """
    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(path)
        if audio is not None and audio.info is not None:
            br = getattr(audio.info, "bitrate", 0) or 0
            return int(br / 1000)  # mutagen returns bit/s -> kbps
    except Exception:
        pass
    return 0


def read_mp3_bitrate(path: str) -> int | None:
    """
    Bitrate (kbps) if the file is really an MP3, else None (upload gate).

    Unlike read_bitrate (which returns the kbps of ANY format and 0 on failure,
    used for the request-flow comparison), this reader is MP3-specific: it only
    returns a number when mutagen parses the file as a real MP3. A file named
    .mp3 that is actually FLAC/m4a/etc. yields None so the upload endpoint can
    reject it as "not an MP3".
    """
    try:
        from mutagen.mp3 import MP3
        audio = MP3(path)
        return int((getattr(audio.info, "bitrate", 0) or 0) / 1000)
    except Exception:
        return None


def parse_upload_name(filename: str) -> tuple[str, str]:
    """
    Parse an uploaded basename "<artist> - <title>.ext" into (artist, title).

    Rules:
      - Strip the directory and the extension.
      - Split on the FIRST " - ": artist = text before it, title = the rest
        (so a multi-part title like "Intro - Outro" stays intact; the artist is
        never multi-part in the user's convention).
      - No " - " at all -> ("", base) so the user fills in the artist.
      - Both parts are stripped.

    This deliberately differs from charts-import's last-separator heuristic,
    which is tuned for Lidarr-renamed library files, not the upload convention.
    The result only pre-fills the editable UI fields; the authoritative values
    come from what the user submits.
    """
    base = os.path.splitext(os.path.basename(filename.replace("\\", "/")))[0]
    if " - " in base:
        artist, title = base.split(" - ", 1)
        return artist.strip(), title.strip()
    return "", base.strip()


def find_existing(music_dir: str, artist: str, title: str) -> str | None:
    """
    Returns the path if the song already exists under MUSIC_DIR/<Artist>/
    (regardless of which album), otherwise None. Comparison via the normalized
    song title; the artist folder is looked up case-insensitively so the input
    casing does not matter.
    """
    if not music_dir or not os.path.isdir(music_dir):
        return None
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
                if ext not in AUDIO_EXTENSIONS:
                    continue
                if _title_key(_extract_title_from_filename(name)) == want:
                    return os.path.join(album_path, name)
    except OSError:
        pass
    return None


def canonical_artist(music_dir: str, artist: str) -> str | None:
    """
    Returns the CANONICAL artist spelling = the real basename of the already
    existing artist folder (e.g. input 'rod stewart' -> 'Rod Stewart'), or None
    if the artist is not yet in the library.

    Reason: the folder is found case-insensitively, but the new song should be
    tagged/stored with the already established spelling rather than the
    (possibly lowercased) user input.
    """
    if not music_dir or not os.path.isdir(music_dir):
        return None
    artist_dir = _find_artist_dir(music_dir, artist)
    if not artist_dir:
        return None
    # IMPORTANT: get the REAL folder name from the filesystem, not the path
    # possibly constructed by _find_artist_dir. On case-INSENSITIVE FS
    # (e.g. macOS/SMB) its fast path os.path.join(dir, input) would match and
    # return the lowercased input. So scan the parent and pick the actually
    # existing name case-insensitively.
    want = os.path.basename(artist_dir)
    try:
        for name in os.listdir(music_dir):
            if name.lower() == want.lower() and os.path.isdir(
                    os.path.join(music_dir, name)):
                return name
    except OSError:
        pass
    return want or None


def canonical_title_from_file(path: str) -> str:
    """
    Gets the canonical song title from the file name of an EXISTING file (for
    the upgrade case: keep the existing spelling instead of the user input).
    Uses the same extraction as the present-check matching.
    """
    return _extract_title_from_filename(path)


def title_case(title: str) -> str:
    """
    Conservative title-case normalization for NEWLY requested songs (no
    existing file anchor): every space-separated word part gets a capital
    first letter, the rest of the word stays unchanged (so e.g.
    'all for love' -> 'All For Love', but already set intra-word capitals
    like 'iTunes' are not destroyed). Deliberately simple - no special-case
    handling of filler words.
    """
    t = (title or "").strip()
    if not t:
        return t
    return " ".join(w[:1].upper() + w[1:] if w else w for w in t.split(" "))
