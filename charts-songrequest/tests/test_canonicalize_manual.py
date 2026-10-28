"""
Tests for FIX 2: manual-input canonicalization via Deezer in charts-songrequest.

Manual input (Upload / YouTube / Request tabs) is routed through
deezer.resolve_canonical before the manifest write, with the RAW user value as
the total fallback. These tests stub app.deezer.resolve_canonical (so no
network) and prove:
  (a) a reliable hit stores the canonical spelling ('mash'/'ewigi liäbi' ->
      'Mash'/'Ewigi Liäbi') for Upload, YouTube and the Request new-song path;
  (b) a Deezer MISS (resolver returns the raw input) stores the raw value
      unchanged - NO naive .title() - so 'SK.music'/'k.d. lang' survive;
  (c) a Deezer EXCEPTION falls back to raw and never crashes the write;
  (d) the library-first find_existing check runs on the RAW input BEFORE any
      canonicalization/download (an already-present song short-circuits).

slskd, yt-dlp, the mutagen MP3 parse and the library scan are monkeypatched;
the manifest/staging/charts dirs are pointed at tmp_path. No network, no Docker.
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
    at tmp_path. Deezer is NOT stubbed here - each test sets its own stub so the
    canonicalization behaviour is explicit. Returns
    (client, main_module, charts_dir, staging_dir).
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
    def _dl(url, dest_dir):
        os.makedirs(dest_dir, exist_ok=True)
        path = os.path.join(dest_dir, "staged.mp3")
        with open(path, "wb") as fh:
            fh.write(b"ID3fake-mp3-bytes")
        return path
    return _dl


def _single_entry(charts_dir):
    """The one manifest entry (these tests write exactly one)."""
    entries = _manifest(charts_dir)["entries"]
    assert len(entries) == 1, entries
    return next(iter(entries.values()))


# === (a) a reliable hit stores the canonical spelling ======================

def test_upload_stores_canonical_spelling(monkeypatch, tmp_path):
    client, main_mod, charts_dir, _sd = _build(monkeypatch, tmp_path)
    monkeypatch.setattr(main_mod.library, "read_mp3_bitrate", lambda p: 300)
    # No upsampling (so it accepts straight away as a good upload).
    monkeypatch.setattr(main_mod, "_upload_upsampled",
                        lambda *a, **k: (False, None, 0.0))
    monkeypatch.setattr(main_mod.deezer, "resolve_canonical",
                        lambda a, t: ("Mash", "Ewigi Liäbi"))
    r = client.post("/api/upload", files={
        "file": ("mash - ewigi liäbi.mp3", b"ID3", "audio/mpeg")},
        data={"artist": "mash", "title": "ewigi liäbi"})
    assert r.status_code == 200
    assert r.json()["accepted"] is True
    # Both the on-disk basename and the manifest carry the canonical spelling.
    assert (charts_dir / "Mash - Ewigi Liäbi.mp3").exists()
    entry = _single_entry(charts_dir)
    assert entry["artist"] == "Mash"
    assert entry["title"] == "Ewigi Liäbi"
    assert entry["source"] == "upload"


def test_youtube_stores_canonical_spelling(monkeypatch, tmp_path):
    client, main_mod, charts_dir, staging_dir = _build(monkeypatch, tmp_path)
    monkeypatch.setattr(main_mod.youtube, "download_audio",
                        _fake_download(staging_dir))
    monkeypatch.setattr(main_mod.deezer, "resolve_canonical",
                        lambda a, t: ("Mash", "Ewigi Liäbi"))
    r = client.post("/api/youtube", data={
        "url": _URL, "artist": "mash", "title": "ewigi liäbi"})
    assert r.status_code == 200
    assert r.json()["accepted"] is True
    assert (charts_dir / "Mash - Ewigi Liäbi.mp3").exists()
    entry = _single_entry(charts_dir)
    assert entry["artist"] == "Mash"
    assert entry["title"] == "Ewigi Liäbi"
    assert entry["source"] == "youtube"


def test_request_new_song_stores_canonical_spelling(monkeypatch, tmp_path):
    client, main_mod, charts_dir, _sd = _build(monkeypatch, tmp_path)
    monkeypatch.setattr(main_mod.slsk, "connect", lambda: object())
    cand = ("Peer", {"filename": "best.mp3", "bitRate": 320}, 95)
    monkeypatch.setattr(main_mod.slsk, "find_candidates", lambda *a, **k: [cand])
    monkeypatch.setattr(main_mod, "_download_best",
                        lambda *a, **k: ("Peer", {"filename": "best.mp3", "bitRate": 320}, 320))
    # No existing library folder -> canonical_artist returns None -> keep canon.
    monkeypatch.setattr(main_mod.library, "canonical_artist", lambda *a, **k: None)
    monkeypatch.setattr(main_mod.deezer, "resolve_canonical",
                        lambda a, t: ("Mash", "Ewigi Liäbi"))
    r = client.post("/api/request", data={"artist": "mash", "title": "ewigi liäbi"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["artist"] == "Mash"
    assert body["title"] == "Ewigi Liäbi"
    entry = _manifest(charts_dir)["entries"]["best.mp3"]
    assert entry["artist"] == "Mash"
    assert entry["title"] == "Ewigi Liäbi"


# === (b) a Deezer MISS keeps the raw value (no .title() mangling) ===========

def test_upload_deezer_miss_keeps_raw(monkeypatch, tmp_path):
    client, main_mod, charts_dir, _sd = _build(monkeypatch, tmp_path)
    monkeypatch.setattr(main_mod.library, "read_mp3_bitrate", lambda p: 300)
    monkeypatch.setattr(main_mod, "_upload_upsampled",
                        lambda *a, **k: (False, None, 0.0))
    # Deezer MISS: resolver returns the raw input unchanged.
    monkeypatch.setattr(main_mod.deezer, "resolve_canonical",
                        lambda a, t: (a, t))
    r = client.post("/api/upload", files={
        "file": ("SK.music - Inside my Soul.mp3", b"ID3", "audio/mpeg")},
        data={"artist": "SK.music", "title": "Inside my Soul"})
    assert r.status_code == 200
    assert r.json()["accepted"] is True
    entry = _single_entry(charts_dir)
    # The deliberate spelling survives EXACTLY - not 'Sk.music', not 'Inside My
    # Soul' - because there is no naive .title() on the upload path.
    assert entry["artist"] == "SK.music"
    assert entry["title"] == "Inside my Soul"


def test_youtube_deezer_miss_keeps_raw(monkeypatch, tmp_path):
    client, main_mod, charts_dir, staging_dir = _build(monkeypatch, tmp_path)
    monkeypatch.setattr(main_mod.youtube, "download_audio",
                        _fake_download(staging_dir))
    monkeypatch.setattr(main_mod.deezer, "resolve_canonical",
                        lambda a, t: (a, t))
    r = client.post("/api/youtube", data={
        "url": _URL, "artist": "k.d. lang", "title": "Constant Craving"})
    assert r.status_code == 200
    assert r.json()["accepted"] is True
    entry = _single_entry(charts_dir)
    assert entry["artist"] == "k.d. lang"
    assert entry["title"] == "Constant Craving"


# === (c) a Deezer EXCEPTION falls back to raw and never crashes =============

def test_upload_deezer_exception_falls_back_to_raw(monkeypatch, tmp_path):
    client, main_mod, charts_dir, _sd = _build(monkeypatch, tmp_path)
    monkeypatch.setattr(main_mod.library, "read_mp3_bitrate", lambda p: 300)
    monkeypatch.setattr(main_mod, "_upload_upsampled",
                        lambda *a, **k: (False, None, 0.0))
    def _boom(a, t):
        raise RuntimeError("deezer hiccup")
    monkeypatch.setattr(main_mod.deezer, "resolve_canonical", _boom)
    r = client.post("/api/upload", files={
        "file": ("SK.music - Inside my Soul.mp3", b"ID3", "audio/mpeg")},
        data={"artist": "SK.music", "title": "Inside my Soul"})
    # The upload still succeeds with the RAW value (try/except fallback).
    assert r.status_code == 200
    assert r.json()["accepted"] is True
    entry = _single_entry(charts_dir)
    assert entry["artist"] == "SK.music"
    assert entry["title"] == "Inside my Soul"


# === (d) library-first runs on the RAW input BEFORE canonicalization ========

def test_upload_already_present_short_circuits_before_canonicalize(monkeypatch, tmp_path):
    client, main_mod, charts_dir, _sd = _build(monkeypatch, tmp_path)
    monkeypatch.setattr(main_mod.library, "read_mp3_bitrate", lambda p: 300)
    monkeypatch.setattr(main_mod, "_upload_upsampled",
                        lambda *a, **k: (False, None, 0.0))
    # Already present at good quality, keyed on the RAW input. The upload is NOT
    # a genuine upgrade (same bitrate), so it is rejected as already-present.
    seen = []
    monkeypatch.setattr(main_mod.library, "find_existing",
                        lambda md, a, t: seen.append((a, t)) or "/music/Music/SK.music/Album/old.mp3")
    monkeypatch.setattr(main_mod.library, "read_bitrate", lambda p: 300)
    # If canonicalization ran, it would raise - proving it is NOT reached before
    # the library-first short-circuit.
    def _boom(a, t):
        raise AssertionError("canonicalization ran before library-first check")
    monkeypatch.setattr(main_mod.deezer, "resolve_canonical", _boom)
    r = client.post("/api/upload", files={
        "file": ("sk.music - inside my soul.mp3", b"ID3", "audio/mpeg")},
        data={"artist": "sk.music", "title": "inside my soul"})
    assert r.status_code == 200
    assert r.json()["already"] is True
    # find_existing was called with the RAW (lowercase) input, not canonical.
    assert seen == [("sk.music", "inside my soul")]
    # Nothing written.
    assert _manifest(charts_dir)["entries"] == {}


# Restore import-time i18n state so test ordering never leaks a language.
@pytest.fixture(autouse=True)
def _restore_setup():
    yield
    i18n.setup()
