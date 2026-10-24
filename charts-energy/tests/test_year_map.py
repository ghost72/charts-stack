"""
Unit tests for playlist_library.write_year_map - the shared year-map writer.

No network: the map is written under a tmp CHARTS_DIR and read back.
"""

import json

import pytest

from app import config, playlist_library


@pytest.fixture(autouse=True)
def tmp_charts(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CHARTS_DIR", str(tmp_path))
    # YEAR_MAP_FILE is computed at import time from the old CHARTS_DIR; repoint
    # it at the tmp dir using the SAME shared flashback filename.
    monkeypatch.setattr(config, "YEAR_MAP_FILE",
                        str(tmp_path / ".charts-flashback-years.json"))
    return tmp_path


def _read_map(tmp_charts):
    return json.loads(
        (tmp_charts / ".charts-flashback-years.json").read_text("utf-8"))


def test_writes_lowercase_artist_title_keys(tmp_charts):
    # Real year (is_fallback False) -> bare string.
    playlist_library.write_year_map(
        [(1, "Depeche Mode", "Enjoy The Silence", "1990", False, "1990")])
    data = _read_map(tmp_charts)
    assert data == {"depeche mode|enjoy the silence": "1990"}


def test_shared_path_matches_charts_import_reader(tmp_charts):
    # Regression pin: the file name is the shared flashback name.
    assert config.YEAR_MAP_FILE.endswith(".charts-flashback-years.json")


def test_existing_entries_preserved_and_grows(tmp_charts):
    (tmp_charts / ".charts-flashback-years.json").write_text(
        json.dumps({"old artist|old title": "1985"}), encoding="utf-8")
    playlist_library.write_year_map(
        [(1, "New Artist", "New Title", "1999", False, "1990")])
    data = _read_map(tmp_charts)
    assert data == {
        "old artist|old title": "1985",
        "new artist|new title": "1999",
    }


def test_empty_year_rows_skipped(tmp_charts):
    playlist_library.write_year_map([
        (1, "With Year", "Song", "1988", False, "1980"),
        (2, "No Year", "Song", "", False, "1980"),
    ])
    data = _read_map(tmp_charts)
    assert data == {"with year|song": "1988"}


def test_no_write_when_nothing_changes(tmp_charts):
    path = tmp_charts / ".charts-flashback-years.json"
    playlist_library.write_year_map([(1, "A", "B", "1990", False, "1990")])
    first_mtime = path.stat().st_mtime_ns
    # Same entry again -> nothing changed -> file not rewritten.
    playlist_library.write_year_map([(1, "A", "B", "1990", False, "1990")])
    assert path.stat().st_mtime_ns == first_mtime


def test_fallback_writes_pending_object(tmp_charts):
    # Decade midpoint (is_fallback True) -> pending object with the schema.
    playlist_library.write_year_map(
        [(1, "A-ha", "Take On Me", "1985", True, "1980")])
    data = _read_map(tmp_charts)
    assert data == {
        "a-ha|take on me": {
            "year": "1985", "pending": True, "decade": "1980",
            "src": "energy-library", "tries": 0,
        }
    }


def test_fallback_reobserved_preserves_tries(tmp_charts):
    # An existing object with the same year+decade keeps its tries on re-observe.
    (tmp_charts / ".charts-flashback-years.json").write_text(
        json.dumps({"a-ha|take on me": {
            "year": "1985", "pending": True, "decade": "1980",
            "src": "energy-library", "tries": 3}}), encoding="utf-8")
    playlist_library.write_year_map(
        [(1, "A-ha", "Take On Me", "1985", True, "1980")])
    data = _read_map(tmp_charts)
    # Identical object already present (same tries) -> no rewrite of the value.
    assert data["a-ha|take on me"]["tries"] == 3


def test_real_year_overwrites_prior_object(tmp_charts):
    # A now-resolved real year overwrites a prior pending object with a bare
    # string.
    (tmp_charts / ".charts-flashback-years.json").write_text(
        json.dumps({"a-ha|take on me": {
            "year": "1985", "pending": True, "decade": "1980",
            "src": "energy-library", "tries": 2}}), encoding="utf-8")
    playlist_library.write_year_map(
        [(1, "A-ha", "Take On Me", "1986", False, "1980")])
    data = _read_map(tmp_charts)
    assert data == {"a-ha|take on me": "1986"}
