"""
Unit tests for the source finder functions in collector.py.

httpx is fully mocked (no real network). Tested are success, Deezer error at
HTTP 200, empty result lists, timeout/exception (fail-open), invalid/empty q,
missing Last.fm key as well as the 24h genre cache. Additionally: every snippet
string is valid JSON and matches the documented CHARTS source schema.

Run (from charts-dashboard/):
    /tmp/charts-venv/bin/python -m pytest tests/ -q
"""

import json
import pathlib

import pytest

from app import collector, config, i18n


# The collector now emits its detail/status prose via i18n (English by default).
# These tests assert the German anchors, so run them with CHARTS_LANG=de active
# (the design guarantees de is byte-identical to the former hardcoded German).
_LANG_DIR = pathlib.Path(i18n.__file__).resolve().parent.parent.parent / "lang"


@pytest.fixture(autouse=True)
def _german_lang():
    i18n.setup(lang="de", lang_dir=_LANG_DIR)
    yield
    i18n.setup()


# --- Mini fake for httpx responses ----------------------------------------

class _FakeResp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


def _patch_get(monkeypatch, resp=None, exc=None, capture=None):
    """Replace httpx.get. Either returns resp or raises exc."""
    def fake_get(url, **kwargs):
        if capture is not None:
            capture.append((url, kwargs))
        if exc is not None:
            raise exc
        return resp
    monkeypatch.setattr(collector.httpx, "get", fake_get)


# --- search_deezer_playlists ----------------------------------------------

def test_deezer_playlists_success(monkeypatch):
    payload = {"data": [
        {"id": 123, "title": "Pop Hits", "nb_tracks": 50,
         "user": {"name": "deezer"}},
        {"id": 456, "title": "Country", "nb_tracks": 30, "user": {}},
    ]}
    _patch_get(monkeypatch, resp=_FakeResp(200, payload))
    out = collector.search_deezer_playlists("pop")
    assert out["ok"] is True
    assert len(out["results"]) == 2
    first = out["results"][0]
    assert first["id"] == "123"
    assert first["title"] == "Pop Hits"
    assert first["nb_tracks"] == 50
    assert first["user"] == "deezer"
    # snippet is valid JSON + matches the schema.
    snip = json.loads(first["snippet"])
    assert snip == {"service": "deezer", "type": "playlist",
                    "source": "123", "limit": "all", "enabled": True}


def test_deezer_playlists_error_on_200(monkeypatch):
    # Deezer reports errors as HTTP 200 + error field.
    _patch_get(monkeypatch, resp=_FakeResp(200, {"error": {"code": 4}}))
    out = collector.search_deezer_playlists("pop")
    assert out["ok"] is False
    assert "detail" in out


def test_deezer_playlists_empty(monkeypatch):
    _patch_get(monkeypatch, resp=_FakeResp(200, {"data": []}))
    out = collector.search_deezer_playlists("zzxxqq")
    assert out["ok"] is True
    assert out["results"] == []


def test_deezer_playlists_empty_query(monkeypatch):
    # Empty q -> no network call.
    calls = []
    _patch_get(monkeypatch, resp=_FakeResp(200, {"data": []}), capture=calls)
    out = collector.search_deezer_playlists("   ")
    assert out["ok"] is False
    assert out["detail"] == "leere Suche"
    assert calls == []


def test_deezer_playlists_too_long(monkeypatch):
    calls = []
    _patch_get(monkeypatch, resp=_FakeResp(200, {"data": []}), capture=calls)
    out = collector.search_deezer_playlists("x" * 101)
    assert out["ok"] is False
    assert calls == []


def test_deezer_playlists_failopen(monkeypatch):
    _patch_get(monkeypatch, exc=RuntimeError("boom"))
    out = collector.search_deezer_playlists("pop")
    assert out["ok"] is False
    assert "nicht erreichbar" in out["detail"]


# --- search_deezer_artists -------------------------------------------------

def test_deezer_artists_success(monkeypatch):
    payload = {"data": [
        {"id": 789, "name": "Gölä", "nb_fan": 12345},
        {"id": 790, "name": "Hecht", "nb_fan": 6789},
    ]}
    _patch_get(monkeypatch, resp=_FakeResp(200, payload))
    out = collector.search_deezer_artists("gölä")
    assert out["ok"] is True
    assert len(out["results"]) == 2
    first = out["results"][0]
    assert first["id"] == "789"
    assert first["name"] == "Gölä"
    assert first["nb_fan"] == 12345
    # snippet is valid JSON + matches the schema (limit:50 as a number).
    snip = json.loads(first["snippet"])
    assert snip == {"service": "deezer", "type": "artist",
                    "source": "789", "limit": 50, "enabled": True}


# --- deezer_genres ----------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_genre_cache():
    collector._genre_cache = None
    collector._genre_ts = 0.0
    yield
    collector._genre_cache = None
    collector._genre_ts = 0.0


def test_deezer_genres_success(monkeypatch):
    payload = {"data": [{"id": 0, "name": "All"}, {"id": 132, "name": "Pop"}]}
    _patch_get(monkeypatch, resp=_FakeResp(200, payload))
    out = collector.deezer_genres()
    assert out["ok"] is True
    assert out["genres"] == [{"id": "0", "name": "All"},
                             {"id": "132", "name": "Pop"}]
    assert out["fetched"]


def test_deezer_genres_cache(monkeypatch):
    payload = {"data": [{"id": 0, "name": "All"}]}
    calls = []
    _patch_get(monkeypatch, resp=_FakeResp(200, payload), capture=calls)
    collector.deezer_genres()
    collector.deezer_genres()   # within the TTL -> no second network call
    assert len(calls) == 1


