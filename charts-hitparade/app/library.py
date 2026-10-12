"""
Library-Index und Matching.

Durchsucht die Navidrome-Musikbibliothek (MUSIC_DIR) nach Audiodateien und
baut einen durchsuchbaren Index auf. Fuer jeden Chart-Track wird geprueft, ob
ein passender Track bereits in der Library existiert.

Das Matching arbeitet zweistufig:
  1. Metadaten-Tags (Kuenstler/Titel) via mutagen, wenn vorhanden.
  2. Fallback auf Dateinamen/Ordnernamen.

Der Vergleich ist tolerant gegenueber Gross-/Kleinschreibung, Sonderzeichen
und Zusaetzen wie "(feat. ...)" oder "(Remastered)".
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


# Zusaetze, die vor dem Vergleich aus Titeln entfernt werden.
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
    Normalisiert einen String fuer den Vergleich:
    - Unicode-Akzente entfernen
    - Kleinschreibung
    - bekannte Zusaetze (feat/remaster/version) entfernen
    - alles ausser Buchstaben/Ziffern auf Leerzeichen reduzieren
    """
    if not text:
        return ""
    text = text.lower()
    for pattern in _NOISE_PATTERNS:
        text = re.sub(pattern, " ", text)
    # Akzente entfernen (é -> e)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    # "&" und "and" vereinheitlichen
    text = text.replace("&", " and ")
    # alles Nicht-Alphanumerische zu Leerzeichen
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def similarity(a: str, b: str) -> int:
    """Aehnlichkeit zweier normalisierter Strings als Wert 0-100."""
    if not a or not b:
        return 0
    return round(SequenceMatcher(None, a, b).ratio() * 100)


# Marker im Pfad oder Album-Tag, die auf eine Compilation / Sampler hindeuten.
# Wird genutzt, um solche Tracks beim Abgleich optional zu ignorieren, damit
# charts-download eine saubere Single-/Album-Version nachladen kann.
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
    Heuristik: True, wenn Pfad, Album- oder AlbumArtist-Tag auf eine
    Compilation hindeuten. Das zuverlaessigste Signal ist der AlbumArtist
    "Various Artists" (Standard-Tag fuer Sampler).
    """
    haystack = normalize(f"{path} {album} {albumartist}".replace("\\", "/"))
    return any(marker in haystack for marker in _COMPILATION_MARKERS)


# Album-Ordner mit Jahresangabe, z.B. "Wonderwall (1995)". Das ist das
# Benennungsschema, das Lidarr beim Renaming erzeugt. Solche Tracks werden bei
# gleich gutem Match bevorzugt, damit die Playlist auf die kuratierte Lidarr-
# Version zeigt (statt auf eine separat geladene Chart-Single) - und die Single
# so nicht mehr referenziert wird (sie kann dann gefahrlos aufgeraeumt werden).
_YEAR_DIR_RE = re.compile(r"\(\d{4}\)")


def _in_year_album(path: str) -> bool:
    """True, wenn ein Ordner im Pfad eine Jahresangabe '(YYYY)' traegt (Lidarr)."""
    return any(_YEAR_DIR_RE.search(part) for part in path.split(os.sep))


class LibraryTrack:
    """Ein in der Library gefundener Track."""

    def __init__(self, path: str, artist: str, title: str,
                 is_compilation: bool = False):
        self.path = path
        self.artist = artist
        self.title = title
        self.is_compilation = is_compilation
        self.norm_artist = normalize(artist)
        self.norm_title = normalize(title)
        # Kombinierter Suchstring fuer den Fallback-Vergleich.
        self.norm_combined = f"{self.norm_artist} {self.norm_title}".strip()
        # Liegt der Track in einem Lidarr-"(Jahr)"-Album? Fuer den Tie-Break.
        self.in_year_album = _in_year_album(path)


def _read_tags(path: str) -> tuple[str, str, str, str]:
    """
    Liest Kuenstler/Titel/Album/AlbumArtist aus den Datei-Tags.
    Leere Strings, wenn nicht lesbar.
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
    Rateversuch aus dem Pfad, wenn keine Tags vorhanden sind.
    Erwartet grob: .../<Artist>/<Album>/<NN - Title>.<ext>
    """
    filename = os.path.splitext(os.path.basename(path))[0]
    # fuehrende Tracknummern wie "01 - " oder "01. " entfernen
    cleaned = re.sub(r"^\s*\d{1,3}\s*[-.\)]\s*", "", filename).strip()

    # "Artist - Title" im Dateinamen hat Vorrang, weil spezifischer als der
    # Ordnername (z.B. "Various Artists/.../Bruno Mars - I Just Might.mp3").
    if " - " in cleaned:
        artist, _, title = cleaned.partition(" - ")
        return artist.strip(), title.strip()

    # Sonst: zwei Ebenen ueber der Datei ist meist der Kuenstler-Ordner.
    parts = path.split(os.sep)
    artist = parts[-3] if len(parts) >= 3 else ""
    return artist.strip(), cleaned.strip()


class Library:
    """Haelt den Index aller gefundenen Tracks im Speicher."""

    def __init__(self, tracks: list[LibraryTrack]):
        self.tracks = tracks

    def find_match(self, artist: str, title: str, threshold: int):
        """
        Sucht den besten Treffer fuer artist/title.
        Gibt (LibraryTrack, score) zurueck oder (None, best_score).

        Bei config.IGNORE_COMPILATIONS werden Compilation-Tracks uebersprungen,
        sodass ein Song, der nur in einer Compilation vorliegt, als "fehlend"
        gilt und die Einzelversion nachgeladen werden kann.
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
                # WICHTIG: Titel UND Kuenstler muessen passen. Ein perfekter
                # Titel darf einen falschen Kuenstler NICHT kompensieren (sonst
                # matcht z.B. Chart "BLACKPINK - Jump" faelschlich "Van Halen -
                # Jump"). Deshalb: harte Kuenstler-Mindestschwelle. Liegt der
                # Kuenstler-Score darunter, ist es kein Treffer, egal wie gut
                # der Titel passt.
                if artist_score < config.ARTIST_MIN_SCORE:
                    continue
                # gewichteter Score: Titel 60%, Kuenstler 40%
                score = round(title_score * 0.6 + artist_score * 0.4)
            else:
                # Fallback (fehlende Tags): Vergleich gegen den kombinierten
                # String. Hier kann der Kuenstler nicht separat geprueft werden.
                score = similarity(n_combined, track.norm_combined)

            if score > best_score:
                best_score = score
                best = track
            elif (score == best_score and best is not None
                  and track.in_year_album and not best.in_year_album):
                # Gleich guter Treffer: die Lidarr-"(Jahr)"-Version bevorzugen,
                # damit die Playlist auf das kuratierte Album zeigt statt auf
                # eine separat geladene Chart-Single.
                best = track

        if best is not None and best_score >= threshold:
            return best, best_score
        return None, best_score


def build_index(music_dir: str) -> Library:
    """Scannt music_dir rekursiv und baut den Library-Index auf."""
    logger.info(t("library.scan", dir=music_dir))
    tracks: list[LibraryTrack] = []
    scanned = 0
    comp_count = 0

    charts_dir_abs = os.path.abspath(config.CHARTS_DIR)

    for root, _dirs, files in os.walk(music_dir):
        # Den Charts-Ordner selbst nicht als Quelle indexieren.
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
