"""
Tests for the manual MP3 upload path in charts-songrequest.

Covers the pure filename parser, the MP3-only content gate, the MIN_BITRATE
local gate, the slskd sub-threshold search branch, and the confirm-anyway
round-trip. slskd and the real mutagen MP3 parse are monkeypatched; the
manifest, staging dir and /charts drop dir are pointed at tmp_path. No network,
no Docker, no real audio decoding.
"""

import importlib
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import config, i18n, library


# --- Pure filename parsing (no I/O) ----------------------------------------

def test_parse_first_separator():
    assert library.parse_upload_name("SK.music - Inside my Soul.mp3") == (
        "SK.music", "Inside my Soul")


def test_parse_multiple_separators():
    # The title keeps the second " - " (split on the FIRST separator only).
    assert library.parse_upload_name("Artist - Intro - Outro.mp3") == (
        "Artist", "Intro - Outro")


def test_parse_missing_separator():
    assert library.parse_upload_name("justatitle.mp3") == ("", "justatitle")


def test_parse_strips_and_handles_path():
    assert library.parse_upload_name("/tmp/a/  Artist  -  Title  .mp3") == (
        "Artist", "Title")
    assert library.parse_upload_name("sub\\dir\\A - B.mp3") == ("A", "B")


# --- MP3 content gate ------------------------------------------------------

def test_read_mp3_bitrate_non_mp3_returns_none(tmp_path, monkeypatch):
    fake = tmp_path / "x.flac"
    fake.write_bytes(b"not really audio")
    # mutagen.mp3.MP3 raises for a non-MP3 -> read_mp3_bitrate returns None.
    assert library.read_mp3_bitrate(str(fake)) is None


# --- Endpoint integration (FastAPI TestClient) -----------------------------

