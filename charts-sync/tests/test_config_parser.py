"""
Unit-Tests fuer den charts-sync-CHARTS-Parser (config._parse_charts /
config._build_feed).

Gedeckt: Abwaertskompatibilitaet (Alt-Config ohne 'service' == bisherige
Zuordnung), das neue 'service'-Feld, die Startup-Validierung (Warnungen statt
Crashes), die lowercase-Normalisierung von Last.fm-Methoden und der Drift-Test
gegen lastfm._METHOD_PARAM.

Der Parser liest ausschliesslich os.environ["CHARTS"]; die Tests setzen ihn pro
Fall und rufen config._parse_charts() direkt auf (statt sich auf das
Modul-Import-Ergebnis config.CHARTS zu verlassen).
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
    """Setzt CHARTS auf das (serialisierte) data und ruft den Parser auf."""
    monkeypatch.setenv("CHARTS", json.dumps(data))
    return config._parse_charts()


def _only_feed(sources):
    """Hilfe: genau eine Playlist mit genau einem Feed -> dieser Feed."""
    assert len(sources) == 1
    assert len(sources[0].feeds) == 1
    return sources[0].feeds[0]


# --- Abwaertskompatibilitaet -------------------------------------------------

def test_legacy_without_service_matches_old_mapping(monkeypatch, examples):
    """Alt-Config OHNE 'service' ergibt exakt die bisherige Feed-Zuordnung."""
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
    """Einzelquelle direkt am Eintrag (ohne 'sources') bleibt gueltig."""
    data = [{"type": "chart", "source": "0", "name": "x.m3u"}]
    feed = _only_feed(_parse(monkeypatch, data))
    assert feed.kind == "deezer"
    assert feed.deezer_type == "chart"
    assert feed.source == "0"


# --- Neues service-Feld ------------------------------------------------------

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


# --- Validierung: Warnungen statt Crashes -----------------------------------

def test_contradiction_service_deezer_type_lastfm(monkeypatch, examples, caplog):
    data = examples["invalid"]["contradiction_service_deezer_type_lastfm"]
    with caplog.at_level(logging.WARNING):
        feed = _only_feed(_parse(monkeypatch, data))
    assert feed.kind == "deezer"  # service gewinnt
    assert any("widerspruechlich" in r.message for r in caplog.records)


def test_unknown_service_falls_back_to_deezer(monkeypatch, examples, caplog):
    data = examples["invalid"]["unknown_service"]
    with caplog.at_level(logging.WARNING):
        feed = _only_feed(_parse(monkeypatch, data))
    assert feed.kind == "deezer"
    assert feed.enabled is True
    assert any("unbekannter service" in r.message for r in caplog.records)


def test_unknown_deezer_type_falls_back_to_chart(monkeypatch, examples, caplog):
    data = examples["invalid"]["unknown_deezer_type"]
    with caplog.at_level(logging.WARNING):
        feed = _only_feed(_parse(monkeypatch, data))
    assert feed.kind == "deezer"
    assert feed.deezer_type == "chart"
    assert feed.enabled is True  # Deezer-Fallback bleibt aktiv
    assert any("unbekannter Deezer-type" in r.message for r in caplog.records)


def test_unknown_lastfm_method_disables_feed(monkeypatch, examples, caplog):
    data = examples["invalid"]["unknown_lastfm_method"]
    with caplog.at_level(logging.WARNING):
        feed = _only_feed(_parse(monkeypatch, data))
    assert feed.kind == "lastfm"
    assert feed.enabled is False
    assert any("unbekannte Last.fm-method" in r.message for r in caplog.records)


def test_lastfm_missing_required_param_disables_feed(monkeypatch, examples, caplog):
    data = examples["invalid"]["lastfm_missing_param"]
    with caplog.at_level(logging.WARNING):
        feed = _only_feed(_parse(monkeypatch, data))
    assert feed.kind == "lastfm"
    assert feed.enabled is False
    assert any("braucht Parameter 'country'" in r.message for r in caplog.records)


def test_lastfm_with_deezer_source_is_ignored(monkeypatch, examples, caplog):
    """MEDIUM-4: Last.fm-Quelle mit Deezer-'source' -> ignoriert + Warnung."""
    data = examples["invalid"]["lastfm_with_deezer_source"]
    with caplog.at_level(logging.WARNING):
        feed = _only_feed(_parse(monkeypatch, data))
    assert feed.kind == "lastfm"
    assert feed.enabled is True  # chart.getTopTracks braucht keinen Parameter
    # 'source' darf NICHT als Last.fm-Query-Param durchgereicht werden.
    assert "source" not in feed.params
    assert any("mit Deezer-Feld 'source'" in r.message for r in caplog.records)


def test_camelcase_lastfm_method_valid_and_lowercased(monkeypatch, examples, caplog):
    """HIGH-2 + MEDIUM-5: CamelCase geo.getTopTracks -> gueltig, lowercase gespeichert."""
    data = examples["invalid"]["camelcase_method"]
    with caplog.at_level(logging.WARNING):
        feed = _only_feed(_parse(monkeypatch, data))
    assert feed.kind == "lastfm"
    assert feed.enabled is True
    assert feed.method == "geo.gettoptracks"
    assert feed.params == {"country": "Switzerland"}
    # KEINE "unbekannte method"-Warnung
    assert not any("unbekannte Last.fm-method" in r.message for r in caplog.records)


# --- Fehlerhafte Eintraege / JSON -------------------------------------------

def test_missing_name_skips_entry(monkeypatch, caplog):
    data = [{"sources": [{"service": "deezer", "type": "chart", "source": "0"}]}]
    with caplog.at_level(logging.WARNING):
        sources = _parse(monkeypatch, data)
    assert sources == []
    assert any("kein 'name'" in r.message for r in caplog.records)


def test_empty_sources_list_warns(monkeypatch, caplog):
    data = [{"name": "x.m3u", "sources": []}]
    with caplog.at_level(logging.WARNING):
        sources = _parse(monkeypatch, data)
    assert len(sources) == 1
    assert sources[0].feeds == []
    assert any("leere sources-Liste" in r.message for r in caplog.records)


def test_broken_json_falls_back_to_defaults(monkeypatch, caplog):
    monkeypatch.setenv("CHARTS", '[{"name": "x.m3u", "sources": [}]')  # kaputt
    with caplog.at_level(logging.ERROR):
        sources = config._parse_charts()
    # Fall auf eingebaute Defaults -> genau so viele Playlists wie _DEFAULT_CHARTS.
    assert len(sources) == len(config._DEFAULT_CHARTS)
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert any("kein gueltiges JSON" in r.message for r in errors)
    assert any("CHARTS-SOURCES.md" in r.message for r in errors)


# --- INFO-Zeile zaehlt aktive Quellen NACH Downgrade (NIT-6) ----------------

def test_info_line_counts_active_feeds_after_downgrade(monkeypatch, caplog):
    """Eine deaktivierte (ungueltige) Last.fm-Quelle zaehlt NICHT als aktiv."""
    data = [{"name": "x.m3u", "sources": [
        {"service": "deezer", "type": "chart", "source": "0", "enabled": True},
        {"service": "lastfm", "method": "geo.getTopTracks"},  # fehlt country -> aus
    ]}]
    with caplog.at_level(logging.INFO):
        sources = _parse(monkeypatch, data)
    # Nur die Deezer-Quelle bleibt aktiv.
    assert sum(len(cs.active_feeds) for cs in sources) == 1
    info = [r for r in caplog.records if r.levelno == logging.INFO]
    assert any("1 aktive Quelle(n)" in r.message for r in info)


# --- Drift-Test (HIGH-1) -----------------------------------------------------

def test_valid_lastfm_methods_mirror_matches_lastfm_module():
    """config._VALID_LASTFM_METHODS muss zu lastfm._METHOD_PARAM passen."""
    assert config._VALID_LASTFM_METHODS == lastfm._METHOD_PARAM


# --- Pro-Playlist Genre-Filter (FEAT-002) -----------------------------------

def _single_source(genre_filter=None):
    """Eine Playlist mit genau einer Deezer-Quelle, optional mit genre_filter."""
    entry = {"name": "x.m3u", "sources": [
        {"service": "deezer", "type": "chart", "source": "0"}]}
    if genre_filter is not None:
        entry["genre_filter"] = genre_filter
    return [entry]


def test_genre_filter_true_overrides_global(monkeypatch):
    """genre_filter:true -> effective True, egal was global GENRE_FILTER ist."""
    for global_default in (True, False):
        monkeypatch.setattr(config, "GENRE_FILTER", global_default)
        sources = _parse(monkeypatch, _single_source(True))
        assert sources[0].genre_filter is True
        assert sources[0].effective_genre_filter is True


def test_genre_filter_false_overrides_global(monkeypatch):
    """genre_filter:false -> effective False, egal was global GENRE_FILTER ist."""
    for global_default in (True, False):
        monkeypatch.setattr(config, "GENRE_FILTER", global_default)
        sources = _parse(monkeypatch, _single_source(False))
        assert sources[0].genre_filter is False
        assert sources[0].effective_genre_filter is False


def test_genre_filter_absent_uses_global(monkeypatch):
    """Fehlt genre_filter -> effective == globaler GENRE_FILTER (beide Werte)."""
    for global_default in (True, False):
        monkeypatch.setattr(config, "GENRE_FILTER", global_default)
        sources = _parse(monkeypatch, _single_source(None))
        assert sources[0].genre_filter is None
        assert sources[0].effective_genre_filter is global_default


def test_genre_filter_non_bool_falls_back_to_global(monkeypatch):
    """Nicht-bool genre_filter (z.B. String) -> None -> globaler Default."""
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
