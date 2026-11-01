"""
Tiny read-only loader for the charts-download manifest.

The manifest maps a downloaded file's (lowercased) basename to its CLEAN chart
metadata. The Manual Tag Editor uses it only as an ADVISORY signal to classify a
search result's origin (stack vs curated) when the file does not carry a TXXX
marker. Same {'entries': {...}} shape read by charts-import's tags._load_manifest.

This is a copy-into-each-service module (no cross-service runtime import).
Fail-open: any read error yields an empty map, so the editor never breaks when
the manifest is absent or malformed.
"""

import json


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
