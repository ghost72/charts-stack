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


def _title_key(title: str) -> str:
    """Normalized song title for the comparison (identical to charts-import)."""
    t = (title or "").lower()
    t = re.sub(r"\((?:feat|ft|with)\.?[^)]*\)", " ", t)
    t = re.sub(r"\[(?:feat|ft|with)\.?[^\]]*\]", " ", t)
    t = re.sub(r"\((?:remaster|remastered|.*version|.*edit|official|audio|video|lyric[s]?)[^)]*\)", " ", t)
    return re.sub(r"[^a-z0-9]+", "", t)


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
    Finds the artist folder under music_dir - CASE-INSENSITIVELY. On ext4
    (Linux) folder names are case-sensitive, i.e. 'dermot kennedy' would
    otherwise miss the folder 'Dermot Kennedy'. We therefore compare normalized
    (sanitized + lowercase) so the input casing does not matter.
    """
    safe_artist = _clean_component(artist, "")
    if not safe_artist:
        return None
    # 1. Fast path: the exact folder exists.
    exact = os.path.join(music_dir, safe_artist)
    if os.path.isdir(exact):
        return exact
    # 2. Case-insensitive comparison across all artist folders.
    target = safe_artist.lower()
    try:
        for name in os.listdir(music_dir):
            if name.lower() == target and os.path.isdir(os.path.join(music_dir, name)):
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
