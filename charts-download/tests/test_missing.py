"""
Tests for charts-download missing-file merging (multi-source labels).

A chart song can appear in several missing files (e.g. missing.txt -> "charts"
and missing-hitparade.txt -> "hitparade"). load_all must keep the song ONCE but
collect EVERY source label (deduplicated, in file order), so the manifest and
the Pushover messages can list all contributing playlists.

All tests run WITHOUT network/Docker.
"""

import pathlib
import sys
import subprocess
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import missing


def _write_missing(path, lines):
    """Write a minimal missing file ('  <pos>. Artist - Title')."""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# Fehlende Chart-Songs\n\n")
        for pos, text in lines:
            fh.write(f"  {pos}. {text}\n")


def test_song_in_two_files_collects_both_labels_in_order(tmp_path):
    charts = tmp_path / "missing.txt"
    hitparade = tmp_path / "missing-hitparade.txt"
    # Ashe - Stop the Wedding! is in BOTH files.
    _write_missing(charts, [(1, "Ashe - Stop the Wedding!"),
                            (2, "Remi Wolf - Bottle")])
    _write_missing(hitparade, [(3, "Ashe - Stop the Wedding!")])

    result = missing.load_all([str(charts), str(hitparade)])

    # The overlapping song appears exactly ONCE.
    ashe = [e for e in result if e[1] == "Ashe"]
    assert len(ashe) == 1
    _pos, _artist, _title, sources = ashe[0]
    # Both labels, in file order (charts first = primary), deduplicated.
    assert sources == ["charts", "hitparade"]

    # A song only in the first file carries just that label.
    remi = [e for e in result if e[1] == "Remi Wolf"][0]
    assert remi[3] == ["charts"]


def test_same_song_twice_in_one_file_is_not_duplicated(tmp_path):
    charts = tmp_path / "missing.txt"
    _write_missing(charts, [(1, "Ashe - Stop the Wedding!"),
                            (5, "Ashe - Stop the Wedding!")])
    result = missing.load_all([str(charts)])
    assert len(result) == 1
    assert result[0][3] == ["charts"]


def test_position_sort_and_exclude_artists_preserved(tmp_path, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "EXCLUDE_ARTISTS", ["Nemo"])
    charts = tmp_path / "missing.txt"
    _write_missing(charts, [(9, "Zeynep - Inat"),
                            (2, "Nemo - The Code"),
                            (4, "Sido - Issa")])
    result = missing.load_all([str(charts)])

    # Nemo excluded, remaining sorted ascending by position.
    titles = [e[2] for e in result]
    assert titles == ["Issa", "Inat"]
    assert all(e[3] == ["charts"] for e in result)


def test_missing_positions_above_999_are_parsed(tmp_path):
    energy = tmp_path / "missing-energy-library.txt"
    _write_missing(energy, [(1001, "Artist - Song")])
    assert missing.parse_missing_file(str(energy)) == [(1001, "Artist", "Song")]


def test_energy_search_fairness_persists_across_runs(tmp_path, monkeypatch):
    from app import config, main

    energy = tmp_path / "missing-energy-library.txt"
    _write_missing(energy, [(i, f"Artist - Song {i}") for i in range(1, 7)])
    monkeypatch.setattr(config, "MISSING_FILES", [str(energy)])
    monkeypatch.setattr(missing, "SCENE_MISSING_PATH", str(tmp_path / "none"))
    monkeypatch.setattr(config, "DRY_RUN", False)
    monkeypatch.setattr(config, "MAX_PER_RUN", 2)
    monkeypatch.setattr(config, "MAX_PER_SOURCE", 2)
    monkeypatch.setattr(config, "SEARCH_COOLDOWN_HOURS", 0)
    monkeypatch.setattr(main.slsk, "connect", lambda: object())
    monkeypatch.setattr(main, "_requeue_failed", lambda *_: (0, []))
    monkeypatch.setattr(main, "_cleanup_stale", lambda *_: None)
    monkeypatch.setattr(main.notify, "send", lambda *args: None)
    monkeypatch.setattr(config, "STATE_FILE", str(tmp_path / "download-state.json"))
    searched = []
    monkeypatch.setattr(main.slsk, "find_candidates",
                        lambda _client, artist, title, **kwargs:
                        searched.append(title) or [])
    for _ in range(3):
        main.run_once()

    assert searched == [f"Song {i}" for i in range(1, 7)]


