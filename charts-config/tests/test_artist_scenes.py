"""Tests for the authenticated Mundart artist inventory and selection."""

import importlib
import json
import pathlib

import pytest
from fastapi.testclient import TestClient

from app import i18n
from app import artist_scenes


_LANG_DIR = pathlib.Path(i18n.__file__).resolve().parent.parent.parent / "lang"


@pytest.fixture
def artist_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(artist_scenes.config, "ARTIST_DATA_DIR", str(tmp_path))
    return tmp_path


def _write_inventory(directory, artists):
    (directory / ".charts-artist-inventory.json").write_text(
        json.dumps({"version": 1, "artists": artists}), encoding="utf-8")


def test_catalog_marks_source_names_without_deezer_id_pending(artist_data_dir):
    _write_inventory(artist_data_dir, {
        "name:energy artist": {
            "key": "name:energy artist",
            "name": "Energy Artist",
            "deezer_id": "",
            "source_counts": {"energy:mundart": {"tracks": 2}},
        },
    })

    catalog = artist_scenes.get_catalog()

    assert catalog["filter_enabled"] is False
    assert catalog["artists"][0]["identity_verified"] is False
    assert catalog["artists"][0]["enabled"] is False
    assert catalog["artists"][0]["source_counts"]["energy:mundart"]["tracks"] == 2


def test_link_and_enable_artist_persists_verified_scene_selection(artist_data_dir):
    key = "name:energy artist"
    _write_inventory(artist_data_dir, {
        key: {"key": key, "name": "Energy Artist", "deezer_id": "",
              "source_counts": {}},
    })
    artist_scenes.link_artist(key, "123", "Canonical Artist")

    result = artist_scenes.save_settings({
        "filter_enabled": True,
        "target_scene": "Swiss German Mundart",
        "artists": [{
            "key": key, "enabled": True,
            "scene": "Swiss German Mundart",
            "evidence": "Official artist biography",
        }],
    }, "admin")

    assert result["filter_enabled"] is True
    artist = result["artists"][0]
    assert artist["name"] == "Canonical Artist"
    assert artist["deezer_id"] == "123"
    assert artist["enabled"] is True
    assert artist["reviewed_by"] == "admin"
    assert artist["reviewed_at"]
    saved = json.loads(
        (artist_data_dir / ".charts-artist-selection.json").read_text(
            encoding="utf-8"))
    assert saved["artists"][key]["evidence"] == "Official artist biography"


def test_unverified_artist_cannot_be_enabled(artist_data_dir):
    key = "name:unverified"
    _write_inventory(artist_data_dir, {
        key: {"key": key, "name": "Unverified", "deezer_id": "",
              "source_counts": {}},
    })

    with pytest.raises(artist_scenes.ArtistSceneError) as error:
        artist_scenes.save_settings({
            "filter_enabled": False,
            "target_scene": "Swiss German Mundart",
            "artists": [{
                "key": key, "enabled": True,
                "scene": "Swiss German Mundart",
            }],
        }, "admin")

    assert error.value.code == "artist_not_verified"


def test_manual_artist_limit_applies_when_promoting_existing_selection(
        artist_data_dir):
    selection = artist_scenes._default_selection()
    selection["artists"] = {
        f"deezer:{index}": {
            "deezer_id": str(index), "manual": True,
        }
        for index in range(1, artist_scenes.MAX_MANUAL_ARTISTS + 1)
    }
    selection["artists"]["deezer:999"] = {
        "deezer_id": "999", "manual": False,
    }
    (artist_data_dir / ".charts-artist-selection.json").write_text(
        json.dumps(selection), encoding="utf-8")

    with pytest.raises(artist_scenes.ArtistSceneError) as error:
        artist_scenes.add_manual_artist("999", "New Manual Artist")

    assert error.value.code == "too_many_manual"


def _load_main(monkeypatch, data_dir):
    monkeypatch.setenv("CONFIG_PASSWORD", "secret")
    monkeypatch.setenv("CONFIG_USERNAME", "admin")
    monkeypatch.setenv("ARTIST_DATA_DIR", str(data_dir))
    monkeypatch.setenv("CHARTS_LANG", "en")
    import app.config as config_mod
    importlib.reload(config_mod)
    i18n.setup(lang="en", lang_dir=_LANG_DIR)
    import app.main as main_mod
    importlib.reload(main_mod)
    return main_mod


def test_artist_routes_require_auth(tmp_path, monkeypatch):
    main_mod = _load_main(monkeypatch, tmp_path)
    client = TestClient(main_mod.app)

    requests = [
        ("get", "/api/mundart/artists", None),
        ("post", "/api/mundart/settings", {}),
        ("post", "/api/mundart/artists/link", {}),
        ("post", "/api/mundart/artists/add", {}),
    ]
    for method, url, payload in requests:
        response = getattr(client, method)(
            url, json=payload) if payload is not None else client.get(url)
        assert response.status_code == 401


def test_authenticated_routes_link_and_save_artist(tmp_path, monkeypatch):
    main_mod = _load_main(monkeypatch, tmp_path)
    key = "name:energy artist"
    _write_inventory(tmp_path, {
        key: {"key": key, "name": "Energy Artist", "deezer_id": "",
              "source_counts": {}},
    })
    monkeypatch.setattr(
        main_mod.collector, "verify_deezer_artist",
        lambda artist_id: {"ok": True, "id": artist_id, "name": "Canonical Artist"},
    )
    main_mod._sessions["token"] = "admin"
    client = TestClient(main_mod.app)
    client.cookies.set("session", "token")

    linked = client.post("/api/mundart/artists/link",
                         json={"key": key, "deezer_id": "123"})
    assert linked.status_code == 200
    assert linked.json()["artists"][0]["deezer_id"] == "123"

    saved = client.post("/api/mundart/settings", json={
        "filter_enabled": True,
        "target_scene": "Swiss German Mundart",
        "artists": [{
            "key": key, "enabled": True, "scene": "Swiss German Mundart",
            "evidence": "Official artist biography",
        }],
    })
    assert saved.status_code == 200
    assert saved.json()["filter_enabled"] is True
    assert saved.json()["artists"][0]["enabled"] is True
