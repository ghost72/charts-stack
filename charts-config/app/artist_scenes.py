"""Read/write helpers for the persistent artist scene selection."""

import hashlib
import json
import os
import re
import stat
import tempfile
import unicodedata
from datetime import datetime, timezone
from functools import wraps

from . import config, playlist_rules

DEFAULT_SCENE = "My artists"
MAX_MANUAL_ARTISTS = 50
MAX_SCENES_PER_ARTIST = 20
_ARTIST_ID_RE = re.compile(r"^[0-9]{1,20}$")
_SCENE_FILE_RE = re.compile(r"(?:mundart-szene-[a-z0-9-]{1,48}-[a-f0-9]{10}|charts-[a-z0-9-]{1,48})\.m3u")
_SCENE_MARKER = "#CHARTS-STACK:MUNDART-SCENE\n"
_GENRE_MARKER = "#CHARTS-STACK:GENRE-PLAYLIST\n"


class ArtistSceneError(ValueError):
    """Raised for invalid artist data or a failed persistent write."""

    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.code = code
        self.status = status


def _selection_write(method):
    @wraps(method)
    def locked(*args, **kwargs):
        try:
            with playlist_rules.selection_lock(config.ARTIST_DATA_DIR):
                return method(*args, **kwargs)
        except OSError as exc:
            raise ArtistSceneError("write_failed", 500) from exc
    return locked


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


def _clean_scenes(values: object) -> list[str] | None:
    if not isinstance(values, list) or len(values) > MAX_SCENES_PER_ARTIST:
        return None
    names: dict[str, str] = {}
    for value in values:
        scene = _clean_name(value, 80)
        if not scene:
            return None
        names.setdefault(_normalize_name(scene), scene)
    return list(names.values())


def _entry_scenes(item: dict) -> list[str]:
    if "scenes" in item:
        scenes = _clean_scenes(item["scenes"])
        if scenes is None:
            raise ArtistSceneError("file_invalid", 500)
        return scenes
    return [item["scene"]] if item.get("scene") else []


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
    if "genre_catalog" in data and not playlist_rules.valid_genre_catalog(data["genre_catalog"]):
        raise ArtistSceneError("file_invalid", 500)
    reserved = data.get("reserved_playlists", [])
    if not isinstance(reserved, list) or any(
            not playlist_rules.valid_name(name, 255) for name in reserved):
        raise ArtistSceneError("file_invalid", 500)
    return data


def _default_selection() -> dict:
    return {
        "version": 1,
        "filter_enabled": False,
        "scene_playlists": False,
        "target_scene": DEFAULT_SCENE,
        "artists": {},
    }


def _load_selection() -> dict:
    data = _read(_selection_path(), _default_selection())
    artists = data.get("artists")
    scene = data.get("target_scene")
    if (data.get("version") != 1
            or type(data.get("filter_enabled")) is not bool
            or type(data.get("scene_playlists", False)) is not bool
            or not isinstance(scene, str) or not _clean_name(scene, 80)
            or not isinstance(artists, dict)
            or not _valid_scene_downloads(data.get("scene_downloads", {}))
            or not playlist_rules.valid_genre_rules(data.get("genre_playlists", {}))):
        raise ArtistSceneError("file_invalid", 500)
    blocked = data.get("disabled_scene_playlists", {})
    pending = data.get("pending_scene_deletions", {})
    scene_files = data.get("scene_files", {})
    if (not isinstance(scene_files, dict)
            or any(not _SCENE_FILE_RE.fullmatch(name) or _clean_name(scene, 80) != scene
                   or not scene for name, scene in scene_files.items())
            or len({_normalize_name(scene) for scene in scene_files.values()}) != len(scene_files)):
        raise ArtistSceneError("file_invalid", 500)
    if any(not isinstance(value, dict) for value in (blocked, pending)) or any(
        not _SCENE_FILE_RE.fullmatch(name) or not _clean_name(title, 100)
        for value in (blocked, pending) for name, title in value.items()
    ):
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
        _entry_scenes(item)
    for name, owner in list(scene_files.items()):
        destination = _scene_filename(owner)
        if (name != destination and destination not in scene_files
                and not os.path.lexists(os.path.join(config.MUSIC_DIR, name))
                and _scene_playlist_title(destination)):
            del scene_files[name]
            scene_files[destination] = owner
    return data


