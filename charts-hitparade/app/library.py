"""
Library index and matching.

Scans the Navidrome music library (MUSIC_DIR) for audio files and builds a
searchable index. For each chart track it checks whether a matching track
already exists in the library.

The matching works in two stages:
  1. Metadata tags (artist/title) via mutagen, if present.
  2. Fallback to file names/folder names.

The comparison is tolerant of case, special characters and additions like
"(feat. ...)" or "(Remastered)".
"""

import logging
import os
import re
import unicodedata
from difflib import SequenceMatcher

from mutagen import File as MutagenFile

from . import config
from .i18n import t

logger = logging.getLogger(__name__)


# Additions removed from titles before comparison.
_NOISE_PATTERNS = [
    r"\(feat\.?.*?\)", r"\[feat\.?.*?\]",
    r"\(ft\.?.*?\)", r"\[ft\.?.*?\]",
    r"\(with.*?\)",
    r"\(remaster.*?\)", r"\[remaster.*?\]",
    r"\(.*?version\)", r"\(.*?edit\)",
    r"\(official.*?\)", r"\(lyric.*?\)", r"\(audio\)", r"\(video\)",
]


def normalize(text: str) -> str:
    """
    Normalize a string for comparison:
    - remove Unicode accents
    - lowercase
    - remove known additions (feat/remaster/version)
    - reduce everything except letters/digits to spaces
    """
    if not text:
        return ""
    text = text.lower()
    for pattern in _NOISE_PATTERNS:
        text = re.sub(pattern, " ", text)
    # Remove accents (é -> e)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    # Unify "&" and "and"
    text = text.replace("&", " and ")
    # Everything non-alphanumeric to spaces
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def similarity(a: str, b: str) -> int:
    """Similarity of two normalized strings as a value 0-100."""
    if not a or not b:
        return 0
    return round(SequenceMatcher(None, a, b).ratio() * 100)


# Markers in the path or album tag that hint at a compilation / sampler.
# Used to optionally ignore such tracks during matching so charts-download can
# fetch a clean single/album version.
_COMPILATION_MARKERS = [
    "va -", "va-", "various artists", "various", "verschiedene interpreten",
    "compilation", "sampler",
    "top 100", "top100", "top 40", "top40", "charts", "hitlist", "hit list",
    "greatest hits",
    "bravo hits", "now that", "kontor", "clubland", "kuschelrock",
    "new music friday", "nouveautes",
]


def _looks_like_compilation(path: str, album: str, albumartist: str) -> bool:
    """
    Heuristic: True if the path, album or albumartist tag hints at a
    compilation. The most reliable signal is the albumartist "Various Artists"
    (standard tag for samplers).
    """
    haystack = normalize(f"{path} {album} {albumartist}".replace("\\", "/"))
    return any(marker in haystack for marker in _COMPILATION_MARKERS)


# Album folders with a year, e.g. "Wonderwall (1995)". This is the naming
# scheme Lidarr produces on renaming. Such tracks are preferred on an
# equally good match so the playlist points at the curated Lidarr version
# (instead of a separately downloaded chart single) - and the single is then
# no longer referenced (so it can be cleaned up safely).
_YEAR_DIR_RE = re.compile(r"\(\d{4}\)")


def _in_year_album(path: str) -> bool:
    """True if a folder in the path carries a year '(YYYY)' (Lidarr)."""
    return any(_YEAR_DIR_RE.search(part) for part in path.split(os.sep))


class LibraryTrack:
    """A track found in the library."""

    def __init__(self, path: str, artist: str, title: str,
                 is_compilation: bool = False):
        self.path = path
        self.artist = artist
        self.title = title
        self.is_compilation = is_compilation
        self.norm_artist = normalize(artist)
        self.norm_title = normalize(title)
        # Combined search string for the fallback comparison.
        self.norm_combined = f"{self.norm_artist} {self.norm_title}".strip()
        # Is the track in a Lidarr "(year)" album? For the tie-break.
        self.in_year_album = _in_year_album(path)


def _read_tags(path: str) -> tuple[str, str, str, str]:
    """
    Read artist/title/album/albumartist from the file tags.
    Empty strings if not readable.
    """
    try:
        audio = MutagenFile(path, easy=True)
        if audio is None:
            return "", "", "", ""
        artist = (audio.get("artist") or [""])[0]
        title = (audio.get("title") or [""])[0]
        album = (audio.get("album") or [""])[0]
        albumartist = (audio.get("albumartist") or [""])[0]
        return artist.strip(), title.strip(), album.strip(), albumartist.strip()
    except Exception:
        return "", "", "", ""


