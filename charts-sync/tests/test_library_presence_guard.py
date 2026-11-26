"""
Tests for the presence guard (Option A) and the Lidarr "(YYYY)" compilation
short-circuit (Option B) in charts-sync.

The key regression: a chart song whose ONLY library copy lives inside a folder
whose name trips a compilation marker (e.g. a Lidarr "Greatest Hits (2008)"
album) must NOT be written to the missing list, even with IGNORE_COMPILATIONS=1.

No network: deezer.fetch_chart is stubbed; MUSIC_DIR / CHARTS_DIR are real tmp
dirs so the real presence.find_existing walks real files (empty files, since the
guard reads only filenames). Playlist/missing output lands in the tmp dirs and is
read back.
"""

import os

import pytest

from app import config, deezer, library, main, playlist, presence
from app.deezer import ChartTrack


def test_invalid_genre_retains_matching_metadata_and_reports_error(monkeypatch, caplog):
    tags = {"artist": ["Artist"], "title": ["Song"], "album": ["Album"],
            "albumartist": ["Artist"], "genre": ["Bad\nGenre"]}
    monkeypatch.setattr(library, "MutagenFile", lambda *_args, **_kwargs: tags)
    assert library._read_tags("file.mp3") == ("Artist", "Song", "Album", "Artist", [], True)
    assert "Could not read genre metadata" in caplog.text
    tags["genre"] = ["R&B", "Pop/Rock;ROCK", "rock"]
    assert library._read_tags("file.mp3")[4:] == (["R&B", "Pop/Rock", "ROCK"], False)
    tags["genre"] = [None]
    assert library._read_tags("file.mp3") == ("Artist", "Song", "Album", "Artist", [], True)


def test_numeric_mp4_genres_use_the_real_easy_tag_wrapper(monkeypatch):
    import io
    import struct
    from mutagen.easymp4 import EasyMP4Tags
    from mutagen.mp4 import Atoms
    def atom(name, data):
        return struct.pack(">I4s", len(data) + 8, name) + data
    genre_atom = atom(b"gnre", atom(b"data", b"\0" * 8 + struct.pack(">H", 18)))
    metadata = atom(b"moov", atom(b"udta", atom(b"meta", b"\0" * 4 + atom(b"ilst", genre_atom))))
    stream = io.BytesIO(metadata)
    audio = EasyMP4Tags()
    audio.load(Atoms(stream), stream)
    assert audio["genre"] == ["Rock"]
    monkeypatch.setattr(library, "MutagenFile", lambda *_args, **_kwargs: audio)
    assert library._read_tags("file.m4a")[4:] == (["Rock"], False)

def test_id3_genre_codes_are_decoded_but_plain_number_tags_are_not(tmp_path, monkeypatch):
    import wave
    from mutagen.wave import WAVE
    from mutagen.id3 import TCON
    path = tmp_path / "coded.wav"
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        output.writeframes(b"\0" * 32)
    tagged = WAVE(path)
    tagged.add_tags()
    tagged.tags.add(TCON(encoding=3, text=["(13)", "17"]))
    tagged.save()
    before = path.read_bytes()
    assert library._read_tags(str(path))[4:] == (["Pop", "Rock"], False)
    assert path.read_bytes() == before
    monkeypatch.setattr(library, "MutagenFile", lambda *_args, **_kwargs: {"genre": ["13"]})
    assert library._read_tags("plain-text-genre.flac")[4:] == (["13"], False)


def test_unavailable_or_partial_library_scan_does_not_publish_an_empty_index(tmp_path, monkeypatch):
    with pytest.raises(FileNotFoundError, match="unavailable"):
        library.build_index(str(tmp_path / "missing"))
    def denied_walk(_root, *, onerror):
        onerror(PermissionError("unreadable album directory"))
        return iter(())
    monkeypatch.setattr(library.os, "walk", denied_walk)
    with pytest.raises(PermissionError, match="unreadable"):
        library.build_index(str(tmp_path))


@pytest.fixture(autouse=True)
def tmp_dirs(tmp_path, monkeypatch):
    music = tmp_path / "Music"
    charts = tmp_path / "Charts"
    music.mkdir()
    charts.mkdir()
    monkeypatch.setattr(config, "MUSIC_DIR", str(music))
    monkeypatch.setattr(config, "CHARTS_DIR", str(charts))
    monkeypatch.setattr(config, "PLAYLIST_DIR", str(charts))
    monkeypatch.setattr(config, "IGNORE_COMPILATIONS", True)
    # No Pushover in tests.
    monkeypatch.setattr(main.notify, "send", lambda *a, **k: None)
    return {"music": music, "charts": charts}


def _touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("")


def _make_greatest_hits(music):
    """Sandra's only copy of 'Everlasting Love' lives in a Lidarr GH album."""
    _touch(str(music / "Sandra" / "Greatest Hits (2008)" / "CD 01"
                / "Sandra - Greatest Hits - 05 - Everlasting Love.mp3"))


class _StubSource:
    """Minimal ChartSource stand-in for _sync_source."""

    def __init__(self):
        self.name = "testchart.m3u"
        self.title = "Test Chart"
        self.missing = "missing-test.txt"
        self.exclude_artists = set()
        self.last_stats = {}


