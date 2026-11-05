"""
Tests for the Manual Tag Editor's Trash (Papierkorb) manager.

No real trash path is ever touched: every test builds a `.charts-trash` tree
under a tmp MUSIC_DIR (reload harness mirroring test_tageditor_convert.py /
test_tageditor_nonmp3.py). No network, no Docker, no real deletion outside tmp.

Pinned (matching the design's acceptance criteria):
  * list route 401 anonymous;
  * list returns entries with rel_path (starting with the source subfolder) +
    ext + format + size + total size + count, scoped strictly to .charts-trash,
    including non-audio sidecars, not following a symlinked dir out;
  * delete route 401 anonymous;
  * delete refuses a path outside MUSIC_DIR (traversal / absolute) WITHOUT
    deleting it;
  * delete refuses a library path inside MUSIC_DIR but outside the trash WITHOUT
    deleting the real library file (the core defense);
  * delete refuses a symlink-in-trash whose target is a library file, and the
    real library file survives;
  * delete of a missing in-trash path -> 404;
  * delete removes a single file inside trash, reports its rel_path, prunes the
    emptied source subfolder (trash root kept), leaves a sibling untouched;
  * empty requires the confirm sentinel (refuses without it / with a wrong
    value) and removes only trash contents (library file outside survives);
  * empty does not follow a symlinked dir out of the trash;
  * _human_size unit test incl. negative clamp;
  * cache is invalidated after a delete (next list reflects the removal
    immediately, without waiting for the TTL).
"""

import importlib
import os
import pathlib

import pytest
from fastapi.testclient import TestClient

from app import i18n


_LANG_DIR = pathlib.Path(i18n.__file__).resolve().parent.parent.parent / "lang"


def _reload_app(monkeypatch, music_dir, manifest_file):
    """Reload config + editor + library + trash + main with a tmp MUSIC_DIR."""
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


@pytest.fixture
def env(tmp_path):
    """A tmp MUSIC_DIR with an empty .charts-trash. Returns (root, trash, manifest)."""
    root = tmp_path / "Music"
    trash_root = root / ".charts-trash"
    trash_root.mkdir(parents=True)
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    return root, trash_root, manifest


# --- list route -------------------------------------------------------------

def test_trash_list_anon_401(env, monkeypatch):
    root, _trash, manifest = env
    main_mod, _ = _reload_app(monkeypatch, root, manifest)
    anon = TestClient(main_mod.app)
    r = anon.get("/api/tageditor/trash")
    assert r.status_code == 401
    assert r.json()["error"] == i18n.t("config.api.not_authed")


def test_trash_list_entries_sizes_total_count(env, monkeypatch):
    root, trash_root, manifest = env
    _write(trash_root / "dedup-2026-11-02" / "A" / "Al" / "x.mp3", b"\x00" * 1000)
    _write(trash_root / "flac2mp3-2026-11-03" / "B" / "Al" / "y.flac", b"\x01" * 2048)
    main_mod, _ = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    r = client.get("/api/tageditor/trash")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["count"] == 2
    assert d["total_bytes"] == 1000 + 2048
    assert d["total_human"]  # non-empty human string
    rels = [e["rel_path"] for e in d["entries"]]
    assert rels == sorted(rels, key=str.lower)
    by_rel = {e["rel_path"]: e for e in d["entries"]}
    assert "dedup-2026-11-02/A/Al/x.mp3" in by_rel
    assert "flac2mp3-2026-11-03/B/Al/y.flac" in by_rel
    mp3 = by_rel["dedup-2026-11-02/A/Al/x.mp3"]
    assert mp3["ext"] == ".mp3" and mp3["format"] == "MP3" and mp3["bytes"] == 1000
    flac = by_rel["flac2mp3-2026-11-03/B/Al/y.flac"]
    assert flac["ext"] == ".flac" and flac["format"] == "FLAC" and flac["bytes"] == 2048
    assert mp3["size_human"] and flac["size_human"]


def test_trash_list_includes_non_audio_sidecars(env, monkeypatch):
    root, trash_root, manifest = env
    _write(trash_root / "dedup-2026-11-02" / "A" / "Al" / "cover.jpg", b"\xff\xd8\xff")
    _write(trash_root / "dedup-2026-11-02" / "A" / "Al" / "notes.txt", b"hi")
    main_mod, _ = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    r = client.get("/api/tageditor/trash")
    rels = {e["rel_path"] for e in r.json()["entries"]}
    assert "dedup-2026-11-02/A/Al/cover.jpg" in rels
    assert "dedup-2026-11-02/A/Al/notes.txt" in rels


