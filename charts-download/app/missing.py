"""
Parser fuer die von charts-sync geschriebene(n) missing-Datei(en).

Format (siehe charts-sync/app/playlist.py -> write_missing):

    # Fehlende Chart-Songs (Stand ...)          <- Kommentar-Zeilen mit '#'
    # ...
                                                <- Leerzeile
     12. Ariana Grande - twilight zone          <- "  <pos>. <artist> - <title>"
      1. Remi Wolf - Bottle

Wir extrahieren daraus (position, artist, title). Kommentar- und Leerzeilen
werden ignoriert.
"""

import logging
import os
import re

from .i18n import t

logger = logging.getLogger(__name__)

# "  12. Artist - Title"  ->  pos=12, rest="Artist - Title"
_LINE_RE = re.compile(r"^\s*(\d{1,3})\.\s+(.*\S)\s*$")


def source_label(path: str) -> str:
    """
    Leitet aus einem missing-Dateipfad ein lesbares Playlist-Label ab, damit
    Meldungen zeigen koennen, aus welcher Playlist ein Song stammt.

    /charts/missing.txt           -> "charts"
    /charts/missing-mundart.txt   -> "mundart"
    /charts/missing-80er-90er.txt -> "80er-90er"
    """
    base = os.path.basename(path)
    name = base[:-4] if base.lower().endswith(".txt") else base
    if name == "missing":
        return "charts"
    if name.startswith("missing-"):
        return name[len("missing-"):]
    return name or "charts"


def parse_missing_file(path: str) -> list[tuple[int, str, str]]:
    """
    Liest eine missing-Datei und liefert (position, artist, title).
    Zeilen ohne " - " (kein Artist/Title trennbar) werden uebersprungen.
    """
    if not os.path.isfile(path):
        logger.warning(t("download.missing.file_not_found", path=path))
        return []

    entries: list[tuple[int, str, str]] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            if line.lstrip().startswith("#") or not line.strip():
                continue
            m = _LINE_RE.match(line)
            if not m:
                continue
            pos = int(m.group(1))
            rest = m.group(2)
            if " - " not in rest:
                logger.debug(t("download.missing.no_separator", line=repr(line.strip())))
                continue
            artist, _, title = rest.partition(" - ")
            artist = artist.strip()
            title = title.strip()
            if artist and title:
                entries.append((pos, artist, title))
    return entries


def load_all(paths: list[str]) -> list[tuple[int, str, str, str]]:
    """
    Liest mehrere missing-Dateien und fasst sie zusammen (dedupliziert nach
    normalisiertem "artist|title", damit ein Song aus zwei Charts nur einmal
    geladen wird). Reihenfolge: nach Chart-Position aufsteigend.

    Liefert (position, artist, title, source). "source" ist das aus dem
    Dateinamen abgeleitete Playlist-Label (siehe source_label). Kommt ein Song
    in mehreren Listen vor, gewinnt das erste Vorkommen (in Reihenfolge der
    paths); dessen Label wird verwendet.
    """
    from . import config
    from .matching import is_excluded_artist, normalize

    seen: set[str] = set()
    result: list[tuple[int, str, str, str]] = []
    excluded_count = 0
    for path in paths:
        label = source_label(path)
        for pos, artist, title in parse_missing_file(path):
            # Global ausgeschlossene Kuenstler NIE laden (siehe
            # config.EXCLUDE_ARTISTS). Song wird gar nicht erst in die
            # Download-Pipeline aufgenommen.
            if is_excluded_artist(artist, config.EXCLUDE_ARTISTS):
                excluded_count += 1
                logger.info(t("download.missing.artist_excluded", artist=artist, title=title))
                continue
            key = f"{normalize(artist)}|{normalize(title)}"
            if key in seen:
                continue
            seen.add(key)
            result.append((pos, artist, title, label))

    result.sort(key=lambda e: e[0])
    if excluded_count:
        logger.info(t("download.missing.excluded_summary", count=excluded_count))
    logger.info(t("download.missing.total", unique=len(result), files=len(paths)))
    return result
