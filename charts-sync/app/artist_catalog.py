"""Persistent artist inventory and selection for the Mundart playlist."""

import json
import os
import re
import tempfile
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone

from . import config, playlist_rules

DEFAULT_SCENE = "My artists"
MAX_MANUAL_ARTISTS = 50
MAX_SCENES_PER_ARTIST = 20
SCENE_MISSING_FILE = ".charts-scene-missing.txt"
_ARTIST_ID_RE = re.compile(r"^[0-9]{1,20}$")
_SCENE_FILE_RE = re.compile(r"(?:mundart-szene-[a-z0-9-]{1,48}-[a-f0-9]{10}|charts-[a-z0-9-]{1,48})\.m3u")


class ArtistCatalogError(RuntimeError):
    """Raised when persisted artist data is invalid or cannot be written."""


def _inventory_path() -> str:
    return os.path.join(config.CHARTS_DIR, ".charts-artist-inventory.json")


def _selection_path() -> str:
    return os.path.join(config.CHARTS_DIR, ".charts-artist-selection.json")


def normalize_name(name: str) -> str:
    """Return a stable exact-match key without removing meaningful accents."""
    return " ".join(unicodedata.normalize("NFKC", name).casefold().split())


def clean_name(value: object) -> str:
    """Validate a display name while preserving its Unicode spelling."""
    if not isinstance(value, str):
        return ""
    name = unicodedata.normalize("NFC", value).strip()
    if not 1 <= len(name) <= 160 or any(ord(char) < 32 for char in name):
        return ""
    return name


def artist_id(value: object) -> str:
    text = str(value or "").strip()
    return text if _ARTIST_ID_RE.fullmatch(text) else ""


def identity_key(name: str, deezer_artist_id: object = "") -> str:
    artist_id_value = artist_id(deezer_artist_id)
    if artist_id_value:
        return f"deezer:{artist_id_value}"
    normalized = normalize_name(name)
    return f"name:{normalized}" if normalized else ""


def _scene_names(item: dict) -> list[str]:
    if "scenes" not in item:
        return [item["scene"]] if item.get("scene") else []
    values = item["scenes"]
    if (not isinstance(values, list) or len(values) > MAX_SCENES_PER_ARTIST
            or any(not isinstance(value, str) or not 1 <= len(value) <= 80
                   or clean_name(value) != value for value in values)):
        raise ArtistCatalogError("Artist selection contains invalid scenes.")
    return values


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_json(path: str, default: dict) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return default
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtistCatalogError(f"Could not read artist data: {exc}") from exc
    if not isinstance(data, dict):
        raise ArtistCatalogError("Artist data must be a JSON object.")
    return data


def _atomic_write(path: str, data: dict) -> None:
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    temp_path = ""
    try:
        fd, temp_path = tempfile.mkstemp(prefix=".artist-data-", dir=directory)
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
        raise ArtistCatalogError(f"Could not write artist data: {exc}") from exc


def _load_inventory() -> dict:
    data = _read_json(_inventory_path(), {"version": 1, "artists": {}})
    artists = data.get("artists")
    if data.get("version") != 1 or not isinstance(artists, dict):
        raise ArtistCatalogError("Artist inventory has an unsupported format.")
    return data


def load_selection() -> dict:
    data = _read_json(
        _selection_path(),
        {
            "version": 1,
            "filter_enabled": False,
            "target_scene": DEFAULT_SCENE,
            "artists": {},
        },
    )
    artists = data.get("artists")
    scene = data.get("target_scene")
    blocked = data.get("disabled_scene_playlists", {})
    pending = data.get("pending_scene_deletions", {})
    downloads = data.get("scene_downloads", {})
    scene_files = data.get("scene_files", {})
    if (data.get("version") != 1
            or type(data.get("filter_enabled")) is not bool
            or type(data.get("scene_playlists", False)) is not bool
            or not isinstance(blocked, dict)
            or not isinstance(pending, dict)
            or any(not isinstance(name, str) or not _SCENE_FILE_RE.fullmatch(name)
                   or not playlist_rules.valid_name(value, 100)
                   for name, value in pending.items())
            or not playlist_rules.valid_genre_rules(data.get("genre_playlists", {}))
            or not isinstance(scene_files, dict)
            or any(not _SCENE_FILE_RE.fullmatch(name) or not clean_name(value)
                   or clean_name(value) != value or len(value) > 80
                   for name, value in scene_files.items())
            or len({normalize_name(value) for value in scene_files.values()}) != len(scene_files)
            or not isinstance(downloads, dict)
            or any(not name or len(name) > 80 or clean_name(name) != name
                   or type(limit) is not int or not 1 <= limit <= 100
                   for name, limit in downloads.items())
            or not isinstance(scene, str) or not scene.strip()
            or len(scene) > 80 or clean_name(scene) != scene
            or not isinstance(artists, dict)):
        raise ArtistCatalogError("Artist selection has an unsupported format.")
    for key, item in artists.items():
        if (not isinstance(key, str) or not isinstance(item, dict)
                or type(item.get("enabled", False)) is not bool
                or type(item.get("manual", False)) is not bool
                or not isinstance(item.get("scene", ""), str)
                or (item.get("scene") and
                    (len(item["scene"]) > 80
                     or clean_name(item["scene"]) != item["scene"]))
                or not isinstance(item.get("deezer_id", ""), str)
                or (item.get("deezer_id")
                    and not artist_id(item.get("deezer_id")))
                or (key.startswith("deezer:")
                    and item.get("deezer_id") != key[7:])
                or (item.get("manual") is True
                    and not artist_id(item.get("deezer_id")))):
            raise ArtistCatalogError("Artist selection contains an invalid entry.")
        _scene_names(item)
    return data


