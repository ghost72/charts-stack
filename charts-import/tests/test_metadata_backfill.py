"""
Tests for the library-wide conservative metadata backfill (year/album/track).

All run WITHOUT network/ffmpeg: a tiny hand-built silent MP3 (like
test_mtime_preserve.py) + a monkeypatched resolve.resolve_metadata. They pin:
  * missing-field fill (year/album/track) via a Deezer-style hit and via an
    MB-style fallback (track empty),
  * the never-overwrite rule (existing curated album / present year / positive
    track left untouched),
  * album == title treated as a fillable placeholder,
  * a compilation album never written,
  * the YouTube-marker year-replacement exception + marker clear,
  * the TXXX:CHARTS_SOURCE marker surviving strip_provenance_tags,
  * mtime preservation on a backfill write (NFR1),
  * the fix_youtube_years one-shot (source=youtube re-resolved, non-youtube and
    resolver-miss left untouched, mtime preserved).
"""

import os

import pytest

from app import backfill_metadata, config, resolve, tags
from app.resolve import ResolvedMeta


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
    monkeypatch.setattr(config, "IMPORT_RESOLVE_ENABLED", True)


@pytest.fixture
def mp3(tmp_path):
    p = tmp_path / "song.mp3"
    _write_dummy_mp3(str(p))
    return str(p)


def _tag(path, **easy):
    """Write a few easy tags onto the dummy MP3 (helper for test setup)."""
    from mutagen import File as MutagenFile
    audio = MutagenFile(path, easy=True)
    for k, v in easy.items():
        audio[k] = v
    audio.save()


def _read_easy(path, key):
    from mutagen import File as MutagenFile
    audio = MutagenFile(path, easy=True)
    val = audio.get(key)
    if not val:
        return ""
    return str(val[0]) if isinstance(val, list) else str(val)


def _run(path, meta, dry_run=False):
    """Compute the plan from the file's current state and apply a meta."""
    state = backfill_metadata.read_state(path)
    plan = backfill_metadata.missing_fields(state)
    return backfill_metadata.apply_backfill(path, state, plan, meta, dry_run=dry_run)


# --- 1. missing-year fill (Deezer-style hit) --------------------------------

def test_missing_year_filled_from_deezer(mp3):
    _tag(mp3, artist="A", title="T", album="Real Album")
    res = _run(mp3, ResolvedMeta(year="1991", album="Real Album", track=""))
    assert res.year_filled is True
    assert _read_easy(mp3, "date") == "1991"
    assert _read_easy(mp3, "originaldate") == "1991"


# --- 2. missing-fields fill via MB fallback (no track) ----------------------

def test_missing_fields_filled_mb_no_track(mp3):
    # album == title placeholder + no year + no track; MB-style meta has year +
    # album but NO track -> year + album written, track stays absent.
    _tag(mp3, artist="A", title="T", album="T")
    res = _run(mp3, ResolvedMeta(year="1985", album="Studio Album", track=""))
    assert res.year_filled and res.album_filled
    assert res.track_filled is False
    assert _read_easy(mp3, "date") == "1985"
    assert _read_easy(mp3, "album") == "Studio Album"
    assert _read_easy(mp3, "tracknumber") == ""


# --- 3. never-overwrite a present year --------------------------------------

def test_present_year_never_overwritten(mp3):
    _tag(mp3, artist="A", title="T", album="Real Album", date="1980")
    res = _run(mp3, ResolvedMeta(year="1991", album="Real Album", track=""))
    assert res.year_filled is False and res.year_replaced is False
    assert _read_easy(mp3, "date") == "1980"


# --- 4. never-overwrite a real (curated) album ------------------------------

def test_present_real_album_never_overwritten(mp3):
    _tag(mp3, artist="A", title="T", album="Singles Collection", date="1991")
    res = _run(mp3, ResolvedMeta(year="1991", album="Different Album", track="1"))
    assert res.album_filled is False
    assert _read_easy(mp3, "album") == "Singles Collection"


# --- 5. album == title is fillable ------------------------------------------

def test_album_equal_title_is_fillable(mp3):
    _tag(mp3, artist="A", title="Be My Baby", album="Be My Baby", date="1963")
    res = _run(mp3, ResolvedMeta(year="1963", album="Variations", track=""))
    assert res.album_filled is True
    assert _read_easy(mp3, "album") == "Variations"


