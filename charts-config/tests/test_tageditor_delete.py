"""
Tests for the Manual Tag Editor's "delete this song" feature.

The delete is REVERSIBLE: it moves the loaded song into
MUSIC_DIR/.charts-trash/tageditor-delete-<YYYY-MM-DD>/ (mirroring the convert
path's trash convention, NOT a hard delete) and clears the file's manifest
entry so charts-import does not re-import it on its next run.

No network, no ffmpeg, no Docker: the engine (editor.delete_song) is pure
filesystem + JSON, so the tests drive the real HTTP route over a tmp MUSIC_DIR
and a tmp manifest and assert OUR orchestration:
  * 401 anonymous (same auth gate as every other /api/tageditor/* route);
  * path outside MUSIC_DIR (traversal + absolute) -> 400 path_outside, nothing
    moved, manifest untouched (the path-guard rejects before any mutation);
  * a missing file -> 404 not_found;
  * happy path: the file is GONE from the library and PRESENT under
    .charts-trash/tageditor-delete-<today>/..., mtime preserved, the manifest
    entry removed, the response carries deleted/trashed_to/manifest_cleared;
  * a stack-single <Artist>/<Title>/ folder is pruned when it empties, but a
    shared folder holding other files is left intact;
  * deleting a non-MP3 (FLAC) song works (delete is not MP3-only);
  * a song with no manifest entry still deletes (manifest_cleared False).
"""

import datetime
import importlib
import json
import os
import pathlib

import pytest
from fastapi.testclient import TestClient

from app import i18n


_LANG_DIR = pathlib.Path(i18n.__file__).resolve().parent.parent.parent / "lang"


