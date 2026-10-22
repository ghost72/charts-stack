"""
Per-channel accumulate + dedupe state for charts-energy.

A single JSON file CHARTS_DIR/.charts-energy-state.json holds the observed
spins per channel. Each poll adds newly-seen spins (deduped by spin_key);
re-reading the same current song only bumps its last_seen. A bounded retention
policy (age + per-channel cap) keeps the buffer a top-up window, not an archive.

Canonical internal timestamps (hard requirement): first_seen / last_seen are
produced with datetime.now(timezone.utc).isoformat(timespec="seconds") (UTC,
"+00:00") and are ALWAYS compared/sorted by parsing with
datetime.fromisoformat, never as raw strings. The Energy-supplied started_at is
stored VERBATIM (it is only a dedupe component of spin_key, never sorted).
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone

from . import config
from .icy import IcyTrack, spin_key

logger = logging.getLogger(__name__)

STATE_VERSION = 1

# How many extra recent keys to keep in seen_keys beyond the surviving tracks,
# so a just-expired song is not immediately re-added within the same block.
_SEEN_KEYS_TAIL = 50


def _now_iso() -> str:
    """Canonical charts-energy timestamp: UTC, second precision, '+00:00'."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse(ts: str) -> datetime | None:
    """Parse a canonical timestamp into an aware datetime, or None."""
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    # Defensive: treat a naive value as UTC so comparisons stay aware-vs-aware.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _state_path() -> str:
    return os.path.join(config.CHARTS_DIR, config.STATE_FILE)


def load() -> dict:
    """
    Load the state file tolerantly. A missing/corrupt/unreadable file yields an
    empty state (fresh start, never crash).
    """
    try:
        with open(_state_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("channels"), dict):
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {"version": STATE_VERSION, "channels": {}}


def save(state: dict) -> None:
    """Write the state file atomically (tmp + os.replace), UTF-8."""
    path = _state_path()
    os.makedirs(config.CHARTS_DIR, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _channel(state: dict, slug: str) -> dict:
    """Return the per-channel record, creating it if absent."""
    channels = state.setdefault("channels", {})
    rec = channels.get(slug)
    if not isinstance(rec, dict):
        rec = {"seen_keys": [], "tracks": []}
        channels[slug] = rec
    rec.setdefault("seen_keys", [])
    rec.setdefault("tracks", [])
    return rec


def record_spin(state: dict, slug: str, track: IcyTrack) -> bool:
    """
    Accumulate one observed spin into the channel record.

    If the spin_key was already seen, bump the existing track's last_seen and
    drop it (no duplicate entry). Otherwise append a new track.
    Returns True when a NEW track was added, False on a dedupe bump.
    """
    rec = _channel(state, slug)
    key = spin_key(track)
    now = _now_iso()

    if key in rec["seen_keys"]:
        for existing in rec["tracks"]:
            if existing.get("spin_key") == key:
                existing["last_seen"] = now
                break
        return False

    rec["seen_keys"].append(key)
    rec["tracks"].append({
        "spin_key": key,
        "artist": track.artist,
        "title": track.title,
        "started_at": track.started_at,   # VERBATIM (dedupe component only)
        "pl_id": track.pl_id,
        "first_seen": now,
        "last_seen": now,
    })
    return True


def trim_channel(rec: dict) -> None:
    """
    Apply the retention policy to one channel record in place:
      - drop tracks whose last_seen is older than RETENTION_DAYS,
      - if still over MAX_TRACKS_PER_CHANNEL, keep the most recent MAX by
        last_seen,
      - trim seen_keys to the survivors plus a small recent tail.
    Age checks / "most recent" sorts parse last_seen into aware datetimes.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(
        days=config.ENERGY_TRACK_RETENTION_DAYS)

    def _last_seen(entry: dict) -> datetime:
        return _parse(entry.get("last_seen", "")) or datetime.min.replace(
            tzinfo=timezone.utc)

    survivors = [e for e in rec.get("tracks", []) if _last_seen(e) >= cutoff]

    if len(survivors) > config.ENERGY_MAX_TRACKS_PER_CHANNEL:
        survivors.sort(key=_last_seen, reverse=True)
        survivors = survivors[:config.ENERGY_MAX_TRACKS_PER_CHANNEL]

    survivor_keys = {e.get("spin_key") for e in survivors}
    # Keep the surviving keys plus a recent tail (preserve seen_keys order).
    tail = [k for k in rec.get("seen_keys", []) if k not in survivor_keys]
    tail = tail[-_SEEN_KEYS_TAIL:]
    rec["tracks"] = survivors
    rec["seen_keys"] = [k for k in rec.get("seen_keys", [])
                        if k in survivor_keys] + tail


def trim(state: dict) -> None:
    """Apply trim_channel to every channel in the state."""
    for rec in state.get("channels", {}).values():
        if isinstance(rec, dict):
            trim_channel(rec)


def tracks_for(state: dict, slug: str) -> list[dict]:
    """
    Return a channel's accumulated tracks ordered first_seen DESCENDING
    (newest spin first). The sort parses first_seen into aware datetimes.
    """
    rec = state.get("channels", {}).get(slug)
    if not isinstance(rec, dict):
        return []

    def _first_seen(entry: dict) -> datetime:
        return _parse(entry.get("first_seen", "")) or datetime.min.replace(
            tzinfo=timezone.utc)

    return sorted(list(rec.get("tracks", [])), key=_first_seen, reverse=True)