def _build(monkeypatch, tmp_path):
    """
    Build an authenticated TestClient with the manifest, staging dir and
    /charts drop dir pointed at tmp_path. Returns (client, main_module,
    charts_dir, staging_dir).
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

    # Point the manifest (and its derived drop dir) + staging at tmp_path.
    monkeypatch.setattr(main_mod.config, "MANIFEST_FILE", manifest_file)
    # manifest module reads config.MANIFEST_FILE at call time via its own import.
    from app import manifest as manifest_mod
    monkeypatch.setattr(manifest_mod.config, "MANIFEST_FILE", manifest_file)
    monkeypatch.setattr(main_mod, "_STAGING_DIR", str(staging_dir))
    # No real library scan.
    monkeypatch.setattr(main_mod.library, "find_existing", lambda *a, **k: None)
    # Status writes must not touch the real filesystem.
    monkeypatch.setattr(main_mod, "_record_request", lambda *a, **k: None)

    token = "test-token"
    main_mod._sessions[token] = "roger"
    client = TestClient(main_mod.app)
    client.cookies.set("session", token)
    return client, main_mod, charts_dir, staging_dir


def _upload(client, filename="Artist - Title.mp3", artist="Artist",
            title="Title", content=b"ID3data"):
    return client.post("/api/upload", files={
        "file": (filename, content, "audio/mpeg")},
        data={"artist": artist, "title": title})


def _manifest(charts_dir):
    path = charts_dir / ".charts-manifest.json"
    if not path.exists():
        return {"entries": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def test_upload_requires_login(monkeypatch, tmp_path):
    client, main_mod, _cd, _sd = _build(monkeypatch, tmp_path)
    client.cookies.clear()
    r = client.post("/api/upload", files={
        "file": ("Artist - Title.mp3", b"x", "audio/mpeg")},
        data={"artist": "Artist", "title": "Title"})
    assert r.status_code == 401


def test_upload_rejects_non_mp3(monkeypatch, tmp_path):
    client, main_mod, charts_dir, _sd = _build(monkeypatch, tmp_path)
    called = []
    monkeypatch.setattr(main_mod.slsk, "find_candidates",
                        lambda *a, **k: called.append(1) or [])
    r = _upload(client, filename="Artist - Title.flac")
    assert r.status_code == 415
    assert r.json()["error"]  # upload_not_mp3 message
    # Nothing written to the drop dir, no manifest entry.
    assert not called
    assert _manifest(charts_dir)["entries"] == {}


def test_upload_mp3_named_non_mp3_content(monkeypatch, tmp_path):
    client, main_mod, charts_dir, staging_dir = _build(monkeypatch, tmp_path)
    # Passes the extension gate but mutagen says it is not an MP3.
    monkeypatch.setattr(main_mod.library, "read_mp3_bitrate", lambda p: None)
    r = _upload(client, filename="Artist - Title.mp3")
    assert r.status_code == 415
    assert _manifest(charts_dir)["entries"] == {}
    # Staged temp was removed.
    assert not list(staging_dir.glob("*.mp3")) if staging_dir.exists() else True


def test_upload_accept_ge_min_bitrate(monkeypatch, tmp_path):
    client, main_mod, charts_dir, _sd = _build(monkeypatch, tmp_path)
    monkeypatch.setattr(main_mod.library, "read_mp3_bitrate", lambda p: 300)
    called = []
    monkeypatch.setattr(main_mod.slsk, "find_candidates",
                        lambda *a, **k: called.append(1) or [])
    r = _upload(client, artist="Artist", title="Title")
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] is True
    assert body["bitrate"] == 300
    # slskd never searched.
    assert not called
    # File landed in /charts.
    assert (charts_dir / "Artist - Title.mp3").exists()
    # Manifest entry with source="upload" + submitted artist/title.
    entry = _manifest(charts_dir)["entries"]["artist - title.mp3"]
    assert entry["source"] == "upload"
    assert entry["artist"] == "Artist"
    assert entry["title"] == "Title"
    assert "replace" not in entry  # no pre-existing song


def test_upload_empty_artist_basename(monkeypatch, tmp_path):
    client, main_mod, charts_dir, _sd = _build(monkeypatch, tmp_path)
    monkeypatch.setattr(main_mod.library, "read_mp3_bitrate", lambda p: 300)
    r = client.post("/api/upload", files={
        "file": ("justatitle.mp3", b"ID3", "audio/mpeg")},
        data={"artist": "", "title": "justatitle"})
    assert r.status_code == 200
    assert r.json()["accepted"] is True
    # Stored basename uses the "Unknown Artist" fallback, never " - title.mp3".
    assert (charts_dir / "Unknown Artist - justatitle.mp3").exists()
    entry = _manifest(charts_dir)["entries"]["unknown artist - justatitle.mp3"]
    # The manifest artist is the submitted (empty) value, recorded verbatim.
    assert entry["artist"] == ""
    assert entry["title"] == "justatitle"
    assert entry["source"] == "upload"


def test_upload_below_min_triggers_search(monkeypatch, tmp_path):
    client, main_mod, charts_dir, _sd = _build(monkeypatch, tmp_path)
    monkeypatch.setattr(main_mod.library, "read_mp3_bitrate", lambda p: 160)
    monkeypatch.setattr(main_mod.slsk, "connect", lambda: object())
    cand = ("Peer", {"filename": "best.mp3", "bitRate": 320}, 95)
    monkeypatch.setattr(main_mod.slsk, "find_candidates", lambda *a, **k: [cand])
    # A delivering candidate -> the slskd download is taken.
    monkeypatch.setattr(main_mod, "_download_best",
                        lambda *a, **k: ("Peer", {"filename": "best.mp3", "bitRate": 320}, 320))
    r = _upload(client, artist="Artist", title="Title")
    assert r.status_code == 200
    body = r.json()
    assert body.get("accepted") is not True  # not an upload-accept
    assert body["ok"] is True
    # The manifest entry is the slskd download (source="manuell"), NOT upload.
    entry = _manifest(charts_dir)["entries"]["best.mp3"]
    assert entry["source"] == config.MANUAL_SOURCE_LABEL
    # No source="upload" entry was written.
    assert all(e.get("source") != "upload"
               for e in _manifest(charts_dir)["entries"].values())


def test_upload_below_min_nothing_better_needs_confirm(monkeypatch, tmp_path):
    client, main_mod, charts_dir, staging_dir = _build(monkeypatch, tmp_path)
    monkeypatch.setattr(main_mod.library, "read_mp3_bitrate", lambda p: 160)
    monkeypatch.setattr(main_mod.slsk, "connect", lambda: object())
    monkeypatch.setattr(main_mod.slsk, "find_candidates", lambda *a, **k: [])
    monkeypatch.setattr(main_mod, "_download_best", lambda *a, **k: None)
    r = _upload(client, artist="Artist", title="Title")
    assert r.status_code == 200
    body = r.json()
    assert body["needs_confirm"] is True
    assert body["bitrate"] == 160
    token = body["token"]
    # File still staged, NOT in /charts, no manifest yet.
    assert (staging_dir / f"{token}.mp3").exists()
    assert not (charts_dir / "Artist - Title.mp3").exists()
    assert _manifest(charts_dir)["entries"] == {}
    assert token in main_mod._pending_uploads


def _needs_confirm(monkeypatch, tmp_path):
    client, main_mod, charts_dir, staging_dir = _build(monkeypatch, tmp_path)
    monkeypatch.setattr(main_mod.library, "read_mp3_bitrate", lambda p: 160)
    monkeypatch.setattr(main_mod.slsk, "connect", lambda: object())
    monkeypatch.setattr(main_mod.slsk, "find_candidates", lambda *a, **k: [])
    monkeypatch.setattr(main_mod, "_download_best", lambda *a, **k: None)
    token = _upload(client, artist="Artist", title="Title").json()["token"]
    return client, main_mod, charts_dir, staging_dir, token


def test_upload_confirm_files_it(monkeypatch, tmp_path):
    client, main_mod, charts_dir, staging_dir, token = _needs_confirm(
        monkeypatch, tmp_path)
    r = client.post("/api/upload/confirm", data={
        "token": token, "artist": "Artist", "title": "Title"})
    assert r.status_code == 200
    assert r.json()["accepted"] is True
    # Moved out of staging into /charts.
    assert not (staging_dir / f"{token}.mp3").exists()
    assert (charts_dir / "Artist - Title.mp3").exists()
    entry = _manifest(charts_dir)["entries"]["artist - title.mp3"]
    assert entry["source"] == "upload"
    assert entry["artist"] == "Artist"
    # Pending entry popped.
    assert token not in main_mod._pending_uploads


def test_confirm_does_not_research(monkeypatch, tmp_path):
    client, main_mod, charts_dir, staging_dir, token = _needs_confirm(
        monkeypatch, tmp_path)
    called = []
    monkeypatch.setattr(main_mod.slsk, "find_candidates",
                        lambda *a, **k: called.append(1) or [])
    # Confirm with EDITED artist/title.
    r = client.post("/api/upload/confirm", data={
        "token": token, "artist": "Corrected", "title": "Fixed Title"})
    assert r.status_code == 200
    assert r.json()["accepted"] is True
    # slskd was NOT searched again during confirm.
    assert not called
    # Filed with the edited values.
    entry = _manifest(charts_dir)["entries"]["corrected - fixed title.mp3"]
    assert entry["artist"] == "Corrected"
    assert entry["title"] == "Fixed Title"


def test_upload_confirm_unknown_token(monkeypatch, tmp_path):
    client, main_mod, charts_dir, _sd = _build(monkeypatch, tmp_path)
    r = client.post("/api/upload/confirm", data={
        "token": "nope", "artist": "A", "title": "B"})
    assert r.status_code == 410
    assert _manifest(charts_dir)["entries"] == {}


def test_upload_upgrade_sets_replace(monkeypatch, tmp_path):
    client, main_mod, charts_dir, _sd = _build(monkeypatch, tmp_path)
    monkeypatch.setattr(main_mod.library, "read_mp3_bitrate", lambda p: 300)
    # Pretend the song already exists -> replace flag on the upload accept.
    monkeypatch.setattr(main_mod.library, "find_existing",
                        lambda *a, **k: "/music/Music/Artist/Album/old.mp3")
    monkeypatch.setattr(main_mod.library, "read_bitrate", lambda p: 192)
    monkeypatch.setattr(main_mod.slsk, "find_candidates", lambda *a, **k: [])
    r = _upload(client, artist="Artist", title="Title")
    assert r.status_code == 200
    assert r.json()["accepted"] is True
    entry = _manifest(charts_dir)["entries"]["artist - title.mp3"]
    assert entry.get("replace") is True


# Restore import-time i18n state so test ordering never leaks a language.
@pytest.fixture(autouse=True)
def _restore_setup():
    yield
    i18n.setup()
