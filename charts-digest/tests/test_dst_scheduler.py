"""
DST scheduler tests for charts-digest.

Pins the short-poll + persisted-date gate against the autumn fall-back
double-fire (the original bug), the spring skipped-hour, and a restart inside
the same local day. Follows the test_enabled_guard.py pattern: monkeypatch
config/clock, tmp_path as STATUS_DIR, assert the written status.json.

The loop's clock is driven by a FakeClock patched onto main._now; it yields a
scripted list of naive datetimes and raises _StopLoop once exhausted so the
`while True` loop terminates deterministically. time.sleep is a no-op and
run_once is replaced by a counter so no I/O happens.
"""

import json
import sys
from datetime import datetime

import pytest

from app import config, main, status


class _StopLoop(Exception):
    """Raised by the fake clock when the scripted datetimes run out, so the
    scheduler's `while True` loop terminates inside the test."""


class FakeClock:
    """Pops the next scripted naive datetime on each .now() call; raises
    _StopLoop when the script is exhausted (loop termination)."""

    def __init__(self, scripted):
        self._it = iter(scripted)

    def now(self):
        try:
            return next(self._it)
        except StopIteration:
            raise _StopLoop


def _read_status(status_dir):
    with open(f"{status_dir}/charts-digest.json", encoding="utf-8") as fh:
        return json.load(fh)


def _run_loop(monkeypatch, tmp_path, scripted):
    """Drive main.main()'s scheduler loop over the scripted clock and return the
    number of run_once() calls. RUN_ON_START is off for all scenarios here."""
    calls = []
    # main() parses argv via argparse; strip pytest's own args so only the
    # scheduler path (no --now) is exercised.
    monkeypatch.setattr(sys, "argv", ["charts-digest"])
    monkeypatch.setattr(config, "RUN_ON_START", False)
    monkeypatch.setattr(main, "_now", FakeClock(scripted).now)
    monkeypatch.setattr(main, "run_once", lambda *a, **k: calls.append(True))
    monkeypatch.setattr(main.time, "sleep", lambda *_: None)
    monkeypatch.setattr(status, "STATUS_DIR", str(tmp_path))
    status.init("charts-digest")
    with pytest.raises(_StopLoop):
        main.main()
    return calls


# --- FR-4.1 Autumn: repeated local hour must NOT double-fire ---------------

def test_autumn_fall_back_fires_once(monkeypatch, tmp_path):
    """DST fall-back repeats 08:00. The gate must fire exactly once for the day
    (pre-fix this count was 2 - the original double-digest bug)."""
    monkeypatch.setattr(config, "DIGEST_HOUR", 8)
    monkeypatch.setattr(config, "DIGEST_MINUTE", 0)
    d = lambda h, m: datetime(2026, 10, 25, h, m)
    scripted = [
        d(7, 59),          # before target -> no fire
        d(8, 0),           # 1st 08:00 -> fire
        d(8, 30),          # same day -> gated
        d(8, 0),           # 2nd 08:00 after fall-back, same date -> gated
        d(8, 30),          # gated
        d(9, 0),           # gated
    ]
    assert _run_loop(monkeypatch, tmp_path, scripted) == [True]


# --- FR-4.2 Spring: skipped hour still fires once via >= -------------------

def test_spring_forward_fires_once(monkeypatch, tmp_path):
    """DST spring-forward skips 02:00. The >= fire condition still triggers once
    at 03:00 (3 > 2), and later same-day polls must not re-fire."""
    monkeypatch.setattr(config, "DIGEST_HOUR", 2)
    monkeypatch.setattr(config, "DIGEST_MINUTE", 0)
    d = lambda h, m: datetime(2026, 3, 29, h, m)
    scripted = [
        d(1, 59),          # before target -> no fire
        d(3, 0),           # target 02:00 skipped; 3 > 2 -> fire once
        d(3, 30),          # same day -> gated
        d(8, 0),           # same day -> gated
    ]
    assert _run_loop(monkeypatch, tmp_path, scripted) == [True]


# --- FR-4.3 Restart inside the same day after a fire -----------------------

def test_restart_same_day_does_not_refire(monkeypatch, tmp_path):
    """A restart on a day whose digest already fired (persisted date == today)
    must NOT fire again; the persisted gate field stays unchanged."""
    monkeypatch.setattr(config, "DIGEST_HOUR", 8)
    monkeypatch.setattr(config, "DIGEST_MINUTE", 0)
    monkeypatch.setattr(status, "STATUS_DIR", str(tmp_path))
    status.init("charts-digest")
    today = "2026-10-25"
    status.set_last_fired(today)  # pre-write the gate as if today already fired

    d = lambda h, m: datetime(2026, 10, 25, h, m)
    scripted = [d(8, 30), d(9, 0)]  # both past target, same day
    assert _run_loop(monkeypatch, tmp_path, scripted) == []
    assert _read_status(tmp_path)["last_fired_local_date"] == today
