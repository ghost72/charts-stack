"""
Manifest-Uebergabe an charts-import (KOPIE aus charts-songrequest, mit
replace-Flag).

charts-upgrade laedt eine bessere Version eines BEREITS vorhandenen Songs. Es
schreibt fuer die heruntergeladene Datei einen Manifest-Eintrag mit
replace=True. charts-import ersetzt dann die vorhandene Datei - aber NUR, wenn
die neue Datei laut echtem Frequenz-Cutoff (quality.py) wirklich besser ist.
Andernfalls bleibt die alte Datei; die neue wird nicht einsortiert.

Format identisch zu charts-download/-songrequest (gemeinsamer /charts-Ordner):
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
    """Normalisierter Schluessel: reiner Dateiname, kleingeschrieben."""
    base = os.path.basename(filename.replace("\\", "/"))
    return base.lower()


def record(filename: str, artist: str, title: str, source: str = "",
           replace: bool = False) -> None:
    """
    Vermerkt fuer die heruntergeladene Datei die sauberen Metadaten.

    replace=True signalisiert charts-import einen QUALITAETS-UPGRADE: der Song
    ist bereits in der Library, soll aber ersetzt werden, WENN die neue Datei
    laut echtem Frequenz-Cutoff besser ist (charts-import entscheidet final).
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