def _run_one_source(monkeypatch, chart_tracks):
    """Build a real Library over MUSIC_DIR, drive _sync_source once."""
    monkeypatch.setattr(deezer, "fetch_chart", lambda src: chart_tracks)
    monkeypatch.setattr(config, "PLAYLIST_ORDER", "chart")
    lib = library.build_index(config.MUSIC_DIR)
    state = {}
    outcome = main._sync_source(_StubSource(), lib, state)
    return outcome


def _missing_body():
    path = os.path.join(config.CHARTS_DIR, "missing-test.txt")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [ln for ln in fh.read().splitlines()
                if ln and not ln.startswith("#")]


def _playlist_lines():
    # write_playlist writes <name>.m3u into PLAYLIST_DIR.
    for name in os.listdir(config.PLAYLIST_DIR):
        if name.endswith(".m3u"):
            with open(os.path.join(config.PLAYLIST_DIR, name), encoding="utf-8") as fh:
                return fh.read().splitlines()
    return []


# --- AC-1: Greatest-Hits suppression ----------------------------------------

def test_greatest_hits_copy_suppresses_missing(monkeypatch, tmp_dirs):
    _make_greatest_hits(tmp_dirs["music"])
    _run_one_source(monkeypatch, [ChartTrack(1, "Sandra", "Everlasting Love")])

    body = _missing_body()
    assert body == [], f"song wrongly reported missing: {body}"
    # The library file is a playlist entry.
    pl = "\n".join(_playlist_lines())
    assert "Everlasting Love" in pl


# --- AC-3: genuinely missing song IS written ---------------------------------

def test_genuinely_missing_song_is_written(monkeypatch, tmp_dirs):
    # No file anywhere for this artist.
    _run_one_source(monkeypatch, [ChartTrack(1, "Nobody", "Unknown Song")])
    body = _missing_body()
    assert body == ["  1. Nobody - Unknown Song"]


@pytest.mark.parametrize("compilation_artist", ["Lady Gaga", "Various Artists"])
@pytest.mark.parametrize("ignore_compilations", [False, True])
def test_playlist_links_artist_release_not_compilation(
        monkeypatch, tmp_dirs, compilation_artist, ignore_compilations):
    music = tmp_dirs["music"]
    sampler = music / "Various Artists" / "Bravo Hits 67 (2009)" / "CD 02" / "Paparazzi.mp3"
    single = music / "Lady Gaga" / "Paparazzi (2009)" / "Paparazzi.mp3"
    _touch(str(sampler))
    _touch(str(single))
    tags = {
        str(sampler): (compilation_artist, "Paparazzi", "Bravo Hits 67", "Various Artists"),
        str(single): ("Lady Gaga", "Paparazzi", "Paparazzi", "Lady Gaga"),
    }
    monkeypatch.setattr(library, "_read_tags", lambda path: (*tags[path], [], False))
    monkeypatch.setattr(config, "IGNORE_COMPILATIONS", ignore_compilations)

    assert _run_one_source(monkeypatch, [ChartTrack(1, "Lady Gaga", "Paparazzi")]) == "ok"

    entries = [line for line in _playlist_lines() if line and not line.startswith("#")]
    assert entries == [str(single.relative_to(music))]
    assert _missing_body() == []
    assert sampler.exists() and single.exists()


# --- FR-4: presence.find_existing direct -------------------------------------

def test_find_existing_direct(monkeypatch, tmp_dirs):
    music = str(tmp_dirs["music"])
    _make_greatest_hits(tmp_dirs["music"])
    # Found regardless of album folder.
    hit = presence.find_existing(music, "Sandra", "Everlasting Love")
    assert hit is not None and hit.endswith(".mp3")
    # Artist-key fold: lowercase query finds the "Sandra" folder.
    assert presence.find_existing(music, "sandra", "Everlasting Love") is not None
    # Absent title -> None.
    assert presence.find_existing(music, "Sandra", "No Such Song") is None
    # Absent artist -> None.
    assert presence.find_existing(music, "Nobody", "Everlasting Love") is None


# --- AC-4: Option B unit (year album not a compilation; Bravo Hits is) -------

def test_option_b_year_album_not_compilation():
    assert library._looks_like_compilation(
        "/music/Music/Sandra/Greatest Hits (2008)/CD 01/x.mp3",
        "Greatest Hits", "Sandra") is False


def test_option_b_non_year_sampler_still_compilation():
    assert library._looks_like_compilation(
        "/music/Music/Various Artists/Bravo Hits 42/x.mp3",
        "Bravo Hits 42", "Various Artists") is True


# --- AC-5: find_match un-flags the (YYYY) track so it becomes matchable ------

def test_year_album_track_now_matchable_via_find_match(monkeypatch, tmp_dirs):
    """
    Option B flips the GH-(2008) track's is_compilation to False, so find_match
    (with a well-tagged file) no longer skips it. Here we assert the index no
    longer marks it as a compilation; the presence guard then also covers the
    filename-only case.
    """
    _make_greatest_hits(tmp_dirs["music"])
    lib = library.build_index(config.MUSIC_DIR)
    assert lib.tracks, "index should not be empty"
    assert all(not t.is_compilation for t in lib.tracks), \
        "a (YYYY) album track must not be flagged as a compilation"
    assert any(t.in_year_album for t in lib.tracks)
