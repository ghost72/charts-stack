"""
Unit tests for the pure health helpers (app/health.py).

produced_now and _evaluate_health do no I/O, so they are tested directly without
a network or filesystem. Covers: first-run bootstrap (no increment), the
per-channel dead-at-48 crossing, the canary-at-4 crossing requiring >=1
ever_produced, a re-enabled channel starting clean, and the liveness dead_probes
/ alarm_active transitions (modelled the same way run_liveness updates them).
"""

from datetime import datetime, timezone

from app import health


def _aware(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


# --- produced_now -----------------------------------------------------------

def test_produced_now_none_is_false():
    """A channel with no tracks (newest None) never counts as produced."""
    assert health.produced_now(None, _aware(2026, 1, 1)) is False


def test_produced_now_newer_than_run_end_is_true():
    assert health.produced_now(_aware(2026, 1, 1, 12, 5),
                               _aware(2026, 1, 1, 12, 0)) is True


def test_produced_now_equal_is_true():
    ts = _aware(2026, 1, 1, 12, 0)
    assert health.produced_now(ts, ts) is True


def test_produced_now_older_is_false():
    assert health.produced_now(_aware(2026, 1, 1, 11, 55),
                               _aware(2026, 1, 1, 12, 0)) is False


def test_produced_now_first_run_bootstrap_marks_all():
    """datetime.min bootstrap -> any real first_seen counts as produced."""
    boot = datetime.min.replace(tzinfo=timezone.utc)
    assert health.produced_now(_aware(2020, 1, 1), boot) is True


# --- _evaluate_health: per-channel dead-across-N ----------------------------

def _run(h, produced, dead=48, canary=4):
    return health._evaluate_health(h, produced, dead, canary)


def test_first_run_produced_no_increment():
    """A first run where every channel produced increments no counter."""
    h = health._empty()
    reasons = _run(h, {"energypop": True, "energyhits": True})
    assert reasons == []
    assert h["channels"]["energypop"] == {"ever_produced": True, "dead_runs": 0}
    assert h["canary"]["dead_runs"] == 0


def test_never_produced_channel_is_not_dead():
    """A channel that has never produced stays dead_runs 0 (not alarmed)."""
    h = health._empty()
    for _ in range(100):
        _run(h, {"energypop": False})
    assert h["channels"]["energypop"]["ever_produced"] is False
    assert h["channels"]["energypop"]["dead_runs"] == 0


def test_per_channel_dead_crosses_at_48():
    h = health._empty()
    # One producing run so it is ever_produced.
    _run(h, {"energypop": True})
    reasons = []
    for i in range(1, 49):
        reasons = _run(h, {"energypop": False})
        if i < 48:
            assert "channels" not in reasons, f"fired early at run {i}"
    assert h["channels"]["energypop"]["dead_runs"] == 48
    assert "channels" in reasons


def test_dead_runs_reset_on_production():
    h = health._empty()
    _run(h, {"energypop": True})
    for _ in range(10):
        _run(h, {"energypop": False})
    assert h["channels"]["energypop"]["dead_runs"] == 10
    _run(h, {"energypop": True})
    assert h["channels"]["energypop"]["dead_runs"] == 0


# --- _evaluate_health: canary ----------------------------------------------

def test_canary_requires_ever_produced():
    """A fresh deploy (nothing ever produced) never trips the canary."""
    h = health._empty()
    reasons = []
    for _ in range(20):
        reasons = _run(h, {"energypop": False, "energyhits": False})
    assert "canary" not in reasons
    assert h["canary"]["dead_runs"] == 0


def test_canary_crosses_at_4():
    h = health._empty()
    # Establish ever_produced on one channel.
    _run(h, {"energypop": True, "energyhits": False})
    reasons = []
    for i in range(1, 5):
        reasons = _run(h, {"energypop": False, "energyhits": False})
        if i < 4:
            assert "canary" not in reasons, f"canary fired early at run {i}"
    assert h["canary"]["dead_runs"] == 4
    assert "canary" in reasons


def test_canary_resets_when_one_channel_produces():
    h = health._empty()
    _run(h, {"energypop": True})
    for _ in range(3):
        _run(h, {"energypop": False})
    assert h["canary"]["dead_runs"] == 3
    _run(h, {"energypop": True})
    assert h["canary"]["dead_runs"] == 0


def test_reenabled_channel_can_be_given_clean_counter():
    """Mirrors the main-loop reset: zeroing dead_runs for an absent channel.

    A channel that was dead for a while, then is disabled (absent from the
    produced map) and re-enabled, must not inherit a near-threshold counter.
    The main loop resets dead_runs for absent channels before evaluation; this
    models that a reset entry then starts clean.
    """
    h = health._empty()
    _run(h, {"energypop": True})
    for _ in range(40):
        _run(h, {"energypop": False})
    assert h["channels"]["energypop"]["dead_runs"] == 40
    # Simulate the main-loop reset for a now-absent (disabled) channel.
    h["channels"]["energypop"]["dead_runs"] = 0
    # Re-enabled: a single quiet run starts the count fresh at 1.
    _run(h, {"energypop": False})
    assert h["channels"]["energypop"]["dead_runs"] == 1


# --- liveness counter model (same shape run_liveness maintains) -------------

def _liveness_probe(liveness, slug, alive, threshold=3):
    """Replicate run_liveness's per-slug update + alarm_active recompute."""
    rec = liveness.get(slug)
    if not isinstance(rec, dict):
        rec = {"ever_alive": False, "dead_probes": 0}
        liveness[slug] = rec
    if alive:
        rec["ever_alive"] = True
        rec["dead_probes"] = 0
    elif rec["ever_alive"]:
        rec["dead_probes"] += 1
    dead = [s for s, r in liveness.items()
            if isinstance(r, dict) and r.get("dead_probes", 0) >= threshold]
    liveness["alarm_active"] = bool(dead)


def test_liveness_dead_probes_crosses_at_3_and_alarm_transitions():
    liveness: dict = {}
    _liveness_probe(liveness, "energypop", alive=True)
    assert liveness["alarm_active"] is False
    # Three dead probes -> alarm_active flips true.
    for i in range(1, 4):
        _liveness_probe(liveness, "energypop", alive=False)
        if i < 3:
            assert liveness["alarm_active"] is False
    assert liveness["energypop"]["dead_probes"] == 3
    assert liveness["alarm_active"] is True
    # Back alive -> recover (alarm_active false, counter reset).
    _liveness_probe(liveness, "energypop", alive=True)
    assert liveness["energypop"]["dead_probes"] == 0
    assert liveness["alarm_active"] is False


def test_liveness_never_alive_slug_not_alarmed():
    liveness: dict = {}
    for _ in range(10):
        _liveness_probe(liveness, "energyxmas", alive=False)
    assert liveness["energyxmas"]["ever_alive"] is False
    assert liveness["energyxmas"]["dead_probes"] == 0
    assert liveness["alarm_active"] is False
