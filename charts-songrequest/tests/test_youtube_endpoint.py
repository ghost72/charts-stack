"""
Endpoint tests for the YouTube URL import (/api/youtube/fetch, /api/youtube).

yt-dlp is NEVER invoked: youtube.fetch_metadata and youtube.download_audio are
monkeypatched to return scripted data / write a fake staged mp3. The manifest,
staging dir and /charts drop dir are pointed at tmp_path. The library scan is
stubbed. No network, no Docker, no real audio.
"""

import importlib
import json
import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import i18n


def _build(monkeypatch, tmp_path):
    """
    Authenticated TestClient with MANIFEST_FILE/_STAGING_DIR/charts dir pointed
    at tmp_path. Returns (client, main_module, charts_dir, staging_dir).
    """
    from fastapi.testclient import TestClient
    monkeypatch.setenv("SONGREQUEST_USERS", "roger:secret")
    import app.config as config_mod
    importlib.reload(config_mod)
    import app.main as main_mod
    importlib.reload(main_mod)

    charts_dir = tmp_path / "charts"
    charts_dir.mkdir()
    staging_dir = tmp_path / "staging"
    manifest_file = str(charts_dir / ".charts-manifest.json")

    monkeypatch.setattr(main_mod.config, "MANIFEST_FILE", manifest_file)
    from app import manifest as manifest_mod
    monkeypatch.setattr(manifest_mod.config, "MANIFEST_FILE", manifest_file)
    monkeypatch.setattr(main_mod, "_STAGING_DIR", str(staging_dir))
    # Default: nothing in the library (individual tests override as needed).
    monkeypatch.setattr(main_mod.library, "find_existing", lambda *a, **k: None)
    monkeypatch.setattr(main_mod, "_record_request", lambda *a, **k: None)

    token = "test-token"
    main_mod._sessions[token] = "roger"
    client = TestClient(main_mod.app)
    client.cookies.set("session", token)
    return client, main_mod, charts_dir, staging_dir


def _manifest(charts_dir):
    path = charts_dir / ".charts-manifest.json"
    if not path.exists():
        return {"entries": {}}
    return json.loads(path.read_text(encoding="utf-8"))


_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def _fake_download(staging_dir):
    """Return a download_audio stub that writes a fake staged mp3 and its path."""
    def _dl(url, dest_dir):
        os.makedirs(dest_dir, exist_ok=True)
        path = os.path.join(dest_dir, "staged.mp3")
        with open(path, "wb") as fh:
            fh.write(b"ID3fake-mp3-bytes")
        return path
    return _dl


# --- /api/youtube/fetch ---------------------------------------------------

def test_fetch_returns_cleaned_suggestion(monkeypatch, tmp_path):
    client, main_mod, _cd, _sd = _build(monkeypatch, tmp_path)
    monkeypatch.setattr(main_mod.youtube, "fetch_metadata",
                        lambda url: {"title": "Dermot Kennedy - Kiss Me (Official Video)"})
    r = client.post("/api/youtube/fetch", data={"url": _URL})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["artist"] == "Dermot Kennedy"
    assert body["title"] == "Kiss Me"


def test_fetch_requires_login(monkeypatch, tmp_path):
    client, main_mod, _cd, _sd = _build(monkeypatch, tmp_path)
    client.cookies.clear()
    r = client.post("/api/youtube/fetch", data={"url": _URL})
    assert r.status_code == 401


def test_fetch_rejects_bad_url(monkeypatch, tmp_path):
    client, main_mod, _cd, _sd = _build(monkeypatch, tmp_path)
    called = []
    monkeypatch.setattr(main_mod.youtube, "fetch_metadata",
                        lambda url: called.append(1) or {})
    r = client.post("/api/youtube/fetch", data={"url": "https://example.com/x"})
    assert r.status_code == 400
    assert not called  # yt-dlp never touched


def test_fetch_failure_is_recoverable(monkeypatch, tmp_path):
    client, main_mod, _cd, _sd = _build(monkeypatch, tmp_path)
    def _boom(url):
        raise RuntimeError("extractor broke")
    monkeypatch.setattr(main_mod.youtube, "fetch_metadata", _boom)
    r = client.post("/api/youtube/fetch", data={"url": _URL})
    assert r.status_code == 502
    assert r.json()["error"]


# --- /api/youtube ---------------------------------------------------------

def test_youtube_requires_login(monkeypatch, tmp_path):
    client, main_mod, _cd, _sd = _build(monkeypatch, tmp_path)
    client.cookies.clear()
    r = client.post("/api/youtube", data={
        "url": _URL, "artist": "A", "title": "B"})
    assert r.status_code == 401


def test_youtube_rejects_bad_url_no_download(monkeypatch, tmp_path):
    client, main_mod, charts_dir, _sd = _build(monkeypatch, tmp_path)
    called = []
    monkeypatch.setattr(main_mod.youtube, "download_audio",
                        lambda *a, **k: called.append(1))
    r = client.post("/api/youtube", data={
        "url": "https://example.com/x", "artist": "A", "title": "B"})
    assert r.status_code == 400
    assert not called
    assert _manifest(charts_dir)["entries"] == {}


