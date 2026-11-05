"""
Tests for the Manual Tag Editor's GREEN restore (Wiederherstellen) action.

Restore is SCOPED to Tag-Editor-delete entries (tageditor-delete-<date>/ rows
that carry a restore sidecar). It moves the trashed file back to its original
library location, re-inserts the manifest entry the delete removed, and cleans
up the sidecar. Collision policy = REFUSE; no sidecar = REFUSE.

No network, no Docker: the engine (trash.restore_file) is pure filesystem +
JSON, driven here over the real HTTP route with a tmp MUSIC_DIR + tmp manifest.

Pinned (the task's acceptance criteria):
  * 401 anonymous (same auth gate as every other /api/tageditor/* route);
  * out-of-trash + traversal paths are refused (path_outside / trash_path_outside)
    WITHOUT touching the library file (reuses the trash delete's guards);
  * a symlink-in-trash whose target is a library file is refused;
  * happy path: the file is moved back to its original location (pruned
    stack-single folder recreated), mtime preserved, the manifest entry is
    re-inserted, and the sidecar is consumed;
  * collision: a file already at the target -> restore_target_exists, nothing
    moved, the newer file untouched;
  * no sidecar (pre-feature deletion) -> restore_no_sidecar, nothing moved.
"""

import importlib
import json
import os
import pathlib

import pytest
from fastapi.testclient import TestClient

from app import i18n


_LANG_DIR = pathlib.Path(i18n.__file__).resolve().parent.parent.parent / "lang"


def _reload_app(monkeypatch, music_dir, manifest_file):
    """Reload config + editor + library + manifest + trash + main with a tmp
    MUSIC_DIR. TAGEDITOR_AUDIO_ENABLED off proves restore is auth-gated only."""
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
    import app.trash as trash_mod
    importlib.reload(trash_mod)
    import app.main as main_mod
    importlib.reload(main_mod)
    return main_mod, trash_mod


def _authed_client(main_mod):
    main_mod._sessions["tok"] = "admin"
    client = TestClient(main_mod.app)
    client.cookies.set("session", "tok")
    return client


def _write(path: pathlib.Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _deleted_entry(tmp_path, *, with_sidecar=True, sidecar_entry=None):
    """Build a tmp MUSIC_DIR with ONE Tag-Editor-trashed file under
    .charts-trash/tageditor-delete-<date>/Artist/Song/Song.mp3 and (optionally)
    its restore sidecar. Returns (root, trashed_path, original_target, manifest)."""
    root = tmp_path / "Music"
    trash_root = root / ".charts-trash" / "tageditor-delete-2026-11-06"
    trashed = trash_root / "Artist" / "Song" / "Song.mp3"
    _write(trashed, b"\xff\xfb\x90\x40" + b"\x00" * 200)
    original = root / "Artist" / "Song" / "Song.mp3"
    if with_sidecar:
        entry = sidecar_entry if sidecar_entry is not None else {
            "artist": "Artist", "title": "Song", "album": "Song",
            "source": "youtube",
        }
        sidecar = {
            "original_rel_path": "Artist/Song/Song.mp3",
            "manifest_key": "song.mp3",
            "manifest_entry": entry,
            "deleted_utc": "2026-11-06T00:00:00+00:00",
        }
        _write(pathlib.Path(str(trashed) + ".trashinfo.json"),
               json.dumps(sidecar).encode("utf-8"))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"entries": {}}), encoding="utf-8")
    return root, str(trashed), original, manifest


# --- auth + guards ----------------------------------------------------------

def test_restore_anon_401(tmp_path, monkeypatch):
    root, trashed, original, manifest = _deleted_entry(tmp_path)
    main_mod, _ = _reload_app(monkeypatch, root, manifest)
    anon = TestClient(main_mod.app)
    r = anon.post("/api/tageditor/trash/restore", data={"path": trashed})
    assert r.status_code == 401
    assert r.json()["error"] == i18n.t("config.api.not_authed")
    assert os.path.isfile(trashed)          # nothing moved
    assert not original.exists()


@pytest.mark.parametrize("bad", ["/etc/passwd", "../../../../etc/passwd"])
def test_restore_refuses_path_outside_music(tmp_path, monkeypatch, bad):
    root, _trashed, _original, manifest = _deleted_entry(tmp_path)
    main_mod, _ = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    existed = os.path.exists("/etc/passwd")
    r = client.post("/api/tageditor/trash/restore", data={"path": bad})
    assert r.status_code == 400
    assert r.json()["error"] == i18n.t("config.tageditor.path_outside")
    assert os.path.exists("/etc/passwd") == existed


def test_restore_refuses_library_path_outside_trash(tmp_path, monkeypatch):
    """A path inside MUSIC_DIR but outside .charts-trash is refused: restore may
    only read FROM the trash (reuses the trash delete's trash-root guard)."""
    root, _trashed, _original, manifest = _deleted_entry(tmp_path)
    libfile = root / "Other" / "Thing" / "Thing.mp3"
    _write(libfile, b"\x00")
    main_mod, _ = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    r = client.post("/api/tageditor/trash/restore", data={"path": str(libfile)})
    assert r.status_code == 400
    assert r.json()["error"] == i18n.t("config.tageditor.trash_path_outside")
    assert libfile.exists()  # untouched


