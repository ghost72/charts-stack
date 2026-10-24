"""
Shared year-map schema helpers for charts-import.

The year map (DOWNLOADS_DIR/.charts-flashback-years.json, the same physical file
charts-energy/charts-flashback write) is a tagged union keyed by lowercase
'artist|title':
  - a confirmed year is a bare string "YYYY" (never a backfill candidate);
  - a song still needing the backfill is an object
    {year, pending:true, src, tries, decade?}.

This module is the ONE place that owns the shape rules so both importer.py
(which creates pending markers) and backfill.py (which reads/clears them) agree.
It replaces the ad-hoc _load_year_map that used to live in importer.py.
"""

import json
import logging
import os
import re
from datetime import datetime

from . import config

logger = logging.getLogger(__name__)

# The shared year map, written by charts-flashback AND charts-energy (library
# mode); charts-import reads it at import time and writes its own generic
# pending markers here too. Physically the same host file as energy's
# CHARTS_DIR/.charts-flashback-years.json because both containers mount
# /volume2/music/Charts (a deployment invariant, not a code guarantee).
YEAR_MAP_FILE = os.path.join(config.DOWNLOADS_DIR, ".charts-flashback-years.json")

_ALBUM_YEAR_RE = re.compile(r"\((\d{4})\)")


def load() -> dict:
    """Load the year map, failing open to {} on a missing/corrupt file."""
    try:
        with open(YEAR_MAP_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def rewrite(data: dict) -> None:
    """Atomically rewrite the year map (tmp + os.replace)."""
    os.makedirs(os.path.dirname(YEAR_MAP_FILE), exist_ok=True)
    tmp = YEAR_MAP_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=0)
    os.replace(tmp, YEAR_MAP_FILE)


def is_pending(value) -> bool:
    """True when the value is a pending object (dict with pending:true)."""
    return isinstance(value, dict) and bool(value.get("pending"))


def year_of(value) -> str:
    """The current best year for a map value (bare string OR object), "" if
    none. A list/null/other shape yields ""."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("year") or "")
    return ""


def make_pending(year: str, src: str, decade: str | None = None) -> dict:
    """Build a pending-marker object. `decade` is set only for the decade-aware
    path (absent for generic markers)."""
    obj = {"year": str(year or ""), "pending": True, "src": src, "tries": 0}
    if decade:
        obj["decade"] = str(decade)
    return obj


def confirm(data: dict, key: str, year: str) -> None:
    """Replace the object value with the bare string of the final year (a bare
    string is never a backfill candidate -> marker cleared by shape change)."""
    data[key] = str(year)


def give_up(data: dict, key: str) -> None:
    """Delete the key from the map (generic give-up)."""
    data.pop(key, None)


def _album_folder_year(path: str) -> str:
    """
    Read a Lidarr "(YYYY)" album-folder year from a file path. Returns the
    FIRST path component that matches r"\\((\\d{4})\\)" with a valid year
    (1900..now+1), else "". The file's own embedded date is never consulted.
    """
    if not path:
        return ""
    now = datetime.now().year
    for comp in path.replace("\\", "/").split("/"):
        m = _ALBUM_YEAR_RE.search(comp)
        if m:
            y = int(m.group(1))
            if 1900 <= y <= now + 1:
                return str(y)
    return ""
