"""Tests for the persistent artist inventory and Mundart scene filter."""

import json

from app import artist_catalog, config, deezer
from app.deezer import ChartTrack


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