def record_library_genres(lib) -> None:
    counts: Counter[str] = Counter()
    names = {}
    tracks = [track for track in lib.tracks
              if ".charts-trash" not in str(getattr(track, "path", "")).split(os.sep)]
    untagged = unreadable = 0
    for track in tracks:
        genres = getattr(track, "genres", [])
        untagged += not genres
        unreadable += bool(getattr(track, "genre_read_error", False))
        for genre in genres:
            key = normalize_name(genre)
            names.setdefault(key, genre)
            counts[key] += 1
    inventory = _load_inventory()
    inventory["genre_catalog"] = {
        "updated_at": _now(), "total": len(tracks),
        "untagged": untagged, "unreadable": unreadable,
        "reserved_playlists": [source.name for source in config.CHARTS],
        "genres": [{"name": names[key], "count": counts[key]} for key in sorted(names)],
    }
    _atomic_write(_inventory_path(), inventory)


def record_playlist_reservations() -> None:
    inventory = _load_inventory()
    inventory["reserved_playlists"] = [source.name for source in config.CHARTS]
    inventory.pop("genre_catalog", None)
    _atomic_write(_inventory_path(), inventory)


def _feed_label(feed) -> str:
    if feed.kind == "energy":
        return f"energy:{feed.theme}"
    if feed.kind == "apple":
        return f"apple:{feed.country}"
    if feed.kind == "lastfm":
        return f"lastfm:{feed.method}"
    return f"deezer:{feed.deezer_type}:{feed.source}"


def record_discoveries(feed_tracks: list[tuple[str, list]]) -> None:
    """Upsert all main and featured artists from successfully fetched feeds."""
    if not feed_tracks:
        return
    data = _load_inventory()
    artists = data["artists"]
    now = _now()

    for source, tracks in feed_tracks:
        counts: Counter[str] = Counter()
        names: dict[str, str] = {}
        ids: dict[str, str] = {}
        roles: dict[str, set[str]] = defaultdict(set)
        for track in tracks:
            credits = [(track.artist, getattr(track, "artist_id", ""), "main")]
            main_key = identity_key(track.artist, getattr(track, "artist_id", ""))
            for contributor in getattr(track, "contributors", []):
                if normalize_name(contributor) != normalize_name(track.artist):
                    credits.append((contributor, "", "featured"))
            seen_in_track: set[str] = set()
            for name, deezer_artist_id, role in credits:
                display_name = clean_name(name)
                key = identity_key(display_name, deezer_artist_id)
                if not display_name or not key or key in seen_in_track:
                    continue
                seen_in_track.add(key)
                counts[key] += 1
                names[key] = display_name
                ids[key] = artist_id(deezer_artist_id)
                roles[key].add(role)
            if main_key:
                roles[main_key].add("main")

        for key, count in counts.items():
            record = artists.setdefault(key, {
                "key": key,
                "name": names[key],
                "deezer_id": ids[key],
                "first_seen": now,
                "source_counts": {},
            })
            if ids[key]:
                record["deezer_id"] = ids[key]
            record["name"] = names[key]
            record["last_seen"] = now
            source_counts = record.setdefault("source_counts", {})
            source_counts[source] = {
                "tracks": count,
                "roles": sorted(roles[key]),
                "last_seen": now,
            }

    data["last_run_at"] = now
    _atomic_write(_inventory_path(), data)


def manual_artist_feeds(selection: dict) -> list:
    """Build bounded Deezer top-track feeds for selected manual artists."""
    if not selection["filter_enabled"]:
        return []
    scene = normalize_name(selection["target_scene"])
    selected = []
    for key, item in selection["artists"].items():
        deezer_id_value = artist_id(item.get("deezer_id"))
        if (item.get("manual") is True
                and item.get("enabled") is True
                and any(normalize_name(name) == scene for name in _scene_names(item))
                and deezer_id_value):
            selected.append((key, deezer_id_value))
    if len(selected) > MAX_MANUAL_ARTISTS:
        raise ArtistCatalogError(
            f"At most {MAX_MANUAL_ARTISTS} manual artists may be enabled.")
    return [
        config.Feed(kind="deezer", limit=50, enabled=True,
                    deezer_type="artist", source=deezer_id_value)
        for _key, deezer_id_value in sorted(selected)
    ]