def test_deezer_genres_failopen(monkeypatch):
    _patch_get(monkeypatch, exc=TimeoutError("slow"))
    out = collector.deezer_genres()
    assert out["ok"] is False
    assert out["genres"] == []


# --- lastfm_tag_check -------------------------------------------------------

def test_lastfm_tag_no_key(monkeypatch):
    monkeypatch.setattr(config, "LASTFM_API_KEY", "")
    calls = []
    _patch_get(monkeypatch, resp=_FakeResp(200, {}), capture=calls)
    out = collector.lastfm_tag_check("pop")
    assert out["ok"] is False
    assert out["configured"] is False
    assert calls == []


def test_lastfm_tag_success(monkeypatch):
    monkeypatch.setattr(config, "LASTFM_API_KEY", "KEY")
    payload = {"tracks": {
        "track": [
            {"name": "Song A", "artist": {"name": "Artist A"}},
            {"name": "Song B", "artist": {"name": "Artist B"}},
        ],
        "@attr": {"total": "1795"},
    }}
    _patch_get(monkeypatch, resp=_FakeResp(200, payload))
    out = collector.lastfm_tag_check("pop")
    assert out["ok"] is True
    assert out["configured"] is True
    assert out["tag"] == "pop"
    # The honest total comes from @attr.total (the real playlist size),
    # not from the small probe sample.
    assert out["total"] == 1795
    assert "sample_count" not in out
    assert out["sample"][0] == "Artist A - Song A"
    snip = json.loads(out["snippet"])
    assert snip == {"service": "lastfm", "method": "tag.getTopTracks",
                    "tag": "pop", "limit": 100, "enabled": True}


def test_lastfm_tag_total_missing(monkeypatch):
    # When Last.fm omits @attr.total (or it is non-numeric), total is None
    # so the UI can fall back to the plain "valid" message.
    monkeypatch.setattr(config, "LASTFM_API_KEY", "KEY")
    payload = {"tracks": {"track": [
        {"name": "Song A", "artist": {"name": "Artist A"}},
    ]}}
    _patch_get(monkeypatch, resp=_FakeResp(200, payload))
    out = collector.lastfm_tag_check("pop")
    assert out["ok"] is True
    assert out["total"] is None


def test_lastfm_tag_sample_capped_at_10(monkeypatch):
    # Up to 10 preview examples are returned (fetch limit raised to 20).
    monkeypatch.setattr(config, "LASTFM_API_KEY", "KEY")
    tracks = [{"name": f"Song {i}", "artist": {"name": f"Artist {i}"}}
              for i in range(20)]
    payload = {"tracks": {"track": tracks, "@attr": {"total": "500"}}}
    calls = []
    _patch_get(monkeypatch, resp=_FakeResp(200, payload), capture=calls)
    out = collector.lastfm_tag_check("pop")
    assert out["ok"] is True
    assert len(out["sample"]) == 10
    assert calls[0][1]["params"]["limit"] == 20


def test_lastfm_tag_multiword_allowed(monkeypatch):
    # Multi-word tags (classic country) are allowed.
    monkeypatch.setattr(config, "LASTFM_API_KEY", "KEY")
    payload = {"tracks": {"track": [{"name": "X", "artist": {"name": "Y"}}]}}
    _patch_get(monkeypatch, resp=_FakeResp(200, payload))
    out = collector.lastfm_tag_check("classic country")
    assert out["ok"] is True
    assert out["tag"] == "classic country"


def test_lastfm_tag_empty_result(monkeypatch):
    monkeypatch.setattr(config, "LASTFM_API_KEY", "KEY")
    _patch_get(monkeypatch, resp=_FakeResp(200, {"tracks": {"track": []}}))
    out = collector.lastfm_tag_check("zzxxqq")
    assert out["ok"] is False
    assert out["configured"] is True
    assert "keine Tracks" in out["detail"]


def test_lastfm_tag_api_error(monkeypatch):
    monkeypatch.setattr(config, "LASTFM_API_KEY", "KEY")
    _patch_get(monkeypatch, resp=_FakeResp(
        200, {"error": 6, "message": "Tag not found"}))
    out = collector.lastfm_tag_check("pop")
    assert out["ok"] is False
    assert out["configured"] is True


def test_lastfm_tag_invalid(monkeypatch):
    monkeypatch.setattr(config, "LASTFM_API_KEY", "KEY")
    calls = []
    _patch_get(monkeypatch, resp=_FakeResp(200, {}), capture=calls)
    out = collector.lastfm_tag_check("pop; drop")   # ';' not allowed
    assert out["ok"] is False
    assert out["detail"] == "ungültiges Tag"
    assert calls == []


def test_lastfm_tag_empty_query(monkeypatch):
    monkeypatch.setattr(config, "LASTFM_API_KEY", "KEY")
    calls = []
    _patch_get(monkeypatch, resp=_FakeResp(200, {}), capture=calls)
    out = collector.lastfm_tag_check("   ")
    assert out["ok"] is False
    assert out["detail"] == "ungültiges Tag"
    assert calls == []


def test_lastfm_tag_failopen(monkeypatch):
    monkeypatch.setattr(config, "LASTFM_API_KEY", "KEY")
    _patch_get(monkeypatch, exc=RuntimeError("boom"))
    out = collector.lastfm_tag_check("pop")
    assert out["ok"] is False
    assert out["configured"] is True
    assert "nicht erreichbar" in out["detail"]
