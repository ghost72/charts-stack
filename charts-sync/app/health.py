"""
Sidecar health-state for charts-sync structural-break detection (G3).

A small JSON file CHARTS_DIR/.charts-sync-health.json keeps the detector
bookkeeping that must NOT pollute the dashboard-facing status JSON:

  - sources.<name> : per-source consecutive-dead counters. Keyed by
                     chart_source.name (the stable playlist key already used in
                     sync's state). Each entry is
                     {"ever_ok": bool, "dead_runs": int}.

Written atomically (tmp + os.replace) exactly like state in main.py, loaded
fail-open: a missing/corrupt/unreadable file yields a fresh empty health map and
is treated as "nothing was ever ok before", never a crash.

The pure helper (_record_source_outcome) does NO I/O so it unit-tests without a
filesystem. Mirrors the shape of charts-energy/app/health.py.
"""

import json
import logging
import os

from . import config

logger = logging.getLogger(__name__)

HEALTH_VERSION = 1


def _empty() -> dict:
    """A fresh, empty health map (used on first run / corrupt file)."""
    return {"version": HEALTH_VERSION, "sources": {}}


def _health_path() -> str:
    return os.path.join(config.CHARTS_DIR, config.SYNC_HEALTH_FILE)


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
            if not isinstance(data.get("sources"), dict):
                data["sources"] = {}
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


def _record_source_outcome(health: dict, name: str, outcome: str) -> None:
    """
    Update the per-source dead-run counter from a single run's outcome.

    outcome is "ok", "empty" or "error". A source that was ok resets its counter
    (ever_ok=True, dead_runs=0); an "empty"/"error" source increments dead_runs
    only if it was EVER ok before - a source that never worked is a config/typo
    issue, not a structural break of a working source, and must stay quiet (same
    ever_ok guard as energy's ever_produced). Mutates health in place.
    """
    sources = health.setdefault("sources", {})
    rec = sources.get(name)
    if not isinstance(rec, dict):
        rec = {"ever_ok": False, "dead_runs": 0}
        sources[name] = rec
    rec.setdefault("ever_ok", False)
    rec.setdefault("dead_runs", 0)
    if outcome == "ok":
        rec["ever_ok"] = True
        rec["dead_runs"] = 0
    elif rec["ever_ok"]:
        rec["dead_runs"] += 1
    # A source that has never been ok is not "dead" - leave dead_runs at 0.