def _valid_scene_downloads(value: object) -> bool:
    return isinstance(value, dict) and all(
        bool(name) and _clean_name(name, 80) == name
        and type(limit) is int and 1 <= limit <= 100
        for name, limit in value.items()
    ) and len({_normalize_name(name) for name in value}) == len(value)


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
            "scenes": _entry_scenes(state),
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
    scene_filenames = {scene: _scene_filename(scene)
                       for scene in _renameable_scenes(selection).values()}
    scene_filenames.update({scene: name for name, scene in _blocked_scene_filenames(selection).items()})
    return {
        "ok": True,
        "playlist": config.CHARTS_ARTIST_PLAYLIST,
        "filter_enabled": selection["filter_enabled"],
        "scene_playlists": selection.get("scene_playlists", False),
        "scene_downloads": selection.get("scene_downloads", {}),
        "target_scene": selection["target_scene"],
        "last_run_at": inventory.get("last_run_at", ""),
        "artists": _catalog_entries(inventory, selection),
        "scene_filenames": scene_filenames,
        "genre_playlists": selection.get("genre_playlists", {}),
        "genre_catalog": inventory.get("genre_catalog"),
        "reserved_playlists": inventory.get("reserved_playlists", []),
    }


def _scene_playlist_title(name: str) -> str:
    path = os.path.join(config.MUSIC_DIR, name)
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            return ""
        with open(path, encoding="utf-8", errors="replace") as fh:
            header = fh.readline(128)
            title = fh.readline(512).removeprefix("#PLAYLIST:")
            marker = fh.readline(128)
        if header == "#EXTM3U\n" and marker in (_SCENE_MARKER, _GENRE_MARKER):
            return _clean_name(title, 100)
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise ArtistSceneError("playlist_read_failed", 500) from exc
    return ""


def list_scene_playlists() -> dict:
    selection = _load_selection()
    blocked = selection.get("disabled_scene_playlists", {})
    pending = selection.get("pending_scene_deletions", {})
    genres = {_scene_filename(scene): (scene, rule)
              for scene, rule in selection.get("genre_playlists", {}).items()}
    try:
        names = os.listdir(config.MUSIC_DIR)
    except OSError as exc:
        raise ArtistSceneError("playlist_read_failed", 500) from exc
    existing = {}
    for name in names:
        if _SCENE_FILE_RE.fullmatch(name) and (title := _scene_playlist_title(name)):
            existing[name] = title
    for alias, owner in selection.get("scene_files", {}).items():
        if alias in existing and owner in selection.get("genre_playlists", {}):
            genres[alias] = (owner, selection["genre_playlists"][owner])
            if alias != _scene_filename(owner) and _scene_filename(owner) not in existing:
                genres.pop(_scene_filename(owner), None)
    renameable = _renameable_scenes(selection)
    return {"playlists": [
        {"name": name, "title": genres[name][0] if name in genres else
         existing.get(name) or blocked.get(name) or pending[name],
         "present": name in existing, "disabled": name in blocked,
         **({"deleting": True} if name in pending else {}),
         **({"kind": "genre", **genres[name][1]} if name in genres else {}),
         **({"renameable": True} if name in renameable
            and (name in existing or name in genres)
            and name not in blocked and name not in pending else {})}
        for name in sorted(existing.keys() | blocked.keys() | pending.keys() | genres.keys())
    ]}


def _scene_filename(scene: str) -> str:
    # Keep this in sync with charts-sync's default scene filename.
    key = _normalize_name(scene)
    ascii_name = unicodedata.normalize("NFKD", key).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-")[:48].rstrip("-") or "scene"
    return f"charts-{slug}.m3u"