def test_trash_list_scoped_to_trash_only(env, monkeypatch):
    root, trash_root, manifest = env
    _write(trash_root / "dedup-2026-11-02" / "x.mp3", b"\x00")
    # A real library file OUTSIDE .charts-trash.
    _write(root / "Artist" / "Album" / "song.mp3", b"\x00")
    main_mod, _ = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    r = client.get("/api/tageditor/trash")
    rels = {e["rel_path"] for e in r.json()["entries"]}
    assert "dedup-2026-11-02/x.mp3" in rels
    assert not any("song.mp3" in rel for rel in rels)
    assert r.json()["count"] == 1


def test_trash_list_does_not_follow_symlink_out(env, monkeypatch):
    root, trash_root, manifest = env
    libdir = root / "Artist" / "Album"
    _write(libdir / "real.mp3", b"\x00")
    link = trash_root / "escape"
    try:
        os.symlink(str(libdir), str(link), target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this platform")
    main_mod, _ = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    r = client.get("/api/tageditor/trash")
    rels = {e["rel_path"] for e in r.json()["entries"]}
    # The symlinked dir is not descended -> the library file is not listed.
    assert not any("real.mp3" in rel for rel in rels)


# --- restorable flag + sidecar hiding ---------------------------------------

def test_trash_list_flags_restorable_and_hides_sidecar(env, monkeypatch):
    """A tageditor-delete row WITH its restore sidecar is flagged restorable; a
    convert/dedup/upgrade row is not. The sidecar file itself is NOT listed as a
    row of its own (nor counted/summed)."""
    root, trash_root, manifest = env
    # Restorable: tageditor-delete entry WITH its sidecar.
    tde = trash_root / "tageditor-delete-2026-11-06" / "Artist" / "Song" / "Song.mp3"
    _write(tde, b"\x00" * 100)
    _write(pathlib.Path(str(tde) + ".trashinfo.json"),
           b'{"original_rel_path": "Artist/Song/Song.mp3", "manifest_key": "song.mp3", "manifest_entry": {}}')
    # NOT restorable: a tageditor-delete entry withOUT a sidecar (pre-feature).
    tde_nosidecar = trash_root / "tageditor-delete-2026-11-06" / "Old" / "Tune" / "Tune.mp3"
    _write(tde_nosidecar, b"\x00" * 50)
    # NOT restorable: convert / dedup / upgrade origins.
    _write(trash_root / "flac2mp3-2026-11-03" / "B" / "Al" / "y.flac", b"\x01" * 20)
    _write(trash_root / "dedup-2026-11-02" / "C" / "Cs" / "z.mp3", b"\x02" * 30)
    _write(trash_root / "20260930" / "Overkill.mp3", b"\x03" * 40)

    main_mod, _ = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    r = client.get("/api/tageditor/trash")
    assert r.status_code == 200, r.text
    d = r.json()
    by_rel = {e["rel_path"]: e for e in d["entries"]}
    # The sidecar is NOT a row and NOT counted.
    assert not any(rel.endswith(".trashinfo.json") for rel in by_rel)
    assert d["count"] == 5
    # Exactly the tageditor-delete row with a sidecar is restorable.
    assert by_rel["tageditor-delete-2026-11-06/Artist/Song/Song.mp3"]["restorable"] is True
    assert by_rel["tageditor-delete-2026-11-06/Old/Tune/Tune.mp3"]["restorable"] is False
    assert by_rel["flac2mp3-2026-11-03/B/Al/y.flac"]["restorable"] is False
    assert by_rel["dedup-2026-11-02/C/Cs/z.mp3"]["restorable"] is False
    assert by_rel["20260930/Overkill.mp3"]["restorable"] is False


# --- delete route -----------------------------------------------------------

def test_trash_delete_anon_401(env, monkeypatch):
    root, trash_root, manifest = env
    f = trash_root / "dedup-2026-11-02" / "x.mp3"
    _write(f, b"\x00")
    main_mod, _ = _reload_app(monkeypatch, root, manifest)
    anon = TestClient(main_mod.app)
    r = anon.post("/api/tageditor/trash/delete", data={"path": str(f)})
    assert r.status_code == 401
    assert f.exists()  # nothing deleted


@pytest.mark.parametrize("bad", ["/etc/passwd", "../../../../etc/passwd"])
def test_trash_delete_refuses_path_outside_music(env, monkeypatch, bad):
    root, _trash, manifest = env
    main_mod, _ = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    existed = os.path.exists("/etc/passwd")
    r = client.post("/api/tageditor/trash/delete", data={"path": bad})
    assert r.status_code == 400
    assert r.json()["error"] == i18n.t("config.tageditor.path_outside")
    # The target (if it existed) is untouched.
    assert os.path.exists("/etc/passwd") == existed


def test_trash_delete_refuses_library_path_outside_trash(env, monkeypatch):
    root, _trash, manifest = env
    libfile = root / "Artist" / "Album" / "song.mp3"
    _write(libfile, b"\x00")
    main_mod, _ = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    r = client.post("/api/tageditor/trash/delete", data={"path": str(libfile)})
    assert r.status_code == 400
    assert r.json()["error"] == i18n.t("config.tageditor.trash_path_outside")
    # The core defense: a real library file is NEVER deleted here.
    assert libfile.exists()


def test_trash_delete_refuses_symlink_escape(env, monkeypatch):
    root, trash_root, manifest = env
    libfile = root / "Artist" / "Album" / "song.mp3"
    _write(libfile, b"\x00")
    link = trash_root / "dedup-2026-11-02" / "sneaky.mp3"
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(str(libfile), str(link))
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this platform")
    main_mod, _ = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    r = client.post("/api/tageditor/trash/delete", data={"path": str(link)})
    assert r.status_code == 400
    assert r.json()["error"] == i18n.t("config.tageditor.trash_path_outside")
    assert libfile.exists()  # the real target survives


def test_trash_delete_missing_file_404(env, monkeypatch):
    root, trash_root, manifest = env
    gone = trash_root / "dedup-2026-11-02" / "gone.mp3"
    main_mod, _ = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    r = client.post("/api/tageditor/trash/delete", data={"path": str(gone)})
    assert r.status_code == 404
    assert r.json()["error"] == i18n.t("config.tageditor.not_found")


def test_trash_delete_removes_single_file(env, monkeypatch):
    root, trash_root, manifest = env
    target = trash_root / "dedup-2026-11-02" / "A" / "Al" / "x.mp3"
    sibling = trash_root / "dedup-2026-11-02" / "A" / "Al" / "keep.mp3"
    _write(target, b"\x00")
    _write(sibling, b"\x00")
    # A separate subfolder that should empty out fully when x's folder is pruned
    # only if it becomes empty - here we delete x but keep sibling, so the Al
    # folder stays. Use a lone-file subfolder to prove pruning.
    lone = trash_root / "flac2mp3-2026-11-03" / "B" / "only.flac"
    _write(lone, b"\x00")
    main_mod, _ = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)

    # Delete x.mp3 (sibling remains -> its folder is kept).
    r = client.post("/api/tageditor/trash/delete", data={"path": str(target)})
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] == "dedup-2026-11-02/A/Al/x.mp3"
    assert not target.exists()
    assert sibling.exists()
    assert (trash_root / "dedup-2026-11-02" / "A" / "Al").is_dir()

    # Delete the lone file -> its now-empty subfolder chain is pruned, trash root kept.
    r2 = client.post("/api/tageditor/trash/delete", data={"path": str(lone)})
    assert r2.status_code == 200, r2.text
    assert not lone.exists()
    assert not (trash_root / "flac2mp3-2026-11-03").exists()
    assert trash_root.is_dir()  # the trash root itself is always kept