def _reload_app(monkeypatch, music_dir, manifest_file):
    """Reload config + editor + library + manifest + main with a tmp MUSIC_DIR.

    TAGEDITOR_AUDIO_ENABLED is deliberately OFF (the default), proving delete is
    auth-gated only, not flag-gated.
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
    import app.manifest as manifest_mod
    importlib.reload(manifest_mod)
    import app.editor as editor_mod
    importlib.reload(editor_mod)
    import app.library as library_mod
    importlib.reload(library_mod)
    import app.main as main_mod
    importlib.reload(main_mod)
    return main_mod


def _write_min_mp3(path: str) -> None:
    """Write a tiny but valid MP3 frame so mutagen/os treat it as a real file."""
    header = b"\xff\xfb\x90\x40"
    frame = header + b"\x00" * (417 - 4)
    with open(path, "wb") as fh:
        fh.write(frame * 30)


def _write_min_flac(path: str) -> None:
    """Write a minimal valid FLAC (fLaC marker + STREAMINFO), as in the convert
    tests, so the delete-of-non-MP3 case uses a real non-MP3 file."""
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


def _authed_client(main_mod):
    main_mod._sessions["tok"] = "admin"
    client = TestClient(main_mod.app)
    client.cookies.set("session", "tok")
    return client


@pytest.fixture
def library_dir(tmp_path):
    """A tmp MUSIC_DIR with a stack-single Artist/Title/Title.mp3 plus a manifest
    entry keyed by the lowercased basename. Returns (root, mp3, manifest)."""
    root = tmp_path / "Music"
    (root / "Wrong Artist" / "My Song").mkdir(parents=True)
    mp3 = root / "Wrong Artist" / "My Song" / "My Song.mp3"
    _write_min_mp3(str(mp3))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"entries": {
        "my song.mp3": {"artist": "Wrong Artist", "title": "My Song",
                        "album": "My Song", "source": "youtube"},
        "other.mp3": {"artist": "Keep", "title": "Keep", "album": "Keep"},
    }}), encoding="utf-8")
    return root, str(mp3), manifest


# --- auth + guards ----------------------------------------------------------

def test_delete_anon_401(library_dir, monkeypatch):
    root, mp3, manifest = library_dir
    main_mod = _reload_app(monkeypatch, root, manifest)
    anon = TestClient(main_mod.app)
    r = anon.post("/api/tageditor/delete", data={"path": mp3})
    assert r.status_code == 401
    assert r.json()["error"] == i18n.t("config.api.not_authed")
    # Nothing moved: the original is still present.
    assert os.path.isfile(mp3)


@pytest.mark.parametrize("bad", ["/etc/passwd", "../../../../etc/passwd"])
def test_delete_path_outside_400(library_dir, monkeypatch, bad):
    root, _mp3, manifest = library_dir
    main_mod = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    r = client.post("/api/tageditor/delete", data={"path": bad})
    assert r.status_code == 400
    assert r.json()["error"] == i18n.t("config.tageditor.path_outside")
    # The manifest is untouched by a rejected request.
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert "my song.mp3" in data["entries"]


def test_delete_traversal_into_trash_leaves_library(library_dir, monkeypatch):
    """A crafted path cannot delete an arbitrary file: anything resolving outside
    MUSIC_DIR is refused. The guard uses realpath, so '..' cannot escape."""
    root, _mp3, manifest = library_dir
    # A secret outside the library the attacker would like to remove.
    secret = root.parent / "secret.txt"
    secret.write_text("keep me", encoding="utf-8")
    main_mod = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    attack = str(root / ".." / "secret.txt")
    r = client.post("/api/tageditor/delete", data={"path": attack})
    assert r.status_code == 400
    assert r.json()["error"] == i18n.t("config.tageditor.path_outside")
    assert secret.is_file()   # never touched


def test_delete_missing_file_404(library_dir, monkeypatch):
    root, _mp3, manifest = library_dir
    gone = str(root / "Wrong Artist" / "My Song" / "gone.mp3")
    main_mod = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    r = client.post("/api/tageditor/delete", data={"path": gone})
    assert r.status_code == 404
    assert r.json()["error"] == i18n.t("config.tageditor.not_found")


# --- happy path -------------------------------------------------------------

def test_delete_happy_path(library_dir, monkeypatch):
    root, mp3, manifest = library_dir
    old = 1_000_000_000.0
    os.utime(mp3, (old, old))
    main_mod = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    r = client.post("/api/tageditor/delete", data={"path": mp3})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["deleted"] is True
    assert body["manifest_cleared"] is True

    # The original is gone from the library...
    assert not os.path.exists(mp3)
    # ...and present under .charts-trash/tageditor-delete-<today>/...
    day = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    trashed = (root / ".charts-trash" / f"tageditor-delete-{day}"
               / "Wrong Artist" / "My Song" / "My Song.mp3")
    assert trashed.is_file()   # moved, not deleted
    assert body["trashed_to"].endswith("My Song.mp3")
    # mtime preserved (date added kept in the trash copy).
    assert int(os.stat(trashed).st_mtime) == int(old)

    # The manifest entry for this file is removed; the OTHER entry is kept.
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert "my song.mp3" not in data["entries"]
    assert "other.mp3" in data["entries"]


def test_delete_prunes_empty_single_folder(library_dir, monkeypatch):
    """The now-empty stack-single <Title> album folder is removed, but its parent
    <Artist> folder is left (it may hold other titles)."""
    root, mp3, manifest = library_dir
    album_dir = root / "Wrong Artist" / "My Song"
    main_mod = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    r = client.post("/api/tageditor/delete", data={"path": mp3})
    assert r.status_code == 200, r.text
    assert not album_dir.exists()                 # emptied -> pruned
    assert (root / "Wrong Artist").exists()       # artist dir kept


def test_delete_keeps_shared_folder(tmp_path, monkeypatch):
    """A folder holding OTHER audio files must never be removed by the delete."""
    root = tmp_path / "Music"
    (root / "Band" / "Album").mkdir(parents=True)
    a = root / "Band" / "Album" / "01 - Song A.mp3"
    b = root / "Band" / "Album" / "02 - Song B.mp3"
    _write_min_mp3(str(a))
    _write_min_mp3(str(b))
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    main_mod = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    r = client.post("/api/tageditor/delete", data={"path": str(a)})
    assert r.status_code == 200, r.text
    assert not a.exists()                                   # deleted (trashed)
    assert b.exists()                                       # sibling kept
    assert (root / "Band" / "Album").is_dir()               # folder kept


def test_delete_non_mp3_flac(tmp_path, monkeypatch):
    """Delete is not MP3-only: a FLAC the editor can load is deletable too."""
    root = tmp_path / "Music"
    (root / "Artist" / "Tune").mkdir(parents=True)
    flac = root / "Artist" / "Tune" / "Tune.flac"
    _write_min_flac(str(flac))
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    main_mod = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    r = client.post("/api/tageditor/delete", data={"path": str(flac)})
    assert r.status_code == 200, r.text
    assert not flac.exists()
    day = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    trashed = (root / ".charts-trash" / f"tageditor-delete-{day}"
               / "Artist" / "Tune" / "Tune.flac")
    assert trashed.is_file()


def test_delete_without_manifest_entry(tmp_path, monkeypatch):
    """A song with no manifest entry still deletes; manifest_cleared is False."""
    root = tmp_path / "Music"
    (root / "Artist" / "Solo").mkdir(parents=True)
    mp3 = root / "Artist" / "Solo" / "Solo.mp3"
    _write_min_mp3(str(mp3))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"entries": {}}), encoding="utf-8")
    main_mod = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    r = client.post("/api/tageditor/delete", data={"path": str(mp3)})
    assert r.status_code == 200, r.text
    assert r.json()["manifest_cleared"] is False
    assert not mp3.exists()


# Restore import-time i18n state so test ordering never leaks a language.
@pytest.fixture(autouse=True)
def _restore_setup():
    yield
    i18n.setup()
