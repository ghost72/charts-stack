"""
Tests for the Manual Tag Editor's full-resolution cover-download endpoint
(GET /api/tageditor/cover).

Covers the auth gate, the path-traversal guard, the audio-extension allowlist,
the raw-bytes + content-type + attachment-disposition contract for an mp3(APIC)
and a flac(picture) [and an m4a `covr` shape via a mutagen stub], the no-cover
404, and the key invariant that the response is NOT subject to
TAGEDITOR_COVER_MAX_BYTES: a cover LARGER than the cap still downloads in full
(the cap only suppresses the inline preview). All WITHOUT network/Docker.
"""

import importlib
import pathlib

import pytest
from fastapi.testclient import TestClient

from app import i18n


_LANG_DIR = pathlib.Path(i18n.__file__).resolve().parent.parent.parent / "lang"

# A tiny JPEG (magic bytes so _sniff_image_mime returns image/jpeg) and PNG.
_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"\xff\xd9"
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _write_min_mp3(path: str) -> None:
    header = b"\xff\xfb\x90\x40"
    frame = header + b"\x00" * (417 - 4)
    with open(path, "wb") as fh:
        fh.write(frame * 30)


def _write_mp3_with_apic(path: str, data: bytes, mime: str = "image/jpeg") -> None:
    from mutagen.id3 import ID3, APIC
    _write_min_mp3(path)
    tags = ID3()
    tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=data))
    tags.save(path)


def _write_min_flac(path: str) -> None:
    min_block = (4096).to_bytes(2, "big")
    max_block = (4096).to_bytes(2, "big")
    min_frame = (0).to_bytes(3, "big")
    max_frame = (0).to_bytes(3, "big")
    packed = (44100 << 44) | ((2 - 1) << 41) | ((16 - 1) << 36) | 0
    streaminfo = (min_block + max_block + min_frame + max_frame
                  + packed.to_bytes(8, "big") + bytes(16))
    header = b"fLaC" + bytes([0x80]) + len(streaminfo).to_bytes(3, "big")
    with open(path, "wb") as fh:
        fh.write(header + streaminfo)


def _write_flac_with_picture(path: str, data: bytes) -> None:
    from mutagen.flac import FLAC, Picture
    _write_min_flac(path)
    fl = FLAC(path)
    pic = Picture()
    pic.type = 3
    pic.mime = "image/jpeg"
    pic.data = data
    fl.add_picture(pic)
    fl.save()


def _reload_app(monkeypatch, music_dir, manifest_file):
    monkeypatch.setenv("CONFIG_PASSWORD", "secret")
    monkeypatch.setenv("CONFIG_USERNAME", "admin")
    monkeypatch.setenv("CHARTS_LANG", "en")
    monkeypatch.setenv("MUSIC_DIR", str(music_dir))
    monkeypatch.setenv("MANIFEST_FILE", str(manifest_file))
    # Deliberately leave TAGEDITOR_AUDIO_ENABLED OFF to prove the cover endpoint
    # is NOT gated by that flag (unlike the audio stream/download endpoint).
    monkeypatch.setenv("TAGEDITOR_AUDIO_ENABLED", "0")
    import app.config as config_mod
    importlib.reload(config_mod)
    i18n.setup(lang="en", lang_dir=_LANG_DIR)
    import app.editor as editor_mod
    importlib.reload(editor_mod)
    import app.library as library_mod
    importlib.reload(library_mod)
    import app.main as main_mod
    importlib.reload(main_mod)
    return main_mod


@pytest.fixture
def library_dir(tmp_path):
    root = tmp_path / "Music"
    (root / "Artist" / "Album").mkdir(parents=True)
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    return root, manifest


def _authed_client(main_mod):
    main_mod._sessions["tok"] = "admin"
    client = TestClient(main_mod.app)
    client.cookies.set("session", "tok")
    return client


# --- auth gate (401 anon, checked first) ------------------------------------

