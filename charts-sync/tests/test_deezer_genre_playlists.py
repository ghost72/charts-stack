"""Genre charts match library titles and reuse the managed missing queue."""

import json
from types import SimpleNamespace

import httpx
import pytest

from app import artist_catalog, config, deezer, main
from app.deezer import ChartTrack


@pytest.fixture
def genre_run(tmp_path, monkeypatch):
    music, charts = tmp_path / "Music", tmp_path / "Charts"
    music.mkdir()
    charts.mkdir()
    for key, value in (("MUSIC_DIR", music), ("PLAYLIST_DIR", music), ("CHARTS_DIR", charts)):
        monkeypatch.setattr(config, key, str(value))
    monkeypatch.setattr(config, "CHARTS", [])
    present = music / "present.mp3"
    present.write_bytes(b"library music stays untouched")
    local_only = music / "local-only.mp3"
    local_only.write_bytes(b"same local tag, not on chart")
    lib = SimpleNamespace(
        tracks=[SimpleNamespace(path=str(present), genres=["Not Pop"]),
                SimpleNamespace(path=str(local_only), genres=["Pop"])],
        find_match=lambda artist, title, _threshold:
        (SimpleNamespace(path=str(present)), 100) if title == "Present" else (None, 0),
    )
    monkeypatch.setattr(main.library, "build_index", lambda _: lib)
    monkeypatch.setattr(main.presence, "find_existing", lambda *_: None)
    state = {"version": 1, "filter_enabled": False, "target_scene": "My artists",
             "scene_playlists": False, "artists": {}, "genre_playlists": {
                 "Pop Mix": {"genre_id": "132", "genre_name": "Pop", "limit": 50}}}
    path = charts / ".charts-artist-selection.json"
    path.write_text(json.dumps(state))
    return music, charts, state, path


def test_genre_chart_uses_titles_not_local_tags_and_queues_only_missing(genre_run, monkeypatch):
    music, charts, state, _path = genre_run
    calls = []
    def fetch(genre_id, limit):
        calls.append((genre_id, limit))
        return [ChartTrack(1, "Artist", "Present"), ChartTrack(2, "Artist", "Missing"),
                ChartTrack(3, "Artist", "Present")]
    monkeypatch.setattr(deezer, "fetch_genre_chart", fetch)
    counters, produced, attempted, _health = main.run_sync()
    assert calls == [("132", 50)] and produced == attempted == 1
    assert counters["sources"] == 1
    content = (music / "charts-pop-mix.m3u").read_text()
    assert content.count("present.mp3") == 1 and "local-only.mp3" not in content
    assert content.splitlines()[2] == "#CHARTS-STACK:GENRE-PLAYLIST"
    queue = (charts / artist_catalog.SCENE_MISSING_FILE).read_text()
    assert '#SCENES: ["Pop Mix"]' in queue and "Artist - Missing" in queue
    assert "Artist - Present" not in queue
    assert "genre_catalog" not in artist_catalog._load_inventory()
    assert (music / "present.mp3").read_bytes() == b"library music stays untouched"
    main._write_scene_missing([(2, ChartTrack(2, "Artist", "Missing"), "Pop Mix")],
                              lambda *_: None, genre_rules={"Pop Mix": {**state["genre_playlists"]["Pop Mix"], "limit": 25}})
    assert (charts / artist_catalog.SCENE_MISSING_FILE).read_text().count("Artist - Missing") == 0


def test_shared_missing_merges_genre_names_and_survives_failed_regular_source(genre_run, monkeypatch):
    _music, charts, state, path = genre_run
    state["genre_playlists"]["Second Pop"] = {**state["genre_playlists"]["Pop Mix"], "limit": 25}
    path.write_text(json.dumps(state))
    monkeypatch.setattr(deezer, "fetch_genre_chart",
                        lambda *_: [ChartTrack(1, "Artist", "Missing")])
    monkeypatch.setattr(config, "CHARTS", [
        config.ChartSource([], "charts.m3u", "Charts", "missing.txt", 50)])
    monkeypatch.setattr(main, "_sync_source", lambda *_: "error")
    counters, produced, attempted, _health = main.run_sync()
    assert counters["sources"] == produced == 2 and attempted == 3
    queue = (charts / artist_catalog.SCENE_MISSING_FILE).read_text()
    assert queue.count("Artist - Missing") == 1
    assert '#SCENES: ["Pop Mix", "Second Pop"]' in queue


