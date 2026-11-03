"""
Tests for the Manual Tag Editor's FLAC/non-MP3 -> MP3 320 kbps CBR convert
feature (Feature A).

No real transcode, no network, no Docker: the ffmpeg subprocess is MOCKED
(monkeypatch on convert.subprocess.run) to simulate success (writing a tiny
valid dummy MP3 to the temp path it is given) and failure. The tests assert
OUR orchestration logic - the guard order, the ffmpeg command line, the
trash-move/mtime/marker handling, and the fail-open ordering - never a real
codec.

Pinned (matching the design's acceptance criteria):
  * 401 anonymous;
  * path outside MUSIC_DIR (traversal + absolute) -> 400 path_outside;
  * an already-MP3 file -> 400 already_mp3 (subprocess NOT called);
  * a non-audio extension -> 400 bad_audio_ext;
  * a missing file -> 404 not_found;
  * target <name>.mp3 already exists -> 400 target_exists (subprocess NOT called);
  * happy path: ffmpeg argv carries -b:a 320k / libmp3lame / -map_metadata 0 /
    the source + a temp dest in the source folder; the final <name>.mp3 exists;
    the original .flac is GONE from the source folder and PRESENT under
    .charts-trash/flac2mp3-<today>/Artist/Album/song.flac (not deleted); the new
    MP3's mtime == the original's pre-convert mtime; markers cleared; the
    response song.path is the new MP3, is_mp3 True, converted True, trashed_to set;
  * tag + cover carryover (seeded source -> reloaded song shows tags + cover);
  * ffmpeg failure (rc 1 / FileNotFoundError / TimeoutExpired) leaves the
    original in place, creates NO final MP3, moves nothing to trash;
  * trash-move failure rolls back the just-created final MP3.
"""

import importlib
import os
import pathlib
import subprocess

import pytest
from fastapi.testclient import TestClient

from app import i18n


_LANG_DIR = pathlib.Path(i18n.__file__).resolve().parent.parent.parent / "lang"


def _reload_app(monkeypatch, music_dir, manifest_file):
    """Reload config + editor + library + convert + main with a tmp MUSIC_DIR.

    Convert is deliberately reloaded with TAGEDITOR_AUDIO_ENABLED OFF (the
    default), proving the server capability is auth-gated only, not flag-gated.
    """
    monkeypatch.setenv("CONFIG_PASSWORD", "secret")
    monkeypatch.setenv("CONFIG_USERNAME", "admin")
    monkeypatch.setenv("CHARTS_LANG", "en")
    monkeypatch.setenv("MUSIC_DIR", str(music_dir))
    monkeypatch.setenv("MANIFEST_FILE", str(manifest_file))
    monkeypatch.setenv("TAGEDITOR_AUDIO_ENABLED", "0")
    import app.config as config_mod
    importlib.reload(config_mod)
    i18n.setup(lang="en", lang_dir=_LANG_DIR)
    import app.editor as editor_mod
    importlib.reload(editor_mod)
    import app.library as library_mod
    importlib.reload(library_mod)
    import app.convert as convert_mod
    importlib.reload(convert_mod)
    import app.main as main_mod
    importlib.reload(main_mod)
    return main_mod, convert_mod


def _write_flac_with_tags(path: str, *, with_cover: bool = False) -> None:
    """Write a minimal real FLAC carrying artist/title/album (+ optional cover)."""
    from mutagen.flac import FLAC, Picture
    # A valid, tiny empty FLAC stream: mutagen can create one from scratch only
    # by opening an existing FLAC, so write the smallest known-good FLAC header.
    # Simplest robust route: use mutagen to build it via a raw FLAC file bytes.
    _write_min_flac(path)
    fl = FLAC(path)
    fl["artist"] = "Seed Artist"
    fl["title"] = "Seed Title"
    fl["album"] = "Seed Album"
    fl["date"] = "2021"
    fl["tracknumber"] = "3"
    if with_cover:
        pic = Picture()
        pic.type = 3
        pic.mime = "image/jpeg"
        pic.data = b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"\xff\xd9"
        fl.add_picture(pic)
    fl.save()


