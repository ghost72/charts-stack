"""
Presence-guard + Option-B tests for charts-recommend (parity with sync/energy).

A song whose only surviving copy lives in a Lidarr "Greatest Hits (YYYY)" album
folder must NOT be written to the missing file (which charts-download consumes),
even though the folder name trips a compilation marker. Two layers protect it:
  1. _looks_like_compilation short-circuits to False on a "(YYYY)" path.
  2. presence.find_existing() finds the copy and _match_all routes it into
     `matched` instead of `missing`.

Uses a real tmp_path music tree with empty audio files; find_existing only lists
names, it never decodes audio.
"""

import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import config, library, main, presence


def _make_song(music_dir, artist_folder, album_folder, filename):
    album = music_dir / artist_folder / album_folder
    album.mkdir(parents=True, exist_ok=True)
    (album / filename).write_bytes(b"")


class _NoMatchLib:
    def find_match(self, artist, title, threshold):
        return None, 0


def test_looks_like_compilation_year_album_is_false():
    year_path = os.sep.join(
        ["", "music", "Artist", "Greatest Hits (2008)",
         "Artist - Greatest Hits - 05 - Into You.mp3"])
    assert library._looks_like_compilation(year_path, "Greatest Hits",
                                           "Artist") is False
    comp_path = os.sep.join(
        ["", "music", "Various Artists", "Bravo Hits 2020", "VA - 05 - X.mp3"])
    assert library._looks_like_compilation(comp_path, "Bravo Hits 2020",
                                           "Various Artists") is True


def test_presence_guard_keeps_year_album_song_out_of_missing(tmp_path,
                                                             monkeypatch):
    music = tmp_path / "Music"
    _make_song(music, "Ariana Grande", "Greatest Hits (2008)",
               "Ariana Grande - Greatest Hits - 05 - Into You.mp3")
    monkeypatch.setattr(config, "MUSIC_DIR", str(music))

    assert presence.find_existing(str(music), "Ariana Grande", "Into You")

    matched, missing = main._match_all(
        _NoMatchLib(), [(1, "Ariana Grande", "Into You")])
    assert missing == []
    assert len(matched) == 1
    assert matched[0][:3] == (1, "Ariana Grande", "Into You")
    assert "Greatest Hits (2008)" in matched[0][3]


def test_presence_guard_absent_song_still_missing(tmp_path, monkeypatch):
    music = tmp_path / "Music"
    music.mkdir()
    monkeypatch.setattr(config, "MUSIC_DIR", str(music))
    matched, missing = main._match_all(
        _NoMatchLib(), [(1, "Nobody", "Unowned Song")])
    assert matched == []
    assert missing == [(1, "Nobody", "Unowned Song")]