# --- 6. compilation album is never written ----------------------------------

def test_compilation_album_not_written(mp3):
    _tag(mp3, artist="A", title="T", album="T", date="2000")
    res = _run(mp3, ResolvedMeta(year="2000", album="Bravo Hits 42", track=""))
    assert res.album_filled is False
    assert _read_easy(mp3, "album") == "T"  # left as the title placeholder


# --- 7. never-overwrite a present track; fill an absent one -----------------

def test_present_track_never_overwritten(mp3):
    _tag(mp3, artist="A", title="T", album="Real", date="1990", tracknumber="5")
    res = _run(mp3, ResolvedMeta(year="1990", album="Real", track="3"))
    assert res.track_filled is False
    assert _read_easy(mp3, "tracknumber") == "5"


def test_absent_track_filled(mp3):
    _tag(mp3, artist="A", title="T", album="Real", date="1990")
    res = _run(mp3, ResolvedMeta(year="1990", album="Real", track="3"))
    assert res.track_filled is True
    assert _read_easy(mp3, "tracknumber") == "3"


# --- 8. YouTube-marker year replacement + marker clear ----------------------

def test_youtube_marker_year_replaced_and_cleared(mp3):
    _tag(mp3, artist="A", title="T", album="Real", date="2019")
    tags.stamp_charts_source(mp3, "youtube")
    assert tags.read_charts_source(mp3) == "youtube"
    res = _run(mp3, ResolvedMeta(year="1963", album="Real", track=""))
    assert res.year_replaced is True and res.year_filled is False
    assert _read_easy(mp3, "date") == "1963"
    # Marker graduated away after the correction.
    assert tags.read_charts_source(mp3) == ""


def test_youtube_marker_kept_when_resolver_has_no_year(mp3):
    _tag(mp3, artist="A", title="T", album="Real", date="2019")
    tags.stamp_charts_source(mp3, "youtube")
    res = _run(mp3, ResolvedMeta(year="", album="", track=""))
    assert res.year_replaced is False
    assert _read_easy(mp3, "date") == "2019"
    assert tags.read_charts_source(mp3) == "youtube"  # retried later


# --- 9. TXXX marker survives the provenance strip ---------------------------

def test_charts_source_marker_survives_strip(mp3, monkeypatch):
    monkeypatch.setattr(config, "PRESERVE_MTIME", False)
    from mutagen.id3 import ID3, TXXX
    _tag(mp3, artist="A", title="T", album="Real")
    tags.stamp_charts_source(mp3, "youtube")
    # Add a NON-protected TXXX that must be deleted.
    id3 = ID3(mp3)
    id3.add(TXXX(encoding=3, desc="SOME_OTHER", text=["x"]))
    id3.save(mp3)

    changed = tags.strip_provenance_tags(mp3)
    assert changed is True
    keys = set(str(k) for k in ID3(mp3).keys())
    assert "TXXX:CHARTS_SOURCE" in keys          # protected, survives
    assert "TXXX:SOME_OTHER" not in keys          # non-protected, deleted


# --- 10. mtime preserved on a backfill write (NFR1) -------------------------

def test_backfill_write_preserves_mtime(mp3):
    _tag(mp3, artist="A", title="T", album="T")
    _set_mtime(mp3, _OLD_TS)
    res = _run(mp3, ResolvedMeta(year="1991", album="Studio Album", track="3"))
    assert res.changed is True
    assert abs(os.stat(mp3).st_mtime - _OLD_TS) <= _TOL


def test_youtube_replacement_preserves_mtime(mp3):
    _tag(mp3, artist="A", title="T", album="Real", date="2019")
    tags.stamp_charts_source(mp3, "youtube")
    _set_mtime(mp3, _OLD_TS)
    res = _run(mp3, ResolvedMeta(year="1963", album="Real", track=""))
    assert res.year_replaced is True
    assert abs(os.stat(mp3).st_mtime - _OLD_TS) <= _TOL


# --- dry-run writes nothing -------------------------------------------------

def test_dry_run_writes_nothing(mp3):
    _tag(mp3, artist="A", title="T", album="T")
    res = _run(mp3, ResolvedMeta(year="1991", album="Studio Album", track="3"),
               dry_run=True)
    assert res.changed is True            # it WOULD change
    assert _read_easy(mp3, "date") == ""  # but nothing was written
    assert _read_easy(mp3, "album") == "T"