def _legacy_scene_filename(scene: str) -> str:
    slug = _scene_filename(scene).removeprefix("charts-").removesuffix(".m3u")
    suffix = hashlib.sha256(_normalize_name(scene).encode()).hexdigest()[:10]
    return f"mundart-szene-{slug}-{suffix}.m3u"


def _blocked_scene_filenames(selection: dict) -> dict[str, str]:
    result = {}
    aliases = selection.get("scene_files", {})
    for name, title in selection.get("disabled_scene_playlists", {}).items():
        scene = aliases.get(name, title.removeprefix("Mundart: ")
                            if name.startswith("mundart-szene-") else title)
        result[_scene_filename(scene)] = scene
    return result


def _renameable_scenes(selection: dict) -> dict[str, str]:
    """Resolve files against assigned scenes, never against arbitrary titles."""
    aliases = selection.get("scene_files", {})
    by_scene = {_normalize_name(scene): name for name, scene in aliases.items()}
    result = dict(aliases)
    result.update({_scene_filename(scene): scene for scene in aliases.values()})
    result.update({_scene_filename(scene): scene for scene in selection.get("genre_playlists", {})})
    for item in selection["artists"].values():
        for scene in _entry_scenes(item):
            key = _normalize_name(scene)
            if key in by_scene:
                result[by_scene[key]] = aliases[by_scene[key]]
            else:
                result.setdefault(_scene_filename(scene), scene)
    try:
        names = os.listdir(config.MUSIC_DIR)
    except OSError as exc:
        raise ArtistSceneError("playlist_read_failed", 500) from exc
    for name in names:
        if name in result or not _SCENE_FILE_RE.fullmatch(name):
            continue
        title = _scene_playlist_title(name)
        scene = _clean_name(title.removeprefix("Mundart: ")
                            if name.startswith("mundart-szene-") else title, 80)
        if scene and name in (_scene_filename(scene), _legacy_scene_filename(scene)):
            result[name] = scene
    return result


def _check_reserved_scene_names(artists: dict, aliases: dict) -> None:
    owners = {_normalize_name(scene) for scene in aliases.values()}
    for item in artists.values():
        if any(_normalize_name(scene) not in owners and _scene_filename(scene) in aliases
               for scene in _entry_scenes(item)):
            raise ArtistSceneError("scene_name_reserved", 409)


def _check_scene_filenames(selection: dict) -> None:
    recognized = _renameable_scenes(selection)
    owners = {_normalize_name(scene): scene for scene in recognized.values()}
    for item in selection["artists"].values():
        owners.update((_normalize_name(scene), scene) for scene in _entry_scenes(item))
    owners.update((_normalize_name(scene), scene) for scene in selection.get("scene_downloads", {}))
    genre_names = selection.get("genre_playlists", {})
    assigned = {_normalize_name(scene) for item in selection["artists"].values()
                for scene in _entry_scenes(item)}
    if selection["filter_enabled"]:
        assigned.add(_normalize_name(selection["target_scene"]))
    if any(_normalize_name(scene) in assigned
           or _normalize_name(scene) in {_normalize_name(name) for name in selection.get("scene_downloads", {})}
           for scene in genre_names):
        raise ArtistSceneError("scene_name_conflict", 409)
    owners.update((_normalize_name(scene), scene) for scene in genre_names)
    pending = selection.get("pending_scene_deletions", {})
    if any(_scene_filename(scene) in pending or any(
            _normalize_name(scene) == _normalize_name(owner) for owner in pending.values())
           for scene in owners.values()):
        raise ArtistSceneError("playlist_delete_pending", 409)
    reserved = {config.CHARTS_ARTIST_PLAYLIST.casefold()}
    inventory = _load_inventory()
    reserved.update(name.casefold() for name in inventory.get("reserved_playlists", []))
    catalog = inventory.get("genre_catalog")
    if catalog:
        reserved.update(name.casefold() for name in catalog.get("reserved_playlists", []))
    files: dict[str, str] = {}
    blocked = _blocked_scene_filenames(selection)
    for key, scene in owners.items():
        name = _scene_filename(scene)
        if name.casefold() in reserved:
            raise ArtistSceneError("scene_name_conflict", 409)
        if name in files and files[name] != key:
            raise ArtistSceneError("scene_name_conflict", 409)
        files[name] = key
        blocked_title = blocked.get(name)
        if blocked_title:
            if _normalize_name(blocked_title) != key:
                raise ArtistSceneError("scene_name_conflict", 409)
        if os.path.lexists(os.path.join(config.MUSIC_DIR, name)):
            if name not in recognized or not _scene_playlist_title(name):
                raise ArtistSceneError("scene_name_conflict", 409)
            if _normalize_name(recognized[name]) != key:
                raise ArtistSceneError("scene_name_conflict", 409)
            if name not in selection.get("scene_files", {}):
                title = _scene_playlist_title(name)
                if name.startswith("mundart-szene-"):
                    title = title.removeprefix("Mundart: ")
                if _normalize_name(title) != key:
                    raise ArtistSceneError("scene_name_conflict", 409)