def _write_min_flac(path: str) -> None:
    """Write a minimal valid FLAC file (fLaC marker + a STREAMINFO block).

    The STREAMINFO must carry a non-zero sample rate or mutagen refuses it. The
    packed tail after the framesize bytes is: 20 bits sample_rate, 3 bits
    (channels-1), 5 bits (bps-1), 36 bits total_samples. We set 44100 Hz, stereo,
    16-bit, 0 samples.
    """
    min_block = (4096).to_bytes(2, "big")
    max_block = (4096).to_bytes(2, "big")
    min_frame = (0).to_bytes(3, "big")
    max_frame = (0).to_bytes(3, "big")
    sample_rate = 44100
    channels = 2
    bps = 16
    total = 0
    packed = (sample_rate << 44) | ((channels - 1) << 41) | ((bps - 1) << 36) | total
    packed_bytes = packed.to_bytes(8, "big")  # 20+3+5+36 = 64 bits
    md5 = bytes(16)
    streaminfo = min_block + max_block + min_frame + max_frame + packed_bytes + md5
    assert len(streaminfo) == 34
    header = b"fLaC" + bytes([0x80]) + len(streaminfo).to_bytes(3, "big")
    with open(path, "wb") as fh:
        fh.write(header + streaminfo)


def _authed_client(main_mod):
    main_mod._sessions["tok"] = "admin"
    client = TestClient(main_mod.app)
    client.cookies.set("session", "tok")
    return client


@pytest.fixture
def library_dir(tmp_path):
    """A tmp MUSIC_DIR with Artist/Album/song.flac. Returns (root, flac, manifest)."""
    root = tmp_path / "Music"
    (root / "Artist" / "Album").mkdir(parents=True)
    flac = root / "Artist" / "Album" / "song.flac"
    _write_flac_with_tags(str(flac))
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    return root, str(flac), manifest


def _ok_ffmpeg(monkeypatch, convert_mod, captured):
    """Install a mock subprocess.run that records argv and writes a dummy MP3."""
    def fake_run(cmd, capture_output=False, timeout=None):
        captured["cmd"] = cmd
        tmp_dest = cmd[-1]
        # Write a tiny valid MP3 frame so the >0-byte check passes.
        _write_dummy_mp3(tmp_dest)
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")
    monkeypatch.setattr(convert_mod.subprocess, "run", fake_run)


def _write_dummy_mp3(path: str) -> None:
    header = b"\xff\xfb\x90\x40"
    frame = header + b"\x00" * (417 - 4)
    with open(path, "wb") as fh:
        fh.write(frame * 30)


# --- auth + guards ----------------------------------------------------------

def test_convert_anon_401(library_dir, monkeypatch):
    root, flac, manifest = library_dir
    main_mod, _ = _reload_app(monkeypatch, root, manifest)
    anon = TestClient(main_mod.app)
    r = anon.post("/api/tageditor/convert", data={"path": flac})
    assert r.status_code == 401
    assert r.json()["error"] == i18n.t("config.api.not_authed")


@pytest.mark.parametrize("bad", ["/etc/passwd", "../../../../etc/passwd"])
def test_convert_path_outside_400(library_dir, monkeypatch, bad):
    root, _flac, manifest = library_dir
    main_mod, _ = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    r = client.post("/api/tageditor/convert", data={"path": bad})
    assert r.status_code == 400
    assert r.json()["error"] == i18n.t("config.tageditor.path_outside")


