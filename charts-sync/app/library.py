"""
Library index and matching.

Scans the Navidrome music library (MUSIC_DIR) for audio files and builds a
searchable index. For each chart track it checks whether a matching track
already exists in the library.

Matching works in two stages:
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

# Additions removed from titles before the comparison.
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
    Normalize a string for the comparison:
    - remove Unicode accents
    - lowercase
    - remove known additions (feat/remaster/version)
    - reduce everything other than letters/digits to spaces
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


# Markers in the path or album tag that indicate a compilation / sampler. Used
# to optionally ignore such tracks during matching so charts-download can
# backfill a clean single/album version.
# NOTE: intentionally DIFFERENT from the matching.py _COMPILATION_MARKERS family
# (download/songrequest/upgrade). That one matches raw slskd folder paths; THIS
# one matches on-disk album tags/paths - do not unify them.
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
    Heuristic: True when the path, album or AlbumArtist tag indicate a
    compilation. The most reliable signal is the AlbumArtist "Various Artists"
    (standard tag for samplers).

    A year in the path exempts only the ambiguous "Greatest Hits" marker:
    curated single-artist albums stay eligible, but dated Various Artists /
    Bravo Hits samplers remain compilations.
    """
    haystack = normalize(f"{path} {album} {albumartist}".replace("\\", "/"))
    year_album = _in_year_album(path)
    return any(marker in haystack for marker in _COMPILATION_MARKERS
               if not (year_album and marker == "greatest hits"))


# Album folders with a year, e.g. "Wonderwall (1995)". That is the naming scheme
# Lidarr produces when renaming. Among equally good non-compilation matches,
# prefer the curated Lidarr version over a separate chart single.
_YEAR_DIR_RE = re.compile(r"\(\d{4}\)")


def _in_year_album(path: str) -> bool:
    """True when a folder in the path carries a year '(YYYY)' (Lidarr)."""
    return any(_YEAR_DIR_RE.search(part) for part in path.split(os.sep))


class LibraryTrack:
    """A track found in the library."""

    def __init__(self, path: str, artist: str, title: str,
                 is_compilation: bool = False, genres: list[str] | None = None,
                 genre_read_error: bool = False):
        self.path = path
        self.artist = artist
        self.title = title
        self.is_compilation = is_compilation
        self.genres = genres or []
        self.genre_read_error = genre_read_error
        self.norm_artist = normalize(artist)
        self.norm_title = normalize(title)
        # Combined search string for the fallback comparison.
        self.norm_combined = f"{self.norm_artist} {self.norm_title}".strip()
        # Does the track sit in a Lidarr "(year)" album? For the tie-break.
        self.in_year_album = _in_year_album(path)


def _read_tags(path: str) -> tuple[str, str, str, str, list[str], bool]:
    """
    Read artist/title/album/AlbumArtist from the file tags.
    Empty strings when not readable.
    """
    try:
        audio = MutagenFile(path, easy=True)
        if audio is None:
            logger.warning("Could not read audio metadata: %s", path)
            return "", "", "", "", [], True
        artist = (audio.get("artist") or [""])[0]
        title = (audio.get("title") or [""])[0]
        album = (audio.get("album") or [""])[0]
        albumartist = (audio.get("albumartist") or [""])[0]
        metadata = (artist.strip(), title.strip(), album.strip(), albumartist.strip())
    except Exception as error:
        logger.warning("Could not read audio metadata %s: %s", path, error.__class__.__name__)
        return "", "", "", "", [], True
    try:
        values = (audio.get("genre") or audio.get("TCON") or audio.get("WM/Genre")
                  or audio.get("\xa9gen") or [])
        values = getattr(values, "genres", getattr(values, "text", values))
        if not isinstance(values, (list, tuple)):
            values = [values]
        if not values and audio.get("gnre"):
            from mutagen.id3 import GENRES
            numeric = audio.get("gnre")
            if any(not isinstance(value, int) or not 1 <= value <= len(GENRES)
                   for value in numeric):
                raise ValueError("Invalid numeric genre tag")
            values = [GENRES[value - 1] for value in numeric]
        genres = {}
        for value in values:
            value = getattr(value, "value", value)
            if not isinstance(value, str):
                raise TypeError("Invalid genre tag value")
            for genre in value.split(";"):
                genre = unicodedata.normalize("NFC", genre).strip()
                if not genre:
                    continue
                if len(genre) > 160 or any(ord(char) < 32 for char in genre):
                    raise ValueError("Invalid genre tag")
                key = " ".join(unicodedata.normalize("NFKC", genre).casefold().split())
                genres.setdefault(key, genre)
        return *metadata, list(genres.values()), False
    except (ValueError, TypeError, AttributeError, KeyError, IndexError, UnicodeError) as error:
        logger.warning("Could not read genre metadata %s: %s", path, error.__class__.__name__)
        return *metadata, [], True