def test_restore_refuses_symlink_escape(tmp_path, monkeypatch):
    root, _trashed, _original, manifest = _deleted_entry(tmp_path)
    libfile = root / "Other" / "Thing" / "Thing.mp3"
    _write(libfile, b"\x00")
    link = root / ".charts-trash" / "tageditor-delete-2026-11-06" / "sneaky.mp3"
    try:
        os.symlink(str(libfile), str(link))
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this platform")
    main_mod, _ = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    r = client.post("/api/tageditor/trash/restore", data={"path": str(link)})
    assert r.status_code == 400
    assert r.json()["error"] == i18n.t("config.tageditor.trash_path_outside")
    assert libfile.exists()


def test_restore_missing_file_404(tmp_path, monkeypatch):
    root, _trashed, _original, manifest = _deleted_entry(tmp_path)
    gone = str(root / ".charts-trash" / "tageditor-delete-2026-11-06" / "gone.mp3")
    main_mod, _ = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    r = client.post("/api/tageditor/trash/restore", data={"path": gone})
    assert r.status_code == 404
    assert r.json()["error"] == i18n.t("config.tageditor.not_found")


# --- happy path -------------------------------------------------------------

def test_restore_happy_path(tmp_path, monkeypatch):
    root, trashed, original, manifest = _deleted_entry(tmp_path)
    old = 1_000_000_000.0
    os.utime(trashed, (old, old))
    main_mod, _ = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    r = client.post("/api/tageditor/trash/restore", data={"path": trashed})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["restored"] == "Artist/Song/Song.mp3"

    # Moved back to the original location (pruned folder recreated), gone from trash.
    assert original.is_file()
    assert not os.path.exists(trashed)
    # mtime preserved.
    assert int(os.stat(original).st_mtime) == int(old)
    # The sidecar is consumed.
    assert not os.path.exists(trashed + ".trashinfo.json")
    # The manifest entry is re-inserted under the lowercased basename.
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert "song.mp3" in data["entries"]
    assert data["entries"]["song.mp3"]["source"] == "youtube"
    # The emptied trash subfolder chain is pruned; the trash root is kept.
    assert not (root / ".charts-trash" / "tageditor-delete-2026-11-06").exists()
    assert (root / ".charts-trash").is_dir()


# --- collision = refuse -----------------------------------------------------

def test_restore_refuses_on_collision(tmp_path, monkeypatch):
    """If a (re-downloaded) file already sits at the original location, restore
    is refused and the newer file is never clobbered."""
    root, trashed, original, manifest = _deleted_entry(tmp_path)
    _write(original, b"NEW GOOD FILE")
    main_mod, _ = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    r = client.post("/api/tageditor/trash/restore", data={"path": trashed})
    assert r.status_code == 400
    assert r.json()["error"] == i18n.t("config.tageditor.restore_target_exists")
    # Nothing moved: the newer file is intact, the trashed file still in trash.
    assert original.read_bytes() == b"NEW GOOD FILE"
    assert os.path.isfile(trashed)
    assert os.path.isfile(trashed + ".trashinfo.json")


# --- no sidecar = refuse ----------------------------------------------------

def test_restore_refuses_without_sidecar(tmp_path, monkeypatch):
    """A pre-feature deletion has no sidecar -> refuse with restore_no_sidecar
    rather than guess a target."""
    root, trashed, original, manifest = _deleted_entry(tmp_path, with_sidecar=False)
    main_mod, _ = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    r = client.post("/api/tageditor/trash/restore", data={"path": trashed})
    assert r.status_code == 400
    assert r.json()["error"] == i18n.t("config.tageditor.restore_no_sidecar")
    assert os.path.isfile(trashed)   # nothing moved
    assert not original.exists()


def test_restore_round_trip_from_real_delete(tmp_path, monkeypatch):
    """End-to-end: delete a real library song via the delete route, then restore
    it via the restore route - the file returns to its exact original path and
    the manifest entry is back."""
    root = tmp_path / "Music"
    (root / "Wrong Artist" / "My Song").mkdir(parents=True)
    mp3 = root / "Wrong Artist" / "My Song" / "My Song.mp3"
    _write(mp3, b"\xff\xfb\x90\x40" + b"\x00" * 200)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"entries": {
        "my song.mp3": {"artist": "Wrong Artist", "title": "My Song",
                        "album": "My Song", "source": "youtube"},
    }}), encoding="utf-8")
    main_mod, _ = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)

    dr = client.post("/api/tageditor/delete", data={"path": str(mp3)})
    assert dr.status_code == 200, dr.text
    assert not mp3.exists()
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert "my song.mp3" not in data["entries"]

    # Find the trashed path via the listing (restorable flag set).
    lr = client.get("/api/tageditor/trash").json()
    restorable = [e for e in lr["entries"] if e["restorable"]]
    assert len(restorable) == 1
    trashed_abs = restorable[0]["path"]

    rr = client.post("/api/tageditor/trash/restore", data={"path": trashed_abs})
    assert rr.status_code == 200, rr.text
    assert rr.json()["restored"] == "Wrong Artist/My Song/My Song.mp3"
    assert mp3.is_file()                     # back at the original path
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["entries"]["my song.mp3"]["source"] == "youtube"


# Restore import-time i18n state so test ordering never leaks a language.
@pytest.fixture(autouse=True)
def _restore_setup():
    yield
    i18n.setup()
