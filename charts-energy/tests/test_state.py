"""
Unit tests for the accumulate + dedupe + retention state (app/state.py).

No network: spins are constructed as IcyTrack objects and the state file is
written under a tmp CHARTS_DIR.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app import config, state
from app.icy import IcyTrack


@pytest.fixture(autouse=True)
def tmp_charts(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CHARTS_DIR", str(tmp_path))
    monkeypatch.setattr(config, "STATE_FILE", ".charts-energy-state.json")
    monkeypatch.setattr(config, "ENERGY_TRACK_RETENTION_DAYS", 30)
    monkeypatch.setattr(config, "ENERGY_MAX_TRACKS_PER_CHANNEL", 500)
    return tmp_path


def _track(artist, title, started_at="", pl_id=""):
    return IcyTrack(artist=artist, title=title, started_at=started_at, pl_id=pl_id)


def test_accumulate_new_tracks():
    st = state.load()
    assert state.record_spin(st, "energypop", _track("A", "1", "t1", "1")) is True
    assert state.record_spin(st, "energypop", _track("B", "2", "t2", "2")) is True
    rec = st["channels"]["energypop"]
    assert len(rec["tracks"]) == 2


def test_dedupe_repeated_spin_bumps_last_seen():
    st = state.load()
    spin = _track("A", "1", "2026-01-01T00:00:00+0000", "99")
    assert state.record_spin(st, "energypop", spin) is True
    first_last = st["channels"]["energypop"]["tracks"][0]["last_seen"]
    # Re-reading the same spin -> no new track, just a bumped last_seen.
    assert state.record_spin(st, "energypop", spin) is False
    rec = st["channels"]["energypop"]
    assert len(rec["tracks"]) == 1
    assert rec["tracks"][0]["last_seen"] >= first_last


def test_atomic_roundtrip():
    st = state.load()
    state.record_spin(st, "energypop", _track("A", "1", "t1", "1"))
    state.save(st)
    reloaded = state.load()
    assert reloaded["channels"]["energypop"]["tracks"][0]["artist"] == "A"


def test_corrupt_or_missing_file_is_empty(tmp_charts):
    # Missing file.
    assert state.load() == {"version": state.STATE_VERSION, "channels": {}}
    # Corrupt file.
    path = tmp_charts / ".charts-energy-state.json"
    path.write_text("{ not json", encoding="utf-8")
    assert state.load() == {"version": state.STATE_VERSION, "channels": {}}


def test_retention_drops_old_and_cap_keeps_newest(monkeypatch):
    monkeypatch.setattr(config, "ENERGY_MAX_TRACKS_PER_CHANNEL", 2)
    old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat(timespec="seconds")
    recent = datetime.now(timezone.utc).isoformat(timespec="seconds")
    newer = (datetime.now(timezone.utc) + timedelta(seconds=5)).isoformat(timespec="seconds")

    rec = {
        "seen_keys": ["kold", "k1", "k2", "k3"],
        "tracks": [
            {"spin_key": "kold", "artist": "Old", "title": "x",
             "first_seen": old, "last_seen": old},
            {"spin_key": "k1", "artist": "A", "title": "1",
             "first_seen": recent, "last_seen": recent},
            {"spin_key": "k2", "artist": "B", "title": "2",
             "first_seen": recent, "last_seen": recent},
            {"spin_key": "k3", "artist": "C", "title": "3",
             "first_seen": newer, "last_seen": newer},
        ],
    }
    state.trim_channel(rec)
    keys = {e["spin_key"] for e in rec["tracks"]}
    # 'kold' dropped by age; cap=2 keeps the two most recent (k3 newest, then k2/k1).
    assert "kold" not in keys
    assert len(rec["tracks"]) == 2
    assert "k3" in keys


def test_ordering_across_dst_boundary():
    """first_seen desc must be correct even across a DST offset change."""
    st = {"version": 1, "channels": {"c": {"seen_keys": [], "tracks": [
        # Winter time (+01:00) earlier in absolute UTC...
        {"spin_key": "w", "artist": "Winter", "title": "x",
         "first_seen": "2026-01-01T12:00:00+00:00", "last_seen": "2026-01-01T12:00:00+00:00"},
        # Summer time later in absolute UTC.
        {"spin_key": "s", "artist": "Summer", "title": "y",
         "first_seen": "2026-07-01T12:00:00+00:00", "last_seen": "2026-07-01T12:00:00+00:00"},
    ]}}}
    ordered = state.tracks_for(st, "c")
    assert [e["artist"] for e in ordered] == ["Summer", "Winter"]
