"""
Unit tests for the charts-sync CHARTS parser (config._parse_charts /
config._build_feed).

Covered: backward compatibility (legacy config without 'service' == former
mapping), the new 'service' field, the startup validation (warnings instead of
crashes), the lowercase normalization of Last.fm methods and the drift test
against lastfm._METHOD_PARAM.

The parser reads only os.environ["CHARTS"]; the tests set it per case and call
config._parse_charts() directly (instead of relying on the module-import result
config.CHARTS).
"""

import json
import logging
import os
from pathlib import Path

import pytest

from app import config, lastfm

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "charts_examples.json"


@pytest.fixture(scope="module")
def examples() -> dict:
    with open(_FIXTURE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _parse(monkeypatch, data) -> list:
    """Set CHARTS to the (serialized) data and call the parser."""
    monkeypatch.setenv("CHARTS", json.dumps(data))
    return config._parse_charts()


def _only_feed(sources):
    """Helper: exactly one playlist with exactly one feed -> that feed."""
    assert len(sources) == 1
    assert len(sources[0].feeds) == 1
    return sources[0].feeds[0]


# --- Backward compatibility --------------------------------------------------

def test_legacy_without_service_matches_old_mapping(monkeypatch, examples):
    """Legacy config WITHOUT 'service' yields exactly the former feed mapping."""
    data = examples["legacy_no_service"]["mixed"]
    sources = _parse(monkeypatch, data)

    assert len(sources) == 1
    feeds = sources[0].feeds
    assert len(feeds) == 3

    # type:"chart" -> Deezer/chart
    assert feeds[0].kind == "deezer"
    assert feeds[0].deezer_type == "chart"
    assert feeds[0].source == "0"
    assert feeds[0].enabled is True

    # type:"playlist" -> Deezer/playlist
    assert feeds[1].kind == "deezer"
    assert feeds[1].deezer_type == "playlist"
    assert feeds[1].source == "1313617925"

    # type:"lastfm" -> Last.fm, method lowercased, country in params
    assert feeds[2].kind == "lastfm"
    assert feeds[2].method == "geo.gettoptracks"
    assert feeds[2].params == {"country": "Switzerland"}
    assert feeds[2].enabled is True


def test_legacy_single_source_without_sources_list(monkeypatch):
    """A single source directly on the entry (without 'sources') stays valid."""
    data = [{"type": "chart", "source": "0", "name": "x.m3u"}]
    feed = _only_feed(_parse(monkeypatch, data))
    assert feed.kind == "deezer"
    assert feed.deezer_type == "chart"
    assert feed.source == "0"


# --- New service field -------------------------------------------------------

def test_service_deezer_and_lastfm(monkeypatch, examples):
    data = examples["runnable"]["minimal"]
    sources = _parse(monkeypatch, data)
    feeds = sources[0].feeds
    assert [f.kind for f in feeds] == ["deezer", "deezer"]
    assert feeds[0].deezer_type == "chart" and feeds[0].source == "0"
    assert feeds[1].deezer_type == "playlist" and feeds[1].source == "1313617925"

    sources = _parse(monkeypatch, examples["runnable"]["country_lastfm"])
    assert all(f.kind == "lastfm" for f in sources[0].feeds)
    assert sources[0].feeds[0].method == "tag.gettoptracks"
    assert sources[0].feeds[0].params == {"tag": "country"}


def test_service_case_insensitive(monkeypatch):
    data = [{"name": "x.m3u", "sources": [
        {"service": "DEEZER", "type": "Chart", "source": "0"}]}]
    feed = _only_feed(_parse(monkeypatch, data))
    assert feed.kind == "deezer"
    assert feed.deezer_type == "chart"


# --- Validation: warnings instead of crashes --------------------------------

def test_contradiction_service_deezer_type_lastfm(monkeypatch, examples, caplog):
    data = examples["invalid"]["contradiction_service_deezer_type_lastfm"]
    with caplog.at_level(logging.WARNING):
        feed = _only_feed(_parse(monkeypatch, data))
    assert feed.kind == "deezer"  # service wins
    assert any("contradictory" in r.message for r in caplog.records)


def test_unknown_service_falls_back_to_deezer(monkeypatch, examples, caplog):
    data = examples["invalid"]["unknown_service"]
    with caplog.at_level(logging.WARNING):
        feed = _only_feed(_parse(monkeypatch, data))
    assert feed.kind == "deezer"
    assert feed.enabled is True
    assert any("unknown service" in r.message for r in caplog.records)


def test_unknown_deezer_type_falls_back_to_chart(monkeypatch, examples, caplog):
    data = examples["invalid"]["unknown_deezer_type"]
    with caplog.at_level(logging.WARNING):
        feed = _only_feed(_parse(monkeypatch, data))
    assert feed.kind == "deezer"
    assert feed.deezer_type == "chart"
    assert feed.enabled is True  # Deezer fallback stays active
    assert any("unknown Deezer type" in r.message for r in caplog.records)


def test_unknown_lastfm_method_disables_feed(monkeypatch, examples, caplog):
    data = examples["invalid"]["unknown_lastfm_method"]
    with caplog.at_level(logging.WARNING):
        feed = _only_feed(_parse(monkeypatch, data))
    assert feed.kind == "lastfm"
    assert feed.enabled is False
    assert any("unknown Last.fm method" in r.message for r in caplog.records)


def test_lastfm_missing_required_param_disables_feed(monkeypatch, examples, caplog):
    data = examples["invalid"]["lastfm_missing_param"]
    with caplog.at_level(logging.WARNING):
        feed = _only_feed(_parse(monkeypatch, data))
    assert feed.kind == "lastfm"
    assert feed.enabled is False
    assert any("needs parameter 'country'" in r.message for r in caplog.records)


def test_lastfm_with_deezer_source_is_ignored(monkeypatch, examples, caplog):
    """MEDIUM-4: Last.fm source with Deezer 'source' -> ignored + warning."""
    data = examples["invalid"]["lastfm_with_deezer_source"]
    with caplog.at_level(logging.WARNING):
        feed = _only_feed(_parse(monkeypatch, data))
    assert feed.kind == "lastfm"
    assert feed.enabled is True  # chart.getTopTracks needs no parameter
    # 'source' must NOT be passed through as a Last.fm query param.
    assert "source" not in feed.params
    assert any("with Deezer field 'source'" in r.message for r in caplog.records)


def test_camelcase_lastfm_method_valid_and_lowercased(monkeypatch, examples, caplog):
    """HIGH-2 + MEDIUM-5: CamelCase geo.getTopTracks -> valid, stored lowercase."""
    data = examples["invalid"]["camelcase_method"]
    with caplog.at_level(logging.WARNING):
        feed = _only_feed(_parse(monkeypatch, data))
    assert feed.kind == "lastfm"
    assert feed.enabled is True
    assert feed.method == "geo.gettoptracks"
    assert feed.params == {"country": "Switzerland"}
    # NO "unknown method" warning
    assert not any("unknown Last.fm method" in r.message for r in caplog.records)


# --- Faulty entries / JSON --------------------------------------------------

def test_missing_name_skips_entry(monkeypatch, caplog):
    data = [{"sources": [{"service": "deezer", "type": "chart", "source": "0"}]}]
    with caplog.at_level(logging.WARNING):
        sources = _parse(monkeypatch, data)
    assert sources == []
    assert any("has no 'name'" in r.message for r in caplog.records)


def test_empty_sources_list_warns(monkeypatch, caplog):
    data = [{"name": "x.m3u", "sources": []}]
    with caplog.at_level(logging.WARNING):
        sources = _parse(monkeypatch, data)
    assert len(sources) == 1
    assert sources[0].feeds == []
    assert any("empty sources list" in r.message for r in caplog.records)


def test_broken_json_falls_back_to_defaults(monkeypatch, caplog):
    monkeypatch.setenv("CHARTS", '[{"name": "x.m3u", "sources": [}]')  # broken
    with caplog.at_level(logging.ERROR):
        sources = config._parse_charts()
    # Fall back to built-in defaults -> exactly as many playlists as _DEFAULT_CHARTS.
    assert len(sources) == len(config._DEFAULT_CHARTS)
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert any("not valid JSON" in r.message for r in errors)
    assert any("CHARTS-SOURCES.md" in r.message for r in errors)


# --- INFO line counts active sources AFTER downgrade (NIT-6) ----------------

def test_info_line_counts_active_feeds_after_downgrade(monkeypatch, caplog):
    """A disabled (invalid) Last.fm source does NOT count as active."""
    data = [{"name": "x.m3u", "sources": [
        {"service": "deezer", "type": "chart", "source": "0", "enabled": True},
        {"service": "lastfm", "method": "geo.getTopTracks"},  # missing country -> off
    ]}]
    with caplog.at_level(logging.INFO):
        sources = _parse(monkeypatch, data)
    # Only the Deezer source stays active.
    assert sum(len(cs.active_feeds) for cs in sources) == 1
    info = [r for r in caplog.records if r.levelno == logging.INFO]
    assert any("1 active source(s)" in r.message for r in info)


# --- Drift test (HIGH-1) -----------------------------------------------------

def test_valid_lastfm_methods_mirror_matches_lastfm_module():
    """config._VALID_LASTFM_METHODS must match lastfm._METHOD_PARAM."""
    assert config._VALID_LASTFM_METHODS == lastfm._METHOD_PARAM


# --- Per-playlist genre filter (FEAT-002) -----------------------------------

def _single_source(genre_filter=None):
    """A playlist with exactly one Deezer source, optionally with genre_filter."""
    entry = {"name": "x.m3u", "sources": [
        {"service": "deezer", "type": "chart", "source": "0"}]}
    if genre_filter is not None:
        entry["genre_filter"] = genre_filter
    return [entry]


def test_genre_filter_true_overrides_global(monkeypatch):
    """genre_filter:true -> effective True, regardless of global GENRE_FILTER."""
    for global_default in (True, False):
        monkeypatch.setattr(config, "GENRE_FILTER", global_default)
        sources = _parse(monkeypatch, _single_source(True))
        assert sources[0].genre_filter is True
        assert sources[0].effective_genre_filter is True


def test_genre_filter_false_overrides_global(monkeypatch):
    """genre_filter:false -> effective False, regardless of global GENRE_FILTER."""
    for global_default in (True, False):
        monkeypatch.setattr(config, "GENRE_FILTER", global_default)
        sources = _parse(monkeypatch, _single_source(False))
        assert sources[0].genre_filter is False
        assert sources[0].effective_genre_filter is False


def test_genre_filter_absent_uses_global(monkeypatch):
    """Missing genre_filter -> effective == global GENRE_FILTER (both values)."""
    for global_default in (True, False):
        monkeypatch.setattr(config, "GENRE_FILTER", global_default)
        sources = _parse(monkeypatch, _single_source(None))
        assert sources[0].genre_filter is None
        assert sources[0].effective_genre_filter is global_default


def test_genre_filter_non_bool_falls_back_to_global(monkeypatch):
    """Non-bool genre_filter (e.g. a string) -> None -> global default."""
    monkeypatch.setattr(config, "GENRE_FILTER", True)
    sources = _parse(monkeypatch, _single_source("yes"))
    assert sources[0].genre_filter is None
    assert sources[0].effective_genre_filter is True


def test_genre_filter_precedence_both_directions(monkeypatch):
    """global False + playlist true -> True; global True + playlist false -> False."""
    monkeypatch.setattr(config, "GENRE_FILTER", False)
    sources = _parse(monkeypatch, _single_source(True))
    assert sources[0].effective_genre_filter is True

    monkeypatch.setattr(config, "GENRE_FILTER", True)
    sources = _parse(monkeypatch, _single_source(False))
    assert sources[0].effective_genre_filter is False