# --- empty route ------------------------------------------------------------

def test_trash_empty_anon_401(env, monkeypatch):
    root, trash_root, manifest = env
    _write(trash_root / "dedup-2026-11-02" / "x.mp3", b"\x00")
    main_mod, _ = _reload_app(monkeypatch, root, manifest)
    anon = TestClient(main_mod.app)
    r = anon.post("/api/tageditor/trash/empty", data={"confirm": "empty"})
    assert r.status_code == 401
    assert (trash_root / "dedup-2026-11-02" / "x.mp3").exists()


@pytest.mark.parametrize("confirm", [None, "", "1", "true", "yes", "EMPTY"])
def test_trash_empty_requires_confirm(env, monkeypatch, confirm):
    root, trash_root, manifest = env
    f = trash_root / "dedup-2026-11-02" / "x.mp3"
    _write(f, b"\x00")
    main_mod, _ = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    data = {} if confirm is None else {"confirm": confirm}
    r = client.post("/api/tageditor/trash/empty", data=data)
    assert r.status_code == 400
    assert r.json()["error"] == i18n.t("config.tageditor.trash_confirm_required")
    assert f.exists()  # nothing removed


def test_trash_empty_removes_only_trash_contents(env, monkeypatch):
    root, trash_root, manifest = env
    _write(trash_root / "dedup-2026-11-02" / "A" / "x.mp3", b"\x00")
    _write(trash_root / "flac2mp3-2026-11-03" / "y.flac", b"\x00")
    _write(trash_root / "loose.jpg", b"\x00")
    libfile = root / "Artist" / "Album" / "song.mp3"
    _write(libfile, b"\x00")
    main_mod, _ = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    r = client.post("/api/tageditor/trash/empty", data={"confirm": "empty"})
    assert r.status_code == 200, r.text
    assert r.json()["removed"] == 3  # two top-level dirs + one loose file
    assert r.json()["failed"] == 0
    # Trash root is now empty but still present.
    assert trash_root.is_dir()
    assert list(trash_root.iterdir()) == []
    # The library file OUTSIDE the trash is untouched.
    assert libfile.exists()


