"""
Manifest handoff to charts-import (COPY from charts-songrequest, with the
replace flag).

charts-upgrade loads a better version of an ALREADY present song. It writes a
manifest entry for the downloaded file with replace=True. charts-import then
replaces the existing file - but ONLY if the new file is really better
according to the real frequency cutoff (quality.py). Otherwise the old file
stays; the new one is not filed in.

Format identical to charts-download/-songrequest (shared /charts folder):
{
  "entries": {
    "song.mp3": {"artist": "...", "title": "...", "album": "...",
                 "source": "upgrade", "replace": true},
    ...
  }
}
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


def record(filename: str, artist: str, title: str, source: str = "",
           replace: bool = False) -> None:
    """
    Records the clean metadata for the downloaded file.

    replace=True signals a QUALITY UPGRADE to charts-import: the song is already
    in the library but should be replaced IF the new file is better according to
    the real frequency cutoff (charts-import makes the final decision).
    """
    data = _load(config.MANIFEST_FILE)
    entry = {
        "artist": artist,
        "title": title,
        "album": title,
    }
    if source:
        entry["source"] = source
    if replace:
        entry["replace"] = True
    data["entries"][_basename_key(filename)] = entry
    _save(config.MANIFEST_FILE, data)
