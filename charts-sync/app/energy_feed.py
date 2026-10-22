"""
Reader for the charts-energy handoff files (FEED-INTO-EXISTING-THEME mode).

charts-energy (a separate collector service) writes one handoff JSON per theme
to CHARTS_DIR/.charts-energy-feed-<theme>.json. charts-sync consumes it as an
additional feed of an existing theme (e.g. the Mundart playlist) via the
"energy" feed kind. This module only ever reads a local JSON file; it opens NO
socket (the Icecast read lives entirely in charts-energy).

Tolerance (mirrors _load_state / _load_charts_data discipline):
  - missing file          -> empty list (DEBUG only; a dead collector is benign)
  - corrupt JSON          -> empty list + one WARN (sync.config.energy_feed_bad_json)
  - stale ('generated' older than ENERGY_FEED_MAX_AGE_HOURS) -> empty + WARN
                             (sync.config.energy_feed_stale)
  - bad shape             -> empty list + the same bad-json WARN

The theme's playlist is still written from the remaining Deezer/Last.fm feeds,
so a dead or stale energy feed never breaks mundart.m3u.

Handoff schema (pinned by charts-energy, design §5.2):
  {
    "version": 1,
    "theme": "<theme>",
    "generated": "<canonical UTC isoformat, timespec=seconds, +00:00>",
    "tracks": [ {"artist": str, "title": str, "first_seen": "<canonical UTC>"} ]
  }
tracks are already merged + deduped and ordered first_seen DESCENDING.
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone

from . import config
from .deezer import ChartTrack
from .i18n import t

logger = logging.getLogger(__name__)


def _feed_path(theme: str) -> str:
    """Absolute path of the handoff file for a theme."""
    return os.path.join(config.CHARTS_DIR, f".charts-energy-feed-{theme}.json")


def _is_stale(generated: str) -> bool:
    """
    True when the handoff is older than config.ENERGY_FEED_MAX_AGE_HOURS.

    'generated' is parsed with datetime.fromisoformat and compared as a
    timezone-aware datetime (never a raw-string compare). A missing/unparseable
    'generated' is treated as NOT stale (the schema/empty-check elsewhere
    decides); we only reject a timestamp we can actually read and that is old.
    """
    if not generated:
        return False
    try:
        gen = datetime.fromisoformat(generated)
    except (TypeError, ValueError):
        return False
    if gen.tzinfo is None:
        # Defensive: a naive timestamp is interpreted as UTC.
        gen = gen.replace(tzinfo=timezone.utc)
    cutoff = datetime.now(timezone.utc) - timedelta(
        hours=max(0, config.ENERGY_FEED_MAX_AGE_HOURS))
    return gen < cutoff


def load(theme: str) -> list[ChartTrack]:
    """
    Read the handoff file for a theme and return its tracks as ChartTrack
    objects in file order (position = 1-based enumerate index).

    The position is synthetic: the handoff JSON has no position field and
    position is meaningless for an energy feed. It is assigned only to hand
    fetch_chart a well-ordered pre-merge list that preserves the file order
    charts-energy wrote (first_seen descending). Final placement in the merged
    playlist is decided by fetch_chart's renumber and the active_feeds
    energy-last partition, not by this index.
    """
    path = _feed_path(theme)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        # Missing handoff is benign (collector disabled / not yet run). No i18n
        # key: keep the sync.* energy key set closed at the four group-3 keys.
        logger.debug("energy feed file not found for theme '%s' (%s)", theme, path)
        return []
    except (json.JSONDecodeError, OSError):
        logger.warning(t("sync.config.energy_feed_bad_json", theme=theme, path=path))
        return []

    # Schema check: a dict with a tracks list.
    if not isinstance(data, dict) or not isinstance(data.get("tracks"), list):
        logger.warning(t("sync.config.energy_feed_bad_json", theme=theme, path=path))
        return []

    if _is_stale(str(data.get("generated", ""))):
        logger.warning(t("sync.config.energy_feed_stale", theme=theme, path=path))
        return []

    tracks: list[ChartTrack] = []
    for item in data["tracks"]:
        if not isinstance(item, dict):
            continue
        artist = str(item.get("artist", "")).strip()
        title = str(item.get("title", "")).strip()
        if not artist or not title:
            continue
        tracks.append(ChartTrack(position=len(tracks) + 1, artist=artist,
                                 title=title))

    logger.info(t("sync.energy.loaded", theme=theme, count=len(tracks)))
    return tracks
