"""
Parser for the missing file(s) written by charts-sync.

Format (see charts-sync/app/playlist.py -> write_missing):

    # Fehlende Chart-Songs (Stand ...)          <- comment lines with '#'
    # ...
                                                <- blank line
     12. Ariana Grande - twilight zone          <- "  <pos>. <artist> - <title>"
      1. Remi Wolf - Bottle

From this we extract (position, artist, title). Scene queues additionally use
#SCENES: JSON comments to attach per-song source labels. Other comments and
blank lines are ignored.
"""

import json
import logging
import os
import re

from .i18n import t

logger = logging.getLogger(__name__)

# "  12. Artist - Title"  ->  pos=12, rest="Artist - Title"
_LINE_RE = re.compile(r"^\s*(\d+)\.\s+(.*\S)\s*$")
SCENE_MISSING_PATH = "/charts/.charts-scene-missing.txt"
SCENE_SOURCE = ".charts-scene-missing"
SCENE_LABEL_PREFIX = "Playlist: "


def source_label(path: str) -> str:
    """
    Derives a readable playlist label from a missing file path, so messages can
    show which playlist a song comes from.

    /charts/missing.txt           -> "charts"
    /charts/missing-mundart.txt   -> "mundart"
    /charts/missing-80er-90er.txt -> "80er-90er"

    The shared scene filename remains an internal identifier here.
    Its per-song #SCENES comments provide the actual display sources.
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
    return [(pos, artist, title) for pos, artist, title, _sources in _parse_missing_entries(path)]


def _parse_missing_entries(path: str) -> list[tuple[int, str, str, list[str]]]:
    if not os.path.isfile(path):
        logger.warning(t("download.missing.file_not_found", path=path))
        return []

    scene_queue = source_label(path) == SCENE_SOURCE
    default_sources = [SCENE_LABEL_PREFIX + t("download.source.scene_tracks")
                       if scene_queue else source_label(path)]
    sources = default_sources
    entries: list[tuple[int, str, str, list[str]]] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line_number, line in enumerate(fh, 1):
            if scene_queue and line.startswith("#SCENES:"):
                try:
                    scenes = json.loads(line.removeprefix("#SCENES:").strip())
                    if (not isinstance(scenes, list) or not scenes
                            or any(not isinstance(scene, str) or not 1 <= len(scene.strip()) <= 80
                                   or any(ord(char) < 32 for char in scene) for scene in scenes)):
                        raise ValueError("Invalid scene names")
                    sources = list(dict.fromkeys(SCENE_LABEL_PREFIX + scene.strip() for scene in scenes))
                except ValueError:
                    logger.error(t("download.missing.invalid_scenes", path=path, line=line_number))
                    sources = []
                continue
            if line.lstrip().startswith("#") or not line.strip():
                continue
            m = _LINE_RE.match(line)
            if not m:
                sources = default_sources
                continue
            pos = int(m.group(1))
            rest = m.group(2)
            if " - " not in rest:
                logger.debug(t("download.missing.no_separator", line=repr(line.strip())))
                sources = default_sources
                continue
            artist, _, title = rest.partition(" - ")
            artist = artist.strip()
            title = title.strip()
            if artist and title and sources:
                entries.append((pos, artist, title, sources))
            sources = default_sources
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
        for pos, artist, title, labels in _parse_missing_entries(path):
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
                sources.extend(label for label in labels if label not in sources)
                continue
            seen[key] = len(result)
            result.append((pos, artist, title, list(labels)))

    result.sort(key=lambda e: e[0])
    if excluded_count:
        logger.info(t("download.missing.excluded_summary", count=excluded_count))
    logger.info(t("download.missing.total", unique=len(result), files=len(paths)))
    return result
