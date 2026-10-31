"""
Tests for the periodic library-wide metadata pass (backfill._metadata_pass).

No network: resolve.resolve_metadata is monkeypatched. Pins the end-to-end
library walk (fills a missing year), the never-re-query state memory (a settled
file is skipped on the next pass with zero resolver calls), the per-pass limit,
and the disabled no-op.
"""

import os

import pytest

from app import backfill, config, resolve
from app.resolve import ResolvedMeta


def _write_dummy_mp3(path: str) -> None:
    header = b"\xff\xfb\x90\x40"
    frame = header + b"\x00" * (417 - 4)
    with open(path, "wb") as fh:
        fh.write(frame * 30)


def _tag(path, **easy):
    from mutagen import File as MutagenFile
    audio = MutagenFile(path, easy=True)
    for k, v in easy.items():
        audio[k] = v
    audio.save()


def _read(path, key):
    from mutagen import File as MutagenFile
    audio = MutagenFile(path, easy=True)
    val = audio.get(key)
    return (str(val[0]) if isinstance(val, list) else str(val)) if val else ""


@pytest.fixture(autouse=True)
def _cfg(monkeypatch):
    monkeypatch.setattr(config, "PRESERVE_MTIME", True)
    monkeypatch.setattr(config, "IMPORT_RESOLVE_ENABLED", True)
    monkeypatch.setattr(config, "METADATA_BACKFILL_ENABLED", True)
    monkeypatch.setattr(config, "METADATA_BACKFILL_LIMIT", 50)
    monkeypatch.setattr(config, "BACKFILL_SLEEP_MS", 0)


@pytest.fixture
def library(tmp_path, monkeypatch):
    music = tmp_path / "music"
    (music / "A").mkdir(parents=True)
    p = music / "A" / "song.mp3"
    _write_dummy_mp3(str(p))
    _tag(str(p), artist="A", title="T", album="T")  # no year -> fillable
    monkeypatch.setattr(config, "MUSIC_DIR", str(music))
    return str(music), str(p)


def test_pass_fills_missing_year(library, monkeypatch):
    music, p = library
    calls = {"n": 0}

    def _resolve(a, t):
        calls["n"] += 1
        return ResolvedMeta(year="1991", album="T", track="")

    monkeypatch.setattr(resolve, "resolve_metadata", _resolve)
    res = backfill._metadata_pass(music)
    assert res["year_filled"] == 1
    assert _read(p, "date") == "1991"
    assert calls["n"] == 1


def test_settled_file_not_requeried_next_pass(library, monkeypatch):
    music, p = library
    calls = {"n": 0}

    def _resolve(a, t):
        calls["n"] += 1
        return ResolvedMeta(year="1991", album="T", track="")

    monkeypatch.setattr(resolve, "resolve_metadata", _resolve)
    backfill._metadata_pass(music)   # first pass: 1 call, fills + marks done
    assert calls["n"] == 1
    backfill._metadata_pass(music)   # second pass: file settled -> no new call
    assert calls["n"] == 1


def test_disabled_is_noop(library, monkeypatch):
    music, p = library
    monkeypatch.setattr(config, "METADATA_BACKFILL_ENABLED", False)
    monkeypatch.setattr(resolve, "resolve_metadata",
                        lambda a, t: ResolvedMeta(year="1991"))
    res = backfill._metadata_pass(music)
    assert res == {"scanned": 0, "year_filled": 0, "year_replaced": 0,
                   "album_filled": 0, "track_filled": 0, "errors": 0}
    assert _read(p, "date") == ""  # untouched


def test_limit_caps_corrections(tmp_path, monkeypatch):
    music = tmp_path / "music"
    (music / "A").mkdir(parents=True)
    for i in range(3):
        p = music / "A" / f"s{i}.mp3"
        _write_dummy_mp3(str(p))
        _tag(str(p), artist="A", title=f"T{i}", album=f"T{i}")
    monkeypatch.setattr(config, "MUSIC_DIR", str(music))
    monkeypatch.setattr(config, "METADATA_BACKFILL_LIMIT", 2)
    monkeypatch.setattr(resolve, "resolve_metadata",
                        lambda a, t: ResolvedMeta(year="1991"))
    res = backfill._metadata_pass(str(music))
    assert res["year_filled"] == 2  # capped at the limit
