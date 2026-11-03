"""
Tests for the Manual Tag Editor's non-MP3 audio file list (Feature B).

The GET /api/tageditor/nonmp3 route is auth-gated (401 anon), read-only, and
piggybacks on the TTL-cached search index, so it inherits the index pruning
(audio-only, @eaDir/#recycle/.charts-trash/dot-folders skipped). No network,
no Docker - a tmp MUSIC_DIR tree.

Pinned:
  * 401 anonymous;
  * lists only non-MP3 AUDIO files (flac/m4a), never mp3/txt/cover.jpg/m3u/nfo,
    each with ext + format;
  * excludes files inside .charts-trash / @eaDir / #recycle / dot-folders;
  * an all-MP3 library -> files == [], truncated False;
  * truncation sets truncated True and len(files) == limit;
  * rel_path is Artist/Album/song.flac form, list sorted case-insensitively.
"""

import importlib
import pathlib

import pytest
from fastapi.testclient import TestClient

from app import i18n


_LANG_DIR = pathlib.Path(i18n.__file__).resolve().parent.parent.parent / "lang"


def _reload_app(monkeypatch, music_dir, manifest_file):
    monkeypatch.setenv("CONFIG_PASSWORD", "secret")
    monkeypatch.setenv("CONFIG_USERNAME", "admin")
    monkeypatch.setenv("CHARTS_LANG", "en")
    monkeypatch.setenv("MUSIC_DIR", str(music_dir))
    monkeypatch.setenv("MANIFEST_FILE", str(manifest_file))
    monkeypatch.setenv("TAGEDITOR_AUDIO_ENABLED", "0")
    # A small TTL is fine; each reload resets the module-level index cache.
    monkeypatch.setenv("TAGEDITOR_INDEX_TTL", "300")
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
    return main_mod, library_mod


def _touch(path: pathlib.Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * 16)


def _authed_client(main_mod):
    main_mod._sessions["tok"] = "admin"
    client = TestClient(main_mod.app)
    client.cookies.set("session", "tok")
    return client


@pytest.fixture
def manifest(tmp_path):
    m = tmp_path / "manifest.json"
    m.write_text("{}", encoding="utf-8")
    return m


def test_nonmp3_anon_401(tmp_path, manifest, monkeypatch):
    root = tmp_path / "Music"
    root.mkdir()
    main_mod, _ = _reload_app(monkeypatch, root, manifest)
    anon = TestClient(main_mod.app)
    r = anon.get("/api/tageditor/nonmp3")
    assert r.status_code == 401
    assert r.json()["error"] == i18n.t("config.api.not_authed")


def test_nonmp3_lists_only_nonmp3_audio(tmp_path, manifest, monkeypatch):
    root = tmp_path / "Music"
    _touch(root / "A" / "Al" / "a.mp3")       # mp3 -> excluded
    _touch(root / "B" / "Al" / "b.flac")      # non-mp3 audio -> listed
    _touch(root / "C" / "Al" / "c.m4a")       # non-mp3 audio -> listed
    _touch(root / "B" / "Al" / "note.txt")    # not audio
    _touch(root / "B" / "Al" / "cover.jpg")   # not audio
    _touch(root / "B" / "Al" / "playlist.m3u")  # not audio
    _touch(root / "C" / "Al" / "info.nfo")    # not audio
    main_mod, _ = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    d = client.get("/api/tageditor/nonmp3").json()
    assert d["ok"] is True
    rels = sorted(f["rel_path"] for f in d["files"])
    assert rels == ["B/Al/b.flac", "C/Al/c.m4a"]
    by_rel = {f["rel_path"]: f for f in d["files"]}
    assert by_rel["B/Al/b.flac"]["ext"] == ".flac"
    assert by_rel["B/Al/b.flac"]["format"] == "FLAC"
    assert by_rel["C/Al/c.m4a"]["format"] == "M4A"


def test_nonmp3_excludes_trash_sidecar_dotfolders(tmp_path, manifest, monkeypatch):
    root = tmp_path / "Music"
    _touch(root / ".charts-trash" / "flac2mp3-x" / "A" / "t.flac")
    _touch(root / "@eaDir" / "e.flac")
    _touch(root / "#recycle" / "r.flac")
    _touch(root / ".hidden" / "h.flac")
    _touch(root / "Real" / "Al" / "keep.flac")   # the only one that should show
    main_mod, _ = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    d = client.get("/api/tageditor/nonmp3").json()
    rels = [f["rel_path"] for f in d["files"]]
    assert rels == ["Real/Al/keep.flac"]


def test_nonmp3_empty_when_all_mp3(tmp_path, manifest, monkeypatch):
    root = tmp_path / "Music"
    _touch(root / "A" / "Al" / "a.mp3")
    _touch(root / "B" / "Al" / "b.mp3")
    main_mod, _ = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    d = client.get("/api/tageditor/nonmp3").json()
    assert d["files"] == []
    assert d["truncated"] is False


def test_nonmp3_truncation(tmp_path, manifest, monkeypatch):
    root = tmp_path / "Music"
    for i in range(5):
        _touch(root / "A" / "Al" / f"s{i}.flac")
    main_mod, library_mod = _reload_app(monkeypatch, root, manifest)
    # Call the pure helper directly with a low limit to assert truncation.
    files, truncated = library_mod.list_nonmp3(str(root), limit=3)
    assert truncated is True
    assert len(files) == 3


def test_nonmp3_rel_path_and_sort(tmp_path, manifest, monkeypatch):
    root = tmp_path / "Music"
    _touch(root / "Zeta" / "Al" / "z.flac")
    _touch(root / "alpha" / "Al" / "a.flac")
    main_mod, _ = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    d = client.get("/api/tageditor/nonmp3").json()
    rels = [f["rel_path"] for f in d["files"]]
    # Case-insensitive sort: "alpha/..." before "Zeta/...".
    assert rels == ["alpha/Al/a.flac", "Zeta/Al/z.flac"]
    assert rels[0].count("/") == 2   # Artist/Album/song.flac form


@pytest.fixture(autouse=True)
def _restore_setup():
    yield
    i18n.setup()