@_selection_write
def rename_scene_playlist(name: object, new_scene: object) -> dict:
    new_scene = _clean_name(new_scene, 80)
    if not isinstance(name, str) or not _SCENE_FILE_RE.fullmatch(name) or not new_scene:
        raise ArtistSceneError("invalid_payload")
    selection = _load_selection()
    old_scene = _renameable_scenes(selection).get(name)
    blocked = selection.get("disabled_scene_playlists", {})
    present = bool(_scene_playlist_title(name))
    if (not old_scene or name in blocked or name in selection.get("pending_scene_deletions", {})
            or (not present and old_scene not in selection.get("genre_playlists", {}))):
        raise ArtistSceneError("playlist_not_found", 404)
    old_key, new_key = _normalize_name(old_scene), _normalize_name(new_scene)
    aliases = selection.setdefault("scene_files", {})
    downloads = selection.get("scene_downloads", {})
    artists = selection["artists"]
    destination = _scene_filename(new_scene)
    if old_key != new_key and (
            any(_normalize_name(scene) == new_key for item in artists.values()
                for scene in _entry_scenes(item))
            or any(_normalize_name(scene) == new_key for scene in selection.get("genre_playlists", {}))
            or any(_normalize_name(scene) == new_key for scene in [*aliases.values(), *downloads])
            or _normalize_name(selection["target_scene"]) == new_key
            or (destination != name and (destination in blocked
                or os.path.lexists(os.path.join(config.MUSIC_DIR, destination))))):
        raise ArtistSceneError("scene_name_conflict", 409)
    for item in artists.values():
        scenes = _entry_scenes(item)
        if any(_normalize_name(scene) == old_key for scene in scenes):
            scenes = [new_scene if _normalize_name(scene) == old_key else scene for scene in scenes]
            item.update(scene=scenes[0], scenes=scenes)
    if _normalize_name(selection["target_scene"]) == old_key:
        selection["target_scene"] = new_scene
    selection["scene_downloads"] = {
        new_scene if _normalize_name(scene) == old_key else scene: limit
        for scene, limit in downloads.items()
    }
    selection["genre_playlists"] = {
        new_scene if _normalize_name(scene) == old_key else scene: rule
        for scene, rule in selection.get("genre_playlists", {}).items()
    }
    for alias, owner in list(aliases.items()):
        if _normalize_name(owner) == old_key:
            del aliases[alias]
    if present:
        aliases[name] = new_scene
    _check_reserved_scene_names(artists, aliases)
    _check_scene_filenames(selection)
    _atomic_write(_selection_path(), selection)
    return {**list_scene_playlists(), "catalog": get_catalog()}


@_selection_write
def set_scene_playlist_disabled(name: object, disabled: object) -> dict:
    if (not isinstance(name, str) or not _SCENE_FILE_RE.fullmatch(name)
            or type(disabled) is not bool):
        raise ArtistSceneError("invalid_payload")
    selection = _load_selection()
    blocked = selection.setdefault("disabled_scene_playlists", {})
    if disabled:
        title = _scene_playlist_title(name)
        if not title:
            raise ArtistSceneError("playlist_not_found", 404)
        blocked[name] = title
    else:
        if name not in blocked:
            raise ArtistSceneError("playlist_not_found", 404)
        del blocked[name]
    _atomic_write(_selection_path(), selection)
    return list_scene_playlists()


