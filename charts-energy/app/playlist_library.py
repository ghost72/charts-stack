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


def write_missing(missing: list[tuple], file_name: str) -> str:
    """
    Write the missing list in the charts-download format ("  <pos>. <artist> -
    <title>") - EXACTLY like the other services, WITHOUT any year character.
    This way charts-import creates path/name/tags identical to all other
    services. The year is NOT transported via the file name (that formerly
    poisoned title/album/path) but separately via the year map
    (write_year_map), purely into the metadata.
    missing: 6-tuple (pos, artist, title, year, is_fallback, decade) - only
    pos/artist/title are used here; the rest is ignored.
    """
    os.makedirs(config.CHARTS_DIR, exist_ok=True)
    missing_path = os.path.join(config.CHARTS_DIR, file_name)

    header = t("energy.library.missing_file.header",
               date=datetime.now().strftime("%d.%m.%Y %H:%M")).split("\n")
    body = [f"{pos:>3}. {artist} - {title}"
            for pos, artist, title, *_rest in missing]
    content = "\n".join(header + body) + "\n"

    tmp_path = missing_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        fh.write(content)
    os.replace(tmp_path, missing_path)

    logger.info(t("playlist.missing_written", path=missing_path, count=len(missing)))
    return missing_path


def write_year_map(missing: list[tuple]) -> None:
    """
    Write/update the year map (artist|title -> year) to config.YEAR_MAP_FILE.
    charts-import reads it when tagging and sets the date/originaldate tag -
    WITHOUT the year ever appearing in the file/folder name. The key is
    identical to the matching key (lowercase 'artist|title') so charts-import
    finds the same song again.

    Tagged union (shared with charts-import, schema owned by
    charts-import/app/yearmap.py):
      - a REAL year (is_fallback False) is written as a bare string "YYYY" (a
        confirmed, non-candidate value);
      - a DECADE MIDPOINT (is_fallback True) is written as the object
        {year, pending:true, decade, src:"energy-library", tries:0} so the
        charts-import backfill can later sharpen it against MusicBrainz.

    Merge rules (re-observation every ~30 min):
      - an existing object with the SAME year+decade preserves its `tries` (so
        re-observation does not defeat the retry stop);
      - a now-resolved REAL year overwrites a prior object with the bare string;
      - empty years are skipped;
      - the file is only rewritten when something actually changed.

    missing: 6-tuple (pos, artist, title, year, is_fallback, decade).
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
    for _pos, artist, title, yr, is_fallback, decade in missing:
        if not yr:
            continue
        key = _key(artist, title)
        existing = data.get(key)
        if is_fallback:
            # Decade midpoint -> pending object. Preserve tries when the same
            # fallback (year+decade) is re-observed.
            tries = 0
            if (isinstance(existing, dict) and existing.get("pending")
                    and str(existing.get("year")) == str(yr)
                    and str(existing.get("decade")) == str(decade)):
                tries = int(existing.get("tries", 0))
                # No change: identical object already present.
                if (existing.get("src") == "energy-library"
                        and int(existing.get("tries", 0)) == tries):
                    continue
            data[key] = {"year": str(yr), "pending": True,
                         "decade": str(decade), "src": "energy-library",
                         "tries": tries}
            changed = True
        else:
            # Real in-decade / non-decade year -> confirmed bare string
            # (overwrites a prior object if the year was resolved later).
            if existing != yr:
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