def test_cover_anon_returns_401(library_dir, monkeypatch):
    root, manifest = library_dir
    mp3 = root / "Artist" / "Album" / "track.mp3"
    _write_mp3_with_apic(str(mp3), _JPEG)
    main_mod = _reload_app(monkeypatch, root, manifest)
    anon = TestClient(main_mod.app)
    r = anon.get("/api/tageditor/cover", params={"path": str(mp3)})
    assert r.status_code == 401
    assert r.json()["error"] == i18n.t("config.api.not_authed")


# --- path-traversal guard ---------------------------------------------------

@pytest.mark.parametrize("bad", ["/etc/passwd", "../../../../etc/passwd"])
def test_cover_traversal_path_400(library_dir, monkeypatch, bad):
    root, manifest = library_dir
    main_mod = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    r = client.get("/api/tageditor/cover", params={"path": bad})
    assert r.status_code == 400
    assert r.json()["error"] == i18n.t("config.tageditor.path_outside")


# --- extension allowlist ----------------------------------------------------

def test_cover_bad_extension_400(library_dir, monkeypatch):
    root, manifest = library_dir
    txt = root / "Artist" / "Album" / "note.txt"
    txt.write_text("hello", encoding="utf-8")
    main_mod = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    r = client.get("/api/tageditor/cover", params={"path": str(txt)})
    assert r.status_code == 400
    assert r.json()["error"] == i18n.t("config.tageditor.bad_audio_ext")


def test_cover_missing_file_404(library_dir, monkeypatch):
    root, manifest = library_dir
    main_mod = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    gone = str(root / "Artist" / "Album" / "gone.mp3")
    r = client.get("/api/tageditor/cover", params={"path": gone})
    assert r.status_code == 404
    assert r.json()["error"] == i18n.t("config.tageditor.not_found")


# --- raw bytes + content-type + attachment disposition ----------------------

def test_cover_mp3_apic_raw_bytes_jpeg(library_dir, monkeypatch):
    root, manifest = library_dir
    mp3 = root / "Artist" / "Album" / "track.mp3"
    _write_mp3_with_apic(str(mp3), _JPEG)
    main_mod = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    r = client.get("/api/tageditor/cover", params={"path": str(mp3)})
    assert r.status_code == 200
    assert r.content == _JPEG  # raw original bytes, no re-encode
    assert r.headers["content-type"].startswith("image/jpeg")
    assert "attachment" in r.headers["content-disposition"]
    assert r.headers["content-disposition"].endswith('cover.jpg"')


def test_cover_mp3_apic_png_content_type(library_dir, monkeypatch):
    root, manifest = library_dir
    mp3 = root / "Artist" / "Album" / "track.mp3"
    _write_mp3_with_apic(str(mp3), _PNG, mime="image/png")
    main_mod = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    r = client.get("/api/tageditor/cover", params={"path": str(mp3)})
    assert r.status_code == 200
    assert r.content == _PNG
    assert r.headers["content-type"].startswith("image/png")
    assert r.headers["content-disposition"].endswith('cover.png"')


def test_cover_flac_picture_raw_bytes(library_dir, monkeypatch):
    root, manifest = library_dir
    flac = root / "Artist" / "Album" / "song.flac"
    _write_flac_with_picture(str(flac), _JPEG)
    main_mod = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    r = client.get("/api/tageditor/cover", params={"path": str(flac)})
    assert r.status_code == 200
    assert r.content == _JPEG
    assert r.headers["content-type"].startswith("image/jpeg")
    assert "attachment" in r.headers["content-disposition"]


