"""Shared playlist-state validation and cross-container write locking."""

import fcntl
import os
import unicodedata
from contextlib import contextmanager


def name_key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def valid_name(value: object, limit: int = 80) -> bool:
    return (isinstance(value, str) and 0 < len(value) <= limit
            and value == unicodedata.normalize("NFC", value).strip()
            and not any(ord(char) < 32 for char in value))


def valid_genre_rules(rules: object) -> bool:
    if not isinstance(rules, dict):
        return False
    for name, rule in rules.items():
        if not valid_name(name) or not isinstance(rule, dict):
            return False
        if "genre_id" in rule:
            genre_id = rule["genre_id"]
            if (not isinstance(genre_id, str) or not genre_id.isascii()
                    or not genre_id.isdigit() or not 1 <= len(genre_id) <= 10
                    or genre_id.startswith("0")
                    or not valid_name(rule.get("genre_name"), 160)
                    or type(rule.get("limit")) is not int
                    or not 1 <= rule["limit"] <= 100 or "genres" in rule):
                return False
            continue
        genres = rule.get("genres")
        if (not isinstance(genres, list) or not genres
                or any(not valid_name(genre, 160) for genre in genres)
                or len({name_key(genre) for genre in genres}) != len(genres)):
            return False
    return len({name_key(name) for name in rules}) == len(rules)


def valid_genre_catalog(catalog: object) -> bool:
    if not isinstance(catalog, dict) or not valid_name(catalog.get("updated_at"), 160):
        return False
    counts = [catalog.get(key) for key in ("total", "untagged", "unreadable")]
    if any(type(value) is not int or value < 0 for value in counts):
        return False
    if counts[1] > counts[0] or counts[2] > counts[0]:
        return False
    genres = catalog.get("genres")
    reserved = catalog.get("reserved_playlists", [])
    if not isinstance(reserved, list) or any(not valid_name(name, 255) for name in reserved):
        return False
    return (isinstance(genres, list) and all(
        isinstance(entry, dict) and valid_name(entry.get("name"), 160)
        and type(entry.get("count")) is int and 0 < entry["count"] <= counts[0]
        for entry in genres)
        and len({name_key(entry["name"]) for entry in genres}) == len(genres))


@contextmanager
def selection_lock(directory: str):
    os.makedirs(directory, exist_ok=True)
    fd = os.open(os.path.join(directory, ".charts-artist-selection.lock"),
                 os.O_CREAT | os.O_RDWR, 0o664)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)