def _guess_from_path(path: str) -> tuple[str, str]:
    """
    Best-effort guess from the path when no tags are present.
    Roughly expects: .../<Artist>/<Album>/<NN - Title>.<ext>
    """
    filename = os.path.splitext(os.path.basename(path))[0]
    # remove leading track numbers like "01 - " or "01. "
    cleaned = re.sub(r"^\s*\d{1,3}\s*[-.\)]\s*", "", filename).strip()

    # "Artist - Title" in the file name takes precedence because it is more
    # specific than the folder name (e.g. "Various Artists/.../Bruno Mars - I
    # Just Might.mp3").
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
        Find the best hit for artist/title.
        Returns (LibraryTrack, score) or (None, best_score).

        With config.IGNORE_COMPILATIONS compilation tracks are skipped, so a
        song that only exists as part of a compilation counts as "missing" and
        the single version can be backfilled.
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
                # NOT compensate for a wrong artist (otherwise chart "BLACKPINK
                # - Jump" would wrongly match "Van Halen - Jump"). Hence a hard
                # artist minimum threshold. If the artist score is below it, it
                # is not a hit, no matter how well the title matches.
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
                  and (not track.is_compilation, track.in_year_album)
                  > (not best.is_compilation, best.in_year_album)):
                # Prefer artist releases over samplers, then dated albums.
                best = track

        if best is not None and best_score >= threshold:
            return best, best_score
        return None, best_score


def build_index(music_dir: str) -> Library:
    """Scan music_dir recursively and build the library index."""
    logger.info(t("library.scan", dir=music_dir))
    if not os.path.isdir(music_dir):
        raise FileNotFoundError(f"Music library directory unavailable: {music_dir}")
    tracks: list[LibraryTrack] = []
    scanned = 0
    comp_count = 0

    charts_dir_abs = os.path.abspath(config.CHARTS_DIR)

    def scan_error(error):
        raise error

    for root, _dirs, files in os.walk(music_dir, onerror=scan_error):
        # Do not index the Charts folder itself as a source.
        if os.path.abspath(root).startswith(charts_dir_abs):
            continue
        for name in files:
            ext = os.path.splitext(name)[1].lower()
            if ext not in config.AUDIO_EXTENSIONS:
                continue
            path = os.path.join(root, name)
            artist, title, album, albumartist, genres, read_error = _read_tags(path)
            if not artist or not title:
                g_artist, g_title = _guess_from_path(path)
                artist = artist or g_artist
                title = title or g_title
            is_comp = _looks_like_compilation(path, album, albumartist)
            tracks.append(LibraryTrack(path, artist, title, is_comp, genres, read_error))
            scanned += 1
            if is_comp:
                comp_count += 1

    logger.info(t("library.index.done", count=scanned, compilations=comp_count))
    if config.IGNORE_COMPILATIONS and comp_count:
        logger.info(t("library.ignore_compilations", count=comp_count))
    return Library(tracks)
