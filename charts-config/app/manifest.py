"""
Tiny read-only loader for the charts-download manifest.

The manifest maps a downloaded file's (lowercased) basename to its CLEAN chart
metadata. The Manual Tag Editor uses it only as an ADVISORY signal to classify a
search result's origin (stack vs curated) when the file does not carry a TXXX
marker. Same {'entries': {...}} shape read by charts-import's tags._load_manifest.

This is a copy-into-each-service module (no cross-service runtime import).
Fail-open: any read error yields an empty map, so the editor never breaks when
the manifest is absent or malformed.

load_entries is read-only (the origin-classification signal). remove_entry /
pop_entry remove the file's manifest entry when the manual Tag-Editor deletes a
song, so charts-import does not re-import the just-trashed file on its next run
(pop_entry additionally hands back the removed value so the delete flow can stash
it in a restore sidecar). add_entry re-inserts it on restore. All keyed by the
lowercased basename exactly as charts-download / charts-songrequest write it
(manifest.record), so a deleted song does not come back - and a restored one is
recognized again.
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

    Thin wrapper around pop_entry (which also hands back the removed value for the
    Tag-Editor delete flow's restore sidecar); this keeps the original boolean
    contract for callers that do not need the value.
    """
    cleared, _value = pop_entry(path, filename)
    return cleared


def pop_entry(path: str, filename: str):
    """
    Remove the manifest entry keyed by the lowercased basename of `filename` and
    return (cleared, value): `cleared` True when an entry was present and the file
    was rewritten (False on missing manifest / unknown key / write error), and
    `value` the removed entry's value (so the Tag-Editor delete flow can stash it
    in the restore sidecar) or None when nothing was removed.

    Preserves the rest of the manifest (loads the FULL document, deletes only the
    one key under "entries", writes atomically via a temp file + os.replace,
    exactly like the writers' _save). Fail-open: any read/parse/write error logs
    a warning and returns (False, None) without raising, so a delete that trashed
    the audio file never crashes on a manifest hiccup (the file is already gone;
    a stale manifest entry only risks a benign re-import, not data loss).
    """
    key = _basename_key(filename)
    if not key:
        return False, None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return False, None
    if not isinstance(data, dict):
        return False, None
    entries = data.get("entries")
    if not isinstance(entries, dict) or key not in entries:
        return False, None
    value = entries[key]
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
        return False, None
    return True, value


def pop_entries(path: str, keys) -> dict:
    """
    Remove SEVERAL manifest entries (exact, already-lowercased keys) in ONE atomic
    rewrite and return {key: removed_value} for the keys that were present.

    Used by the Tag-Editor delete, which can match more than one entry for a song
    (YouTube/upload key, "(2)" collision names, re-downloads). Same contract as
    pop_entry: loads the full document, keeps everything else, writes via temp +
    os.replace, and is fail-open - any read/parse/write error logs a warning and
    returns {} without raising.
    """
    wanted = [k for k in (keys or []) if k]
    if not wanted:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    entries = data.get("entries")
    if not isinstance(entries, dict):
        return {}
    removed = {}
    for k in wanted:
        if k in entries and k not in removed:
            removed[k] = entries.pop(k)
    if not removed:
        return {}
    data["entries"] = entries
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError as e:
        logger.warning("tag editor manifest remove failed for %s: %s", path, e)
        return {}
    return removed


def add_entry(path: str, key: str, value) -> bool:
    """
    (Re-)insert `value` under the lowercased basename `key` into the manifest at
    `path` (used by the Tag-Editor restore flow to put back the entry that delete
    removed, so charts-import recognizes the restored song again). Returns True
    when written, False when `key`/`value` is missing or a read/parse/write error
    occurred.

    Preserves the rest of the manifest and writes atomically (temp + os.replace),
    exactly like pop_entry. Overwrites an existing entry at `key` (restore of a
    re-created key simply re-establishes the known metadata). Fail-open: any error
    logs a warning and returns False without raising, so a best-effort manifest
    re-insert never turns a successful file restore into a failure.
    """
    key = (key or "").lower()
    if not key or value is None:
        return False
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        data = {}
    except (json.JSONDecodeError, OSError):
        return False
    if not isinstance(data, dict):
        data = {}
    entries = data.get("entries")
    if not isinstance(entries, dict):
        entries = {}
    entries[key] = value
    data["entries"] = entries
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError as e:
        logger.warning("tag editor manifest add failed for %s: %s", path, e)
        return False
    return True
