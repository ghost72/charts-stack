"""
Marker tests for the charts-config copy of clear_charts_album_src.

Mirrors FEAT-001's charts-import test for the copied function: clearing
TXXX:CHARTS_ALBUM_SRC removes it, is a harmless no-op when absent, preserves
mtime, and does not disturb a co-present TXXX:CHARTS_SOURCE marker.
"""

import os

import pytest
from mutagen.id3 import ID3, TXXX

from app import config, tags


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


def _stamp_album_src(path, value):
    try:
        id3 = ID3(path)
    except Exception:
        id3 = ID3()  # bare dummy mp3 has no ID3 header yet
    id3.add(TXXX(encoding=3, desc=tags.CHARTS_ALBUM_SRC_DESC, text=[value]))
    id3.save(path)


def test_clear_removes_marker(mp3):
    _stamp_album_src(mp3, "resolver")
    assert tags.read_charts_album_src(mp3) == "resolver"
    tags.clear_charts_album_src(mp3)
    assert tags.read_charts_album_src(mp3) == ""


def test_clear_is_noop_when_absent(mp3):
    # No ID3 header at all -> clear is a harmless no-op.
    tags.clear_charts_album_src(mp3)
    assert tags.read_charts_album_src(mp3) == ""


def test_clear_preserves_mtime(mp3):
    _stamp_album_src(mp3, "resolver")
    _set_mtime(mp3, _OLD_TS)
    tags.clear_charts_album_src(mp3)
    assert abs(os.stat(mp3).st_mtime - _OLD_TS) <= _TOL


def test_clear_leaves_source_marker(mp3):
    id3 = ID3()
    id3.add(TXXX(encoding=3, desc=tags.CHARTS_SOURCE_DESC, text=["youtube"]))
    id3.add(TXXX(encoding=3, desc=tags.CHARTS_ALBUM_SRC_DESC, text=["resolver"]))
    id3.save(mp3)
    tags.clear_charts_album_src(mp3)
    assert tags.read_charts_album_src(mp3) == ""
    assert tags.read_charts_source(mp3) == "youtube"
