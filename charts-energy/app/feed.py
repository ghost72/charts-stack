"""
Handoff-file writer for FEED-mode channels.

A FEED-mode channel does NOT get its own playlist. Instead charts-energy writes
one handoff file per theme, CHARTS_DIR/.charts-energy-feed-<theme>.json, that
charts-sync reads as an additional feed of an existing theme (e.g. mundart). So
there stays exactly ONE mundart.m3u, written by charts-sync alone.

When several FEED channels target the same theme (e.g. energy80s + energy90s
both -> "achtziger-neunziger"), their accumulated tracks are merged and deduped
by normalized artist|title into the single theme file, ordered first_seen
descending (the sort parses first_seen into aware datetimes - never raw
strings).
"""

import json
import logging
import os
from datetime import datetime, timezone

from . import config
from .i18n import t
from .library import normalize

logger = logging.getLogger(__name__)

FEED_VERSION = 1


def _now_iso() -> str:
    """Canonical charts-energy timestamp: UTC, second precision, '+00:00'."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse(ts: str) -> datetime:
    """Parse a canonical first_seen into an aware datetime (min on failure)."""
    try:
        parsed = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _feed_path(theme: str) -> str:
    return os.path.join(config.CHARTS_DIR, f".charts-energy-feed-{theme}.json")


def _merge_tracks(track_lists: list[list[dict]]) -> list[dict]:
    """
    Merge several channels' track lists for one theme: dedupe by normalized
    artist|title (first occurrence wins), ordered first_seen descending.
    Each input track is a state dict (artist, title, first_seen).
    """
    merged: list[dict] = []
    seen: set[str] = set()
    for tracks in track_lists:
        for entry in tracks:
            artist = entry.get("artist", "")
            title = entry.get("title", "")
            key = f"{normalize(artist)}|{normalize(title)}"
            if key in seen:
                continue
            seen.add(key)
            merged.append({
                "artist": artist,
                "title": title,
                "first_seen": entry.get("first_seen", ""),
            })
    merged.sort(key=lambda e: _parse(e.get("first_seen", "")), reverse=True)
    return merged


def write(theme: str, track_lists: list[list[dict]]) -> str:
    """
    Write the per-theme handoff file atomically.

    theme:       the theme key (e.g. "mundart", "achtziger-neunziger").
    track_lists: one list of state-track dicts per FEED channel on this theme.
    Returns: the path of the written handoff file.
    """
    tracks = _merge_tracks(track_lists)
    path = _feed_path(theme)
    os.makedirs(config.CHARTS_DIR, exist_ok=True)

    if len(track_lists) > 1:
        # Several FEED channels share this theme -> merged into one file.
        logger.info(t("energy.feed.merged", theme=theme, channels=len(track_lists)))

    payload = {
        "version": FEED_VERSION,
        "theme": theme,
        "generated": _now_iso(),
        "tracks": tracks,
    }

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

    logger.info(t("energy.feed.written", theme=theme, count=len(tracks)))
    return path
