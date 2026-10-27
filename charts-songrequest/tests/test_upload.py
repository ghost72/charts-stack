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

# Capture the REAL find_existing at import time. _build() monkeypatches
# library.find_existing to a None stub; the duplicate-reject test restores this
# genuine implementation (monkeypatch only stored _build's lambda as "original").
_REAL_FIND_EXISTING = library.find_existing


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
    # The manifest artist is floored to "Unknown Artist" (matching the on-disk
    # basename), never an empty string - charts-import skips an empty artist.
    assert entry["artist"] == "Unknown Artist"
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


def test_upload_decorated_title_duplicate_rejects(monkeypatch, tmp_path):
    """A decorated upload title whose clean form already exists at equal-or-
    better quality rejects at /api/upload with already:true (FIX 1), instead of
    missing the library match and reaching the slskd-search / confirm path."""
    client, main_mod, charts_dir, _sd = _build(monkeypatch, tmp_path)
    # Build a real on-disk library tree with the CLEAN title at 320 kbps.
    music = tmp_path / "Music"
    album = music / "HPRoos" / "Das Isch mini Schwiiz"
    album.mkdir(parents=True)
    (album / "Das Isch mini Schwiiz.mp3").write_bytes(b"")
    monkeypatch.setattr(main_mod.config, "MUSIC_DIR", str(music))
    # Restore the REAL find_existing (default _build stubs it to None).
    monkeypatch.setattr(main_mod.library, "find_existing", _REAL_FIND_EXISTING)
    # Existing file reads as 320; the upload tag reads as 320 (equal -> reject).
    monkeypatch.setattr(main_mod.library, "read_bitrate", lambda p: 320)
    monkeypatch.setattr(main_mod.library, "read_mp3_bitrate", lambda p: 320)
    # slskd must never be searched if the library-first reject fires.
    called = []
    monkeypatch.setattr(main_mod.slsk, "find_candidates",
                        lambda *a, **k: called.append(1) or [])
    r = _upload(client, filename="HPRoos - Das Isch mini Schwiiz (320 Kbps).mp3",
                artist="HPRoos",
                title="Das Isch mini Schwiiz (Melodic Techno Version) - (320 Kbps)")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["already"] is True
    assert "token" not in body and "needs_confirm" not in body
    assert not called  # slskd never searched
    assert _manifest(charts_dir)["entries"] == {}  # nothing written


# --- Upsampling / real-quality upload path ---------------------------------
# The FFT verdict (_upload_upsampled) and slskd are mocked; no real ffmpeg/numpy
# decode and no network.

def test_upload_upsampled_triggers_search(monkeypatch, tmp_path):
    # Tag claims 320 but the real cutoff is low -> upsampled. slskd delivers a
    # genuinely better copy -> that download is filed, the uploaded bytes go.
    client, main_mod, charts_dir, staging_dir = _build(monkeypatch, tmp_path)
    monkeypatch.setattr(main_mod.library, "read_mp3_bitrate", lambda p: 320)
    monkeypatch.setattr(main_mod, "_upload_upsampled",
                        lambda *a, **k: (True, 16.0, 19.0))
    monkeypatch.setattr(main_mod.slsk, "connect", lambda: object())
    cand = ("Peer", {"filename": "best.mp3", "bitRate": 320}, 95)
    monkeypatch.setattr(main_mod.slsk, "find_candidates", lambda *a, **k: [cand])
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
    assert all(e.get("source") != "upload"
               for e in _manifest(charts_dir)["entries"].values())
    # Staged bytes removed, nothing in /charts from the upload.
    assert not list(staging_dir.glob("*.mp3")) if staging_dir.exists() else True


def test_upload_upsampled_nothing_better_needs_confirm(monkeypatch, tmp_path):
    # Upsampled tag 320, but slskd has nothing better -> honest confirm prompt
    # naming BOTH the tag bitrate and the measured cutoff; file stays staged.
    client, main_mod, charts_dir, staging_dir = _build(monkeypatch, tmp_path)
    monkeypatch.setattr(main_mod.library, "read_mp3_bitrate", lambda p: 320)
    monkeypatch.setattr(main_mod, "_upload_upsampled",
                        lambda *a, **k: (True, 16.0, 19.0))
    monkeypatch.setattr(main_mod.slsk, "connect", lambda: object())
    monkeypatch.setattr(main_mod.slsk, "find_candidates", lambda *a, **k: [])
    monkeypatch.setattr(main_mod, "_download_best", lambda *a, **k: None)
    r = _upload(client, artist="Artist", title="Title")
    assert r.status_code == 200
    body = r.json()
    assert body["needs_confirm"] is True
    assert body["bitrate"] == 320
    # Honest message mentions both the claimed bitrate and the measured cutoff.
    assert "320" in body["message"]
    assert "16" in body["message"]
    token = body["token"]
    assert (staging_dir / f"{token}.mp3").exists()
    assert not (charts_dir / "Artist - Title.mp3").exists()
    assert _manifest(charts_dir)["entries"] == {}
    assert main_mod._pending_uploads[token]["reason"] == "upsampled"


