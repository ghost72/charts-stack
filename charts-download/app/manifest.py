"""
Manifest for handing the clean chart metadata over to charts-import.

Problem: slskd downloads often have bad or missing tags. charts-import would
then file them as "Unknown Artist/Unknown Album", and charts-sync cannot match
them against the charts.

Solution: at download time charts-download knows the REAL chart artist and
title. This mapping is written here into a shared JSON file (in the /charts
folder that charts-import also sees). The key is the basename of the downloaded
file - it is preserved no matter how slskd nests the path. charts-import looks
it up there and tags the file cleanly.

Format:
{
  "entries": {
    "golden.mp3": {"artist": "HUNTR/X", "title": "Golden", "album": "Golden",
                   "source": "charts"},
    ...
  }
}

"source" is the playlist label (from the missing file, e.g. "charts",
"mundart", "80er-90er"), so charts-import can show the origin in its import
message. Optional - older entries without "source" are fine.
"""

import json
import logging
import os

from . import config
from .i18n import t

logger = logging.getLogger(__name__)


def _load(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and "entries" in data:
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {"entries": {}}


def _save(path: str, data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError as e:
        logger.error(t("download.manifest.save_failed", error=e))


def _basename_key(filename: str) -> str:
    """Normalized key: bare file name, lowercased."""
    base = os.path.basename(filename.replace("\\", "/"))
    return base.lower()


def record(filename: str, artist: str, title: str, source: str = "") -> None:
    """
    Record the clean chart metadata for the downloaded file. Album is set to
    the title (chart songs are singles) so charts-import does not fall back to
    "Unknown Album".

    source = playlist label (e.g. "charts", "mundart", "flashback") so
    charts-import can show the origin in its import message.
    """
    data = _load(config.MANIFEST_FILE)
    entry = {
        "artist": artist,
        "title": title,
        "album": title,
    }
    if source:
        entry["source"] = source
    data["entries"][_basename_key(filename)] = entry
    _save(config.MANIFEST_FILE, data)
