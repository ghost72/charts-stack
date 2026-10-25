"""
DST scheduler tests for charts-hitparade.

Pins the persisted + seeded daily gate against the autumn fall-back double-fire,
the spring skipped-hour, and a restart inside the same day. Follows the
test_enabled_guard.py pattern: monkeypatch config/clock, tmp_path as STATUS_DIR,
assert the written status.json.

Clock handling (see 02-design.md "Testability"): main.datetime is replaced by a
Fake whose staticmethod now() pops the next scripted REAL datetime and raises
_StopLoop when exhausted. The loop's other per-iteration clock reads are
neutralised (_next_run_iso -> "" and status.set_next_run -> no-op) so exactly
one scripted value is consumed per poll, making the fire count deterministic.
All scripted dates fall on a real Sunday so the weekday gate does not trivially
block the run.
"""

import json
from datetime import date, datetime

import pytest

from app import config, main, status


# 2026-10-25 is a Sunday -> matches the default RUN_WEEKDAY (6).
_SUNDAY = date(2026, 10, 25)


class _StopLoop(Exception):
    """Raised by the fake clock when the scripted datetimes run out, so the
    scheduler's `while True` loop terminates inside the test."""


def _make_fake_datetime(scripted):
    """Build a drop-in for main.datetime whose now() pops the next scripted
    REAL datetime (so .replace()/.weekday()/comparisons keep working) and raises
    _StopLoop when exhausted."""
    it = iter(scripted)

    class _FakeDatetime:
        @staticmethod
        def now():
            try:
                return next(it)
            except StopIteration:
                raise _StopLoop

    return _FakeDatetime


def _read_status(status_dir):
    with open(f"{status_dir}/charts-hitparade.json", encoding="utf-8") as fh:
        return json.load(fh)


def _run_loop(monkeypatch, tmp_path, scripted, *, run_hour=20):
    """Drive main.main()'s scheduler loop over the scripted clock and return the
    number of _guarded_run() calls. RUN_ON_START is off for all scenarios."""
    calls = []
    monkeypatch.setattr(config, "RUN_ON_START", False)
    monkeypatch.setattr(config, "HITPARADE_ENABLED", True)
    monkeypatch.setattr(config, "RUN_WEEKDAY", 6)     # Sunday
    monkeypatch.setattr(config, "RUN_HOUR", run_hour)
    monkeypatch.setattr(config, "RUN_MINUTE", 0)
    # Only the top-of-loop `now = datetime.now()` should read the clock: stub the
    # extra per-iteration/startup reads so exactly one scripted value is consumed
    # per poll.
    monkeypatch.setattr(main, "datetime", _make_fake_datetime(scripted))
    monkeypatch.setattr(main, "_next_run_iso", lambda: "")
    monkeypatch.setattr(main.status, "set_next_run", lambda *_: None)
    monkeypatch.setattr(main, "_hitparade_resync_requested", lambda: False)
    monkeypatch.setattr(main, "_guarded_run", lambda: calls.append(True))
    monkeypatch.setattr(main.time, "sleep", lambda *_: None)
    monkeypatch.setattr(status, "STATUS_DIR", str(tmp_path))
    status.init("charts-hitparade")
    with pytest.raises(_StopLoop):
        main.main()
    return calls


# --- FR-4.1 Autumn: repeated local hour must NOT double-fire ---------------

def test_autumn_fall_back_fires_once(monkeypatch, tmp_path):
    """DST fall-back repeats 20:00 on the Sunday. The gate must fire exactly
    once (pre-fix this count was 2)."""
    d = lambda h, m: datetime(2026, 10, 25, h, m)
    scripted = [
        d(19, 59),         # before target -> no fire
        d(20, 0),          # 1st 20:00 -> fire
        d(20, 30),         # same day -> gated
        d(20, 0),          # 2nd 20:00 after fall-back, same date -> gated
        d(20, 30),         # gated
    ]
    assert _run_loop(monkeypatch, tmp_path, scripted) == [True]
    assert _read_status(tmp_path)["last_fired_local_date"] == _SUNDAY.isoformat()


# --- FR-4.2 Spring: skipped hour still fires once via >= -------------------

def test_spring_forward_fires_once(monkeypatch, tmp_path):
    """DST spring-forward skips 02:00. The >= fire condition still triggers once
    at 03:00 (3 > 2), and later same-day polls must not re-fire. Uses the same
    Sunday (weekday gate stays satisfied)."""
    d = lambda h, m: datetime(2026, 10, 25, h, m)
    scripted = [
        d(1, 59),          # before target -> no fire
        d(3, 0),           # target 02:00 skipped; 3 > 2 -> fire once
        d(3, 30),          # same day -> gated
        d(8, 0),           # same day -> gated
    ]
    assert _run_loop(monkeypatch, tmp_path, scripted, run_hour=2) == [True]


# --- FR-4.3 Restart inside the same day after a fire -----------------------

def test_restart_same_day_does_not_refire(monkeypatch, tmp_path):
    """A restart on a Sunday whose weekly run already fired (persisted date ==
    that Sunday) must NOT fire again; the persisted field stays unchanged."""
    monkeypatch.setattr(status, "STATUS_DIR", str(tmp_path))
    status.init("charts-hitparade")
    status.set_last_fired(_SUNDAY.isoformat())  # pre-write: already ran today

    d = lambda h, m: datetime(2026, 10, 25, h, m)
    scripted = [d(20, 30), d(21, 0)]  # past target, same Sunday
    assert _run_loop(monkeypatch, tmp_path, scripted) == []
    assert _read_status(tmp_path)["last_fired_local_date"] == _SUNDAY.isoformat()


# --- Finding-2 control: older persisted date -> the DATE gate (not the
#     weekday) is what blocks the restart re-fire ---------------------------

def test_restart_older_persisted_date_fires(monkeypatch, tmp_path):
    """Same Sunday, but the persisted date is the PREVIOUS Sunday. The weekly
    run must fire exactly once - proving it is the date gate, not the weekday,
    that blocks the re-fire in the restart test above."""
    monkeypatch.setattr(status, "STATUS_DIR", str(tmp_path))
    status.init("charts-hitparade")
    prev_sunday = date(2026, 10, 18)  # a week earlier, also a Sunday
    status.set_last_fired(prev_sunday.isoformat())

    d = lambda h, m: datetime(2026, 10, 25, h, m)
    scripted = [d(20, 30), d(21, 0)]  # past target, current Sunday
    assert _run_loop(monkeypatch, tmp_path, scripted) == [True]
    assert _read_status(tmp_path)["last_fired_local_date"] == _SUNDAY.isoformat()
