"""Read/write helpers for the persistent artist scene selection."""

import json
import os
import re
import tempfile
import unicodedata
from datetime import datetime, timezone

from . import config

DEFAULT_SCENE = "Swiss German Mundart"
MAX_MANUAL_ARTISTS = 50
_ARTIST_ID_RE = re.compile(r"^[0-9]{1,20}$")


class ArtistSceneError(ValueError):
    """Raised for invalid artist data or a failed persistent write."""

    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.code = code
        self.status = status


def _inventory_path() -> str:
    return os.path.join(config.ARTIST_DATA_DIR, ".charts-artist-inventory.json")


def _selection_path() -> str:
    return os.path.join(config.ARTIST_DATA_DIR, ".charts-artist-selection.json")


def _clean_name(value: object, max_length: int = 160) -> str:
    if not isinstance(value, str):
        return ""
    name = unicodedata.normalize("NFC", value).strip()
    if (not 1 <= len(name) <= max_length
            or any(ord(char) < 32 for char in name)):
        return ""
    return name


def _artist_id(value: object) -> str:
    text = str(value or "").strip()
    return text if _ARTIST_ID_RE.fullmatch(text) else ""


def _normalize_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _read(path: str, default: dict) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return default
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtistSceneError("file_invalid", 500) from exc
    if not isinstance(data, dict):
        raise ArtistSceneError("file_invalid", 500)
    return data


def _load_inventory() -> dict:
    data = _read(_inventory_path(), {"version": 1, "artists": {}})
    artists = data.get("artists")
    if data.get("version") != 1 or not isinstance(artists, dict):
        raise ArtistSceneError("file_invalid", 500)
    return data


def _default_selection() -> dict:
    return {
        "version": 1,
        "filter_enabled": False,
        "target_scene": DEFAULT_SCENE,
        "artists": {},
    }


def _load_selection() -> dict:
    data = _read(_selection_path(), _default_selection())
    artists = data.get("artists")
    scene = data.get("target_scene")
    if (data.get("version") != 1
            or type(data.get("filter_enabled")) is not bool
            or not isinstance(scene, str) or not _clean_name(scene, 80)
            or not isinstance(artists, dict)):
        raise ArtistSceneError("file_invalid", 500)
    for key, item in artists.items():
        if (not isinstance(key, str) or not isinstance(item, dict)
                or type(item.get("enabled", False)) is not bool
                or type(item.get("manual", False)) is not bool
                or not isinstance(item.get("scene", ""), str)
                or (item.get("scene") and
                    not _clean_name(item.get("scene"), 80))
                or not isinstance(item.get("deezer_id", ""), str)
                or (item.get("deezer_id")
                    and not _artist_id(item.get("deezer_id")))
                or not isinstance(item.get("name", ""), str)
                or (item.get("name") and not _clean_name(item.get("name")))
                or not isinstance(item.get("evidence", ""), str)
                or (item.get("evidence") and
                    not _clean_name(item.get("evidence"), 500))):
            raise ArtistSceneError("file_invalid", 500)
    return data


def _atomic_write(path: str, data: dict) -> None:
    directory = os.path.dirname(path)
    temp_path = ""
    try:
        os.makedirs(directory, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(prefix=".artist-selection-", dir=directory)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(temp_path, 0o664)
        os.replace(temp_path, path)
    except OSError as exc:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)
        raise ArtistSceneError("write_failed", 500) from exc


def _catalog_entries(inventory: dict, selection: dict) -> list[dict]:
    records = inventory["artists"]
    selected = selection["artists"]
    keys = set(records) | {
        key for key, item in selected.items() if item.get("manual") is True
    }
    entries = []
    for key in keys:
        record = records.get(key, {})
        state = selected.get(key, {})
        raw_name = state.get("name") or record.get("name") or ""
        name = _clean_name(raw_name)
        if not name:
            raise ArtistSceneError("file_invalid", 500)
        deezer_id = _artist_id(
            state.get("deezer_id") or record.get("deezer_id") or "")
        source_counts = record.get("source_counts", {})
        if not isinstance(source_counts, dict):
            raise ArtistSceneError("file_invalid", 500)
        entries.append({
            "key": key,
            "name": name,
            "deezer_id": deezer_id,
            "identity_verified": bool(deezer_id),
            "manual": state.get("manual") is True,
            "enabled": state.get("enabled") is True,
            "scene": state.get("scene", ""),
            "evidence": state.get("evidence", ""),
            "reviewed_by": state.get("reviewed_by", ""),
            "reviewed_at": state.get("reviewed_at", ""),
            "source_counts": source_counts,
            "first_seen": record.get("first_seen", ""),
            "last_seen": record.get("last_seen", ""),
        })
    return sorted(entries, key=lambda item: (item["name"].casefold(), item["key"]))


def get_catalog() -> dict:
    inventory = _load_inventory()
    selection = _load_selection()
    return {
        "ok": True,
        "filter_enabled": selection["filter_enabled"],
        "target_scene": selection["target_scene"],
        "last_run_at": inventory.get("last_run_at", ""),
        "artists": _catalog_entries(inventory, selection),
    }


