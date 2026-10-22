"""
Unit tests for the pure charts-sync health helpers (app/health.py) and the G3
aggregate verdict.

_record_source_outcome does no I/O, so it is tested directly without a
filesystem. Covers: the per-source dead-at-6 crossing (ever_ok guard), a
never-ok source staying quiet, the ok-reset, the whole_run_empty verdict
(including the all-crash case), the non-alarming no-sources return, and the
"sources" counter still meaning "did-not-raise" (an empty/caught-exception
source does not touch it).
"""

from app import config, health


# --- _record_source_outcome ------------------------------------------------

def test_ok_sets_ever_ok_and_resets_dead_runs():
    h = health._empty()
    health._record_source_outcome(h, "charts.m3u", "ok")
    assert h["sources"]["charts.m3u"] == {"ever_ok": True, "dead_runs": 0}


def test_never_ok_source_stays_quiet():
    """A source that never worked never increments dead_runs (config/typo)."""
    h = health._empty()
    for _ in range(100):
        health._record_source_outcome(h, "mundart.m3u", "empty")
    assert h["sources"]["mundart.m3u"]["ever_ok"] is False
    assert h["sources"]["mundart.m3u"]["dead_runs"] == 0


def test_dead_runs_crosses_at_6():
    h = health._empty()
    # One ok run so it is ever_ok.
    health._record_source_outcome(h, "charts.m3u", "ok")
    for i in range(1, 7):
        outcome = "empty" if i % 2 else "error"
        health._record_source_outcome(h, "charts.m3u", outcome)
        if i < 6:
            assert h["sources"]["charts.m3u"]["dead_runs"] == i
    assert h["sources"]["charts.m3u"]["dead_runs"] == 6


def test_dead_runs_reset_on_ok():
    h = health._empty()
    health._record_source_outcome(h, "charts.m3u", "ok")
    for _ in range(4):
        health._record_source_outcome(h, "charts.m3u", "error")
    assert h["sources"]["charts.m3u"]["dead_runs"] == 4
    health._record_source_outcome(h, "charts.m3u", "ok")
    assert h["sources"]["charts.m3u"]["dead_runs"] == 0


def test_error_counts_like_empty_once_ever_ok():
    h = health._empty()
    health._record_source_outcome(h, "charts.m3u", "ok")
    health._record_source_outcome(h, "charts.m3u", "error")
    health._record_source_outcome(h, "charts.m3u", "empty")
    assert h["sources"]["charts.m3u"]["dead_runs"] == 2


# --- the aggregate verdict (as _guarded_sync computes it) -------------------

def _whole_run_empty(produced: int, attempted: int) -> bool:
    return attempted > 0 and produced == 0


def _dead_sources(h: dict, threshold: int) -> list[str]:
    return [name for name, rec in h.get("sources", {}).items()
            if isinstance(rec, dict)
            and rec.get("dead_runs", 0) >= threshold]


def test_whole_run_empty_only_when_attempted_and_zero_produced():
    assert _whole_run_empty(0, 2) is True
    assert _whole_run_empty(1, 2) is False
    # No sources: attempted == 0 keeps it quiet (non-alarming).
    assert _whole_run_empty(0, 0) is False


def test_all_crash_is_whole_run_empty():
    """Every source raising past the fetch guard -> outcome 'error' for all,
    produced stays 0, so whole_run_empty fires."""
    h = health._empty()
    outcomes = ["error", "error", "error"]
    produced = sum(1 for o in outcomes if o == "ok")
    attempted = len(outcomes)
    for name, o in zip(("a.m3u", "b.m3u", "c.m3u"), outcomes):
        health._record_source_outcome(h, name, o)
    assert produced == 0
    assert _whole_run_empty(produced, attempted) is True


