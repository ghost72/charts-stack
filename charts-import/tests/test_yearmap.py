"""
Unit tests for app/yearmap.py - the shared year-map schema helpers.

No network: the map is written under a tmp year-map file and read back. These
pin the tagged-union shape rules (bare string = confirmed; object = pending)
that both importer.py and backfill.py rely on.
"""

import json

import pytest

from app import yearmap


@pytest.fixture(autouse=True)
def tmp_map(tmp_path, monkeypatch):
    p = tmp_path / ".charts-flashback-years.json"
    monkeypatch.setattr(yearmap, "YEAR_MAP_FILE", str(p))
    return p


# --- is_pending / year_of ---------------------------------------------------

def test_is_pending_on_string_object_malformed():
    assert yearmap.is_pending("1985") is False
    assert yearmap.is_pending({"year": "1985", "pending": True}) is True
    assert yearmap.is_pending({"year": "1985"}) is False  # no pending flag
    assert yearmap.is_pending(["1985"]) is False
    assert yearmap.is_pending(None) is False


def test_year_of_on_string_object_malformed():
    assert yearmap.year_of("1985") == "1985"
    assert yearmap.year_of({"year": "1990", "pending": True}) == "1990"
    assert yearmap.year_of({"year": "", "pending": True}) == ""
    assert yearmap.year_of(["x"]) == ""
    assert yearmap.year_of(None) == ""


# --- make_pending -----------------------------------------------------------

def test_make_pending_generic_shape():
    obj = yearmap.make_pending(year="", src="import-generic")
    assert obj == {"year": "", "pending": True, "src": "import-generic",
                   "tries": 0}
    assert "decade" not in obj


def test_make_pending_decade_shape():
    obj = yearmap.make_pending(year="1985", src="energy-library", decade="1980")
    assert obj == {"year": "1985", "pending": True, "src": "energy-library",
                   "tries": 0, "decade": "1980"}


# --- confirm / give_up ------------------------------------------------------

def test_confirm_turns_object_into_bare_string():
    data = {"a|b": {"year": "", "pending": True, "src": "import-generic",
                    "tries": 2}}
    yearmap.confirm(data, "a|b", "1999")
    assert data == {"a|b": "1999"}


def test_give_up_deletes_key():
    data = {"a|b": {"year": "", "pending": True, "src": "import-generic"}}
    yearmap.give_up(data, "a|b")
    assert data == {}


# --- atomic rewrite round-trip ----------------------------------------------

def test_rewrite_round_trip(tmp_map):
    data = {"a|b": "1980", "c|d": {"year": "", "pending": True,
                                   "src": "import-generic", "tries": 0}}
    yearmap.rewrite(data)
    assert json.loads(tmp_map.read_text("utf-8")) == data
    assert yearmap.load() == data


def test_load_fails_open_on_missing_or_corrupt(tmp_map):
    assert yearmap.load() == {}  # missing
    tmp_map.write_text("not json", encoding="utf-8")
    assert yearmap.load() == {}  # corrupt


# --- _album_folder_year -----------------------------------------------------

def test_album_folder_year_parses_year_folder():
    assert yearmap._album_folder_year(
        "/music/Music/A-ha/Take On Me (1985)/01 - Take On Me.mp3") == "1985"


def test_album_folder_year_rejects_non_year_folder():
    assert yearmap._album_folder_year(
        "/music/Music/A-ha/Take On Me/Take On Me.mp3") == ""


def test_album_folder_year_rejects_implausible_year():
    assert yearmap._album_folder_year(
        "/music/Music/A/Album (1823)/x.mp3") == ""
