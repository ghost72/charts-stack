"""
Persistent state: which songs have already been requested/loaded.

Prevents the same song from being searched and loaded again on every run.
Stored as JSON, the key is "normalized_artist|normalized_title".
"""

import json
import logging
import os
import time

from . import config
from .i18n import t
from .matching import normalize

logger = logging.getLogger(__name__)


def _key(artist: str, title: str) -> str:
    return f"{normalize(artist)}|{normalize(title)}"


def load() -> dict:
    """Loads the state. Empty dict if the file is missing/invalid."""
    try:
        with open(config.STATE_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and "requested" in data:
            data.setdefault("failed", {})
            data.setdefault("blocked_providers", {})
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {"requested": {}, "failed": {}, "blocked_providers": {}}


def save(state: dict) -> None:
    """Writes the state atomically."""
    try:
        os.makedirs(os.path.dirname(config.STATE_FILE), exist_ok=True)
        tmp = config.STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, config.STATE_FILE)
    except OSError as e:
        logger.error(t("download.state.save_failed", error=e))


def already_requested(state: dict, artist: str, title: str) -> bool:
    return _key(artist, title) in state.get("requested", {})


def mark_requested(state: dict, artist: str, title: str,
                   username: str, filename: str) -> None:
    """
    Records a requested download incl. slskd reference (username + filename),
    so a later run can track the download status and release failures.
    """
    state.setdefault("requested", {})[_key(artist, title)] = {
        "artist": artist,
        "title": title,
        "username": username,
        "filename": filename,
    }


def clear_requested(state: dict, artist: str, title: str) -> None:
    """Removes an entry so the song is searched/loaded again."""
    state.get("requested", {}).pop(_key(artist, title), None)


# --- Failed searches (cooldown) ---
# A song that was searched for but not found in sufficient quality is recorded
# with a timestamp and skipped for SEARCH_COOLDOWN_HOURS. This keeps
# permanently unfindable songs (e.g. many Mundart titles) from blocking the
# MAX_PER_RUN quota on EVERY run so the findable songs get through. After the
# cooldown expires the search is retried (in case someone now shares a good
# version).

def mark_failed(state: dict, artist: str, title: str) -> None:
    """Records an unsuccessful search with the current timestamp."""
    state.setdefault("failed", {})[_key(artist, title)] = {
        "artist": artist,
        "title": title,
        "ts": int(time.time()),
    }


def in_cooldown(state: dict, artist: str, title: str, cooldown_hours: int) -> bool:
    """
    True if the song was recently (within cooldown_hours) searched without
    success and should therefore be skipped. cooldown_hours <= 0 = no cooldown
    (always search again).
    """
    if cooldown_hours <= 0:
        return False
    entry = state.get("failed", {}).get(_key(artist, title))
    if not entry:
        return False
    age = time.time() - entry.get("ts", 0)
    return age < cooldown_hours * 3600


def clear_failed(state: dict, artist: str, title: str) -> None:
    """Removes a failed-attempt entry (e.g. after a successful download)."""
    state.get("failed", {}).pop(_key(artist, title), None)


# --- Per-song provider block list ---
# charts-import remembers (via blocked_providers in the state) which Soulseek
# provider delivered a bad/upsampled file for a given song. This combination
# (song, username) is skipped on the next search - so the same junk is not
# loaded endlessly from the same provider. Deliberately PER SONG (not global):
# the same provider may have a good file for other songs. The block expires
# after BLOCK_TTL_DAYS so a provider that has since replaced its file gets
# another chance.

def blocked_providers_for(state: dict, artist: str, title: str,
                          ttl_days: int) -> set[str]:
    """
    Returns the currently (not yet expired) blocked provider names for this
    song. Expired entries (older than ttl_days) are removed from the state at
    the same time (lazy cleanup). ttl_days <= 0 -> never expire.
    """
    key = _key(artist, title)
    song_block = state.get("blocked_providers", {}).get(key)
    if not isinstance(song_block, dict) or not song_block:
        return set()

    if ttl_days and ttl_days > 0:
        cutoff = time.time() - ttl_days * 86400
        expired = [user for user, ts in song_block.items()
                   if not isinstance(ts, (int, float)) or ts < cutoff]
        for user in expired:
            song_block.pop(user, None)
        if not song_block:
            # Remove the whole song entry if no block is active anymore.
            state.get("blocked_providers", {}).pop(key, None)

    return set(song_block.keys())
