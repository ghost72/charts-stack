"""Official Deezer genre rule management and API validation."""

import json

import pytest
from fastapi.testclient import TestClient

from app import artist_scenes
from .test_artist_scenes import _load_main


@pytest.fixture
def genre_client(tmp_path, monkeypatch):
    main = _load_main(monkeypatch, tmp_path)
    monkeypatch.setattr(main.collector, "deezer_genres", lambda: {
        "ok": True, "genres": [{"id": "0", "name": "All"},
                                {"id": "132", "name": "Pop"},
                                {"id": "152", "name": "Rock"}],
    })
    client = TestClient(main.app)
    client.cookies.set("session", "genre-test")
    main._sessions["genre-test"] = "admin"
    return client, main, tmp_path


def test_deezer_genre_create_edit_rename_before_sync_and_delete(genre_client, monkeypatch):
    client, main, directory = genre_client
    payload = {"name": "Pop Mix", "genre_id": "132", "genre_name": "Forged", "limit": 25}
    response = client.post("/api/genre-playlists", json=payload)
    assert response.status_code == 200
    rule = {"genre_id": "132", "genre_name": "Pop", "limit": 25}
    assert response.json()["catalog"]["genre_playlists"] == {"Pop Mix": rule}
    item = response.json()["playlists"][0]
    assert item["kind"] == "genre" and item["renameable"] and not item["present"]
    assert "genres" not in item
    monkeypatch.setattr(main.collector, "deezer_genres",
                        lambda: pytest.fail("unchanged verified genre needs no new catalog lookup"))
    response = client.post("/api/genre-playlists", json={**payload, "replace": True, "limit": 100})
    assert response.status_code == 200
    renamed = client.post("/api/mundart/scene-playlists",
                          json={"name": item["name"], "new_scene": "My Pop"})
    assert renamed.status_code == 200
    assert renamed.json()["catalog"]["genre_playlists"] == {
        "My Pop": {**rule, "limit": 100}}
    assert not artist_scenes._load_selection().get("scene_files")
    deleted = client.post("/api/mundart/scene-playlists",
                          json={"name": "charts-my-pop.m3u", "delete": True})
    assert deleted.status_code == 200 and deleted.json()["playlists"] == []
    assert json.loads((directory / ".charts-artist-selection.json").read_text())["genre_playlists"] == {}
    overview = client.get("/api/mundart/scene-playlists").json()
    assert overview["playlists"] == [] and overview["catalog"]["scene_filenames"] == {}


@pytest.mark.parametrize("limit", [0, 101, True, "50", 2.5])
def test_deezer_genre_limit_validation_does_not_write(genre_client, limit):
    client, _main, directory = genre_client
    response = client.post("/api/genre-playlists",
                           json={"name": "Pop", "genre_id": "132", "limit": limit})
    assert response.status_code == 400
    assert not (directory / ".charts-artist-selection.json").exists()


def test_deezer_genre_auth_source_failure_unknown_and_reserved_names(genre_client, monkeypatch):
    client, main, directory = genre_client
    payload = {"name": "Pop", "genre_id": "132", "limit": 50}
    client.cookies.clear()
    assert client.post("/api/genre-playlists", json=payload).status_code == 401
    client.cookies.set("session", "genre-test")
    for genre_id in ("0", "999", 132, True):
        assert client.post("/api/genre-playlists",
                           json={**payload, "genre_id": genre_id}).status_code == 409
    (directory / ".charts-artist-inventory.json").write_text(json.dumps({
        "version": 1, "artists": {}, "reserved_playlists": ["charts-pop.m3u"]}))
    assert client.post("/api/genre-playlists", json=payload).status_code == 409
    assert not (directory / ".charts-artist-selection.json").exists()
    monkeypatch.setattr(main.collector, "deezer_genres", lambda: {"ok": False, "genres": []})
    assert client.post("/api/genre-playlists", json=payload).status_code == 502


def test_deezer_genre_present_playlist_rename_and_delete_preserve_music(genre_client):
    client, _main, directory = genre_client
    payload = {"name": "Pop", "genre_id": "132"}
    assert client.post("/api/genre-playlists", json=payload).status_code == 200
    music = directory / "keep.mp3"
    music.write_bytes(b"existing music")
    m3u = directory / "charts-pop.m3u"
    m3u.write_text("#EXTM3U\n#PLAYLIST:Pop\n#CHARTS-STACK:GENRE-PLAYLIST\nkeep.mp3\n")
    response = client.post("/api/mundart/scene-playlists",
                           json={"name": m3u.name, "new_scene": "Pop Hits"})
    assert response.status_code == 200
    assert response.json()["playlists"][0]["genre_id"] == "132"
    response = client.post("/api/mundart/scene-playlists", json={"name": m3u.name, "delete": True})
    assert response.status_code == 200
    assert response.json()["catalog"]["genre_playlists"] == {}
    assert artist_scenes._load_selection()["pending_scene_deletions"] == {m3u.name: "Pop Hits"}
    assert m3u.exists() and music.read_bytes() == b"existing music"