def test_convert_already_mp3_400_no_subprocess(library_dir, monkeypatch):
    root, _flac, manifest = library_dir
    mp3 = root / "Artist" / "Album" / "track.mp3"
    _write_dummy_mp3(str(mp3))
    main_mod, convert_mod = _reload_app(monkeypatch, root, manifest)
    called = {"n": 0}
    monkeypatch.setattr(convert_mod.subprocess, "run",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    client = _authed_client(main_mod)
    r = client.post("/api/tageditor/convert", data={"path": str(mp3)})
    assert r.status_code == 400
    assert r.json()["error"] == i18n.t("config.tageditor.already_mp3")
    assert called["n"] == 0


def test_convert_bad_extension_400(library_dir, monkeypatch):
    root, _flac, manifest = library_dir
    txt = root / "Artist" / "Album" / "note.txt"
    txt.write_text("hi", encoding="utf-8")
    main_mod, _ = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    r = client.post("/api/tageditor/convert", data={"path": str(txt)})
    assert r.status_code == 400
    assert r.json()["error"] == i18n.t("config.tageditor.bad_audio_ext")


def test_convert_missing_file_404(library_dir, monkeypatch):
    root, _flac, manifest = library_dir
    gone = str(root / "Artist" / "Album" / "gone.flac")
    main_mod, _ = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    r = client.post("/api/tageditor/convert", data={"path": gone})
    assert r.status_code == 404
    assert r.json()["error"] == i18n.t("config.tageditor.not_found")


def test_convert_target_exists_400_no_subprocess(library_dir, monkeypatch):
    root, flac, manifest = library_dir
    # Place a sibling song.mp3 next to song.flac.
    sibling = root / "Artist" / "Album" / "song.mp3"
    _write_dummy_mp3(str(sibling))
    main_mod, convert_mod = _reload_app(monkeypatch, root, manifest)
    called = {"n": 0}
    monkeypatch.setattr(convert_mod.subprocess, "run",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    client = _authed_client(main_mod)
    r = client.post("/api/tageditor/convert", data={"path": flac})
    assert r.status_code == 400
    assert r.json()["error"] == i18n.t("config.tageditor.target_exists")
    assert called["n"] == 0


# --- happy path -------------------------------------------------------------

def test_convert_happy_path(library_dir, monkeypatch):
    import datetime
    root, flac, manifest = library_dir
    # Set a known old mtime on the source so we can assert it carried over.
    old = 1_000_000_000.0
    os.utime(flac, (old, old))
    main_mod, convert_mod = _reload_app(monkeypatch, root, manifest)
    captured = {}
    _ok_ffmpeg(monkeypatch, convert_mod, captured)
    client = _authed_client(main_mod)
    r = client.post("/api/tageditor/convert", data={"path": flac})
    assert r.status_code == 200, r.text
    song = r.json()["song"]

    # Command assertion: 320 CBR + tag/cover carryover flags, src + temp dest.
    cmd = captured["cmd"]
    assert "libmp3lame" in cmd
    assert "320k" in cmd
    assert "-map_metadata" in cmd and cmd[cmd.index("-map_metadata") + 1] == "0"
    assert flac in cmd
    tmp_dest = cmd[-1]
    assert os.path.dirname(tmp_dest) == os.path.dirname(flac)

    # The final <name>.mp3 exists in the source folder.
    final_mp3 = str(root / "Artist" / "Album" / "song.mp3")
    assert os.path.isfile(final_mp3)

    # The original .flac is gone from the source folder...
    assert not os.path.exists(flac)
    # ...and present under .charts-trash/flac2mp3-<today>/Artist/Album/song.flac.
    day = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    trashed = root / ".charts-trash" / f"flac2mp3-{day}" / "Artist" / "Album" / "song.flac"
    assert trashed.is_file()   # moved, not deleted

    # New MP3 mtime == the original's pre-convert mtime (date added preserved).
    assert int(os.stat(final_mp3).st_mtime) == int(old)

    # Markers cleared on the result.
    assert convert_mod.tags.read_charts_source(final_mp3) == ""
    assert convert_mod.tags.read_charts_album_src(final_mp3) == ""

    # Response payload.
    assert song["path"] == final_mp3
    assert song["is_mp3"] is True
    assert song["converted"] is True
    assert song["trashed_to"].endswith("song.flac")


def test_convert_carries_tags_and_cover(library_dir, monkeypatch):
    root, _flac, manifest = library_dir
    # Overwrite the fixture flac with a cover-bearing one at the same path.
    flac = str(root / "Artist" / "Album" / "song.flac")
    _write_flac_with_tags(flac, with_cover=True)
    main_mod, convert_mod = _reload_app(monkeypatch, root, manifest)
    captured = {}

    # Mock ffmpeg: copy the SOURCE flac's tags+cover into a real MP3 at the temp
    # path, so the reload reflects the carried-over metadata our re-assert +
    # cover-preserve logic expects (we assert OUR carryover, not a real codec).
    def fake_run(cmd, capture_output=False, timeout=None):
        captured["cmd"] = cmd
        tmp_dest = cmd[-1]
        _write_dummy_mp3(tmp_dest)
        from mutagen.easyid3 import EasyID3
        from mutagen.id3 import ID3, APIC
        try:
            tag = EasyID3(tmp_dest)
        except Exception:
            tag = EasyID3()
            tag.save(tmp_dest)
            tag = EasyID3(tmp_dest)
        tag["artist"] = "Seed Artist"
        tag["title"] = "Seed Title"
        tag["album"] = "Seed Album"
        tag.save(tmp_dest)
        id3 = ID3(tmp_dest)
        id3.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="",
                     data=b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"\xff\xd9"))
        id3.save(tmp_dest)
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")
    monkeypatch.setattr(convert_mod.subprocess, "run", fake_run)

    client = _authed_client(main_mod)
    r = client.post("/api/tageditor/convert", data={"path": flac})
    assert r.status_code == 200, r.text
    song = r.json()["song"]
    assert song["artist"] == "Seed Artist"
    assert song["title"] == "Seed Title"
    assert song["album"] == "Seed Album"
    assert song["cover"]["present"] is True


