"""
Tests for the one-shot scripts/fix_youtube_years.py (historical YouTube-year
fix). No network: resolve.resolve_metadata and build_manifest_index are
monkeypatched; a tiny hand-built MP3 carries the year to rewrite.

Pins:
  * a manifest source="youtube" entry is re-resolved and its present year
    REPLACED,
  * a non-youtube (source="charts") entry is left untouched even if the resolver
    would return a year,
  * a resolver miss leaves the file unchanged (fail-open),
  * the year rewrite preserves mtime.
"""

import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import config, resolve, tags  # noqa: E402
from app.resolve import ResolvedMeta  # noqa: E402
from scripts import fix_youtube_years as fyy  # noqa: E402


def _write_dummy_mp3(path: str) -> None:
    header = b"\xff\xfb\x90\x40"
    frame = header + b"\x00" * (417 - 4)
    with open(path, "wb") as fh:
        fh.write(frame * 30)


_OLD_TS = 1_000_000_000.0
_TOL = 1.0


@pytest.fixture(autouse=True)
def _preserve_on(monkeypatch):
    monkeypatch.setattr(config, "PRESERVE_MTIME", True)
    monkeypatch.setattr(config, "IMPORT_RESOLVE_ENABLED", True)


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


def _setup(tmp_path, monkeypatch, source, resolved, artist="A", title="T",
           present_year="2019"):
    """Build a library file + a manifest entry + stub the resolver/index."""
    music = tmp_path / "music"
    (music / artist).mkdir(parents=True)
    path = music / artist / "T.mp3"
    _write_dummy_mp3(str(path))
    _tag(str(path), artist=artist, title=title, album="Real", date=present_year)
    _set = os.utime
    _set(str(path), (_OLD_TS, _OLD_TS))

    monkeypatch.setattr(config, "MUSIC_DIR", str(music))

    # Stub the manifest read -> one entry with the given source.
    entry = {"artist": artist, "title": title, "album": "Real", "source": source}
    monkeypatch.setattr(fyy, "_load_youtube_entries",
                        lambda: [entry] if source == "youtube" else [])
    # Stub the on-disk index to map the normalized key to our file.
    from app import backfill
    key = backfill._norm_lookup_key(artist, title)
    monkeypatch.setattr(fyy, "build_manifest_index", lambda m: {key: [str(path)]})
    # Stub the resolver.
    monkeypatch.setattr(resolve, "resolve_metadata", lambda a, t: resolved)
    return str(path)


def test_youtube_entry_year_replaced(tmp_path, monkeypatch):
    path = _setup(tmp_path, monkeypatch, "youtube",
                  ResolvedMeta(year="1963", album="Real", track=""))
    monkeypatch.setattr(sys, "argv", ["fix_youtube_years", "--apply"])
    rc = fyy.main()
    assert rc == 0
    assert _read(path, "date") == "1963"


def test_non_youtube_entry_untouched(tmp_path, monkeypatch):
    path = _setup(tmp_path, monkeypatch, "charts",
                  ResolvedMeta(year="1963", album="Real", track=""))
    monkeypatch.setattr(sys, "argv", ["fix_youtube_years", "--apply"])
    rc = fyy.main()
    assert rc == 0
    # Non-youtube -> _load_youtube_entries returns [] -> nothing processed.
    assert _read(path, "date") == "2019"


def test_resolver_miss_leaves_file_unchanged(tmp_path, monkeypatch):
    path = _setup(tmp_path, monkeypatch, "youtube",
                  ResolvedMeta(year="", album="", track=""))
    monkeypatch.setattr(sys, "argv", ["fix_youtube_years", "--apply"])
    rc = fyy.main()
    assert rc == 0
    assert _read(path, "date") == "2019"  # unchanged, no exception


def test_year_rewrite_preserves_mtime(tmp_path, monkeypatch):
    path = _setup(tmp_path, monkeypatch, "youtube",
                  ResolvedMeta(year="1963", album="Real", track=""))
    monkeypatch.setattr(sys, "argv", ["fix_youtube_years", "--apply"])
    fyy.main()
    assert _read(path, "date") == "1963"
    assert abs(os.stat(path).st_mtime - _OLD_TS) <= _TOL


def test_dry_run_writes_nothing(tmp_path, monkeypatch):
    path = _setup(tmp_path, monkeypatch, "youtube",
                  ResolvedMeta(year="1963", album="Real", track=""))
    monkeypatch.setattr(sys, "argv", ["fix_youtube_years", "--dry-run"])
    fyy.main()
    assert _read(path, "date") == "2019"  # default dry-run leaves it
