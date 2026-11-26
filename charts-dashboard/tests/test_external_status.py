"""Offline regression tests for the Hitparade connection check."""

import importlib
import threading

import httpx
import pytest

from app import collector, config


def test_collect_runs_independent_checks_in_parallel(monkeypatch):
    barrier = threading.Barrier(6)
    services = {"charts-sync": {"status": "running"}}
    reads = []
    def read_status():
        reads.append(True)
        return services
    monkeypatch.setattr(collector, "_read_status_files", read_status)
    def check(value):
        barrier.wait(timeout=2)
        return value
    for name in ("_container_states", "_slskd_status", "_lidarr_status",
                 "_external_status", "_dir_stats"):
        monkeypatch.setattr(collector, name, lambda name=name: check({"source": name}))
    def inflight(value):
        assert value is services
        return check({"active": {"ok": True}})
    monkeypatch.setattr(collector, "_inflight", inflight)
    result = collector.collect()
    assert reads == [True]
    assert result["services"] is services
    assert result["containers"] == {"source": "_container_states"}
    assert result["external"] == {"source": "_external_status"}
    assert result["storage"] == {"source": "_dir_stats"}
    assert result["inflight"] == {"active": {"ok": True}}
    assert set(result) == {"generated", "services", "containers", "slskd",
                           "lidarr", "external", "storage", "inflight"}


def test_collect_does_not_hide_unexpected_check_failures(monkeypatch):
    monkeypatch.setattr(collector, "_read_status_files", lambda: {})
    for name in ("_container_states", "_slskd_status", "_lidarr_status",
                 "_external_status", "_dir_stats", "_inflight"):
        monkeypatch.setattr(collector, name, lambda *args: {})
    def failure():
        raise RuntimeError("status check failed")
    monkeypatch.setattr(collector, "_container_states", failure)
    with pytest.raises(RuntimeError, match="status check failed"):
        collector.collect()