def test_upload_upsampled_confirm_sets_accept_anyway(monkeypatch, tmp_path):
    # Confirming an upsampled pending upload files it WITH accept_anyway=true.
    client, main_mod, charts_dir, staging_dir = _build(monkeypatch, tmp_path)
    monkeypatch.setattr(main_mod.library, "read_mp3_bitrate", lambda p: 320)
    monkeypatch.setattr(main_mod, "_upload_upsampled",
                        lambda *a, **k: (True, 16.0, 19.0))
    monkeypatch.setattr(main_mod.slsk, "connect", lambda: object())
    monkeypatch.setattr(main_mod.slsk, "find_candidates", lambda *a, **k: [])
    monkeypatch.setattr(main_mod, "_download_best", lambda *a, **k: None)
    token = _upload(client, artist="Artist", title="Title").json()["token"]
    r = client.post("/api/upload/confirm", data={
        "token": token, "artist": "Artist", "title": "Title"})
    assert r.status_code == 200
    assert r.json()["accepted"] is True
    assert (charts_dir / "Artist - Title.mp3").exists()
    entry = _manifest(charts_dir)["entries"]["artist - title.mp3"]
    assert entry["source"] == "upload"
    assert entry["accept_anyway"] is True


def test_upload_good_not_upsampled_accepts(monkeypatch, tmp_path):
    # Good tag AND not upsampled -> straight accept, no slskd, no accept_anyway.
    client, main_mod, charts_dir, _sd = _build(monkeypatch, tmp_path)
    monkeypatch.setattr(main_mod.library, "read_mp3_bitrate", lambda p: 300)
    monkeypatch.setattr(main_mod, "_upload_upsampled",
                        lambda *a, **k: (False, 20.0, 18.0))
    called = []
    monkeypatch.setattr(main_mod.slsk, "find_candidates",
                        lambda *a, **k: called.append(1) or [])
    r = _upload(client, artist="Artist", title="Title")
    assert r.status_code == 200
    assert r.json()["accepted"] is True
    assert not called  # slskd never searched
    entry = _manifest(charts_dir)["entries"]["artist - title.mp3"]
    assert entry["source"] == "upload"
    assert "accept_anyway" not in entry


def test_upload_sub256_confirm_no_accept_anyway(monkeypatch, tmp_path):
    # The existing sub-256 confirm path must NOT set accept_anyway (regression).
    client, main_mod, charts_dir, staging_dir, token = _needs_confirm(
        monkeypatch, tmp_path)
    r = client.post("/api/upload/confirm", data={
        "token": token, "artist": "Artist", "title": "Title"})
    assert r.status_code == 200
    entry = _manifest(charts_dir)["entries"]["artist - title.mp3"]
    assert entry["source"] == "upload"
    assert "accept_anyway" not in entry


# --- Early library check on the good-quality direct-accept branch ----------

def test_upload_already_present_equal_or_better_not_staged(monkeypatch, tmp_path):
    # Good tag + not upsampled, but the library already has an equal-or-better
    # copy -> do NOT accept/stage; report already:true, no manifest entry.
    client, main_mod, charts_dir, staging_dir = _build(monkeypatch, tmp_path)
    monkeypatch.setattr(main_mod.library, "read_mp3_bitrate", lambda p: 256)
    monkeypatch.setattr(main_mod, "_upload_upsampled",
                        lambda *a, **k: (False, 20.0, 18.0))
    monkeypatch.setattr(main_mod.library, "find_existing",
                        lambda *a, **k: "/music/Music/Artist/Album/old.mp3")
    monkeypatch.setattr(main_mod.library, "read_bitrate", lambda p: 320)
    called = []
    monkeypatch.setattr(main_mod.slsk, "find_candidates",
                        lambda *a, **k: called.append(1) or [])
    r = _upload(client, artist="Artist", title="Title")
    assert r.status_code == 200
    body = r.json()
    assert body.get("already") is True
    assert body.get("accepted") is not True
    assert body["bitrate"] == 320
    # slskd not searched, nothing staged, no manifest entry, nothing in /charts.
    assert not called
    assert not (charts_dir / "Artist - Title.mp3").exists()
    assert _manifest(charts_dir)["entries"] == {}
    assert not list(staging_dir.glob("*.mp3")) if staging_dir.exists() else True


