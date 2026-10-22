"""
Library-mode writers for charts-energy (download-only, no .m3u).

A `library` channel writes NO playlist. It only tops up the shared library:
per run it writes a missing file that charts-download consumes, and for each
*missing* song it writes a release YEAR into the SAME shared year-map file
charts-import already reads (CHARTS_DIR/.charts-flashback-years.json) so the
song lands in the right decade Navidrome Smart Playlist (NSP) without the year
ever appearing in a path or filename.

Both functions are copied from charts-flashback/app/main.py (adapted to the
charts-energy config/i18n namespace), kept in their own module so the own-mode
3-tuple writer in playlist.py stays untouched.
"""

import json
import logging
import os
from datetime import datetime

from . import config
from .i18n import t

logger = logging.getLogger(__name__)


def _key(artist: str, title: str) -> str:
    """Year-map key: lowercase 'artist|title' (matches charts-import's reader)."""
    return f"{(artist or '').lower()}|{(title or '').lower()}"


def write_missing(missing: list[tuple[int, str, str, str]], file_name: str) -> str:
    """
    Write the missing list in the charts-download format ("  <pos>. <artist> -
    <title>") - EXACTLY like the other services, WITHOUT any year character.
    This way charts-import creates path/name/tags identical to all other
    services. The year is NOT transported via the file name (that formerly
    poisoned title/album/path) but separately via the year map
    (write_year_map), purely into the metadata.
    missing: (position, artist, title, year) - year is IGNORED here.
    """
    os.makedirs(config.CHARTS_DIR, exist_ok=True)
    missing_path = os.path.join(config.CHARTS_DIR, file_name)

    header = t("energy.library.missing_file.header",
               date=datetime.now().strftime("%d.%m.%Y %H:%M")).split("\n")
    body = [f"{pos:>3}. {artist} - {title}" for pos, artist, title, _yr in missing]
    content = "\n".join(header + body) + "\n"

    tmp_path = missing_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        fh.write(content)
    os.replace(tmp_path, missing_path)

    logger.info(t("playlist.missing_written", path=missing_path, count=len(missing)))
    return missing_path


def write_year_map(missing: list[tuple[int, str, str, str]]) -> None:
    """
    Write/update the year map (artist|title -> year) to config.YEAR_MAP_FILE.
    charts-import reads it when tagging and sets the date/originaldate tag -
    WITHOUT the year ever appearing in the file/folder name. The key is
    identical to the matching key (lowercase 'artist|title') so charts-import
    finds the same song again. Existing entries are kept (the map grows along),
    rows with an empty year are skipped, and the file is only rewritten when
    something actually changed.
    """
    path = config.YEAR_MAP_FILE
    data = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            loaded = json.load(fh)
        if isinstance(loaded, dict):
            data = loaded
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        data = {}

    changed = False
    for _pos, artist, title, yr in missing:
        if not yr:
            continue
        key = _key(artist, title)
        if data.get(key) != yr:
            data[key] = yr
            changed = True
    if not changed:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=0)
        os.replace(tmp, path)
        logger.info(t("energy.library.year_map.updated", path=path, count=len(data)))
    except OSError as e:
        logger.warning(t("energy.library.year_map.write_failed", error=e))
