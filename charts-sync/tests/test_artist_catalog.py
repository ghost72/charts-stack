"""Tests for the persistent artist inventory and Mundart scene filter."""

import json
import os
import hashlib

import pytest

from app import artist_catalog, config, deezer
from app.deezer import ChartTrack


def test_all_sources_contribute_scenes_without_changing_primary_filters(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from app import main, library

    for key in ("CHARTS_DIR", "PLAYLIST_DIR", "MUSIC_DIR"):
        monkeypatch.setattr(config, key, str(tmp_path))
    monkeypatch.setattr(config, "CHARTS_ARTIST_PLAYLIST", "mundart.m3u")
    sources = [
        config.ChartSource(
            feeds=[config.Feed(kind="deezer", source=identifier, deezer_type="playlist", limit=10)],
            name=name, title=name, missing=f"missing-{identifier}.txt", limit=10, genre_filter=False)
        for identifier, name in [("1", "mundart.m3u"), ("2", "france.m3u")]
    ]
    monkeypatch.setattr(config, "CHARTS", sources)
    selection = {
        "version": 1, "filter_enabled": True, "target_scene": "French Rock",
        "scene_playlists": True, "artists": {
            "deezer:123": {"name": "French Artist", "deezer_id": "123",
                          "enabled": True, "scene": "French Rock"},
        },
    }
    (tmp_path / ".charts-artist-selection.json").write_text(json.dumps(selection))
    legacy = "mundart-szene-french-rock-" + hashlib.sha256(b"french rock").hexdigest()[:10] + ".m3u"
    (tmp_path / legacy).write_text("#EXTM3U\n#PLAYLIST:Mundart: French Rock\n#CHARTS-STACK:MUNDART-SCENE\n")
    calls = []

    def fetch(feed, _client):
        calls.append(feed.source)
        return ([ChartTrack(1, "Other Artist", "Other", artist_id="456")] if feed.source == "1"
                else [ChartTrack(1, "French Artist", "French Hit", artist_id="123")])

    monkeypatch.setattr(deezer, "_fetch_one_feed", fetch)
    lib = SimpleNamespace(tracks=[1], find_match=lambda artist, title, threshold:
                          (SimpleNamespace(path=str(tmp_path / f"{title}.mp3")), 100))
    monkeypatch.setattr(library, "build_index", lambda _directory: lib)
    monkeypatch.setattr(main, "_load_state", lambda: {})
    monkeypatch.setattr(main, "_save_state", lambda _state: None)
    monkeypatch.setattr(main.notify, "send", lambda *_: None)
    main.run_sync()
    assert calls == ["1", "2"]
    assert not (tmp_path / "mundart.m3u").exists()  # existing main filter excludes Other
    assert "French Hit.mp3" in (tmp_path / "france.m3u").read_text()
    migrated = tmp_path / "charts-french-rock.m3u"
    assert migrated.read_text().startswith("#EXTM3U\n#PLAYLIST:French Rock\n")
    assert "French Hit.mp3" in migrated.read_text()
    assert set(artist_catalog._load_inventory()["artists"]) == {"deezer:123", "deezer:456"}

    # Renaming a new scene yields a clean filename, independently of the main source.
    selection["artists"]["deezer:123"]["scene"] = "French Pop"
    selection["target_scene"] = "French Pop"
    (tmp_path / ".charts-artist-selection.json").write_text(json.dumps(selection))
    main.run_sync()
    assert "#PLAYLIST:French Pop" in (tmp_path / "charts-french-pop.m3u").read_text()
    assert not (tmp_path / legacy).exists()

    previous = (tmp_path / "charts-french-pop.m3u").read_text()
    def failed_feed(feed, client):
        if feed.source == "2":
            raise RuntimeError("source unavailable")
        return fetch(feed, client)
    monkeypatch.setattr(deezer, "_fetch_one_feed", failed_feed)
    main.run_sync()
    assert (tmp_path / "charts-french-pop.m3u").read_text() == previous

    monkeypatch.setattr(deezer, "_fetch_one_feed", fetch)
    selection["artists"]["deezer:456"] = {
        "deezer_id": "456", "enabled": True, "scene": "French-Pop"}
    (tmp_path / ".charts-artist-selection.json").write_text(json.dumps(selection))
    main.run_sync()
    assert (tmp_path / "charts-french-pop.m3u").read_text() == previous

    selection["artists"]["deezer:456"]["scene"] = "French Pop"
    selection["artists"]["deezer:123"]["scene"] = "French Rock"
    selection["artists"]["deezer:123"]["manual"] = True
    selection["scene_downloads"] = {"French Rock": 1}
    (tmp_path / ".charts-artist-selection.json").write_text(json.dumps(selection))
    monkeypatch.setattr(config, "CHARTS", [sources[1]])
    calls.clear()
    def with_manual(feed, client):
        if feed.source == "123":
            calls.append(feed.source)
            return [ChartTrack(1, "French Artist", "Manual Extra", artist_id="123")]
        return fetch(feed, client)
    monkeypatch.setattr(deezer, "_fetch_one_feed", with_manual)
    lib.find_match = lambda artist, title, threshold: (
        (None, 0) if title == "Manual Extra"
        else (SimpleNamespace(path=str(tmp_path / f"{title}.mp3")), 100))
    main.run_sync()
    assert calls == ["2", "123"]
    assert "Manual Extra" not in (tmp_path / "france.m3u").read_text()
    assert '#SCENES: ["French Rock"]' in (tmp_path / artist_catalog.SCENE_MISSING_FILE).read_text()
    assert "1. French Artist - Manual Extra" in (tmp_path / artist_catalog.SCENE_MISSING_FILE).read_text()


def test_deleted_legacy_scene_stays_blocked_without_replacement(tmp_path, monkeypatch):
    from app import main
    monkeypatch.setattr(config, "PLAYLIST_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CHARTS_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CHARTS", [])
    name = "mundart-szene-mundart-pop-30a58c7081.m3u"
    selection = {
        "version": 1, "filter_enabled": False, "target_scene": "Mundart-Pop",
        "scene_playlists": True, "artists": {
            "deezer:123": {"enabled": True, "deezer_id": "123", "scene": "Mundart-Pop"},
        }, "disabled_scene_playlists": {name: "Mundart: Mundart-Pop"},
    }
    assert artist_catalog.scene_filename("Mundart-Pop", selection) == "charts-mundart-pop.m3u"
    assert artist_catalog.configured_scenes(selection) == {}
    (tmp_path / ".charts-artist-selection.json").write_text(json.dumps(selection))
    for filename in (name, "charts-mundart-pop.m3u"):
        (tmp_path / filename).write_text("#EXTM3U\n#PLAYLIST:Mundart-Pop\n#CHARTS-STACK:MUNDART-SCENE\n")
    main._apply_scene_playlist_changes()
    assert not (tmp_path / name).exists()
    assert not (tmp_path / "charts-mundart-pop.m3u").exists()
    selection["disabled_scene_playlists"]["charts-rock-pop.m3u"] = "Rock & Pop"
    selection["artists"]["deezer:123"]["scene"] = "Rock-Pop"
    with pytest.raises(artist_catalog.ArtistCatalogError, match="disabled playlist"):
        artist_catalog.configured_scenes(selection)


def test_real_genre_tags_generate_or_playlists_without_feeds_or_audio_changes(tmp_path, monkeypatch):
    import wave
    from mutagen.wave import WAVE
    from mutagen.id3 import TCON
    from app import main

    music = tmp_path / "Music"
    charts = tmp_path / "Charts"
    music.mkdir()
    charts.mkdir()
    for key, value in (("MUSIC_DIR", music), ("PLAYLIST_DIR", music), ("CHARTS_DIR", charts)):
        monkeypatch.setattr(config, key, str(value))
    monkeypatch.setattr(config, "CHARTS", [])
    def audio(name, genres):
        path = music / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as output:
            output.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
            output.writeframes(b"\0" * 32)
        if genres:
            tagged = WAVE(path)
            tagged.add_tags()
            tagged.tags.add(TCON(encoding=3, text=genres))
            tagged.save()
        return path
    first = audio("Artist/Album/01 - One.wav", ["Rock;Pop"])
    second = audio("Artist/Album/02 - Two.wav", ["rock"])
    jazz = audio("Artist/Album/03 - Jazz.wav", ["Jazz"])
    unknown = audio("Artist/Album/04 - Unknown.wav", [])
    deleted = audio(".charts-trash/old/05 - Deleted.wav", ["Rock"])
    before = {path: path.read_bytes() for path in (first, second, jazz, unknown, deleted)}
    selection_path = charts / ".charts-artist-selection.json"
    selection = {"version": 1, "filter_enabled": False, "target_scene": "My artists",
                 "artists": {}, "genre_playlists": {"Rock Mix": {"genres": ["ROCK", "Pop"]}}}
    selection_path.write_text(json.dumps(selection))
    monkeypatch.setattr(main.deezer, "fetch_chart", lambda *_: pytest.fail("genre rules must not fetch feeds"))
    counters, produced, attempted, _health = main.run_sync()
    assert counters["library_tracks"] == 5
    assert produced == attempted == 0
    m3u = music / "charts-rock-mix.m3u"
    content = m3u.read_text()
    assert content.startswith("#EXTM3U\n#PLAYLIST:Rock Mix\n#CHARTS-STACK:GENRE-PLAYLIST\n")
    assert content.count("01 - One.wav") == 1
    assert content.count("02 - Two.wav") == 1
    assert "Jazz.wav" not in content and "Unknown.wav" not in content and "Deleted.wav" not in content
    assert before == {path: path.read_bytes() for path in before}
    catalog = artist_catalog._load_inventory()["genre_catalog"]
    assert catalog["total"] == 4 and catalog["untagged"] == 1 and catalog["unreadable"] == 0
    assert {entry["name"].casefold(): entry["count"] for entry in catalog["genres"]} == {
        "rock": 2, "pop": 1, "jazz": 1}
    assert not (charts / artist_catalog.SCENE_MISSING_FILE).exists()

    selection["genre_playlists"] = {"French Mix": {"genres": ["Jazz"]}}
    selection["scene_files"] = {m3u.name: "French Mix"}
    selection_path.write_text(json.dumps(selection))
    main.run_sync()
    assert not m3u.exists()
    renamed = music / "charts-french-mix.m3u"
    assert renamed.read_text().splitlines()[2] == "#CHARTS-STACK:GENRE-PLAYLIST"
    assert "Jazz.wav" in renamed.read_text() and "One.wav" not in renamed.read_text()

    selection["genre_playlists"] = {}
    selection["scene_files"] = {}
    selection["pending_scene_deletions"] = {renamed.name: "French Mix"}
    selection_path.write_text(json.dumps(selection))
    monkeypatch.setattr(config, "PLAYLIST_DIR", str(tmp_path / "unavailable"))
    with pytest.raises(FileNotFoundError, match="unavailable"):
        main._apply_scene_playlist_changes()
    assert json.loads(selection_path.read_text())["pending_scene_deletions"]
    assert renamed.exists()
    monkeypatch.setattr(config, "PLAYLIST_DIR", str(music))
    main.run_sync()
    assert not renamed.exists()
    assert "pending_scene_deletions" not in json.loads(selection_path.read_text())


def test_genre_creation_and_deletion_refuse_foreign_files_and_symlinks(tmp_path, monkeypatch):
    from app import main, playlist
    monkeypatch.setattr(config, "PLAYLIST_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CHARTS_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CHARTS", [])
    foreign = tmp_path / "charts-jazz.m3u"
    foreign.write_text("#EXTM3U\n#PLAYLIST:My own Jazz\nsong.mp3\n")
    before = foreign.read_text()
    with pytest.raises(FileExistsError):
        playlist.write_genre_playlist([], foreign.name, "Jazz")
    selection = {"version": 1, "filter_enabled": False, "target_scene": "My artists",
                 "artists": {}, "pending_scene_deletions": {foreign.name: "Jazz"}}
    state = tmp_path / ".charts-artist-selection.json"
    state.write_text(json.dumps(selection))
    with pytest.raises(ValueError, match="unmanaged"):
        main._apply_scene_playlist_changes()
    assert foreign.read_text() == before
    assert json.loads(state.read_text())["pending_scene_deletions"]
    link = tmp_path / "charts-rock.m3u"
    link.symlink_to(foreign)
    with pytest.raises(FileExistsError):
        playlist.write_genre_playlist([], link.name, "Rock")
    assert link.is_symlink() and foreign.read_text() == before


def test_deleted_scene_is_not_recreated_by_stale_collected_tracks(tmp_path, monkeypatch):
    from app import main
    monkeypatch.setattr(config, "PLAYLIST_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CHARTS_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CHARTS", [])
    (tmp_path / ".charts-artist-selection.json").write_text(json.dumps({
        "version": 1, "filter_enabled": False, "target_scene": "My artists",
        "scene_playlists": True, "artists": {}, "scene_downloads": {},
    }))
    track = ChartTrack(1, "Artist", "Song", artist_id="123")
    main._write_scene_playlists({"Deleted": [track]}, lambda *_: str(tmp_path / "Song.mp3"), {})
    main._write_scene_missing([(1, track, "Deleted")], lambda *_: None)
    assert not (tmp_path / "charts-deleted.m3u").exists()
    assert "Song" not in (tmp_path / artist_catalog.SCENE_MISSING_FILE).read_text()


def test_new_genre_file_does_not_replace_a_destination_created_during_install(tmp_path, monkeypatch):
    from app import playlist
    monkeypatch.setattr(config, "PLAYLIST_DIR", str(tmp_path))
    monkeypatch.setattr(config, "MUSIC_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CHARTS", [])
    target = tmp_path / "charts-jazz.m3u"
    original_link = playlist.os.link
    def competing_file(source, destination):
        target.write_text("#EXTM3U\n#PLAYLIST:Unmanaged\nmy-song.mp3\n")
        original_link(source, destination)
    monkeypatch.setattr(playlist.os, "link", competing_file)
    with pytest.raises(FileExistsError):
        playlist.write_genre_playlist([], target.name, "Jazz")
    assert target.read_text() == "#EXTM3U\n#PLAYLIST:Unmanaged\nmy-song.mp3\n"
    assert {path.name for path in tmp_path.iterdir()} == {target.name}


def test_scene_filename_migration_preserves_entries_and_recovers_interrupted_move(tmp_path, monkeypatch):
    from app import playlist
    monkeypatch.setattr(config, "PLAYLIST_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CHARTS", [])
    old = tmp_path / "mundart-szene-mundart-pop-30a58c7081.m3u"
    content = "#EXTM3U\n#PLAYLIST:Mundart: Mundart-Pop\n#CHARTS-STACK:MUNDART-SCENE\n#EXTINF:-1,Artist - Song\nAlbum/Song.mp3\n"
    old.write_text(content)
    new = tmp_path / "charts-mundart-pop.m3u"
    os.link(old, new)
    playlist.migrate_scene_playlists({}, set())
    assert not old.exists()
    assert new.read_text() == content.replace("#PLAYLIST:Mundart: ", "#PLAYLIST:")
    playlist.migrate_scene_playlists({}, set())
    playlist.migrate_scene_playlists({new.name: "Swiss Pop"}, set())
    renamed = tmp_path / "charts-swiss-pop.m3u"
    assert not new.exists()
    assert renamed.read_text().splitlines()[2:] == content.splitlines()[2:]
    assert renamed.read_text().splitlines()[1] == "#PLAYLIST:Swiss Pop"


def test_scene_migration_collisions_do_not_move_any_files(tmp_path, monkeypatch):
    from app import playlist
    monkeypatch.setattr(config, "PLAYLIST_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CHARTS", [])
    old = tmp_path / "mundart-szene-mundart-pop-30a58c7081.m3u"
    content = "#EXTM3U\n#PLAYLIST:Mundart: Mundart-Pop\n#CHARTS-STACK:MUNDART-SCENE\nAlbum/Song.mp3\n"
    old.write_text(content)
    target = tmp_path / "charts-mundart-pop.m3u"
    target.write_text("#EXTM3U\n#PLAYLIST:My own playlist\n")
    with pytest.raises(FileExistsError, match="already exists"):
        playlist.migrate_scene_playlists({}, set())
    assert old.read_text() == content
    assert target.read_text() == "#EXTM3U\n#PLAYLIST:My own playlist\n"
    target.unlink()
    second = tmp_path / "mundart-szene-rock-pop-0123456789.m3u"
    second.write_text(content.replace("Mundart-Pop", "Rock & Pop"))
    with pytest.raises(ValueError, match="collision"):
        playlist.migrate_scene_playlists({old.name: "Rock-Pop"}, set())
    assert old.read_text() == content
    assert second.exists()
    assert not (tmp_path / "charts-rock-pop.m3u").exists()
    second.unlink()
    target.symlink_to(old)
    with pytest.raises(FileExistsError):
        playlist.migrate_scene_playlists({}, set())
    assert old.read_text() == content and target.is_symlink()
    target.unlink()
    from types import SimpleNamespace
    monkeypatch.setattr(config, "CHARTS", [SimpleNamespace(name=target.name)])
    with pytest.raises(ValueError, match="reserved"):
        playlist.migrate_scene_playlists({}, set())
    assert old.read_text() == content and not target.exists()
    monkeypatch.setattr(config, "CHARTS", [])
    real_link = os.link
    def conflicting_link(source, destination, **kwargs):
        target.write_text("#EXTM3U\n#PLAYLIST:Created concurrently\n")
        return real_link(source, destination, **kwargs)
    monkeypatch.setattr(os, "link", conflicting_link)
    with pytest.raises(FileExistsError):
        playlist.migrate_scene_playlists({}, set())
    assert old.read_text() == content
    assert target.read_text() == "#EXTM3U\n#PLAYLIST:Created concurrently\n"


def test_record_discoveries_tracks_sources_and_featured_artists(
        tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CHARTS_DIR", str(tmp_path))
    track = ChartTrack(
        1, "Gölä", "Song", contributors=["Gölä", "Guest Artist"],
        artist_id="123")

    artist_catalog.record_discoveries([
        ("deezer:playlist:42", [track]),
    ])

    with open(tmp_path / ".charts-artist-inventory.json",
              encoding="utf-8") as fh:
        inventory = json.load(fh)
    assert set(inventory["artists"]) == {
        "deezer:123", "name:guest artist",
    }
    assert inventory["artists"]["deezer:123"]["source_counts"][
        "deezer:playlist:42"]["tracks"] == 1
    assert inventory["artists"]["name:guest artist"]["source_counts"][
        "deezer:playlist:42"]["roles"] == ["featured"]


def test_mundart_filter_is_off_by_default_and_collects_all_candidates(
        tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CHARTS_DIR", str(tmp_path))
    monkeypatch.setattr(config, "GENRE_FILTER", False)
    source = config.ChartSource(
        feeds=[config.Feed(kind="deezer", limit=10, deezer_type="playlist",
                           source="42")],
        name="mundart.m3u", title="Mundart", missing="missing.txt", limit=10,
        genre_filter=False,
    )
    tracks = [
        ChartTrack(1, "Approved", "One", artist_id="100"),
        ChartTrack(2, "Unreviewed", "Two", artist_id="200"),
    ]
    monkeypatch.setattr(deezer, "_fetch_one_feed", lambda _feed, _client: tracks)

    assert [track.artist for track in deezer.fetch_chart(source)] == [
        "Approved", "Unreviewed",
    ]
    assert set(artist_catalog._load_inventory()["artists"]) == {
        "deezer:100", "deezer:200",
    }

    selection = {
        "version": 1,
        "filter_enabled": True,
        "target_scene": "Swiss German Mundart",
        "artists": {
            "deezer:100": {
                "deezer_id": "100", "enabled": True,
                "scene": "Swiss German Mundart",
            },
        },
    }
    with open(tmp_path / ".charts-artist-selection.json", "w",
              encoding="utf-8") as fh:
        json.dump(selection, fh)

    assert [track.artist for track in deezer.fetch_chart(source)] == ["Approved"]
    # Filtering must not hide unreviewed artists from the ongoing inventory.
    assert set(artist_catalog._load_inventory()["artists"]) == {
        "deezer:100", "deezer:200",
    }
    assert source.last_stats["artist_filtered"] == 1


def test_linked_feature_artist_can_approve_a_collaboration():
    selection = {
        "version": 1,
        "filter_enabled": True,
        "target_scene": "Swiss German Mundart",
        "artists": {
            "name:mundart guest": {
                "deezer_id": "300", "enabled": True,
                "scene": "Swiss German Mundart",
            },
        },
    }
    track = ChartTrack(1, "Other Act", "Collaboration",
                       contributors=["Other Act", "Mundart Guest"],
                       artist_id="400")

    assert artist_catalog.is_allowed(track, selection)


def test_linked_energy_name_approves_deezer_identity_across_feeds():
    selection = {
        "version": 1,
        "filter_enabled": True,
        "target_scene": "Swiss German Mundart",
        "artists": {
            "name:energy artist": {
                "deezer_id": "300", "enabled": True,
                "scene": "Swiss German Mundart",
            },
        },
    }
    track = ChartTrack(1, "Canonical Artist", "Song", artist_id="300")

    assert artist_catalog.is_allowed(track, selection)


def test_manual_artist_feeds_are_only_created_for_enabled_target_scene():
    selection = {
        "version": 1,
        "filter_enabled": True,
        "target_scene": "Swiss German Mundart",
        "artists": {
            "deezer:123": {
                "deezer_id": "123", "manual": True, "enabled": True,
                "scene": "Swiss German Mundart",
            },
            "deezer:456": {
                "deezer_id": "456", "manual": True, "enabled": False,
                "scene": "Swiss German Mundart",
            },
            "deezer:789": {
                "deezer_id": "789", "manual": True, "enabled": True,
                "scene": "Other scene",
            },
        },
    }

    feeds = artist_catalog.manual_artist_feeds(selection)
    assert [(feed.deezer_type, feed.source, feed.limit) for feed in feeds] == [
        ("artist", "123", 50),
    ]


def test_enabled_manual_artist_adds_top_tracks_to_mundart_feed(
        tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CHARTS_DIR", str(tmp_path))
    monkeypatch.setattr(config, "GENRE_FILTER", False)
    source = config.ChartSource(
        feeds=[config.Feed(kind="deezer", limit=10, deezer_type="playlist",
                           source="42")],
        name="mundart.m3u", title="Mundart", missing="missing.txt", limit=10,
        genre_filter=False,
    )
    selection = {
        "version": 1,
        "filter_enabled": True,
        "target_scene": "Swiss German Mundart",
        "artists": {
            "deezer:42": {
                "deezer_id": "42", "enabled": True,
                "scene": "Swiss German Mundart",
            },
            "deezer:500": {
                "deezer_id": "500", "manual": True, "enabled": True,
                "scene": "Swiss German Mundart",
            },
        },
    }
    (tmp_path / ".charts-artist-selection.json").write_text(
        json.dumps(selection), encoding="utf-8")
    fetched = []

    def fetch(feed, _client):
        fetched.append(feed.source)
        if feed.source == "42":
            return [ChartTrack(1, "Feed Artist", "Feed Song", artist_id="42")]
        return [ChartTrack(1, "Manual Artist", "Manual Song", artist_id="500")]

    monkeypatch.setattr(deezer, "_fetch_one_feed", fetch)

    tracks = deezer.fetch_chart(source)

    assert fetched == ["42", "500"]
    assert [(track.artist, track.title) for track in tracks] == [
        ("Feed Artist", "Feed Song"), ("Manual Artist", "Manual Song"),
    ]


def test_deezer_parser_retains_artist_identity():
    tracks = deezer._parse_tracks([{
        "position": 1,
        "title": "Song",
        "artist": {"id": 123, "name": "Artist"},
    }])

    assert len(tracks) == 1
    assert tracks[0].artist_id == "123"


@pytest.mark.parametrize("name", ["mundart.m3u", "chanson.m3u"])
def test_scene_playlists_leave_primary_and_missing_list_unchanged(tmp_path, monkeypatch, name):
    from types import SimpleNamespace

    from app import main, playlist

    for key in ("CHARTS_DIR", "PLAYLIST_DIR", "MUSIC_DIR"):
        monkeypatch.setattr(config, key, str(tmp_path))
    monkeypatch.setattr(config, "PLAYLIST_ORDER", "chart")
    monkeypatch.setattr(config, "CHARTS_ARTIST_PLAYLIST", name)
    monkeypatch.setattr(main, "_handle_notify", lambda *args: None)
    monkeypatch.setattr(main.notify, "send", lambda *args: None)
    source = config.ChartSource(
        feeds=[config.Feed(kind="deezer", limit=10, deezer_type="playlist", source="42")],
        name=name, title="Primary", missing="missing.txt", limit=10,
        genre_filter=False)
    selection = {
        "version": 1, "filter_enabled": True, "target_scene": "Pop",
        "artists": {
            "deezer:100": {"deezer_id": "100", "enabled": True, "scene": "Pop"},
            "deezer:200": {"deezer_id": "200", "enabled": True, "scene": "Rock"},
            "deezer:300": {"deezer_id": "300", "enabled": True, "scene": "Rock", "manual": True},
        },
    }
    selection_path = tmp_path / ".charts-artist-selection.json"

    def fetch(feed, _client):
        if feed.source == "300":
            return [ChartTrack(1, "Manual", "Three", artist_id="300")]
        return [ChartTrack(1, "Pop", "One", artist_id="100"),
                ChartTrack(2, "Rock", "Two", artist_id="200")]

    monkeypatch.setattr(deezer, "_fetch_one_feed", fetch)
    lib = SimpleNamespace(find_match=lambda artist, title, _threshold:
                          (SimpleNamespace(path=str(tmp_path / f"{title}.mp3")), 100))

    selection_path.write_text(json.dumps(selection))
    primary = tmp_path / name
    if name != "mundart.m3u":
        other = config.ChartSource(
            feeds=source.feeds, name="mundart.m3u", title="Unmanaged",
            missing="missing-other.txt", limit=10, genre_filter=False)
        assert main._sync_source(other, lib, {}) == "ok"
        assert "Two.mp3" in (tmp_path / other.name).read_text()
        assert not (tmp_path / ".charts-artist-inventory.json").exists()
    assert main._sync_source(source, lib, {}) == "ok"
    original = primary.read_text()
    assert "One.mp3" in original and "Two.mp3" not in original
    assert set(artist_catalog._load_inventory()["artists"]) == {"deezer:100", "deezer:200"}
    missing = (tmp_path / "missing.txt").read_text()
    assert not list(tmp_path.glob("mundart-szene-*.m3u"))

    selection["scene_playlists"] = True
    selection_path.write_text(json.dumps(selection))
    assert main._sync_source(source, lib, {}) == "ok"
    assert primary.read_text() == original
    assert (tmp_path / "missing.txt").read_text().splitlines()[2:] == missing.splitlines()[2:]
    pop = tmp_path / artist_catalog.scene_filename("Pop")
    rock = tmp_path / artist_catalog.scene_filename("Rock")
    assert "One.mp3" in pop.read_text() and "Two.mp3" not in pop.read_text()
    assert "Two.mp3" in rock.read_text() and "Three.mp3" in rock.read_text()
    assert "One.mp3" not in rock.read_text()

    queue = tmp_path / artist_catalog.SCENE_MISSING_FILE
    assert not queue.exists()
    selection["scene_downloads"] = {"Rock": 2}
    selection_path.write_text(json.dumps(selection))
    original_fetch = fetch
    monkeypatch.setattr(deezer, "_fetch_one_feed", lambda feed, client:
                        [ChartTrack(i, "Manual", title, artist_id="300")
                         for i, title in enumerate(("Three", "Four", "Five"), 1)]
                        if feed.source == "300" else original_fetch(feed, client))
    previous_match = lib.find_match
    lib.find_match = lambda artist, title, threshold: (
        (None, 0) if title in ("Four", "Five") else previous_match(artist, title, threshold))
    assert main._sync_source(source, lib, {}) == "ok"
    assert "2. Manual - Four" in queue.read_text()
    assert "Manual - Five" not in queue.read_text()
    assert "Manual - Three" not in queue.read_text()
    assert primary.read_text() == original
    assert "Manual - Four" not in (tmp_path / "missing.txt").read_text()
    assert '#SCENES: ["Rock"]' in queue.read_text()
    selection["artists"]["deezer:300"]["scenes"] = ["Rock", "Blues"]
    selection["scene_downloads"]["Blues"] = 1
    selection_path.write_text(json.dumps(selection))
    assert main._sync_source(source, lib, {}) == "ok"
    assert '#SCENES: ["Rock"]' in queue.read_text()
    selection["scene_downloads"]["Blues"] = 2
    selection_path.write_text(json.dumps(selection))
    assert main._sync_source(source, lib, {}) == "ok"
    assert '#SCENES: ["Blues", "Rock"]' in queue.read_text()
    assert queue.read_text().count("Manual - Four") == 1
    assert "Manual - Five" not in queue.read_text()
    selection["artists"]["deezer:300"].pop("scenes")
    selection["scene_downloads"].pop("Blues")
    selection_path.write_text(json.dumps(selection))
    lib.find_match = previous_match
    assert main._sync_source(source, lib, {}) == "ok"
    assert "Four.mp3" in rock.read_text()
    assert "Manual - Four" not in queue.read_text()
    monkeypatch.setattr(deezer, "_fetch_one_feed", original_fetch)

    selection["disabled_scene_playlists"] = {rock.name: "Rock"}
    selection_path.write_text(json.dumps(selection))
    if name != "mundart.m3u":
        assert main._sync_source(other, lib, {}) == "ok"
        assert rock.exists()
    assert main._sync_source(source, lib, {}) == "ok"
    assert not rock.exists()
    assert "Manual -" not in queue.read_text()
    assert pop.exists()
    assert primary.read_text() == original

    rock.write_text("#EXTM3U\n#PLAYLIST:Local playlist\n")
    assert main._sync_source(source, lib, {}) == "ok"
    playlist.write_scene_playlist([], rock.name, "Rock")
    assert rock.read_text() == "#EXTM3U\n#PLAYLIST:Local playlist\n"

    selection["disabled_scene_playlists"] = {}
    selection_path.write_text(json.dumps(selection))
    rock.unlink()
    assert main._sync_source(source, lib, {}) == "ok"
    assert "Two.mp3" in rock.read_text()

    selection["artists"]["deezer:100"]["scenes"] = ["Pop", "Rock"]
    selection_path.write_text(json.dumps(selection))
    assert main._sync_source(source, lib, {}) == "ok"
    assert primary.read_text() == original
    assert "One.mp3" in pop.read_text()
    assert "One.mp3" in rock.read_text()

    selection["scene_files"] = {rock.name: "Rock & Roll"}
    selection["scene_downloads"] = {"Rock & Roll": 2}
    for item in selection["artists"].values():
        item["scenes"] = ["Rock & Roll" if value == "Rock" else value
                          for value in item.get("scenes", [item["scene"]])]
        item["scene"] = item["scenes"][0]
    selection_path.write_text(json.dumps(selection))
    notifications = []
    monkeypatch.setattr(main.notify, "send", lambda *args: notifications.append(args))
    assert main._sync_source(source, lib, {}) == "ok"
    assert not rock.exists()
    rock = tmp_path / artist_catalog.scene_filename("Rock & Roll")
    assert "#PLAYLIST:Rock & Roll" in rock.read_text()
    assert "One.mp3" in rock.read_text()
    assert rock.exists()
    assert not notifications
    assert primary.read_text() == original

    # A stale scene with no remaining assignments still gets its new title.
    selection["artists"] = {}
    selection["scene_files"] = {rock.name: "Old favourites"}
    selection_path.write_text(json.dumps(selection))
    entries = rock.read_text().splitlines()[2:]
    assert main._sync_source(source, lib, {}) == "empty"
    assert not rock.exists()
    rock = tmp_path / artist_catalog.scene_filename("Old favourites")
    assert rock.read_text().splitlines()[1] == "#PLAYLIST:Old favourites"
    assert rock.read_text().splitlines()[2:] == entries
    assert not notifications
    assert primary.read_text() == original

    local = "#EXTM3U\n#PLAYLIST:Local playlist\n"
    rock.write_text(local)
    playlist.rename_scene_playlist(rock.name, "Do not replace")
    assert rock.read_text() == local
    rock.unlink()
    rock.symlink_to(primary)
    playlist.rename_scene_playlist(rock.name, "Do not follow")
    assert primary.read_text() == original
