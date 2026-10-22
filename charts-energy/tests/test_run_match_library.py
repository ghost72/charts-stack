"""
Unit tests for run_match with LIBRARY-mode channels.

No network/Docker: library.build_index, state.load/tracks_for, notify.send and
year.resolve_year are stubbed; the shared missing file + year map are written
into a tmp CHARTS_DIR and read back.
"""

import json

import pytest

from app import config, library, main, playlist_library, state
from app import year as year_mod


class _FakeLibrary:
    """A library where only the given (artist, title) pairs are present."""

    def __init__(self, present):
        self._present = {(a.lower(), t.lower()) for a, t in present}

    def find_match(self, artist, title, threshold):
        if (artist.lower(), title.lower()) in self._present:
            return object(), 100
        return None, 0


@pytest.fixture(autouse=True)
def tmp_charts(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CHARTS_DIR", str(tmp_path))
    monkeypatch.setattr(config, "YEAR_MAP_FILE",
                        str(tmp_path / ".charts-flashback-years.json"))
    monkeypatch.setattr(config, "LIBRARY_MISSING_FILE",
                        "missing-energy-library.txt")
    monkeypatch.setattr(config, "NOTIFY_MISSING_LIMIT", 40)
    # No Pushover.
    monkeypatch.setattr(main.notify, "send", lambda *a, **k: None)
    # Deterministic year resolution (no MB network): year = decade or "".
    monkeypatch.setattr(year_mod, "resolve_year",
                        lambda a, t, decade: decade or "")
    return tmp_path


def _track(artist, title):
    return {"artist": artist, "title": title}


def _wire(monkeypatch, channels, tracks_by_slug, present):
    monkeypatch.setattr(config, "active_channels", lambda: channels)
    monkeypatch.setattr(state, "load", lambda: {"channels": {}})
    monkeypatch.setattr(state, "tracks_for",
                        lambda st, slug: tracks_by_slug.get(slug, []))
    monkeypatch.setattr(library, "build_index",
                        lambda music_dir: _FakeLibrary(present))


def test_library_channel_writes_missing_and_year_map(monkeypatch, tmp_charts):
    ch = config.Channel(slug="energy80s", mode="library")
    _wire(monkeypatch, [ch],
          {"energy80s": [_track("a-ha", "Take On Me"),
                         _track("Present Act", "Owned Song")]},
          present=[("Present Act", "Owned Song")])

    result = main.run_match()

    # No .m3u written anywhere.
    assert not list(tmp_charts.glob("*.m3u"))
    # Missing file carries only the missing song.
    missing = (tmp_charts / "missing-energy-library.txt").read_text("utf-8")
    body = [ln for ln in missing.splitlines() if ln and not ln.startswith("#")]
    assert body == ["  1. a-ha - Take On Me"]
    # Year map: ONLY the missing song (present song NOT added).
    year_map = json.loads(
        (tmp_charts / ".charts-flashback-years.json").read_text("utf-8"))
    assert year_map == {"a-ha|take on me": "1980"}
    assert "present act|owned song" not in year_map
    # Counter.
    assert result["library_missing"] == 1


def test_two_library_channels_share_missing_file(monkeypatch, tmp_charts):
    ch80 = config.Channel(slug="energy80s", mode="library")
    ch90 = config.Channel(slug="energy90s", mode="library")
    _wire(monkeypatch, [ch80, ch90],
          {"energy80s": [_track("Madonna", "Vogue")],
           "energy90s": [_track("Nirvana", "Lithium")]},
          present=[])

    result = main.run_match()

    missing = (tmp_charts / "missing-energy-library.txt").read_text("utf-8")
    body = [ln for ln in missing.splitlines() if ln and not ln.startswith("#")]
    assert body == ["  1. Madonna - Vogue", "  2. Nirvana - Lithium"]
    assert result["library_missing"] == 2
    year_map = json.loads(
        (tmp_charts / ".charts-flashback-years.json").read_text("utf-8"))
    assert year_map == {"madonna|vogue": "1980", "nirvana|lithium": "1990"}


def test_library_counter_is_wired_into_dashboard_labels():
    assert "library_missing" in main._COUNTER_LABEL_KEYS
