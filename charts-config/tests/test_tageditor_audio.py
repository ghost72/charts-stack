"""
Tests for the Manual Tag Editor's in-browser play + download feature.

Covers the three security gates (auth -> TAGEDITOR_AUDIO_ENABLED flag ->
_guarded_realpath path guard + extension allowlist), the Range-aware streaming
(200 / 206 / 416 / 404 cases), the read-only file/bitrate payload fields, and
the play/download availability flags. All WITHOUT network/Docker (a tmp MP3 and
a tmp FLAC inside a tmp MUSIC_DIR).

Pinned (matching the design's acceptance criteria):
  * flag OFF + authed -> 403 audio_disabled; song payload audio_enabled False;
  * flag ON  + anon   -> 401 (auth is checked BEFORE the flag);
  * flag ON  + authed + traversal/absolute-outside path -> 400 path_outside;
  * flag ON  + authed + valid mp3 -> 200 audio/mpeg + Accept-Ranges; a
    Range: bytes=0-99 -> 206, 100-byte body, Content-Length 100, Content-Range;
  * flag ON  + authed + missing in-library mp3 -> 404 not_found;
  * flag ON  + authed + unsatisfiable Range -> 416, Content-Range */SIZE,
    Content-Length 0, empty body;
  * flag ON  + authed + suffix Range bytes=-N (N>=size) -> 206 whole file;
    a malformed Range -> 200 full body;
  * flag ON  + authed + .flac -> payload playable False; ?download=1 -> 200 with
    Content-Disposition: attachment;
  * the bitrate/format reader returns kbps for mp3 and a lossless marker for flac.
"""

import importlib
import pathlib

import pytest
from fastapi.testclient import TestClient

from app import i18n


_LANG_DIR = pathlib.Path(i18n.__file__).resolve().parent.parent.parent / "lang"


def _write_dummy_mp3(path: str) -> None:
    header = b"\xff\xfb\x90\x40"
    frame = header + b"\x00" * (417 - 4)
    with open(path, "wb") as fh:
        fh.write(frame * 30)


def _reload_app(monkeypatch, music_dir, manifest_file, audio_enabled):
    """Reload config + editor + library + main with the chosen flag + MUSIC_DIR."""
    monkeypatch.setenv("CONFIG_PASSWORD", "secret")
    monkeypatch.setenv("CONFIG_USERNAME", "admin")
    monkeypatch.setenv("CHARTS_LANG", "en")
    monkeypatch.setenv("MUSIC_DIR", str(music_dir))
    monkeypatch.setenv("MANIFEST_FILE", str(manifest_file))
    monkeypatch.setenv("TAGEDITOR_AUDIO_ENABLED", "1" if audio_enabled else "0")
    import app.config as config_mod
    importlib.reload(config_mod)
    i18n.setup(lang="en", lang_dir=_LANG_DIR)
    import app.editor as editor_mod
    importlib.reload(editor_mod)
    import app.library as library_mod
    importlib.reload(library_mod)
    import app.audio as audio_mod
    importlib.reload(audio_mod)
    import app.main as main_mod
    importlib.reload(main_mod)
    return main_mod


@pytest.fixture
def library_dir(tmp_path):
    """A tmp MUSIC_DIR holding one MP3 and one FLAC; returns (root, mp3, flac)."""
    root = tmp_path / "Music"
    (root / "Artist" / "Album").mkdir(parents=True)
    mp3 = root / "Artist" / "Album" / "track.mp3"
    _write_dummy_mp3(str(mp3))
    flac = root / "Artist" / "Album" / "song.flac"
    flac.write_bytes(b"\x00" * 5000)  # not a real FLAC; only served, not decoded
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    return root, str(mp3), str(flac), manifest


def _authed_client(main_mod):
    main_mod._sessions["tok"] = "admin"
    client = TestClient(main_mod.app)
    client.cookies.set("session", "tok")
    return client


# --- flag OFF (default, fail-closed) ----------------------------------------

def test_flag_off_audio_returns_403_even_authed(library_dir, monkeypatch):
    root, mp3, _flac, manifest = library_dir
    main_mod = _reload_app(monkeypatch, root, manifest, audio_enabled=False)
    client = _authed_client(main_mod)
    r = client.get("/api/tageditor/audio", params={"path": mp3})
    assert r.status_code == 403
    assert r.json()["error"] == i18n.t("config.tageditor.audio_disabled")


