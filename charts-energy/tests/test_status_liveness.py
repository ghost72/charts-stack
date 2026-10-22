"""
Unit tests for status.set_liveness (the additive 'energy_liveness' publisher).

Mirrors the tmp-STATUS_DIR read helper pattern in test_status_channels.py.

Covered:
  - set_liveness writes the current live/dead map under 'energy_liveness' with a
    'checked_at' timestamp and a 'channels' sub-map, and touches NOTHING else
    (a pre-existing status=running and the run counters survive).
  - a second set_liveness overwrites only energy_liveness.
"""

import json
import os

from app import status


def _read_status(status_dir: str) -> dict:
    with open(os.path.join(status_dir, "charts-energy.json"), encoding="utf-8") as fh:
        return json.load(fh)


def test_set_liveness_writes_only_its_key(monkeypatch, tmp_path):
    monkeypatch.setattr(status, "STATUS_DIR", str(tmp_path))
    status.init("charts-energy")

    status.start()
    status.finish("ok", "all good", counters={"tracks": 5},
                  next_run="2026-10-20T01:00:00+00:00")
    status.set_liveness({"energypop": "live", "energyxmas": "dead"})

    data = _read_status(str(tmp_path))
    # The run fields are untouched.
    assert data["status"] == "ok"
    assert data["message"] == "all good"
    assert data["counters"] == {"tracks": 5}
    assert data["next_run"] == "2026-10-20T01:00:00+00:00"
    # Only energy_liveness was added.
    live = data["energy_liveness"]
    assert live["channels"] == {"energypop": "live", "energyxmas": "dead"}
    assert isinstance(live.get("checked_at"), str) and live["checked_at"]


def test_set_liveness_preserves_running(monkeypatch, tmp_path):
    monkeypatch.setattr(status, "STATUS_DIR", str(tmp_path))
    status.init("charts-energy")

    status.start()
    status.set_liveness({"energypop": "live"})

    data = _read_status(str(tmp_path))
    assert data["status"] == "running"
    assert data["energy_liveness"]["channels"] == {"energypop": "live"}


def test_set_liveness_overwrites_previous_map(monkeypatch, tmp_path):
    monkeypatch.setattr(status, "STATUS_DIR", str(tmp_path))
    status.init("charts-energy")

    status.start()
    status.set_liveness({"energypop": "live", "energyxmas": "dead"})
    status.set_liveness({"energypop": "dead"})

    data = _read_status(str(tmp_path))
    assert data["energy_liveness"]["channels"] == {"energypop": "dead"}
