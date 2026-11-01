"""
Tests for the durable TXXX provenance markers in app.tags, focused on the
clear_charts_album_src counterpart to clear_charts_source that the Manual Tag
Editor's save path relies on.

All run WITHOUT network/ffmpeg: a tiny hand-built silent MP3 (like
test_metadata_backfill.py) + the raw mutagen ID3 interface. They pin:
  * clear_charts_album_src removes a stamped TXXX:CHARTS_ALBUM_SRC marker,
  * clearing is a harmless no-op on a file without the marker,
  * clearing preserves mtime (NFR1 / PRESERVE_MTIME),
  * clearing CHARTS_ALBUM_SRC leaves a co-present CHARTS_SOURCE marker intact.
"""

import os

import pytest

from app import config, tags


# --- tiny valid MP3 ---------------------------------------------------------

def _write_dummy_mp3(path: str) -> None:
    header = b"\xff\xfb\x90\x40"
    frame = header + b"\x00" * (417 - 4)
    with open(path, "wb") as fh:
        fh.write(frame * 30)


_OLD_TS = 1_000_000_000.0
_TOL = 1.0


def _set_mtime(path: str, ts: float) -> None:
    os.utime(path, (ts, ts))


@pytest.fixture(autouse=True)
def _preserve_on(monkeypatch):
    monkeypatch.setattr(config, "PRESERVE_MTIME", True)


@pytest.fixture
def mp3(tmp_path):
    p = tmp_path / "song.mp3"
    _write_dummy_mp3(str(p))
    return str(p)


# --- (a) clear removes a stamped marker -------------------------------------

def test_clear_charts_album_src_removes_marker(mp3):
    tags.stamp_charts_album_src(mp3, "resolver")
    assert tags.read_charts_album_src(mp3) == "resolver"
    tags.clear_charts_album_src(mp3)
    assert tags.read_charts_album_src(mp3) == ""


# --- (b) clear on a file without the marker is a harmless no-op --------------

def test_clear_charts_album_src_noop_when_absent(mp3):
    # No marker present (and no ID3 header at all) -> no error, still empty.
    assert tags.read_charts_album_src(mp3) == ""
    tags.clear_charts_album_src(mp3)
    assert tags.read_charts_album_src(mp3) == ""


# --- (c) clearing the marker preserves mtime (NFR1) -------------------------

def test_clear_charts_album_src_preserves_mtime(mp3):
    tags.stamp_charts_album_src(mp3, "resolver")
    _set_mtime(mp3, _OLD_TS)
    tags.clear_charts_album_src(mp3)
    assert tags.read_charts_album_src(mp3) == ""
    assert abs(os.stat(mp3).st_mtime - _OLD_TS) <= _TOL


# --- (d) clearing CHARTS_ALBUM_SRC leaves CHARTS_SOURCE intact --------------

def test_clear_charts_album_src_leaves_charts_source(mp3):
    tags.stamp_charts_source(mp3, "youtube")
    tags.stamp_charts_album_src(mp3, "resolver")
    tags.clear_charts_album_src(mp3)
    assert tags.read_charts_album_src(mp3) == ""
    assert tags.read_charts_source(mp3) == "youtube"