# --- ffmpeg failure leaves the original in place ----------------------------

def _assert_original_untouched(root, flac):
    assert os.path.isfile(flac)                                 # original stands
    assert not os.path.exists(str(root / "Artist" / "Album" / "song.mp3"))
    # No trash entry for this file.
    trash_root = root / ".charts-trash"
    if trash_root.exists():
        for _d, _s, files in os.walk(trash_root):
            assert "song.flac" not in files
    # No leftover temp.
    for name in os.listdir(root / "Artist" / "Album"):
        assert not name.endswith(".converting.tmp.mp3")


def test_convert_ffmpeg_rc1_leaves_original(library_dir, monkeypatch):
    root, flac, manifest = library_dir
    main_mod, convert_mod = _reload_app(monkeypatch, root, manifest)
    monkeypatch.setattr(convert_mod.subprocess, "run",
                        lambda cmd, **k: subprocess.CompletedProcess(
                            cmd, 1, stdout=b"", stderr=b"boom"))
    client = _authed_client(main_mod)
    r = client.post("/api/tageditor/convert", data={"path": flac})
    assert r.status_code == 500
    assert r.json()["error"] == i18n.t("config.tageditor.convert_failed")
    _assert_original_untouched(root, flac)


def test_convert_ffmpeg_missing_binary_leaves_original(library_dir, monkeypatch):
    root, flac, manifest = library_dir
    main_mod, convert_mod = _reload_app(monkeypatch, root, manifest)
    def boom(*a, **k):
        raise FileNotFoundError("ffmpeg")
    monkeypatch.setattr(convert_mod.subprocess, "run", boom)
    client = _authed_client(main_mod)
    r = client.post("/api/tageditor/convert", data={"path": flac})
    assert r.status_code == 500
    assert r.json()["error"] == i18n.t("config.tageditor.convert_failed")
    _assert_original_untouched(root, flac)


def test_convert_ffmpeg_timeout_leaves_original(library_dir, monkeypatch):
    root, flac, manifest = library_dir
    main_mod, convert_mod = _reload_app(monkeypatch, root, manifest)
    def boom(cmd, **k):
        raise subprocess.TimeoutExpired(cmd, 600)
    monkeypatch.setattr(convert_mod.subprocess, "run", boom)
    client = _authed_client(main_mod)
    r = client.post("/api/tageditor/convert", data={"path": flac})
    assert r.status_code == 500
    assert r.json()["error"] == i18n.t("config.tageditor.convert_failed")
    _assert_original_untouched(root, flac)


# --- trash-move failure rolls back the new MP3 ------------------------------

def test_convert_trash_move_failure_rolls_back(library_dir, monkeypatch):
    root, flac, manifest = library_dir
    main_mod, convert_mod = _reload_app(monkeypatch, root, manifest)
    captured = {}
    _ok_ffmpeg(monkeypatch, convert_mod, captured)

    final_mp3 = str(root / "Artist" / "Album" / "song.mp3")
    real_replace = os.replace

    def fake_replace(src, dst):
        # Fail ONLY the trash move (original .flac -> .charts-trash). Let the
        # temp -> final_mp3 rename through.
        if str(src) == flac:
            raise OSError("trash move boom")
        return real_replace(src, dst)
    monkeypatch.setattr(convert_mod.os, "replace", fake_replace)

    client = _authed_client(main_mod)
    r = client.post("/api/tageditor/convert", data={"path": flac})
    assert r.status_code == 500
    assert r.json()["error"] == i18n.t("config.tageditor.trash_failed")
    # Original still present; the just-created final MP3 rolled back (removed).
    assert os.path.isfile(flac)
    assert not os.path.exists(final_mp3)


# Restore import-time i18n state so test ordering never leaks a language.
@pytest.fixture(autouse=True)
def _restore_setup():
    yield
    i18n.setup()