def _entry_scenes(key: str, deezer_artist_id: str, selection: dict) -> set[str]:
    artists = selection["artists"]
    candidates = [artists.get(key)]
    if deezer_artist_id:
        candidates.extend(
            item for item in artists.values()
            if isinstance(item, dict)
            and artist_id(item.get("deezer_id")) == deezer_artist_id
        )
    scenes: set[str] = set()
    for item in candidates:
        if not isinstance(item, dict) or item.get("enabled") is not True:
            continue
        linked_id = artist_id(item.get("deezer_id"))
        if linked_id and (not deezer_artist_id or linked_id == deezer_artist_id):
            scenes.update(normalize_name(scene) for scene in _scene_names(item))
    return scenes


def is_allowed(track, selection: dict) -> bool:
    """Allow a track when any verified, enabled credited artist matches."""
    if not selection["filter_enabled"]:
        return True
    return normalize_name(selection["target_scene"]) in track_scenes(track, selection)


def track_scenes(track, selection: dict) -> set[str]:
    main_id = artist_id(getattr(track, "artist_id", ""))
    keys = [(identity_key(track.artist, main_id), main_id)]
    for contributor in getattr(track, "contributors", []):
        if normalize_name(contributor) != normalize_name(track.artist):
            keys.append((identity_key(contributor), ""))
    return {scene for key, aid in keys if key
            for scene in _entry_scenes(key, aid, selection)}


def configured_scenes(selection: dict) -> dict[str, str]:
    if not selection.get("scene_playlists"):
        return {}
    blocked = blocked_scene_filenames(selection)
    scenes = {normalize_name(scene): scene
            for item in selection["artists"].values()
            if item.get("enabled") and item.get("deezer_id")
            for scene in _scene_names(item)}
    for scene in scenes.values():
        name = scene_filename(scene, selection)
        if name in blocked:
            owner = blocked[name]
            if normalize_name(owner) != normalize_name(scene):
                raise ArtistCatalogError("Scene filename is reserved by a disabled playlist.")
    filenames = [scene_filename(scene, selection) for scene in scenes.values()]
    if len(set(filenames)) != len(filenames):
        raise ArtistCatalogError("Scene names resolve to the same playlist filename.")
    if any(name.casefold() in {source.name.casefold() for source in config.CHARTS}
           for name in filenames):
        raise ArtistCatalogError("Scene filename is reserved by a configured chart playlist.")
    return {key: scene for key, scene in scenes.items()
            if scene_filename(scene, selection) not in blocked}


def scene_filename(scene: str, selection: dict | None = None) -> str:
    normalized = normalize_name(scene)
    ascii_name = unicodedata.normalize("NFKD", normalized).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-")[:48].rstrip("-") or "scene"
    return f"charts-{slug}.m3u"


def blocked_scene_filenames(selection: dict) -> dict[str, str]:
    result = {}
    aliases = selection.get("scene_files", {})
    for name, title in selection.get("disabled_scene_playlists", {}).items():
        scene = aliases.get(name, title.removeprefix("Mundart: ")
                            if name.startswith("mundart-szene-") else title)
        result[scene_filename(scene)] = scene
    for name, scene in selection.get("pending_scene_deletions", {}).items():
        result[name] = scene
        result[scene_filename(scene)] = scene
    return result


def manual_scene_downloads(selection: dict) -> dict[str, dict[str, int]]:
    """Return the opted-in scene limits for each eligible Deezer artist."""
    scenes = configured_scenes(selection)
    limits = {normalize_name(scene): limit
              for scene, limit in selection.get("scene_downloads", {}).items()}
    result: dict[str, dict[str, int]] = {}
    for item in selection["artists"].values():
        if not (item.get("manual") and item.get("enabled") and item.get("deezer_id")):
            continue
        for scene in _scene_names(item):
            key = normalize_name(scene)
            if key in scenes and key in limits:
                result.setdefault(item["deezer_id"], {})[key] = limits[key]
    return result


def manual_scene_feeds(selection: dict, existing_ids: set[str],
                       downloads: dict[str, dict[str, int]] | None = None) -> list:
    scenes = configured_scenes(selection)
    downloads = downloads if downloads is not None else manual_scene_downloads(selection)
    ids = {item["deezer_id"] for item in selection["artists"].values()
           if item.get("manual") and item.get("enabled") and item.get("deezer_id")
           and any(normalize_name(scene) in scenes for scene in _scene_names(item))}
    if len(ids) > MAX_MANUAL_ARTISTS:
        raise ArtistCatalogError("Too many manual artists in scene playlists.")
    return [config.Feed(kind="deezer", limit=max([50, *downloads.get(aid, {}).values()]),
                        enabled=True,
                        deezer_type="artist", source=aid)
            for aid in sorted((ids - existing_ids) | downloads.keys())]