def test_energy_fairness_respects_other_sources_and_custom_filename(tmp_path, monkeypatch):
    from app import config, main

    chart = tmp_path / "missing.txt"
    energy = tmp_path / "missing-radio.txt"
    _write_missing(chart, [(1, "Chart - Hit")])
    _write_missing(energy, [(1, "Radio - One"), (2, "Radio - Two")])
    monkeypatch.setattr(config, "MISSING_FILES", [str(chart), str(energy)])
    monkeypatch.setattr(config, "ENERGY_LIBRARY_MISSING_FILE", "missing-radio.txt")
    monkeypatch.setattr(missing, "SCENE_MISSING_PATH", str(tmp_path / "none"))
    monkeypatch.setattr(config, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(config, "DRY_RUN", False)
    monkeypatch.setattr(config, "MAX_PER_RUN", 2)
    monkeypatch.setattr(config, "MAX_PER_SOURCE", 1)
    monkeypatch.setattr(main.slsk, "connect", lambda: object())
    monkeypatch.setattr(main, "_requeue_failed", lambda *_: (0, []))
    monkeypatch.setattr(main, "_cleanup_stale", lambda *_: None)
    monkeypatch.setattr(main.notify, "send", lambda *args: None)
    monkeypatch.setattr(main.manifest, "record", lambda *args: None)
    searches = []
    monkeypatch.setattr(main.slsk, "find_candidates",
                        lambda _client, artist, title, **kwargs:
                        searches.append((artist, title)) or
                        [("peer", {"filename": title + ".mp3", "bitRate": 320}, 100)])
    monkeypatch.setattr(main.slsk, "enqueue", lambda *_: True)

    assert main.run_once()["enqueued"] == 2
    assert searches == [("Chart", "Hit"), ("Radio", "One")]
    assert main.run_once()["enqueued"] == 1
    assert searches[-1] == ("Radio", "Two")


def test_energy_writer_and_downloader_clear_front_of_large_backlog(tmp_path, monkeypatch):
    from app import config, main

    energy_dir = pathlib.Path(__file__).resolve().parents[2] / "charts-energy"
    producer = """
import sys
from app import config, library, main, presence, state, year
config.CHARTS_DIR = sys.argv[1]
config.YEAR_MAP_FILE = sys.argv[1] + '/years.json'
config.LIBRARY_MISSING_FILE = 'missing-energy-library.txt'
config.active_channels = lambda: [
    config.Channel(slug=f'channel{i}', mode='library') for i in range(5)]
state.load = lambda: {}
state.tracks_for = lambda _, slug: [
    {'artist': slug, 'title': f'Song {i}'} for i in range(100)]
class EmptyLibrary:
    def find_match(self, *args): return None, 0
library.build_index = lambda _: EmptyLibrary()
presence.find_existing = lambda *args: None
year.resolve_year = lambda *args: ('', False)
main._notify = lambda *args: None
main.run_match()
"""
    subprocess.run([sys.executable, "-c", producer, str(tmp_path)],
                   cwd=energy_dir, check=True, capture_output=True, text=True)
    energy = tmp_path / "missing-energy-library.txt"
    monkeypatch.setattr(config, "MISSING_FILES", [str(energy)])
    monkeypatch.setattr(missing, "SCENE_MISSING_PATH", str(tmp_path / "none"))
    monkeypatch.setattr(config, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(config, "DRY_RUN", False)
    monkeypatch.setattr(config, "MAX_PER_RUN", 40)
    monkeypatch.setattr(config, "MAX_PER_SOURCE", 10)
    monkeypatch.setattr(main.slsk, "connect", lambda: object())
    monkeypatch.setattr(main, "_requeue_failed", lambda *_: (0, []))
    monkeypatch.setattr(main, "_cleanup_stale", lambda *_: None)
    monkeypatch.setattr(main.notify, "send", lambda *args: None)
    searched = []
    monkeypatch.setattr(main.slsk, "find_candidates",
                        lambda _client, artist, title, **kwargs:
                        searched.append((artist, title)) or [])

    for _ in range(11):
        main.run_once()

    assert {artist for artist, _ in searched[:10]} == {
        f"channel{i}" for i in range(5)}
    assert len(searched) == len(set(searched)) == 110
    assert ("channel0", "Song 20") in searched
    assert ("channel4", "Song 20") in searched


@pytest.mark.parametrize("dry_run,api_error", [(True, False), (False, True)])
def test_energy_fairness_does_not_record_dry_runs_or_failed_api(
        tmp_path, monkeypatch, dry_run, api_error):
    from app import config, main

    energy = tmp_path / "missing-energy-library.txt"
    _write_missing(energy, [(1, "Artist - Song")])
    monkeypatch.setattr(config, "MISSING_FILES", [str(energy)])
    monkeypatch.setattr(missing, "SCENE_MISSING_PATH", str(tmp_path / "none"))
    monkeypatch.setattr(config, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(config, "DRY_RUN", dry_run)
    monkeypatch.setattr(main.slsk, "connect", lambda: object())
    monkeypatch.setattr(main, "_requeue_failed", lambda *_: (0, []))
    monkeypatch.setattr(main, "_cleanup_stale", lambda *_: None)
    monkeypatch.setattr(main.notify, "send", lambda *args: None)

    def search(*args, **kwargs):
        if api_error:
            raise main.slsk.SlskdError("offline")
        return [("peer", {"filename": "song.mp3", "bitRate": 320}, 100)]

    monkeypatch.setattr(main.slsk, "find_candidates", search)
    main.run_once()
    assert not main.state.load().get("energy_search_order")


def test_scene_queue_is_loaded_automatically_and_preserves_chart_priority(tmp_path, monkeypatch):
    from app import config, main, missing

    charts = tmp_path / "missing.txt"
    charts.write_text("1. Artist - Shared\n")
    scene = tmp_path / ".charts-scene-missing.txt"
    scene.write_text('#SCENES: ["Mundart-Pop", "Rock"]\n1. Artist - Shared\n'
                     '#SCENES: ["Mundart-Pop"]\n2. Artist - Scene Only\n')
    monkeypatch.setattr(missing, "SCENE_MISSING_PATH", str(scene))
    monkeypatch.setattr(config, "MISSING_FILES", [str(charts)])
    assert main._load_entries() == [
        (1, "Artist", "Shared", ["charts", "Playlist: Mundart-Pop", "Playlist: Rock"]),
        (2, "Artist", "Scene Only", ["Playlist: Mundart-Pop"]),
    ]
    scene.write_text("")
    assert main._load_entries() == [(1, "Artist", "Shared", ["charts"])]


def test_scene_metadata_is_per_song_and_legacy_queue_has_readable_label(tmp_path, caplog):
    scene = tmp_path / ".charts-scene-missing.txt"
    scene.write_text('#SCENES: ["Rock", "Rock"]\n1. Artist - First\n'
                     '2. Artist - Legacy\n#SCENES: not-json\n3. Artist - Invalid\n'
                     '#SCENES: ["Pop"]\n4. Artist - Fourth\n')
    assert missing.load_all([str(scene)]) == [
        (1, "Artist", "First", ["Playlist: Rock"]),
        (2, "Artist", "Legacy", [missing.SCENE_LABEL_PREFIX + missing.t("download.source.scene_tracks")]),
        (4, "Artist", "Fourth", ["Playlist: Pop"]),
    ]
    assert any(record.levelname == "ERROR" for record in caplog.records)


def test_sync_writer_roundtrip_preserves_scene_names(tmp_path):
    sync_dir = pathlib.Path(__file__).resolve().parents[2] / "charts-sync"
    subprocess.run([
        sys.executable, "-c",
        "import sys; from app import config, playlist; "
        "config.CHARTS_DIR = sys.argv[1]; "
        "playlist.write_missing([(2, 'Polo Hofer', 'Sennehund')], "
        "'.charts-scene-missing.txt', scene_sources={"
        "('Polo Hofer', 'Sennehund'): ['Mundart-Pop', 'Swiss German Mundart']})",
        str(tmp_path),
    ], cwd=sync_dir, check=True, capture_output=True, text=True)
    queue = tmp_path / ".charts-scene-missing.txt"
    assert missing.load_all([str(queue)]) == [
        (2, "Polo Hofer", "Sennehund",
         ["Playlist: Mundart-Pop", "Playlist: Swiss German Mundart"]),
    ]
    assert missing.parse_missing_file(str(queue)) == [(2, "Polo Hofer", "Sennehund")]


def test_named_scenes_retain_shared_round_robin_bucket(monkeypatch):
    from app import config, main
    monkeypatch.setattr(config, "MAX_PER_SOURCE", 1)
    entries = [
        (1, "Artist", "First", ["Playlist: Pop"]),
        (2, "Artist", "Second", ["Playlist: Rock"]),
        (3, "Artist", "Chart", ["charts", "Playlist: Pop"]),
    ]
    assert main._interleave_by_source(entries) == [entries[0], entries[2], entries[1]]


def test_search_fallback_preserves_blocked_provider_filter(monkeypatch):
    from app import slsk

    def response(user):
        return {"username": user, "hasFreeUploadSlot": True,
                "files": [{"filename": r"Artist\Artist - Title.mp3", "bitRate": 320}]}

    calls = []

    def search(_client, query):
        calls.append(query)
        return [response("unapproved")] + ([response("approved")] if query == "Title" else [])

    monkeypatch.setattr(slsk, "_search_and_collect", search)
    result = slsk.find_candidates(None, "Artist", "Title",
                                  blocked_users={"unapproved"})
    assert calls == ["Artist Title", "Title"]
    assert [user for user, _file, _score in result] == ["approved"]


@pytest.mark.parametrize("max_run,max_source", [(1, 0), (10, 1)])
def test_scene_queue_searches_enqueues_and_records_import_metadata(
        tmp_path, monkeypatch, max_run, max_source):
    from app import config, main, missing

    scene = tmp_path / ".charts-scene-missing.txt"
    scene.write_text('#SCENES: ["Rock & Roll", "Pop"]\n1. Example - First\n'
                     '#SCENES: ["Mundart-Pop"]\n2. Example - Second\n')
    monkeypatch.setattr(missing, "SCENE_MISSING_PATH", str(scene))
    monkeypatch.setattr(config, "MISSING_FILES", [])
    monkeypatch.setattr(config, "DRY_RUN", False)
    monkeypatch.setattr(config, "MAX_PER_RUN", max_run)
    monkeypatch.setattr(config, "MAX_PER_SOURCE", max_source)
    monkeypatch.setattr(main.slsk, "connect", lambda: object())
    monkeypatch.setattr(main, "_requeue_failed", lambda *_: (0, []))
    monkeypatch.setattr(main, "_cleanup_stale", lambda *_: None)
    messages = []
    monkeypatch.setattr(main.notify, "send", lambda title, body: messages.append(body))
    st = {}
    monkeypatch.setattr(main.state, "load", lambda: st)
    monkeypatch.setattr(main.state, "save", lambda *_: None)
    searched, transfers, metadata = [], [], []

    def search(_client, artist, title, blocked_users=None):
        searched.append((artist, title))
        return [("any-peer", {"filename": f"Example - {title}.mp3", "bitRate": 320}, 100)]

    monkeypatch.setattr(main.slsk, "find_candidates", search)
    monkeypatch.setattr(main.slsk, "enqueue",
                        lambda _client, user, file: transfers.append((user, file)) or True)
    monkeypatch.setattr(main.manifest, "record", lambda *args: metadata.append(args))
    result = main.run_once()
    assert result["enqueued"] == 1
    assert searched == [("Example", "First")]
    assert transfers[0][0] == "any-peer"
    assert metadata == [("Example - First.mp3", "Example", "First",
                         ["Playlist: Rock & Roll", "Playlist: Pop"])]
    assert "Playlist: Rock &amp; Roll+Playlist: Pop" in messages[0]
    assert missing.SCENE_SOURCE not in messages[0]
    assert main.state.already_requested(st, "Example", "First")
