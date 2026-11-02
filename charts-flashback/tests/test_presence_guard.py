"""
Presence-guard + Option-B tests for charts-flashback (parity with sync/energy).

A song whose only surviving copy lives in a Lidarr "Greatest Hits (YYYY)" album
folder must NOT be written to missing-flashback.txt (which charts-download
consumes), even though the folder name trips a compilation marker. Two layers
protect it:
  1. _looks_like_compilation short-circuits to False on a "(YYYY)" path.
  2. presence.find_existing() finds the copy; run_once counts it as present and
     never appends it to `missing`.

Uses a real tmp_path music tree with empty audio files; find_existing only lists
names, it never decodes audio. The GraphQL fetch and Pushover are stubbed so the
run is offline and pure.
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


def test_presence_guard_finds_year_album_copy(tmp_path):
    music = tmp_path / "Music"
    _make_song(music, "Ariana Grande", "Greatest Hits (2008)",
               "Ariana Grande - Greatest Hits - 05 - Into You.mp3")
    assert presence.find_existing(str(music), "Ariana Grande", "Into You")


def test_run_once_keeps_present_song_out_of_missing(tmp_path, monkeypatch):
    music = tmp_path / "Music"
    _make_song(music, "Ariana Grande", "Greatest Hits (2008)",
               "Ariana Grande - Greatest Hits - 05 - Into You.mp3")
    monkeypatch.setattr(config, "MUSIC_DIR", str(music))
    # Make the run deterministic and offline.
    monkeypatch.setattr(config, "FLASHBACK_HASH", "dummy-hash")
    monkeypatch.setattr(config, "FLASHBACK_CHANNELS", ["80s"])
    monkeypatch.setattr(config, "FLASHBACK_LIMIT", 100)
    monkeypatch.setattr(config, "WRITE_MISSING", True)

    monkeypatch.setattr(main.library, "build_index", lambda _d: _NoMatchLib())
    monkeypatch.setattr(main, "_load_state", lambda: {"seen": {}})
    monkeypatch.setattr(main, "_save_state", lambda _s: None)
    monkeypatch.setattr(main.flashback, "fetch_played",
                        lambda ch, h, lim: [("Ariana Grande", "Into You", 2016)])
    monkeypatch.setattr(main.deezer, "resolve_canonical",
                        lambda a, t: (a, t))
    monkeypatch.setattr(main.year_mod, "resolve_year",
                        lambda a, t, ty: str(ty or ""))
    monkeypatch.setattr(main, "_notify", lambda *a, **k: None)

    written = {}

    def _fake_write_missing(missing, path):
        written["missing"] = list(missing)

    monkeypatch.setattr(main, "write_missing", _fake_write_missing)
    monkeypatch.setattr(main, "write_year_map", lambda missing: None)

    counters, _errs, _att, _fail = main.run_once()

    # The present copy must be counted as matched, not written to missing.
    assert counters["missing"] == 0
    assert counters["matched"] == 1
    # write_missing is only called when there IS a missing list; here none.
    assert written.get("missing", []) == []
