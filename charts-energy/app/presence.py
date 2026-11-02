"""
Presence guard: is a song already in the library, ignoring the compilation flag?

This module answers one question on the MISSING path: does a file for a given
artist+title already exist anywhere under MUSIC_DIR/<Artist>/ - regardless of
which album folder, and WITHOUT any compilation filter? If so, charts-sync /
charts-energy must NOT report the song as missing and re-download it.

The bug this fixes: find_match() in library.py skips is_compilation tracks when
IGNORE_COMPILATIONS=1, so a song whose only surviving copy lives in a folder
whose name trips a compilation marker (e.g. a deduped single kept as a Lidarr
"Greatest Hits (2008)" album track) reads as missing and gets re-fetched as a
single. This guard mirrors charts-songrequest's find_existing() semantics exactly,
so the two services agree with it by construction.

Copy-convention: this is a self-contained behavioural copy of
charts-songrequest/app/library.py's find_existing() helper chain (_clean_component,
_title_key + decoration strippers, _artist_key, _extract_title_from_filename,
_find_artist_dir, find_existing). It deliberately does NOT import from this
service's own library.py nor from any other service at runtime - just like
charts-songrequest keeps its own copies of slsk.py / matching.py / quality.py.
The title/artist key helpers are byte-identical to the reference, so the guard
produces the same keys songrequest does. find_existing's directory walk is a
strict SUPERSET of the reference: it recurses the full tree under <Artist>/ via
os.walk (not just one album level) so it also catches a copy that only survives
in a Lidarr CD multi-disc subfolder - everything songrequest finds, plus deeper.
"""

import os
import re
import unicodedata

# Extensions recognized as audio (same set charts-songrequest uses).
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
# Mirrors charts-songrequest exactly so the MATCH key folds identically.
_TITLE_DECORATION = re.compile(
    r"^\s*\d{2,4}\s*$"                                 # bare number group, e.g. [320]
    r"|\d{2,4}\s*k(?:bps|bit|b)?"                      # 320kbps / 320k
    r"|flac|wav|m4a|aac|ogg|opus"                      # bare format tags
    r"|extended|original|radio|remix|mix|version|edit"  # version/mix words
    r"|remaster(?:ed)?|official|audio|video|visual[sz]er|lyric[s]?"
    r"|feat|ft|with",
    re.IGNORECASE,
)

# Trailing UNBALANCED/open opener-tail whose first word is a decoration keyword
# (e.g. "... (original", "... (radio edit"). Mirrors charts-songrequest.
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
    for the MATCH key only. Verbatim behaviour copy of charts-songrequest's
    _strip_title_decoration so the presence key agrees with the reference.
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
        new = re.sub(r"\s+\d{2,4}\s*k(?:bps|bit|b)?\s*$", "", t, flags=re.IGNORECASE)
        if new != t:
            t, changed = new.rstrip(), True
            continue
        # Trailing (...) / [...] group - strip only if it reads as decoration.
        m = re.search(r"\s*[\(\[]([^\(\)\[\]]*)[\)\]]\s*$", t)
        if m and _TITLE_DECORATION.search(m.group(1)):
            t, changed = t[:m.start()].rstrip(), True
            continue
        # Trailing UNBALANCED opener-tail (open "(" / "[" with no closing bracket).
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
    t = _strip_title_decoration(t)
    # Transliterate accents (ä -> a, é -> e) BEFORE the [a-z0-9] fold so an
    # umlaut is folded, not dropped. MATCH key only.
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", t)


def _artist_key(artist: str) -> str:
    """
    Normalized artist key for MATCHING only (never for on-disk folder names).
    Folds dot/underscore/whitespace runs plus case, identical to
    charts-songrequest, so 'DJ.ILHAM' == 'DJ ILHAM' -> 'djilham'.
    """
    return re.sub(r"[^a-z0-9]+", "", (artist or "").lower())


def _extract_title_from_filename(filename: str) -> str:
    """Gets the song title from a file name (identical to charts-songrequest)."""
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
    and case via _artist_key (identical to charts-songrequest). On ext4 folder
    names are case-sensitive, so we compare on the normalized match key.
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


def find_existing(music_dir: str, artist: str, title: str) -> str | None:
    """
    Return the path if the song already exists under MUSIC_DIR/<Artist>/
    (regardless of which album folder, and WITHOUT any compilation filter),
    otherwise None.

    Deliberately mirrors charts-songrequest's find_existing() so charts-sync /
    charts-energy agree with it by construction: resolve the artist folder
    (exact, else _artist_key fold), recurse every album subfolder, compare
    normalized title keys built from the on-disk filename. Presence alone
    suppresses a re-download; no bitrate/quality check is applied here.

    Fail-open: a missing MUSIC_DIR / artist folder, an empty key, or an OSError
    during the walk all return None (the song then follows the normal missing
    path), so this guard can only suppress a re-download, never cause one.
    """
    if not music_dir or not os.path.isdir(music_dir):
        return None
    artist_dir = _find_artist_dir(music_dir, artist)
    if not artist_dir:
        return None
    want = _title_key(title)
    if not want:
        return None
    # Recurse EVERY album subfolder under <Artist>/, INCLUDING Lidarr CD
    # multi-disc subfolders (e.g. "Greatest Hits (2008)/CD 01/..."), via
    # os.walk. This is a strict superset of charts-songrequest's two-level
    # scan - it finds everything songrequest finds plus files one level deeper -
    # so the keys agree with the reference while the real-world Sandra case
    # (the single surviving copy lives in a CD subfolder) is also caught.
    try:
        for root, _dirs, names in os.walk(artist_dir):
            for name in names:
                ext = os.path.splitext(name)[1].lower()
                if ext not in AUDIO_EXTENSIONS:
                    continue
                if _title_key(_extract_title_from_filename(name)) == want:
                    return os.path.join(root, name)
    except OSError:
        pass
    return None