def test_flag_off_song_payload_reports_disabled(library_dir, monkeypatch):
    root, mp3, _flac, manifest = library_dir
    main_mod = _reload_app(monkeypatch, root, manifest, audio_enabled=False)
    client = _authed_client(main_mod)
    song = client.get("/api/tageditor/song", params={"path": mp3}).json()["song"]
    assert song["audio_enabled"] is False
    assert song["playable"] is False


# --- auth is checked BEFORE the flag ----------------------------------------

def test_flag_on_anon_returns_401(library_dir, monkeypatch):
    root, mp3, _flac, manifest = library_dir
    main_mod = _reload_app(monkeypatch, root, manifest, audio_enabled=True)
    anon = TestClient(main_mod.app)
    r = anon.get("/api/tageditor/audio", params={"path": mp3})
    assert r.status_code == 401
    assert r.json()["error"] == i18n.t("config.api.not_authed")


# --- path guard (reuses _guarded_realpath) ----------------------------------

@pytest.mark.parametrize("bad", ["/etc/passwd", "../../../../etc/passwd"])
def test_flag_on_traversal_path_400(library_dir, monkeypatch, bad):
    root, _mp3, _flac, manifest = library_dir
    main_mod = _reload_app(monkeypatch, root, manifest, audio_enabled=True)
    client = _authed_client(main_mod)
    r = client.get("/api/tageditor/audio", params={"path": bad})
    assert r.status_code == 400
    assert r.json()["error"] == i18n.t("config.tageditor.path_outside")


def test_flag_on_missing_file_404(library_dir, monkeypatch):
    root, _mp3, _flac, manifest = library_dir
    main_mod = _reload_app(monkeypatch, root, manifest, audio_enabled=True)
    client = _authed_client(main_mod)
    gone = str(root / "Artist" / "Album" / "gone.mp3")
    r = client.get("/api/tageditor/audio", params={"path": gone})
    assert r.status_code == 404
    assert r.json()["error"] == i18n.t("config.tageditor.not_found")


def test_flag_on_bad_extension_400(library_dir, monkeypatch):
    root, _mp3, _flac, manifest = library_dir
    txt = root / "Artist" / "Album" / "note.txt"
    txt.write_text("hello", encoding="utf-8")
    main_mod = _reload_app(monkeypatch, root, manifest, audio_enabled=True)
    client = _authed_client(main_mod)
    r = client.get("/api/tageditor/audio", params={"path": str(txt)})
    assert r.status_code == 400
    assert r.json()["error"] == i18n.t("config.tageditor.bad_audio_ext")


# --- streaming (200 / 206 / 416) --------------------------------------------

def test_flag_on_mp3_full_stream_200(library_dir, monkeypatch):
    root, mp3, _flac, manifest = library_dir
    main_mod = _reload_app(monkeypatch, root, manifest, audio_enabled=True)
    client = _authed_client(main_mod)
    r = client.get("/api/tageditor/audio", params={"path": mp3})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("audio/mpeg")
    assert r.headers["accept-ranges"] == "bytes"
    size = pathlib.Path(mp3).stat().st_size
    assert int(r.headers["content-length"]) == size
    assert len(r.content) == size


def test_flag_on_mp3_range_206(library_dir, monkeypatch):
    root, mp3, _flac, manifest = library_dir
    main_mod = _reload_app(monkeypatch, root, manifest, audio_enabled=True)
    client = _authed_client(main_mod)
    size = pathlib.Path(mp3).stat().st_size
    r = client.get("/api/tageditor/audio", params={"path": mp3},
                   headers={"Range": "bytes=0-99"})
    assert r.status_code == 206
    assert r.headers["accept-ranges"] == "bytes"
    assert r.headers["content-range"] == f"bytes 0-99/{size}"
    assert int(r.headers["content-length"]) == 100
    assert len(r.content) == 100


def test_flag_on_unsatisfiable_range_416(library_dir, monkeypatch):
    root, mp3, _flac, manifest = library_dir
    main_mod = _reload_app(monkeypatch, root, manifest, audio_enabled=True)
    client = _authed_client(main_mod)
    size = pathlib.Path(mp3).stat().st_size
    for rng in (f"bytes={size}-", "bytes=-0"):
        r = client.get("/api/tageditor/audio", params={"path": mp3},
                       headers={"Range": rng})
        assert r.status_code == 416, rng
        assert r.headers["content-range"] == f"bytes */{size}", rng
        assert r.headers["content-length"] == "0", rng
        assert r.content == b"", rng


