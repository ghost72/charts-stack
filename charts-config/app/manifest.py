"""
Tiny read-only loader for the charts-download manifest.

The manifest maps a downloaded file's (lowercased) basename to its CLEAN chart
metadata. The Manual Tag Editor uses it only as an ADVISORY signal to classify a
search result's origin (stack vs curated) when the file does not carry a TXXX
marker. Same {'entries': {...}} shape read by charts-import's tags._load_manifest.

This is a copy-into-each-service module (no cross-service runtime import).
Fail-open: any read error yields an empty map, so the editor never breaks when
the manifest is absent or malformed.

load_entries is read-only (the origin-classification signal). remove_entry is
the ONE mutating helper: the manual Tag-Editor's delete action removes the
file's manifest entry so charts-import does not re-import the just-trashed file
on its next run. Keyed by the lowercased basename exactly as charts-download /
charts-songrequest write it (manifest.record), so a deleted song does not come
back.
"""

import json
import logging
import os

logger = logging.getLogger(__name__)


def load_entries(path: str) -> dict:
    """
    Load the manifest at `path` and return its {basename-lowercased: entry} map.
    Fail-open to {} on FileNotFoundError / JSONDecodeError / OSError or any
    non-dict shape.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    entries = data.get("entries", {})
    return entries if isinstance(entries, dict) else {}


def _basename_key(filename: str) -> str:
    """Normalized key: plain file name, lowercased (identical to the writers'
    manifest.record key, so a delete removes the entry the download wrote)."""
    base = os.path.basename((filename or "").replace("\\", "/"))
    return base.lower()


def remove_entry(path: str, filename: str) -> bool:
    """
    Remove the manifest entry keyed by the lowercased basename of `filename` from
    the manifest file at `path`. Returns True when an entry was present and the
    file was rewritten, False when there was nothing to remove (missing manifest,
    unknown key) or the key was absent.

    Preserves the rest of the manifest (loads the FULL document, deletes only the
    one key under "entries", writes atomically via a temp file + os.replace,
    exactly like the writers' _save). Fail-open: any read/parse/write error logs
    a warning and returns False without raising, so a delete that trashed the
    audio file never crashes on a manifest hiccup (the file is already gone; a
    stale manifest entry only risks a benign re-import, not data loss).
    """
    key = _basename_key(filename)
    if not key:
        return False
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return False
    if not isinstance(data, dict):
        return False
    entries = data.get("entries")
    if not isinstance(entries, dict) or key not in entries:
        return False
    del entries[key]
    data["entries"] = entries
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError as e:
        logger.warning("tag editor manifest remove failed for %s: %s", path, e)
        return False
    return True
