"""
Tests for the ListenBrainz "createdfor" JSON-parse hardening: an empty or
non-JSON (HTML) 200 body on either createdfor call must NOT raise - it is
treated as "no data" and returns the empty shape the caller expects. HTTP is
mocked, NO network.
"""
import logging
import pathlib
import sys

import httpx

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from app import i18n, listenbrainz, main

_LANG_DIR = pathlib.Path(main.__file__).resolve().parent.parent.parent / "lang"


def _mock_get(monkeypatch, status=200, content=b""):
    """Make listenbrainz._get_with_retry return a canned response."""
    resp = httpx.Response(status, content=content)
    monkeypatch.setattr(listenbrainz, "_get_with_retry",
                        lambda client, url, **kw: resp)


# --- _list_created_for (GET .../playlists/createdfor) ----------------------

def test_list_created_for_empty_body_returns_empty(monkeypatch, caplog):
    i18n.setup(lang="en", lang_dir=_LANG_DIR)
    _mock_get(monkeypatch, 200, b"")
    with caplog.at_level(logging.INFO):
        with httpx.Client() as client:
            assert listenbrainz._list_created_for("u", "", client) == []
    assert "empty response body" in caplog.text


def test_list_created_for_invalid_json_returns_empty(monkeypatch, caplog):
    i18n.setup(lang="en", lang_dir=_LANG_DIR)
    _mock_get(monkeypatch, 200, b"<html>nope</html>")
    with caplog.at_level(logging.INFO):
        with httpx.Client() as client:
            assert listenbrainz._list_created_for("u", "", client) == []
    rec = [r for r in caplog.records if "not valid JSON" in r.getMessage()]
    assert rec and rec[0].levelno == logging.WARNING


# --- _fetch_playlist_tracks (GET /1/playlist/<mbid>) -----------------------

def test_fetch_playlist_tracks_empty_body_returns_empty(monkeypatch, caplog):
    i18n.setup(lang="en", lang_dir=_LANG_DIR)
    _mock_get(monkeypatch, 200, b"")
    with caplog.at_level(logging.INFO):
        with httpx.Client() as client:
            assert listenbrainz._fetch_playlist_tracks("mbid", "", client) == []
    assert "empty response body" in caplog.text


def test_fetch_playlist_tracks_invalid_json_returns_empty(monkeypatch, caplog):
    i18n.setup(lang="en", lang_dir=_LANG_DIR)
    _mock_get(monkeypatch, 200, b"<html>nope</html>")
    with caplog.at_level(logging.INFO):
        with httpx.Client() as client:
            assert listenbrainz._fetch_playlist_tracks("mbid", "", client) == []
    rec = [r for r in caplog.records if "not valid JSON" in r.getMessage()]
    assert rec and rec[0].levelno == logging.WARNING


# --- top-level: a createdfor empty/HTML body does not fail the run ----------

def test_fetch_recommendations_empty_body_no_raise(monkeypatch):
    i18n.setup(lang="en", lang_dir=_LANG_DIR)
    _mock_get(monkeypatch, 200, b"")
    assert listenbrainz.fetch_recommendations("u", "", ["jams"]) == {}


def test_fetch_recommendations_html_body_no_raise(monkeypatch):
    i18n.setup(lang="en", lang_dir=_LANG_DIR)
    _mock_get(monkeypatch, 200, b"<html>nope</html>")
    assert listenbrainz.fetch_recommendations("u", "", ["jams"]) == {}