def test_fetch_failure_preserves_playlist_and_clears_stale_missing(genre_run, monkeypatch):
    music, charts, _state, _path = genre_run
    m3u = music / "charts-pop-mix.m3u"
    before = "#EXTM3U\n#PLAYLIST:Pop Mix\n#CHARTS-STACK:GENRE-PLAYLIST\npresent.mp3\n"
    m3u.write_text(before)
    queue = charts / artist_catalog.SCENE_MISSING_FILE
    queue.write_text('#SCENES: ["Pop Mix"]\n1. Artist - Stale\n')
    monkeypatch.setattr(deezer, "fetch_genre_chart",
                        lambda *_: (_ for _ in ()).throw(ValueError("Deezer unavailable")))
    with pytest.raises(RuntimeError, match="Pop Mix: Deezer unavailable"):
        main.run_sync()
    assert m3u.read_text() == before and "Artist - Stale" not in queue.read_text()


def test_rule_deleted_during_fetch_is_not_recreated_or_queued(genre_run, monkeypatch):
    music, charts, state, path = genre_run
    def fetch(*_):
        state["genre_playlists"] = {}
        path.write_text(json.dumps(state))
        return [ChartTrack(1, "Artist", "Missing")]
    monkeypatch.setattr(deezer, "fetch_genre_chart", fetch)
    counters, produced, attempted, _health = main.run_sync()
    assert produced == attempted == counters["sources"] == 0
    assert not (music / "charts-pop-mix.m3u").exists()
    assert not (charts / artist_catalog.SCENE_MISSING_FILE).exists()


def test_edited_rule_during_fetch_is_not_written_with_stale_titles(genre_run, monkeypatch):
    music, charts, state, path = genre_run
    def fetch(*_):
        state["genre_playlists"]["Pop Mix"] = {"genre_id": "152", "genre_name": "Rock", "limit": 25}
        path.write_text(json.dumps(state))
        return [ChartTrack(1, "Artist", "Missing")]
    monkeypatch.setattr(deezer, "fetch_genre_chart", fetch)
    main.run_sync()
    assert not (music / "charts-pop-mix.m3u").exists()
    assert not (charts / artist_catalog.SCENE_MISSING_FILE).exists()


@pytest.mark.parametrize("payload", [{"error": {"message": "bad genre"}}, {},
                                   {"data": []}, {"data": [{"artist": "invalid"}]}])
def test_strict_genre_api_rejects_failed_empty_or_invalid_payload(monkeypatch, payload):
    original = httpx.Client
    monkeypatch.setattr(deezer.httpx, "Client", lambda **kwargs: original(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)), **kwargs))
    with pytest.raises((ValueError, RuntimeError)):
        deezer.fetch_genre_chart("132", 50)


def test_genre_api_fetches_official_chart_with_limit_and_deduplicates(monkeypatch):
    original = httpx.Client
    def respond(request):
        assert request.url.path == "/chart/132/tracks"
        assert request.url.params["limit"] == "3" and request.url.params["index"] == "0"
        return httpx.Response(200, json={"data": [
            {"id": 1, "artist": {"name": "Artist"}, "title": "One"},
            {"id": 1, "artist": {"name": "Artist"}, "title": "One"},
            {"id": 2, "artist": {"name": "Artist"}, "title": "Two"},
        ]})
    monkeypatch.setattr(deezer.httpx, "Client", lambda **kwargs: original(
        transport=httpx.MockTransport(respond), **kwargs))
    tracks = deezer.fetch_genre_chart("132", 3)
    assert [(track.position, track.title) for track in tracks] == [(1, "One"), (2, "Two")]
