"""
Sidecar health-state for charts-energy structural-break detection (G1/G2).

A small JSON file CHARTS_DIR/.charts-energy-health.json keeps the detector
bookkeeping that must NOT pollute the dashboard-facing status JSON:

  - channels.<slug>  : per-channel producing counters (G1a). Keyed by CONFIG
                       slugs ({c.slug for c in config.CHANNELS}).
  - canary.dead_runs : consecutive runs in which NO active channel produced a
                       valid spin (G1b format/separator canary).
  - liveness.<slug>  : per-channel daily-probe counters (G2). Keyed by
                       discover.CONFIRMED_CHANNELS slugs. INDEPENDENT key
                       universe from channels.<slug> - the two are never
                       conflated.
  - liveness.alarm_active : gating flag for the G2 liveness alarm.

Written atomically (tmp + os.replace) exactly like state.py, loaded fail-open:
a missing/corrupt/unreadable file yields a fresh empty health map and is treated
as "nothing was alive/producing before", never a crash.

The pure helpers (produced_now, _evaluate_health) do NO I/O so they unit-test
without a network or filesystem.
"""

import json
import logging
import os
from datetime import datetime

from . import config

logger = logging.getLogger(__name__)

HEALTH_VERSION = 1


def _empty() -> dict:
    """A fresh, empty health map (used on first run / corrupt file)."""
    return {
        "version": HEALTH_VERSION,
        "channels": {},
        "liveness": {},
        "canary": {"dead_runs": 0},
    }


def _health_path() -> str:
    return os.path.join(config.CHARTS_DIR, config.ENERGY_HEALTH_FILE)


def load() -> dict:
    """
    Load the health file tolerantly. A missing/corrupt/unreadable file yields an
    empty health map (fresh start, never crash). Missing sub-keys are filled in
    so callers can assume the full shape.
    """
    try:
        with open(_health_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            data.setdefault("version", HEALTH_VERSION)
            if not isinstance(data.get("channels"), dict):
                data["channels"] = {}
            if not isinstance(data.get("liveness"), dict):
                data["liveness"] = {}
            canary = data.get("canary")
            if not isinstance(canary, dict):
                data["canary"] = {"dead_runs": 0}
            else:
                canary.setdefault("dead_runs", 0)
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return _empty()


def save(health: dict) -> None:
    """Write the health file atomically (tmp + os.replace), UTF-8."""
    path = _health_path()
    os.makedirs(config.CHARTS_DIR, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(health, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def produced_now(newest_first_seen: datetime | None,
                 last_run_end_prev: datetime) -> bool:
    """
    Did a channel produce a genuinely new spin since the previous run ended?

    newest_first_seen is the first_seen of the channel's newest accumulated
    track (None when the channel has no tracks at all). A channel produced this
    run iff that newest first_seen is at or after the previous run's end. Both
    values are aware datetimes compared directly (aware-vs-aware); callers pass
    datetime.min (UTC-aware) for an absent previous run end so the first run
    counts every channel as "produced".
    """
    if newest_first_seen is None:
        return False
    return newest_first_seen >= last_run_end_prev


def _evaluate_health(health: dict, per_channel_produced: dict[str, bool],
                     dead_threshold: int, canary_threshold: int) -> list[str]:
    """
    Update the G1 counters from this run's per-channel produced flags and return
    the list of verdict reason KINDS ("channels" and/or "canary").

    per_channel_produced maps each ACTIVE channel slug to its produced_now
    result. This helper mutates health["channels"][slug].{ever_produced,
    dead_runs} and health["canary"]["dead_runs"] in place, then reads the
    just-updated counters to decide which reason kinds crossed their threshold.

    Pure w.r.t. I/O (operates only on the passed dict): per-channel dead at
    dead_threshold runs; canary dead at canary_threshold runs, requiring at
    least one ever_produced channel so a fresh deploy never trips it.
    """
    channels = health.setdefault("channels", {})
    canary = health.setdefault("canary", {})
    canary.setdefault("dead_runs", 0)

    # (a) per-channel producing counters.
    for slug, produced in per_channel_produced.items():
        rec = channels.get(slug)
        if not isinstance(rec, dict):
            rec = {"ever_produced": False, "dead_runs": 0}
            channels[slug] = rec
        rec.setdefault("ever_produced", False)
        rec.setdefault("dead_runs", 0)
        if produced:
            rec["ever_produced"] = True
            rec["dead_runs"] = 0
        elif rec["ever_produced"]:
            rec["dead_runs"] += 1
        # A channel that has never produced is not "dead" - leave dead_runs 0.

    # (b) canary against the now-updated flags: >=1 produced this run resets it,
    # otherwise increment only once at least one channel has ever produced.
    produced_count = sum(1 for v in per_channel_produced.values() if v)
    any_ever = any(
        isinstance(r, dict) and r.get("ever_produced")
        for r in channels.values())
    if produced_count >= 1:
        canary["dead_runs"] = 0
    elif any_ever:
        canary["dead_runs"] += 1

    # (c) verdict.
    reasons: list[str] = []
    if canary["dead_runs"] >= canary_threshold:
        reasons.append("canary")
    dead_channels = [
        slug for slug, rec in channels.items()
        if isinstance(rec, dict) and rec.get("dead_runs", 0) >= dead_threshold]
    if dead_channels:
        reasons.append("channels")
    return reasons
