"""
Unit tests for year.resolve_year - the NEW decade-clamp resolver.

The MusicBrainz call (_mb_year) is monkeypatched so NO network is hit; each
assertion binds to one row of the normative branch table in design section 1.2.
"""

import pytest

from app import config, year


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    # Energy-owned MB cache in a tmp dir; reset the process-wide cache so one
    # test never leaks a cached value into the next.
    monkeypatch.setattr(config, "MB_CACHE_FILE", str(tmp_path / "mbcache.json"))
    monkeypatch.setattr(config, "MB_ENABLED", True)
    monkeypatch.setattr(year, "_cache", None)
    yield


def _stub_mb(monkeypatch, value, ok=True):
    monkeypatch.setattr(year, "_mb_year", lambda a, t: (value, ok))


def test_mb_inside_decade_returns_mb_year(monkeypatch):
    _stub_mb(monkeypatch, "1983")
    assert year.resolve_year("A", "B", "1980") == "1983"


def test_mb_outside_decade_returns_midpoint(monkeypatch):
    _stub_mb(monkeypatch, "1975")
    assert year.resolve_year("A", "B", "1980") == "1985"


def test_mb_empty_with_decade_returns_midpoint(monkeypatch):
    _stub_mb(monkeypatch, "")
    assert year.resolve_year("A", "B", "1980") == "1985"


def test_mb_year_no_decade_returns_mb_year(monkeypatch):
    _stub_mb(monkeypatch, "1999")
    assert year.resolve_year("A", "B", "") == "1999"


def test_mb_empty_no_decade_returns_empty(monkeypatch):
    _stub_mb(monkeypatch, "")
    assert year.resolve_year("A", "B", "") == ""


def test_transient_mb_error_not_cached(monkeypatch):
    # ok=False -> the miss must NOT be cached so the next run retries.
    _stub_mb(monkeypatch, "", ok=False)
    cache = year._load_cache()
    assert cache == {}
    year.resolve_year("Some Artist", "Some Title", "1980")
    assert cache == {}  # unchanged -> not cached


def test_non_four_digit_decade_treated_as_no_decade(monkeypatch):
    # A non-4-digit decade string falls through to branch C (MB or "").
    _stub_mb(monkeypatch, "1999")
    assert year.resolve_year("A", "B", "80s") == "1999"