def test_dead_sources_crosses_at_threshold_only_for_ever_ok():
    h = health._empty()
    # charts.m3u becomes dead across the threshold after being ok once.
    health._record_source_outcome(h, "charts.m3u", "ok")
    for _ in range(config.SYNC_DEAD_RUNS_THRESHOLD):
        health._record_source_outcome(h, "charts.m3u", "empty")
    # never-ok source: always empty, must never be dead.
    for _ in range(config.SYNC_DEAD_RUNS_THRESHOLD + 5):
        health._record_source_outcome(h, "typo.m3u", "empty")
    dead = _dead_sources(h, config.SYNC_DEAD_RUNS_THRESHOLD)
    assert dead == ["charts.m3u"]


def test_single_transient_source_among_many_stays_quiet():
    """A single flaky source resets long before 6; the run is not whole-empty."""
    h = health._empty()
    for name in ("a.m3u", "b.m3u", "c.m3u"):
        health._record_source_outcome(h, name, "ok")
    # One run: b is empty, the others ok. produced=2, attempted=3.
    outcomes = {"a.m3u": "ok", "b.m3u": "empty", "c.m3u": "ok"}
    for name, o in outcomes.items():
        health._record_source_outcome(h, name, o)
    produced = sum(1 for o in outcomes.values() if o == "ok")
    assert _whole_run_empty(produced, len(outcomes)) is False
    assert _dead_sources(h, config.SYNC_DEAD_RUNS_THRESHOLD) == []
    # b recovers next run -> dead_runs back to 0.
    health._record_source_outcome(h, "b.m3u", "ok")
    assert h["sources"]["b.m3u"]["dead_runs"] == 0


def test_run_sync_sources_counter_is_did_not_raise_count(tmp_path, monkeypatch):
    """The 'sources' counter stays ok_sources: incremented ONLY for an 'ok'
    _sync_source that does not raise. An 'empty', an 'error' return, and a
    caught run-loop exception all leave it unchanged (unchanged dashboard
    meaning)."""
    from app import library, main

    monkeypatch.setattr(config, "CHARTS_DIR", str(tmp_path))

    class _Src:
        def __init__(self, name, title):
            self.name = name
            self.title = title

    sources = [_Src("ok.m3u", "OK"), _Src("empty.m3u", "Empty"),
               _Src("err.m3u", "Err"), _Src("crash.m3u", "Crash")]
    monkeypatch.setattr(config, "CHARTS", sources)

    class _Lib:
        tracks = [1, 2, 3]
    monkeypatch.setattr(library, "build_index", lambda _d: _Lib())
    monkeypatch.setattr(main, "_load_state", lambda: {})
    monkeypatch.setattr(main, "_save_state", lambda _s: None)

    def _fake_sync(src, lib, state):
        if src.name == "ok.m3u":
            return "ok"
        if src.name == "empty.m3u":
            return "empty"
        if src.name == "err.m3u":
            return "error"
        raise RuntimeError("boom")  # caught by run_sync's per-source except

    monkeypatch.setattr(main, "_sync_source", _fake_sync)

    counters, produced, attempted, h = main.run_sync()
    # Only the single "ok" source counts toward the dashboard 'sources' number.
    assert counters["sources"] == 1
    assert counters["library_tracks"] == 3
    assert produced == 1
    assert attempted == 4
    # The crashing source is recorded as outcome 'error' in the health map.
    assert set(h["sources"].keys()) == {"ok.m3u", "empty.m3u", "err.m3u", "crash.m3u"}
    assert h["sources"]["ok.m3u"]["ever_ok"] is True


def test_no_sources_return_is_four_tuple_and_non_alarming():
    """run_sync with empty CHARTS returns the 4-tuple; attempted==0 so the
    whole_run_empty guard is False and the health map has no dead sources."""
    from app import main

    saved = config.CHARTS
    config.CHARTS = []
    try:
        result = main.run_sync()
    finally:
        config.CHARTS = saved
    assert isinstance(result, tuple) and len(result) == 4
    counters, produced, attempted, h = result
    assert counters == {"sources": 0, "library_tracks": 0}
    assert produced == 0
    assert attempted == 0
    assert _whole_run_empty(produced, attempted) is False
    assert _dead_sources(h, config.SYNC_DEAD_RUNS_THRESHOLD) == []