def test_upload_better_than_existing_proceeds(monkeypatch, tmp_path):
    # Good tag + not upsampled + a worse existing copy -> the upload is a
    # genuine upgrade and is accepted (with the replace flag).
    client, main_mod, charts_dir, _sd = _build(monkeypatch, tmp_path)
    monkeypatch.setattr(main_mod.library, "read_mp3_bitrate", lambda p: 320)
    monkeypatch.setattr(main_mod, "_upload_upsampled",
                        lambda *a, **k: (False, 20.0, 18.0))
    monkeypatch.setattr(main_mod.library, "find_existing",
                        lambda *a, **k: "/music/Music/Artist/Album/old.mp3")
    monkeypatch.setattr(main_mod.library, "read_bitrate", lambda p: 192)
    r = _upload(client, artist="Artist", title="Title")
    assert r.status_code == 200
    assert r.json()["accepted"] is True
    entry = _manifest(charts_dir)["entries"]["artist - title.mp3"]
    assert entry["source"] == "upload"
    assert entry.get("replace") is True


# --- Library-first reject now authoritative for UPSAMPLED uploads too ------
# FIX A: the already-present reject runs BEFORE the upsampling/quality branch,
# so an upsampled duplicate is rejected at the same point a good-quality one is
# and never reaches the slskd-search/confirm path.

def test_upload_upsampled_already_present_rejected(monkeypatch, tmp_path):
    # The live SK.music bug: the upload's TAG bitrate (320) EXCEEDS the honest
    # existing copy (257), but the upload is UPSAMPLED. The old predicate
    # (measured <= existing) let the fake-320 beat the honest 257 and proceed;
    # the new rule rejects it BEFORE slskd because an upsampled file is never a
    # genuine upgrade over a decent existing copy. Nothing staged/filed, confirm
    # path never reached.
    client, main_mod, charts_dir, staging_dir = _build(monkeypatch, tmp_path)
    monkeypatch.setattr(main_mod.library, "read_mp3_bitrate", lambda p: 320)
    monkeypatch.setattr(main_mod, "_upload_upsampled",
                        lambda *a, **k: (True, 16.0, 19.0))
    monkeypatch.setattr(main_mod.library, "find_existing",
                        lambda *a, **k: "/music/Music/Artist/Album/old.mp3")
    monkeypatch.setattr(main_mod.library, "read_bitrate", lambda p: 257)
    called = []
    monkeypatch.setattr(main_mod.slsk, "find_candidates",
                        lambda *a, **k: called.append(1) or [])
    r = _upload(client, artist="Artist", title="Title")
    assert r.status_code == 200
    body = r.json()
    assert body.get("already") is True
    assert body.get("accepted") is not True
    assert body.get("needs_confirm") is not True
    assert body["bitrate"] == 257  # the EXISTING copy's honest bitrate
    # Honest "good quality" message (257 >= MIN_BITRATE).
    assert body["message"] == i18n.t("songrequest.ui.good_quality", bitrate=257)
    # slskd not searched, nothing staged, no manifest, nothing in /charts,
    # nothing left pending for a confirm round-trip.
    assert not called
    assert not (charts_dir / "Artist - Title.mp3").exists()
    assert _manifest(charts_dir)["entries"] == {}
    assert not list(staging_dir.glob("*.mp3")) if staging_dir.exists() else True
    assert main_mod._pending_uploads == {}


