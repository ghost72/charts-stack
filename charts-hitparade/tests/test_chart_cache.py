"""
Unit tests for the chart cache (app/chart_cache.py).

The cache persists the last parsed hitparade chart so the resync trigger can
re-match against it without fetching hitparade.ch. Covered:
  - save/load round-trip returns tuples in stored order.
  - load() on a missing file -> {}.
  - load() on broken JSON -> {}.
  - load() on a malformed (non-dict / wrong shape) structure -> {}.
All tests run WITHOUT network/Docker and use a tmp CHARTS_DIR.
"""

from app import chart_cache, config


def test_save_load_round_trip(monkeypatch, tmp_path):
    """A saved chart dict is read back with tuple entries in order."""
    monkeypatch.setattr(config, "CHARTS_DIR", str(tmp_path))
    charts = {"singles": [(1, "Artist A", "Title A"), (2, "Artist B", "Title B")]}
    chart_cache.save(charts)
    loaded = chart_cache.load()
    assert loaded == charts
    # Entries are tuples (not lists) after the round-trip.
    assert all(isinstance(s, tuple) for s in loaded["singles"])
    # Stored (chart-position) order is preserved.
    assert [s[0] for s in loaded["singles"]] == [1, 2]


def test_load_missing_file_returns_empty(monkeypatch, tmp_path):
    """No cache file yet -> {}."""
    monkeypatch.setattr(config, "CHARTS_DIR", str(tmp_path))
    assert chart_cache.load() == {}


def test_load_broken_json_returns_empty(monkeypatch, tmp_path):
    """Corrupt JSON -> {} (treated as no cache)."""
    monkeypatch.setattr(config, "CHARTS_DIR", str(tmp_path))
    (tmp_path / config.CHART_CACHE_FILE).write_text("{not json", encoding="utf-8")
    assert chart_cache.load() == {}


def test_load_malformed_structure_returns_empty(monkeypatch, tmp_path):
    """Valid JSON but wrong shape (charts not a dict) -> {}."""
    monkeypatch.setattr(config, "CHARTS_DIR", str(tmp_path))
    (tmp_path / config.CHART_CACHE_FILE).write_text(
        '{"charts": [1, 2, 3]}', encoding="utf-8")
    assert chart_cache.load() == {}


def test_load_non_object_returns_empty(monkeypatch, tmp_path):
    """Top-level JSON that is not an object -> {}."""
    monkeypatch.setattr(config, "CHARTS_DIR", str(tmp_path))
    (tmp_path / config.CHART_CACHE_FILE).write_text("[1, 2]", encoding="utf-8")
    assert chart_cache.load() == {}