def _guess_from_path(path: str) -> tuple[str, str]:
    """
    Best-effort guess from the path when no tags are present.
    Roughly expects: .../<Artist>/<Album>/<NN - Title>.<ext>
    """
    filename = os.path.splitext(os.path.basename(path))[0]
    # Remove leading track numbers like "01 - " or "01. "
    cleaned = re.sub(r"^\s*\d{1,3}\s*[-.\)]\s*", "", filename).strip()

    # "Artist - Title" in the file name takes precedence, as it is more specific
    # than the folder name (e.g. "Various Artists/.../Bruno Mars - I Just Might.mp3").
    if " - " in cleaned:
        artist, _, title = cleaned.partition(" - ")
        return artist.strip(), title.strip()

    # Otherwise: two levels above the file is usually the artist folder.
    parts = path.split(os.sep)
    artist = parts[-3] if len(parts) >= 3 else ""
    return artist.strip(), cleaned.strip()


class Library:
    """Holds the index of all found tracks in memory."""

    def __init__(self, tracks: list[LibraryTrack]):
        self.tracks = tracks

    def find_match(self, artist: str, title: str, threshold: int):
        """
        Find the best match for artist/title.
        Returns (LibraryTrack, score) or (None, best_score).

        With config.IGNORE_COMPILATIONS compilation tracks are skipped, so a
        song that only exists in a compilation counts as "missing" and the
        single version can be fetched.
        """
        n_artist = normalize(artist)
        n_title = normalize(title)
        n_combined = f"{n_artist} {n_title}".strip()

        best: LibraryTrack | None = None
        best_score = 0

        for track in self.tracks:
            if config.IGNORE_COMPILATIONS and track.is_compilation:
                continue

            title_score = similarity(n_title, track.norm_title)
            artist_score = similarity(n_artist, track.norm_artist)

            if track.norm_title and track.norm_artist:
                # IMPORTANT: title AND artist must match. A perfect title must
                # NOT compensate for a wrong artist (otherwise e.g. chart
                # "BLACKPINK - Jump" would wrongly match "Van Halen - Jump").
                # Hence a hard minimum artist threshold. If the artist score is
                # below it, it is no match, no matter how well the title fits.
                if artist_score < config.ARTIST_MIN_SCORE:
                    continue
                # weighted score: title 60%, artist 40%
                score = round(title_score * 0.6 + artist_score * 0.4)
            else:
                # Fallback (missing tags): compare against the combined string.
                # Here the artist cannot be checked separately.
                score = similarity(n_combined, track.norm_combined)

            if score > best_score:
                best_score = score
                best = track
            elif (score == best_score and best is not None
                  and track.in_year_album and not best.in_year_album):
                # Equally good match: prefer the Lidarr "(year)" version so the
                # playlist points at the curated album instead of a separately
                # downloaded chart single.
                best = track

        if best is not None and best_score >= threshold:
            return best, best_score
        return None, best_score


def build_index(music_dir: str) -> Library:
    """Scan music_dir recursively and build the library index."""
    logger.info(t("library.scan", dir=music_dir))
    tracks: list[LibraryTrack] = []
    scanned = 0
    comp_count = 0

    charts_dir_abs = os.path.abspath(config.CHARTS_DIR)

    for root, _dirs, files in os.walk(music_dir):
        # Do not index the charts folder itself as a source.
        if os.path.abspath(root).startswith(charts_dir_abs):
            continue
        for name in files:
            ext = os.path.splitext(name)[1].lower()
            if ext not in config.AUDIO_EXTENSIONS:
                continue
            path = os.path.join(root, name)
            artist, title, album, albumartist = _read_tags(path)
            if not artist or not title:
                g_artist, g_title = _guess_from_path(path)
                artist = artist or g_artist
                title = title or g_title
            is_comp = _looks_like_compilation(path, album, albumartist)
            tracks.append(LibraryTrack(path, artist, title, is_comp))
            scanned += 1
            if is_comp:
                comp_count += 1

    logger.info(t("library.index.done", count=scanned, compilations=comp_count))
    if config.IGNORE_COMPILATIONS and comp_count:
        logger.info(t("library.ignore_compilations", count=comp_count))
    return Library(tracks)
