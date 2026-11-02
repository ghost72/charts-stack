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
