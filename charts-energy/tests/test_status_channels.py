"""
Unit tests for status.set_channels (the additive 'channels' publisher).

Mirrors the tmp-STATUS_DIR read helper pattern in test_enabled_guard.py.

Covered:
  - set_channels writes the passed list verbatim under the 'channels' key and
    preserves a pre-existing status=running (the read-modify-write merge did
    not clobber it).
  - after a finished run, a subsequent set_channels keeps both the run's
    'counters'/'status' and the newly written 'channels'.
"""

import json
import os

from app import status


def _read_status(status_dir: str) -> dict:
    with open(os.path.join(status_dir, "charts-energy.json"), encoding="utf-8") as fh:
        return json.load(fh)


def test_set_channels_preserves_running(monkeypatch, tmp_path):
    """set_channels writes 'channels' and keeps a running status intact."""
    monkeypatch.setattr(status, "STATUS_DIR", str(tmp_path))
    status.init("charts-energy")

    status.start()
    channels = [
        {"slug": "energy80s", "mode": "library", "enabled": True},
        {"slug": "energymadeinswitzerland", "mode": "feed", "enabled": True},
    ]
    status.set_channels(channels)

    data = _read_status(str(tmp_path))
    assert data["channels"] == channels
    assert data["status"] == "running"


def test_set_channels_coexists_with_finished_run(monkeypatch, tmp_path):
    """After finish(), set_channels keeps both counters/status and channels."""
    monkeypatch.setattr(status, "STATUS_DIR", str(tmp_path))
    status.init("charts-energy")

    status.start()
    status.finish("ok", counters={"tracks": 3})
    channels = [
        {"slug": "energy80s", "mode": "library", "enabled": True},
        {"slug": "energymadeinswitzerland", "mode": "feed", "enabled": True},
    ]
    status.set_channels(channels)

    data = _read_status(str(tmp_path))
    assert data["channels"] == channels
    assert data["status"] == "ok"
    assert data["counters"] == {"tracks": 3}