def test_youtube_missing_title(monkeypatch, tmp_path):
    client, main_mod, charts_dir, _sd = _build(monkeypatch, tmp_path)
    called = []
    monkeypatch.setattr(main_mod.youtube, "download_audio",
                        lambda *a, **k: called.append(1))
    r = client.post("/api/youtube", data={
        "url": _URL, "artist": "A", "title": ""})
    assert r.status_code == 400
    assert not called


def test_youtube_already_present_good_quality_no_download(monkeypatch, tmp_path):
    client, main_mod, charts_dir, _sd = _build(monkeypatch, tmp_path)
    monkeypatch.setattr(main_mod.library, "find_existing",
                        lambda *a, **k: "/music/Music/Artist/Album/old.mp3")
    monkeypatch.setattr(main_mod.library, "read_bitrate", lambda p: 320)
    called = []
    monkeypatch.setattr(main_mod.youtube, "download_audio",
                        lambda *a, **k: called.append(1))
    r = client.post("/api/youtube", data={
        "url": _URL, "artist": "Artist", "title": "Title"})
    assert r.status_code == 200
    body = r.json()
    assert body["already"] is True
    assert body["bitrate"] == 320
    # No download happened, no manifest entry.
    assert not called
    assert _manifest(charts_dir)["entries"] == {}


def test_youtube_download_writes_manifest(monkeypatch, tmp_path):
    client, main_mod, charts_dir, staging_dir = _build(monkeypatch, tmp_path)
    monkeypatch.setattr(main_mod.youtube, "download_audio",
                        _fake_download(staging_dir))
    r = client.post("/api/youtube", data={
        "url": _URL, "artist": "Dermot Kennedy", "title": "Kiss Me"})
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] is True
    # File landed in /charts.
    assert (charts_dir / "Dermot Kennedy - Kiss Me.mp3").exists()
    entry = _manifest(charts_dir)["entries"]["dermot kennedy - kiss me.mp3"]
    assert entry["source"] == "youtube"
    assert entry["accept_anyway"] is True
    assert entry["artist"] == "Dermot Kennedy"
    assert entry["title"] == "Kiss Me"
    assert "replace" not in entry  # did not pre-exist


def test_youtube_empty_artist_floored(monkeypatch, tmp_path):
    client, main_mod, charts_dir, staging_dir = _build(monkeypatch, tmp_path)
    monkeypatch.setattr(main_mod.youtube, "download_audio",
                        _fake_download(staging_dir))
    r = client.post("/api/youtube", data={
        "url": _URL, "artist": "", "title": "JustATitle"})
    assert r.status_code == 200
    assert r.json()["accepted"] is True
    entry = _manifest(charts_dir)["entries"]["unknown artist - justatitle.mp3"]
    # Never an empty manifest artist (charts-import would skip it).
    assert entry["artist"] == "Unknown Artist"
    assert entry["source"] == "youtube"
    assert entry["accept_anyway"] is True


def test_youtube_below_min_bitrate_proceeds_as_upgrade(monkeypatch, tmp_path):
    client, main_mod, charts_dir, staging_dir = _build(monkeypatch, tmp_path)
    # Existing copy is below MIN_BITRATE -> the YouTube copy may be better, so
    # it downloads with the replace flag (is_upgrade=True).
    monkeypatch.setattr(main_mod.library, "find_existing",
                        lambda *a, **k: "/music/Music/Artist/Album/old.mp3")
    monkeypatch.setattr(main_mod.library, "read_bitrate", lambda p: 128)
    monkeypatch.setattr(main_mod.youtube, "download_audio",
                        _fake_download(staging_dir))
    r = client.post("/api/youtube", data={
        "url": _URL, "artist": "Artist", "title": "Title"})
    assert r.status_code == 200
    assert r.json()["accepted"] is True
    entry = _manifest(charts_dir)["entries"]["artist - title.mp3"]
    assert entry["source"] == "youtube"
    assert entry.get("replace") is True


def test_youtube_download_failure_recoverable(monkeypatch, tmp_path):
    client, main_mod, charts_dir, staging_dir = _build(monkeypatch, tmp_path)
    def _boom(url, dest_dir):
        raise RuntimeError("video unavailable")
    monkeypatch.setattr(main_mod.youtube, "download_audio", _boom)
    r = client.post("/api/youtube", data={
        "url": _URL, "artist": "Artist", "title": "Title"})
    assert r.status_code == 502
    assert r.json()["error"]
    assert _manifest(charts_dir)["entries"] == {}


# Restore import-time i18n state so test ordering never leaks a language.
@pytest.fixture(autouse=True)
def _restore_setup():
    yield
    i18n.setup()