def test_trash_empty_does_not_follow_symlink_out(env, monkeypatch):
    root, trash_root, manifest = env
    libdir = root / "Artist" / "Album"
    libfile = libdir / "keep.mp3"
    _write(libfile, b"\x00")
    link = trash_root / "escape"
    try:
        os.symlink(str(libdir), str(link), target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this platform")
    _write(trash_root / "real.mp3", b"\x00")
    main_mod, _ = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    r = client.post("/api/tageditor/trash/empty", data={"confirm": "empty"})
    assert r.status_code == 200, r.text
    # The symlink was unlinked (not followed); the real library file survives.
    assert libfile.exists()
    assert libdir.is_dir()
    assert not link.exists()
    assert not (trash_root / "real.mp3").exists()


# --- cache invalidation -----------------------------------------------------

def test_trash_cache_invalidated_after_delete(env, monkeypatch):
    root, trash_root, manifest = env
    a = trash_root / "dedup-2026-11-02" / "a.mp3"
    b = trash_root / "dedup-2026-11-02" / "b.mp3"
    _write(a, b"\x00")
    _write(b, b"\x00")
    main_mod, _ = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    # Prime the cache.
    assert client.get("/api/tageditor/trash").json()["count"] == 2
    # Delete one; the next list reflects it immediately (no TTL wait).
    client.post("/api/tageditor/trash/delete", data={"path": str(a)})
    d2 = client.get("/api/tageditor/trash").json()
    assert d2["count"] == 1
    assert not any(e["rel_path"].endswith("a.mp3") for e in d2["entries"])


# --- unit: _human_size ------------------------------------------------------

def test_human_size(env, monkeypatch):
    root, _trash, manifest = env
    _main, trash_mod = _reload_app(monkeypatch, root, manifest)
    assert trash_mod._human_size(0) == "0 B"
    assert trash_mod._human_size(512) == "512 B"
    assert trash_mod._human_size(1023) == "1023 B"
    assert trash_mod._human_size(1024) == "1.0 KB"
    assert trash_mod._human_size(1536) == "1.5 KB"
    assert trash_mod._human_size(8 * 1024 * 1024) == "8.0 MB"
    assert trash_mod._human_size(1024 ** 3) == "1.0 GB"
    assert trash_mod._human_size(1024 ** 4) == "1.0 TB"
    # Negative clamps to 0.
    assert trash_mod._human_size(-5) == "0 B"
    # Non-numeric clamps to 0 (fail-open).
    assert trash_mod._human_size(None) == "0 B"


# Restore import-time i18n state so test ordering never leaks a language.
@pytest.fixture(autouse=True)
def _restore_setup():
    yield
    i18n.setup()
