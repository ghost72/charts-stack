"""
HTTP route tests for the Manual Tag Editor (FEAT-003).

These cover the layer FEAT-002 deliberately deferred: the FastAPI routes under
/api/tageditor/*, the shared admin-session auth guard, and the EditorError ->
localized JSON mapping. They run WITHOUT network/Docker (a tmp MP3 inside a
MUSIC_DIR; no cover URL fetch).

Pinned:
  * every route returns 401 config.api.not_authed when unauthenticated;
  * an authenticated-session happy-path save succeeds, writes the changed
    field, and clears both provenance markers (the authed coverage that lands
    here, as agreed when the engine test stayed route-free in FEAT-002);
  * search + song routes work for a logged-in session;
  * the path-outside guard maps to 400; a non-MP3 save maps to 400 mp3_only.
"""

import importlib
import pathlib

import pytest
from fastapi.testclient import TestClient
from mutagen.id3 import ID3, TXXX

from app import i18n


_LANG_DIR = pathlib.Path(i18n.__file__).resolve().parent.parent.parent / "lang"


def _write_dummy_mp3(path: str) -> None:
    header = b"\xff\xfb\x90\x40"
    frame = header + b"\x00" * (417 - 4)
    with open(path, "wb") as fh:
        fh.write(frame * 30)


def _reload_app(monkeypatch, music_dir, manifest_file):
    """Reload config + main with CONFIG_PASSWORD and the tmp MUSIC_DIR set."""
    monkeypatch.setenv("CONFIG_PASSWORD", "secret")
    monkeypatch.setenv("CONFIG_USERNAME", "admin")
    monkeypatch.setenv("CHARTS_LANG", "en")
    monkeypatch.setenv("MUSIC_DIR", str(music_dir))
    monkeypatch.setenv("MANIFEST_FILE", str(manifest_file))
    import app.config as config_mod
    importlib.reload(config_mod)
    i18n.setup(lang="en", lang_dir=_LANG_DIR)
    # editor reads config at call time via `from . import config`, so the
    # reloaded module object must be re-imported by main too.
    import app.editor as editor_mod
    importlib.reload(editor_mod)
    import app.library as library_mod
    importlib.reload(library_mod)
    import app.main as main_mod
    importlib.reload(main_mod)
    return main_mod


@pytest.fixture
def app_and_song(tmp_path, monkeypatch):
    root = tmp_path / "Music"
    (root / "Artist" / "Album").mkdir(parents=True)
    p = root / "Artist" / "Album" / "track.mp3"
    _write_dummy_mp3(str(p))
    from mutagen import File as MutagenFile
    audio = MutagenFile(str(p), easy=True)
    audio["artist"] = "Old Artist"
    audio["title"] = "Old Title"
    audio["album"] = "Old Album"
    audio.save()
    from app import tags as tags_mod
    id3 = ID3(str(p))
    id3.add(TXXX(encoding=3, desc=tags_mod.CHARTS_SOURCE_DESC, text=["youtube"]))
    id3.add(TXXX(encoding=3, desc=tags_mod.CHARTS_ALBUM_SRC_DESC, text=["resolver"]))
    id3.save(str(p))
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    main_mod = _reload_app(monkeypatch, root, manifest)
    return main_mod, str(p)


def _authed_client(main_mod):
    main_mod._sessions["tok"] = "admin"
    client = TestClient(main_mod.app)
    client.cookies.set("session", "tok")
    return client


# --- unauthenticated -> 401 on every route ----------------------------------

def test_routes_require_auth(app_and_song):
    main_mod, path = app_and_song
    anon = TestClient(main_mod.app)
    for method, url in (("get", "/api/tageditor/search?q=old"),
                        ("get", f"/api/tageditor/song?path={path}")):
        r = getattr(anon, method)(url)
        assert r.status_code == 401
        assert r.json()["error"] == i18n.t("config.api.not_authed")
    r = anon.post("/api/tageditor/save", data={"path": path, "title": "X"})
    assert r.status_code == 401
    assert r.json()["error"] == i18n.t("config.api.not_authed")


# --- authenticated happy path ------------------------------------------------

def test_authed_save_happy_path(app_and_song):
    main_mod, path = app_and_song
    from app import tags as tags_mod
    assert tags_mod.read_charts_source(path) == "youtube"
    client = _authed_client(main_mod)
    r = client.post("/api/tageditor/save",
                    data={"path": path, "artist": "Old Artist",
                          "title": "New Title", "album": "Old Album",
                          "year": "", "track": ""})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["song"]["title"] == "New Title"
    # The changed field was written and both markers cleared.
    from mutagen import File as MutagenFile
    audio = MutagenFile(path, easy=True)
    assert str(audio["title"][0]) == "New Title"
    assert tags_mod.read_charts_source(path) == ""
    assert tags_mod.read_charts_album_src(path) == ""


def test_authed_search_and_song(app_and_song):
    main_mod, path = app_and_song
    client = _authed_client(main_mod)
    r = client.get("/api/tageditor/search?q=old")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    r = client.get(f"/api/tageditor/song?path={path}")
    assert r.status_code == 200
    song = r.json()["song"]
    assert song["title"] == "Old Title"
    assert song["is_mp3"] is True


# --- route error mapping -----------------------------------------------------

def test_song_path_outside_maps_400(app_and_song):
    main_mod, _ = app_and_song
    client = _authed_client(main_mod)
    r = client.get("/api/tageditor/song?path=/etc/passwd")
    assert r.status_code == 400
    assert r.json()["error"] == i18n.t("config.tageditor.path_outside")


def test_save_non_mp3_maps_400(app_and_song, tmp_path, monkeypatch):
    main_mod, _ = app_and_song
    # A .flac inside MUSIC_DIR is rejected mp3_only.
    import app.config as config_mod
    flac = pathlib.Path(config_mod.MUSIC_DIR) / "Artist" / "Album" / "x.flac"
    flac.write_bytes(b"nope")
    client = _authed_client(main_mod)
    r = client.post("/api/tageditor/save", data={"path": str(flac), "title": "X"})
    assert r.status_code == 400
    assert r.json()["error"] == i18n.t("config.tageditor.mp3_only")


# Restore import-time i18n state so test ordering never leaks a language.
@pytest.fixture(autouse=True)
def _restore_setup():
    yield
    i18n.setup()