def test_flag_on_suffix_range_whole_file_206(library_dir, monkeypatch):
    root, mp3, _flac, manifest = library_dir
    main_mod = _reload_app(monkeypatch, root, manifest, audio_enabled=True)
    client = _authed_client(main_mod)
    size = pathlib.Path(mp3).stat().st_size
    # bytes=-N with N >= size -> 206 serving the whole file.
    r = client.get("/api/tageditor/audio", params={"path": mp3},
                   headers={"Range": f"bytes=-{size + 10}"})
    assert r.status_code == 206
    assert r.headers["content-range"] == f"bytes 0-{size - 1}/{size}"
    assert len(r.content) == size


def test_flag_on_malformed_range_200(library_dir, monkeypatch):
    root, mp3, _flac, manifest = library_dir
    main_mod = _reload_app(monkeypatch, root, manifest, audio_enabled=True)
    client = _authed_client(main_mod)
    size = pathlib.Path(mp3).stat().st_size
    r = client.get("/api/tageditor/audio", params={"path": mp3},
                   headers={"Range": "bogus-header"})
    assert r.status_code == 200
    assert len(r.content) == size


# --- FLAC: not playable but downloadable ------------------------------------

def test_flac_payload_not_playable(library_dir, monkeypatch):
    root, _mp3, flac, manifest = library_dir
    main_mod = _reload_app(monkeypatch, root, manifest, audio_enabled=True)
    client = _authed_client(main_mod)
    song = client.get("/api/tageditor/song", params={"path": flac}).json()["song"]
    assert song["audio_enabled"] is True
    assert song["playable"] is False  # FLAC: browsers may not decode it
    assert song["is_lossless"] is True


def test_flac_download_attachment(library_dir, monkeypatch):
    root, _mp3, flac, manifest = library_dir
    main_mod = _reload_app(monkeypatch, root, manifest, audio_enabled=True)
    client = _authed_client(main_mod)
    r = client.get("/api/tageditor/audio", params={"path": flac, "download": 1})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("audio/flac")
    assert "attachment" in r.headers["content-disposition"]
    assert 'filename="song.flac"' in r.headers["content-disposition"]


# --- read-only file/bitrate payload fields ----------------------------------

def test_song_payload_has_fileinfo(library_dir, monkeypatch):
    root, mp3, _flac, manifest = library_dir
    main_mod = _reload_app(monkeypatch, root, manifest, audio_enabled=True)
    client = _authed_client(main_mod)
    song = client.get("/api/tageditor/song", params={"path": mp3}).json()["song"]
    assert song["rel_path"] == "Artist/Album/track.mp3"
    assert isinstance(song["bitrate_kbps"], int) and song["bitrate_kbps"] > 0
    assert song["ext"] == ".mp3"
    assert song["is_lossless"] is False


# --- unit: read_audio_info --------------------------------------------------

def test_read_audio_info_mp3_and_flac(library_dir, monkeypatch):
    root, mp3, flac, manifest = library_dir
    _reload_app(monkeypatch, root, manifest, audio_enabled=True)
    import app.library as library_mod
    mp3_info = library_mod.read_audio_info(mp3)
    assert mp3_info["is_mp3"] is True and mp3_info["playable"] is True
    assert mp3_info["bitrate_kbps"] > 0 and mp3_info["is_lossless"] is False
    flac_info = library_mod.read_audio_info(flac)
    assert flac_info["is_mp3"] is False and flac_info["playable"] is False
    assert flac_info["is_lossless"] is True


def test_read_audio_info_fail_open_on_garbage(library_dir, monkeypatch):
    root, _mp3, _flac, manifest = library_dir
    _reload_app(monkeypatch, root, manifest, audio_enabled=True)
    import app.library as library_mod
    info = library_mod.read_audio_info("/no/such/file.mp3")
    assert info["ext"] == ".mp3" and info["is_mp3"] is True
    assert info["bitrate_kbps"] == 0  # unreadable -> 0, no raise


# --- unit: _rel_to_music fails closed to basename for an outside path -------

def test_rel_to_music_outside_is_basename_only(library_dir, monkeypatch):
    root, _mp3, _flac, manifest = library_dir
    _reload_app(monkeypatch, root, manifest, audio_enabled=True)
    import app.editor as editor_mod
    rel = editor_mod._rel_to_music("/etc/passwd")
    assert rel == "passwd"  # basename only, never a leading path


# Restore import-time i18n state so test ordering never leaks a language.
@pytest.fixture(autouse=True)
def _restore_setup():
    yield
    i18n.setup()