def test_upload_non_upsampled_better_than_decent_proceeds(monkeypatch, tmp_path):
    # Existing decent copy (257), upload tag 320 and NOT upsampled -> a genuine
    # upgrade: it is accepted straight away (not rejected), with the replace flag.
    client, main_mod, charts_dir, _sd = _build(monkeypatch, tmp_path)
    monkeypatch.setattr(main_mod.library, "read_mp3_bitrate", lambda p: 320)
    monkeypatch.setattr(main_mod, "_upload_upsampled",
                        lambda *a, **k: (False, 20.0, 18.0))
    monkeypatch.setattr(main_mod.library, "find_existing",
                        lambda *a, **k: "/music/Music/Artist/Album/old.mp3")
    monkeypatch.setattr(main_mod.library, "read_bitrate", lambda p: 257)
    r = _upload(client, artist="Artist", title="Title")
    assert r.status_code == 200
    assert r.json()["accepted"] is True
    entry = _manifest(charts_dir)["entries"]["artist - title.mp3"]
    assert entry["source"] == "upload"
    assert entry.get("replace") is True


def test_upload_poor_existing_non_upsampled_upgrades(monkeypatch, tmp_path):
    # Existing copy is POOR (200 < MIN_BITRATE), upload tag 320 and NOT upsampled
    # -> the always-reject must NOT fire (poor copies may be improved); the
    # upload is accepted as a genuine upgrade with the replace flag.
    client, main_mod, charts_dir, _sd = _build(monkeypatch, tmp_path)
    monkeypatch.setattr(main_mod.library, "read_mp3_bitrate", lambda p: 320)
    monkeypatch.setattr(main_mod, "_upload_upsampled",
                        lambda *a, **k: (False, 20.0, 18.0))
    monkeypatch.setattr(main_mod.library, "find_existing",
                        lambda *a, **k: "/music/Music/Artist/Album/old.mp3")
    monkeypatch.setattr(main_mod.library, "read_bitrate", lambda p: 200)
    r = _upload(client, artist="Artist", title="Title")
    assert r.status_code == 200
    assert r.json()["accepted"] is True
    entry = _manifest(charts_dir)["entries"]["artist - title.mp3"]
    assert entry["source"] == "upload"
    assert entry.get("replace") is True


def test_upload_no_existing_upsampled_goes_to_confirm(monkeypatch, tmp_path):
    # No existing copy at all + an upsampled upload -> unchanged behaviour: it
    # still runs the slskd search and (nothing better) reaches the confirm path.
    client, main_mod, charts_dir, staging_dir = _build(monkeypatch, tmp_path)
    monkeypatch.setattr(main_mod.library, "read_mp3_bitrate", lambda p: 320)
    monkeypatch.setattr(main_mod, "_upload_upsampled",
                        lambda *a, **k: (True, 16.0, 19.0))
    # _build already stubs find_existing -> None (no existing copy).
    monkeypatch.setattr(main_mod.slsk, "connect", lambda: object())
    monkeypatch.setattr(main_mod.slsk, "find_candidates", lambda *a, **k: [])
    monkeypatch.setattr(main_mod, "_download_best", lambda *a, **k: None)
    r = _upload(client, artist="Artist", title="Title")
    assert r.status_code == 200
    body = r.json()
    assert body["needs_confirm"] is True
    assert body["bitrate"] == 320
    token = body["token"]
    assert (staging_dir / f"{token}.mp3").exists()
    assert main_mod._pending_uploads[token]["reason"] == "upsampled"


def test_upload_upsampled_better_still_proceeds_to_confirm(monkeypatch, tmp_path):
    # Tag 320 but upsampled, existing copy is POOR (192 < MIN_BITRATE) -> the
    # always-reject does NOT fire; it reaches the slskd search and (nothing
    # better online) the confirm path, carrying the "upsampled" reason. Upsampled
    # never counts as the genuine upgrade, so it is NOT straight-accepted even
    # over a poor copy.
    client, main_mod, charts_dir, staging_dir = _build(monkeypatch, tmp_path)
    monkeypatch.setattr(main_mod.library, "read_mp3_bitrate", lambda p: 320)
    monkeypatch.setattr(main_mod, "_upload_upsampled",
                        lambda *a, **k: (True, 16.0, 19.0))
    monkeypatch.setattr(main_mod.library, "find_existing",
                        lambda *a, **k: "/music/Music/Artist/Album/old.mp3")
    monkeypatch.setattr(main_mod.library, "read_bitrate", lambda p: 192)
    monkeypatch.setattr(main_mod.slsk, "connect", lambda: object())
    monkeypatch.setattr(main_mod.slsk, "find_candidates", lambda *a, **k: [])
    monkeypatch.setattr(main_mod, "_download_best", lambda *a, **k: None)
    r = _upload(client, artist="Artist", title="Title")
    assert r.status_code == 200
    body = r.json()
    assert body["needs_confirm"] is True
    assert body["bitrate"] == 320
    token = body["token"]
    assert (staging_dir / f"{token}.mp3").exists()
    assert main_mod._pending_uploads[token]["reason"] == "upsampled"