@_selection_write
def delete_scene_playlist(name: object) -> dict:
    if not isinstance(name, str) or not _SCENE_FILE_RE.fullmatch(name):
        raise ArtistSceneError("invalid_payload")
    selection = _load_selection()
    blocked = selection.get("disabled_scene_playlists", {})
    title = _scene_playlist_title(name) or blocked.get(name, "")
    scene = selection.get("scene_files", {}).get(name)
    if not scene:
        scene = next((owner for owner in selection.get("genre_playlists", {})
                      if _scene_filename(owner) == name), None)
    if not scene:
        scene = title.removeprefix("Mundart: ") if name.startswith("mundart-szene-") else title
    scene = _clean_name(scene, 80)
    if not scene:
        raise ArtistSceneError("playlist_not_found", 404)
    key = _normalize_name(scene)
    if (name.casefold() == config.CHARTS_ARTIST_PLAYLIST.casefold()
            or (selection["filter_enabled"] and _normalize_name(selection["target_scene"]) == key)):
        raise ArtistSceneError("playlist_main_filter", 409)
    aliases = selection.get("scene_files", {})
    names = {name, _scene_filename(scene)} | {
        alias for alias, owner in aliases.items() if _normalize_name(owner) == key}
    names.update(alias for alias, owner in _blocked_scene_filenames(selection).items()
                 if _normalize_name(owner) == key)
    pending = selection.setdefault("pending_scene_deletions", {})
    for filename in names:
        if os.path.lexists(os.path.join(config.MUSIC_DIR, filename)):
            physical = _scene_playlist_title(filename)
            owner = aliases.get(filename, physical.removeprefix("Mundart: ")
                                if filename.startswith("mundart-szene-") else physical)
            if not physical or _normalize_name(owner) != key:
                raise ArtistSceneError("scene_name_conflict", 409)
            pending[filename] = scene
    for item in selection["artists"].values():
        if not any(_normalize_name(value) == key for value in _entry_scenes(item)):
            continue
        scenes = [value for value in _entry_scenes(item) if _normalize_name(value) != key]
        item.update(scene=scenes[0] if scenes else "", scenes=scenes)
        if not scenes:
            item["enabled"] = False
    for field in ("scene_downloads", "genre_playlists"):
        values = selection.get(field, {})
        selection[field] = {
            entry: value for entry, value in values.items()
            if _normalize_name(entry) != key
        }
    selection["scene_files"] = {entry: owner for entry, owner in aliases.items()
                               if entry not in names and _normalize_name(owner) != key}
    selection["disabled_scene_playlists"] = {
        entry: owner for entry, owner in blocked.items()
        if entry not in names and _normalize_name(aliases.get(
            entry, owner.removeprefix("Mundart: ") if entry.startswith("mundart-szene-") else owner)) != key
    }
    _atomic_write(_selection_path(), selection)
    return {**list_scene_playlists(), "catalog": get_catalog()}


@_selection_write
def save_deezer_genre_playlist(payload: object, genre: dict) -> dict:
    if not isinstance(payload, dict):
        raise ArtistSceneError("invalid_payload")
    name = _clean_name(payload.get("name"), 80)
    replacing = payload.get("replace", False)
    rule = {"genre_id": payload.get("genre_id"),
            "genre_name": _clean_name(genre.get("name"), 160),
            "limit": payload.get("limit", 50)}
    if (not name or type(replacing) is not bool
            or not playlist_rules.valid_genre_rules({name: rule})):
        raise ArtistSceneError("invalid_payload")
    if genre.get("id") != rule["genre_id"]:
        raise ArtistSceneError("genre_unknown", 409)
    selection = _load_selection()
    rules = selection.setdefault("genre_playlists", {})
    if replacing and (name not in rules or "genre_id" not in rules[name]):
        raise ArtistSceneError("playlist_not_found", 404)
    if not replacing and any(_normalize_name(name) == _normalize_name(owner)
                             for owner in _renameable_scenes(selection).values()):
        raise ArtistSceneError("scene_name_conflict", 409)
    rules[name] = rule
    _check_scene_filenames(selection)
    _atomic_write(_selection_path(), selection)
    return {**list_scene_playlists(), "catalog": get_catalog()}