def test_progress_reports_completed_checks_while_another_is_running(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(collector, "_read_status_files", lambda: {})
    for name in ("_container_states", "_slskd_status", "_lidarr_status",
                 "_dir_stats", "_inflight"):
        monkeypatch.setattr(collector, name, lambda *args: {})
    def slow_external():
        if not release.wait(3):
            raise RuntimeError("test check was not released")
        return {"ok": False}
    monkeypatch.setattr(collector, "_external_status", slow_external)
    events = collector.collect_events()
    try:
        first = next(events)
        assert first["type"] == "progress"
        assert len(first["checks"]) == 6
        finished = [next(events) for _ in range(5)]
        assert {event["check"] for event in finished} == set(first["checks"]) - {"external"}
        assert all(event["type"] == "checked" for event in finished)
    finally:
        release.set()
    assert next(events) == {"type": "checked", "check": "external"}
    snapshot = next(events)
    assert snapshot["type"] == "result"
    assert snapshot["data"]["external"] == {"ok": False}
    assert list(events) == []


@pytest.mark.parametrize("host,key", [
    ("http://slskd:5030", ""),
    ("", "test-key"),
    ("", ""),
])
def test_unconfigured_slskd_does_not_probe(monkeypatch, host, key):
    monkeypatch.setattr(config, "SLSKD_HOST", host)
    monkeypatch.setattr(config, "SLSKD_API_KEY", key)

    def unexpected_request(*args, **kwargs):
        pytest.fail("Unconfigured slskd must not issue an HTTP request")

    monkeypatch.setattr(collector.httpx, "get", unexpected_request)
    result = collector._slskd_status()
    assert result["configured"] is False
    assert result["ok"] is False
    assert result["detail"]


def test_configured_slskd_timeout_remains_a_connection_failure(monkeypatch):
    monkeypatch.setattr(config, "SLSKD_HOST", "http://slskd:5030")
    monkeypatch.setattr(config, "SLSKD_API_KEY", "test-key")

    def timeout(*args, **kwargs):
        raise httpx.ReadTimeout("test timeout")

    monkeypatch.setattr(collector.httpx, "get", timeout)
    result = collector._slskd_status()
    assert result["configured"] is True
    assert result["ok"] is False
    assert "ReadTimeout" in result["detail"]


@pytest.mark.parametrize("override", [None, "https://example.test/custom-chart"])
def test_hitparade_url_default_and_override(monkeypatch, override):
    with monkeypatch.context() as env:
        env.delenv("HITPARADE_URL", raising=False)
        if override is not None:
            env.setenv("HITPARADE_URL", override)
        try:
            importlib.reload(config)
            assert config.HITPARADE_URL == (
                override or "https://hitparade.ch/charts/singles"
            )
        finally:
            env.undo()
            importlib.reload(config)


@pytest.mark.parametrize("status_code", [200, 404, 503])
def test_hitparade_check_reports_chart_response(monkeypatch, status_code):
    monkeypatch.setattr(collector, "_ext_cache", None)
    monkeypatch.setattr(collector, "_ext_ts", 0.0)
    monkeypatch.setattr(config, "HITPARADE_ENABLED", True)
    monkeypatch.setattr(config, "LASTFM_API_KEY", "")
    monkeypatch.setattr(config, "PUSHOVER_TOKEN", "")
    monkeypatch.setattr(config, "FLASHBACK_HASH", "")
    monkeypatch.setattr(config, "ENERGY_ENABLED", False)
    monkeypatch.setattr(config, "MB_ENABLED", False)
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        if url == "https://hitparade.ch/charts":
            return httpx.Response(404)
        if url == config.HITPARADE_URL:
            return httpx.Response(status_code)
        return httpx.Response(200, json={})

    monkeypatch.setattr(collector.httpx, "get", fake_get)
    result = collector._external_status()["hitparade"]

    assert result["configured"] is True
    assert result["ok"] is (status_code == 200)
    assert bool(result["detail"]) is (status_code != 200)
    chart_calls = [(url, kwargs) for url, kwargs in calls
                   if "hitparade.ch" in url]
    assert len(chart_calls) == 1
    url, kwargs = chart_calls[0]
    assert url == "https://hitparade.ch/charts/singles"
    assert kwargs["follow_redirects"] is True
    assert "Mozilla" in kwargs["headers"]["User-Agent"]


@pytest.mark.parametrize("enabled,status,payload,ok,requests", [
    (False, 200, {}, False, 0),
    (True, 200, {"feed": {"results": [{"artistName": "Artist", "name": "Song"}]}}, True, 1),
    (True, 503, {}, False, 1),
    (True, 200, {"feed": {"results": []}}, False, 1),
    (True, 200, {}, False, 1),
])
def test_apple_chart_check_is_separate_and_optional(monkeypatch, enabled, status,
                                                   payload, ok, requests):
    monkeypatch.setattr(collector, "_ext_cache", None)
    monkeypatch.setattr(config, "APPLE_CHARTS_ENABLED", enabled)
    monkeypatch.setattr(config, "APPLE_CHARTS_COUNTRY", "ch")
    monkeypatch.setattr(config, "ENERGY_ENABLED", False)
    monkeypatch.setattr(config, "LASTFM_API_KEY", "")
    monkeypatch.setattr(config, "FLASHBACK_HASH", "")
    monkeypatch.setattr(config, "PUSHOVER_TOKEN", "")
    calls = []

    def fake_get(url, **kwargs):
        if "rss.marketingtools.apple.com" in url:
            calls.append(url)
            return httpx.Response(status, json=payload)
        return httpx.Response(200, json={})

    monkeypatch.setattr(collector.httpx, "get", fake_get)
    result = collector._external_status()
    assert result["apple"]["configured"] is enabled
    assert result["apple"]["ok"] is ok
    assert len(calls) == requests
    assert result["itunes"]["ok"] is True
    if calls:
        assert calls == [
            "https://rss.marketingtools.apple.com/api/v2/ch/music/most-played/100/songs.json"]


def test_apple_invalid_country_never_reaches_network(monkeypatch):
    monkeypatch.setattr(collector, "_ext_cache", None)
    monkeypatch.setattr(config, "APPLE_CHARTS_ENABLED", True)
    monkeypatch.setattr(config, "APPLE_CHARTS_COUNTRY", "../ch")
    monkeypatch.setattr(config, "ENERGY_ENABLED", False)
    monkeypatch.setattr(config, "PUSHOVER_TOKEN", "")
    monkeypatch.setattr(config, "FLASHBACK_HASH", "")

    def fake_get(url, **kwargs):
        assert "rss.marketingtools.apple.com" not in url
        return httpx.Response(200, json={})

    monkeypatch.setattr(collector.httpx, "get", fake_get)
    result = collector._external_status()["apple"]
    assert result["configured"] is True
    assert result["ok"] is False
    assert result["detail"]