def test_upload_confirm_rejects_now_duplicate(monkeypatch, tmp_path):
    # A sub-256 upload with nothing better is pending a confirm; before the
    # confirm POST the library gains an equal-or-better copy -> confirm rejects
    # it as already present instead of filing a duplicate.
    client, main_mod, charts_dir, staging_dir, token = _needs_confirm(
        monkeypatch, tmp_path)
    monkeypatch.setattr(main_mod.library, "find_existing",
                        lambda *a, **k: "/music/Music/Artist/Album/old.mp3")
    monkeypatch.setattr(main_mod.library, "read_bitrate", lambda p: 320)
    r = client.post("/api/upload/confirm", data={
        "token": token, "artist": "Artist", "title": "Title"})
    assert r.status_code == 200
    body = r.json()
    assert body.get("already") is True
    assert body.get("accepted") is not True
    assert body["bitrate"] == 320
    # Nothing filed, no manifest entry, staged bytes gone, token popped.
    assert not (charts_dir / "Artist - Title.mp3").exists()
    assert _manifest(charts_dir)["entries"] == {}
    assert not (staging_dir / f"{token}.mp3").exists()
    assert token not in main_mod._pending_uploads


def test_upload_confirm_rejects_upsampled_over_decent(monkeypatch, tmp_path):
    # An UPSAMPLED upload (tag 320) reaches the confirm stage with no existing
    # copy; by confirm time a DECENT copy (257) is present. The confirm must
    # reject it as already-present instead of filing it with accept_anyway -
    # closing the "add it anyway" route for a song now present in decent quality.
    client, main_mod, charts_dir, staging_dir = _build(monkeypatch, tmp_path)
    monkeypatch.setattr(main_mod.library, "read_mp3_bitrate", lambda p: 320)
    monkeypatch.setattr(main_mod, "_upload_upsampled",
                        lambda *a, **k: (True, 16.0, 19.0))
    monkeypatch.setattr(main_mod.slsk, "connect", lambda: object())
    monkeypatch.setattr(main_mod.slsk, "find_candidates", lambda *a, **k: [])
    monkeypatch.setattr(main_mod, "_download_best", lambda *a, **k: None)
    # Upload with NO existing copy -> upsampled nothing-better -> confirm pending.
    body = _upload(client, artist="Artist", title="Title").json()
    assert body["needs_confirm"] is True
    token = body["token"]
    assert main_mod._pending_uploads[token]["reason"] == "upsampled"
    # Now a decent copy appears before the confirm POST.
    monkeypatch.setattr(main_mod.library, "find_existing",
                        lambda *a, **k: "/music/Music/Artist/Album/old.mp3")
    monkeypatch.setattr(main_mod.library, "read_bitrate", lambda p: 257)
    r = client.post("/api/upload/confirm", data={
        "token": token, "artist": "Artist", "title": "Title"})
    assert r.status_code == 200
    rbody = r.json()
    assert rbody.get("already") is True
    assert rbody.get("accepted") is not True
    assert rbody["bitrate"] == 257
    # Nothing filed, no accept_anyway, staged bytes gone, token popped.
    assert not (charts_dir / "Artist - Title.mp3").exists()
    assert _manifest(charts_dir)["entries"] == {}
    assert not (staging_dir / f"{token}.mp3").exists()
    assert token not in main_mod._pending_uploads


# --- manifest.record flag -------------------------------------------------

def test_record_accept_anyway(monkeypatch, tmp_path):
    from app import manifest as manifest_mod
    manifest_file = str(tmp_path / ".charts-manifest.json")
    monkeypatch.setattr(manifest_mod.config, "MANIFEST_FILE", manifest_file)
    manifest_mod.record("a.mp3", "Artist", "Title", source="upload",
                        accept_anyway=True)
    manifest_mod.record("b.mp3", "Artist", "Title", source="upload",
                        accept_anyway=False)
    data = json.loads(open(manifest_file, encoding="utf-8").read())
    assert data["entries"]["a.mp3"]["accept_anyway"] is True
    assert "accept_anyway" not in data["entries"]["b.mp3"]


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