@_selection_write
def save_genre_playlist(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise ArtistSceneError("invalid_payload")
    name = _clean_name(payload.get("name"), 80)
    values = payload.get("genres")
    replacing = payload.get("replace", False)
    if (not name or type(replacing) is not bool
            or not playlist_rules.valid_genre_rules({name: {"genres": values}})):
        raise ArtistSceneError("invalid_payload")
    selection = _load_selection()
    rules = selection.setdefault("genre_playlists", {})
    if replacing and name not in rules:
        raise ArtistSceneError("playlist_not_found", 404)
    if not replacing and any(_normalize_name(name) == _normalize_name(owner)
                             for owner in _renameable_scenes(selection).values()):
        raise ArtistSceneError("scene_name_conflict", 409)
    catalog = _load_inventory().get("genre_catalog")
    if not isinstance(catalog, dict):
        raise ArtistSceneError("genre_catalog_missing", 409)
    known = {_normalize_name(entry["name"]): entry["name"] for entry in catalog["genres"]}
    if replacing:
        known.update({_normalize_name(genre): genre for genre in rules[name]["genres"]})
    if any(_normalize_name(genre) not in known for genre in values):
        raise ArtistSceneError("genre_unknown", 409)
    rules[name] = {"genres": [known[_normalize_name(genre)] for genre in values]}
    _check_scene_filenames(selection)
    _atomic_write(_selection_path(), selection)
    return {**list_scene_playlists(), "catalog": get_catalog()}


@_selection_write
def save_settings(payload: object, username: str) -> dict:
    if not isinstance(payload, dict):
        raise ArtistSceneError("invalid_payload")
    filter_enabled = payload.get("filter_enabled")
    target_scene = _clean_name(payload.get("target_scene"), 80)
    updates = payload.get("artists")
    scene_playlists = payload.get("scene_playlists")
    scene_downloads = payload.get("scene_downloads", {})
    if (type(filter_enabled) is not bool or not target_scene
            or ("scene_playlists" in payload and type(scene_playlists) is not bool)
            or not _valid_scene_downloads(scene_downloads)
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
        scenes = _clean_scenes(row.get("scenes", [scene] if scene else []))
        clean_evidence = _clean_name(evidence, 500) if evidence else ""
        if (scenes is None or (evidence and not clean_evidence)
                or (enabled and not scenes)):
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
            "scene": scenes[0] if scenes else "",
            "scenes": scenes,
            "evidence": clean_evidence,
            "manual": previous.get("manual") is True,
        })
        if enabled:
            item["reviewed_by"] = _clean_name(username, 80) or "admin"
            item["reviewed_at"] = now
        updated[key] = item

    manual_count = sum(1 for item in updated.values()
                       if item.get("manual") is True)
    _check_reserved_scene_names(updated, selection.get("scene_files", {}))
    if manual_count > MAX_MANUAL_ARTISTS:
        raise ArtistSceneError("too_many_manual")
    if filter_enabled and not any(
            item.get("enabled") is True
            and any(_normalize_name(scene) == _normalize_name(target_scene)
                    for scene in _entry_scenes(item))
            and _artist_id(item.get("deezer_id"))
            for item in updated.values()):
        raise ArtistSceneError("empty_selection")

    selection.update({
        "version": 1,
        "filter_enabled": filter_enabled,
        "scene_playlists": selection.get("scene_playlists", False)
        if scene_playlists is None else scene_playlists,
        "target_scene": target_scene,
        "artists": updated,
    })
    if "scene_downloads" in payload:
        selection["scene_downloads"] = scene_downloads
    _check_scene_filenames(selection)
    _atomic_write(_selection_path(), selection)
    return get_catalog()


@_selection_write
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


@_selection_write
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