def save_settings(payload: object, username: str) -> dict:
    if not isinstance(payload, dict):
        raise ArtistSceneError("invalid_payload")
    filter_enabled = payload.get("filter_enabled")
    target_scene = _clean_name(payload.get("target_scene"), 80)
    updates = payload.get("artists")
    if (type(filter_enabled) is not bool or not target_scene
            or not isinstance(updates, list) or len(updates) > 5000):
        raise ArtistSceneError("invalid_payload")

    inventory = _load_inventory()
    selection = _load_selection()
    known_keys = set(inventory["artists"]) | set(selection["artists"])
    updated = dict(selection["artists"])
    seen: set[str] = set()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for row in updates:
        if not isinstance(row, dict):
            raise ArtistSceneError("invalid_payload")
        key = row.get("key")
        enabled = row.get("enabled")
        scene = row.get("scene", "")
        evidence = row.get("evidence", "")
        if (not isinstance(key, str) or key not in known_keys or key in seen
                or type(enabled) is not bool
                or not isinstance(scene, str)
                or not isinstance(evidence, str)):
            raise ArtistSceneError("invalid_payload")
        seen.add(key)
        clean_scene = _clean_name(scene, 80) if scene else ""
        clean_evidence = _clean_name(evidence, 500) if evidence else ""
        if ((scene and not clean_scene)
                or (evidence and not clean_evidence)
                or (enabled and (not clean_scene or not target_scene))):
            raise ArtistSceneError("invalid_payload")
        previous = updated.get(key, {})
        record = inventory["artists"].get(key, {})
        deezer_id = _artist_id(
            previous.get("deezer_id") or record.get("deezer_id") or "")
        if enabled and not deezer_id:
            raise ArtistSceneError("artist_not_verified")
        item = dict(previous)
        item.update({
            "name": previous.get("name") or record.get("name", ""),
            "deezer_id": deezer_id,
            "enabled": enabled,
            "scene": clean_scene,
            "evidence": clean_evidence,
            "manual": previous.get("manual") is True,
        })
        if enabled:
            item["reviewed_by"] = _clean_name(username, 80) or "admin"
            item["reviewed_at"] = now
        updated[key] = item

    manual_count = sum(1 for item in updated.values()
                       if item.get("manual") is True)
    if manual_count > MAX_MANUAL_ARTISTS:
        raise ArtistSceneError("too_many_manual")
    if filter_enabled and not any(
            item.get("enabled") is True
            and _normalize_name(item.get("scene", "")) == _normalize_name(target_scene)
            and _artist_id(item.get("deezer_id"))
            for item in updated.values()):
        raise ArtistSceneError("empty_selection")

    selection.update({
        "version": 1,
        "filter_enabled": filter_enabled,
        "target_scene": target_scene,
        "artists": updated,
    })
    _atomic_write(_selection_path(), selection)
    return get_catalog()


def link_artist(key: str, deezer_artist_id: str, canonical_name: str) -> dict:
    inventory = _load_inventory()
    selection = _load_selection()
    if key not in inventory["artists"] and key not in selection["artists"]:
        raise ArtistSceneError("artist_missing", 404)
    deezer_artist_id = _artist_id(deezer_artist_id)
    canonical_name = _clean_name(canonical_name)
    if not deezer_artist_id or not canonical_name:
        raise ArtistSceneError("invalid_payload")
    if key.startswith("deezer:") and key != f"deezer:{deezer_artist_id}":
        raise ArtistSceneError("invalid_payload")
    item = dict(selection["artists"].get(key, {}))
    item.update({
        "name": canonical_name,
        "deezer_id": deezer_artist_id,
        "manual": item.get("manual") is True,
        "enabled": item.get("enabled") is True,
        "scene": item.get("scene", ""),
        "evidence": item.get("evidence", ""),
    })
    selection["artists"][key] = item
    _atomic_write(_selection_path(), selection)
    return get_catalog()


def add_manual_artist(deezer_artist_id: str, canonical_name: str) -> dict:
    deezer_artist_id = _artist_id(deezer_artist_id)
    canonical_name = _clean_name(canonical_name)
    if not deezer_artist_id or not canonical_name:
        raise ArtistSceneError("invalid_payload")
    selection = _load_selection()
    key = f"deezer:{deezer_artist_id}"
    manual_count = sum(
        1 for item in selection["artists"].values()
        if item.get("manual") is True
    )
    is_already_manual = (
        selection["artists"].get(key, {}).get("manual") is True
    )
    if not is_already_manual and manual_count >= MAX_MANUAL_ARTISTS:
        raise ArtistSceneError("too_many_manual")
    item = dict(selection["artists"].get(key, {}))
    item.update({
        "name": canonical_name,
        "deezer_id": deezer_artist_id,
        "manual": True,
        "enabled": item.get("enabled") is True,
        "scene": item.get("scene", ""),
        "evidence": item.get("evidence", ""),
    })
    selection["artists"][key] = item
    _atomic_write(_selection_path(), selection)
    return get_catalog()