def test_cover_m4a_covr_via_stub(library_dir, monkeypatch):
    """MP4 `covr` shape: a real .m4a container cannot be synthesised without an
    encoder, so stub cover.MutagenFile to return the exact mutagen shape and
    assert the endpoint hands back those raw bytes for an .m4a path."""
    root, manifest = library_dir
    m4a = root / "Artist" / "Album" / "track.m4a"
    m4a.write_bytes(b"\x00" * 128)  # existing regular file with an audio ext
    main_mod = _reload_app(monkeypatch, root, manifest)

    class _FakeTags(dict):
        pass

    class _FakeAudio:
        def __init__(self, tags):
            self.tags = tags

    tags = _FakeTags()
    tags["covr"] = [bytearray(_JPEG)]
    import app.cover as cover_mod
    monkeypatch.setattr(cover_mod, "MutagenFile",
                        lambda *a, **k: _FakeAudio(tags))

    client = _authed_client(main_mod)
    r = client.get("/api/tageditor/cover", params={"path": str(m4a)})
    assert r.status_code == 200
    assert r.content == _JPEG
    assert r.headers["content-type"].startswith("image/jpeg")


# --- no embedded cover -> 404 -----------------------------------------------

def test_cover_mp3_without_cover_404(library_dir, monkeypatch):
    root, manifest = library_dir
    mp3 = root / "Artist" / "Album" / "nocover.mp3"
    _write_min_mp3(str(mp3))
    main_mod = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    r = client.get("/api/tageditor/cover", params={"path": str(mp3)})
    assert r.status_code == 404
    assert r.json()["error"] == i18n.t("config.tageditor.not_found")


# --- the size cap does NOT apply to the download ----------------------------

def test_cover_over_cap_still_downloads_full(library_dir, monkeypatch):
    """A cover LARGER than TAGEDITOR_COVER_MAX_BYTES still downloads in full.

    The cap only suppresses the inline base64 preview (_cover_preview returns
    {present:true, oversize:true}); the download endpoint must return every
    byte. We shrink the cap to a few bytes so a tiny embedded JPEG is already
    'oversize', then assert the full bytes come back and the preview is flagged
    oversize for the same file (proving the two paths diverge as designed).
    """
    root, manifest = library_dir
    mp3 = root / "Artist" / "Album" / "big.mp3"
    _write_mp3_with_apic(str(mp3), _JPEG)
    main_mod = _reload_app(monkeypatch, root, manifest)
    # Force the cap below the embedded cover size on the already-loaded config.
    import app.config as config_mod
    monkeypatch.setattr(config_mod, "TAGEDITOR_COVER_MAX_BYTES", 4)

    client = _authed_client(main_mod)
    # Preview is suppressed (oversize) for this file under the tiny cap...
    song = client.get("/api/tageditor/song", params={"path": str(mp3)}).json()["song"]
    assert song["cover"]["present"] is True
    assert song["cover"].get("oversize") is True
    assert "data_url" not in song["cover"]
    # ...yet the download endpoint still returns the FULL original bytes.
    r = client.get("/api/tageditor/cover", params={"path": str(mp3)})
    assert r.status_code == 200
    assert r.content == _JPEG  # full bytes, cap did not truncate/reject
    assert len(r.content) > config_mod.TAGEDITOR_COVER_MAX_BYTES


# --- song payload drives the UI control visibility --------------------------

def test_song_payload_present_true_with_cover(library_dir, monkeypatch):
    root, manifest = library_dir
    mp3 = root / "Artist" / "Album" / "track.mp3"
    _write_mp3_with_apic(str(mp3), _JPEG)
    main_mod = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    song = client.get("/api/tageditor/song", params={"path": str(mp3)}).json()["song"]
    assert song["cover"]["present"] is True


def test_song_payload_present_false_without_cover(library_dir, monkeypatch):
    root, manifest = library_dir
    mp3 = root / "Artist" / "Album" / "nocover.mp3"
    _write_min_mp3(str(mp3))
    main_mod = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    song = client.get("/api/tageditor/song", params={"path": str(mp3)}).json()["song"]
    assert song["cover"]["present"] is False


# Restore import-time i18n state so test ordering never leaks a language.
@pytest.fixture(autouse=True)
def _restore_setup():
    yield
    i18n.setup()
