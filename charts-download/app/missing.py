"""
Parser for the missing file(s) written by charts-sync.

Format (see charts-sync/app/playlist.py -> write_missing):

    # Fehlende Chart-Songs (Stand ...)          <- comment lines with '#'
    # ...
                                                <- blank line
     12. Ariana Grande - twilight zone          <- "  <pos>. <artist> - <title>"
      1. Remi Wolf - Bottle

From this we extract (position, artist, title). Comment and blank lines are
ignored.
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
    Derives a readable playlist label from a missing file path, so messages can
    show which playlist a song comes from.

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
    Reads a missing file and returns (position, artist, title).
    Lines without " - " (no separable artist/title) are skipped.
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


def load_all(paths: list[str]) -> list[tuple[int, str, str, list[str]]]:
    """
    Reads multiple missing files and merges them (deduplicated by normalized
    "artist|title" so a song in two charts is loaded only once). Order:
    ascending by chart position.

    Returns (position, artist, title, sources). "sources" is the list of
    playlist labels (see source_label) a song belongs to. If a song appears in
    several lists, its label from each file is collected (in order of paths,
    deduplicated); the FIRST label (index 0) is the primary source (the first
    file the song appeared in) and keeps driving per-source fairness/logging in
    main.py. The other services (manifest, Pushover messages) use the full list
    so every contributing playlist is shown.
    """
    from . import config
    from .matching import is_excluded_artist, normalize

    # Map normalized "artist|title" -> index into result, so a later file can
    # APPEND its label to the existing entry instead of dropping the song.
    seen: dict[str, int] = {}
    result: list[tuple[int, str, str, list[str]]] = []
    excluded_count = 0
    for path in paths:
        label = source_label(path)
        for pos, artist, title in parse_missing_file(path):
            # NEVER load globally excluded artists (see config.EXCLUDE_ARTISTS).
            # The song is not even added to the download pipeline.
            if is_excluded_artist(artist, config.EXCLUDE_ARTISTS):
                excluded_count += 1
                logger.info(t("download.missing.artist_excluded", artist=artist, title=title))
                continue
            key = f"{normalize(artist)}|{normalize(title)}"
            if key in seen:
                # Song already seen in an earlier file: add this file's label
                # (if new) to its source list, keeping first-seen order.
                sources = result[seen[key]][3]
                if label not in sources:
                    sources.append(label)
                continue
            seen[key] = len(result)
            result.append((pos, artist, title, [label]))

    result.sort(key=lambda e: e[0])
    if excluded_count:
        logger.info(t("download.missing.excluded_summary", count=excluded_count))
    logger.info(t("download.missing.total", unique=len(result), files=len(paths)))
    return result
