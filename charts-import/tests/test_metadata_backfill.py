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


# --- 6b. junk album (stale cache) is never written (defence-in-depth) -------

def test_junk_album_not_written(mp3):
    # A stale/poisoned resolved album ("c" single-char, or "Various Artists")
    # must be rejected by the last-gate junk guard even though it is not a
    # title-marked compilation.
    for junk in ("c", "Various Artists", "various"):
        _tag(mp3, artist="A", title="T", album="T", date="2000")
        res = _run(mp3, ResolvedMeta(year="2000", album=junk, track=""))
        assert res.album_filled is False, junk
        assert _read_easy(mp3, "album") == "T", junk  # placeholder kept


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


# --- 11. album provenance marker stamped on a fill --------------------------

def _run_rc(path, meta, dry_run=False):
    """Run with --recorrect-albums enabled (opt-in album overwrite)."""
    state = backfill_metadata.read_state(path)
    plan = backfill_metadata.missing_fields(state, recorrect_albums=True)
    return backfill_metadata.apply_backfill(path, state, plan, meta, dry_run=dry_run)


def test_album_fill_stamps_marker_and_survives_strip(mp3):
    # Filling a missing album stamps TXXX:CHARTS_ALBUM_SRC=resolver so the file
    # becomes eligible for future marker-gated re-correction.
    _tag(mp3, artist="A", title="T", album="T", date="1990")
    res = _run(mp3, ResolvedMeta(year="1990", album="Studio Album", track=""))
    assert res.album_filled is True
    assert tags.read_charts_album_src(mp3) == "resolver"
    assert _read_easy(mp3, "album") == "Studio Album"


def test_charts_album_src_marker_survives_strip(mp3, monkeypatch):
    monkeypatch.setattr(config, "PRESERVE_MTIME", False)
    from mutagen.id3 import ID3, TXXX
    _tag(mp3, artist="A", title="T", album="Real")
    tags.stamp_charts_album_src(mp3, "resolver")
    id3 = ID3(mp3)
    id3.add(TXXX(encoding=3, desc="SOME_OTHER", text=["x"]))
    id3.save(mp3)
    assert tags.strip_provenance_tags(mp3) is True
    keys = set(str(k) for k in ID3(mp3).keys())
    assert "TXXX:CHARTS_ALBUM_SRC" in keys     # protected, survives
    assert "TXXX:SOME_OTHER" not in keys        # non-protected, deleted


# --- 12. re-correction: marker-gated overwrite ------------------------------

def test_recorrect_overwrites_marker_stamped_album(mp3):
    # A present album the stack wrote (CHARTS_ALBUM_SRC=resolver) is overwritten
    # with a freshly resolved non-secondary original under --recorrect-albums.
    _tag(mp3, artist="A", title="T", album="On The Night", date="1993")
    tags.stamp_charts_album_src(mp3, "resolver")
    res = _run_rc(mp3, ResolvedMeta(year="1985", album="Brothers in Arms",
                                    album_year="1985", track=""))
    assert res.album_recorrected is True
    assert res.album_filled is False
    assert _read_easy(mp3, "album") == "Brothers in Arms"
    assert tags.read_charts_album_src(mp3) == "resolver"   # re-stamped


def test_recorrect_keeps_unmarked_curated_album(mp3):
    # A present album WITHOUT the marker (user-curated) is NEVER overwritten,
    # even with --recorrect-albums, when its title is not a secondary edition.
    _tag(mp3, artist="A", title="T", album="My Curated Album", date="1990")
    res = _run_rc(mp3, ResolvedMeta(year="1985", album="Some Original",
                                    album_year="1985", track=""))
    assert res.album_recorrected is False
    assert _read_easy(mp3, "album") == "My Curated Album"


def test_recorrect_disabled_by_default_keeps_marked_album(mp3):
    # Without --recorrect-albums even a marker-stamped present album is kept
    # (re-correction is strictly opt-in).
    _tag(mp3, artist="A", title="T", album="On The Night", date="1993")
    tags.stamp_charts_album_src(mp3, "resolver")
    res = _run(mp3, ResolvedMeta(year="1985", album="Brothers in Arms",
                                 album_year="1985", track=""))
    assert res.album_recorrected is False
    assert _read_easy(mp3, "album") == "On The Night"


def test_recorrect_rejects_secondary_resolved_album(mp3):
    # Even eligible (marker-stamped), the overwrite is refused when the freshly
    # resolved album is ITSELF a secondary edition (never write a worse value).
    _tag(mp3, artist="A", title="T", album="On The Night", date="1993")
    tags.stamp_charts_album_src(mp3, "resolver")
    res = _run_rc(mp3, ResolvedMeta(year="1990", album="Wembley (Live)",
                                    album_year="1990", track=""))
    assert res.album_recorrected is False
    assert _read_easy(mp3, "album") == "On The Night"


# --- 13. re-correction: heuristic catch-up for pre-marker files -------------

def test_recorrect_heuristic_catches_unmarked_secondary(mp3):
    # A pre-marker file (NO marker) whose album is a secondary-edition pattern
    # IS overwritten under --recorrect-albums, but ONLY when the resolved
    # original has a strictly-earlier year than the present edition.
    _tag(mp3, artist="A", title="T", album="Rumours (Deluxe Edition)", date="2004")
    res = _run_rc(mp3, ResolvedMeta(year="1977", album="Rumours",
                                    album_year="1977", track=""))
    assert res.album_recorrected is True
    assert _read_easy(mp3, "album") == "Rumours"
    assert tags.read_charts_album_src(mp3) == "resolver"   # stamped on catch-up


def test_recorrect_heuristic_requires_strictly_earlier_year(mp3):
    # The heuristic refuses to overwrite an unmarked secondary edition when the
    # resolved original is NOT strictly earlier (no confident originality).
    _tag(mp3, artist="A", title="T", album="Rumours (Deluxe Edition)", date="1977")
    res = _run_rc(mp3, ResolvedMeta(year="1977", album="Rumours",
                                    album_year="1977", track=""))
    assert res.album_recorrected is False
    assert _read_easy(mp3, "album") == "Rumours (Deluxe Edition)"


# --- 13b. re-correction: BAD targets never overwrite (conservative) ---------

@pytest.mark.parametrize("bad_album", [
    "Super Slows: De grootste Slows aller tijden",
    "Beautiful Duets",
    "Der Neue Deutsche Welle Mix",
    "Electronic Pleasure 2",
    "2 Meter Sessies: De beste allertijden",
    "Jumpin' Jive Volume 4",
    "Twelve Deadly Cyns...And Then Some",
    "A New Day...Live In Las Vegas",
    "Parts of the Process",
])
def test_recorrect_rejects_bad_resolved_targets(mp3, bad_album):
    # Every BAD album target from the live dry-run must produce NO album change
    # even on a marker-stamped (eligible) file: the conservative gate rejects a
    # compilation / live / best-of resolved target and keeps the present album.
    _tag(mp3, artist="A", title="T", album="The Real Original", date="1990")
    tags.stamp_charts_album_src(mp3, "resolver")
    res = _run_rc(mp3, ResolvedMeta(year="1988", album=bad_album,
                                    album_year="1988", track=""))
    assert res.album_recorrected is False, bad_album
    assert _read_easy(mp3, "album") == "The Real Original", bad_album


def test_recorrect_rejects_title_equals_resolved_album(mp3):
    # "Push The Feeling On -> Push The Feeling On": a resolved album that merely
    # equals the song TITLE is the single-as-album placeholder, never a valid
    # re-correction target. The present album must be kept.
    _tag(mp3, artist="A", title="Push The Feeling On",
         album="The Real Original", date="1994")
    tags.stamp_charts_album_src(mp3, "resolver")
    res = _run_rc(mp3, ResolvedMeta(year="1994", album="Push The Feeling On",
                                    album_year="1994", track="1"))
    assert res.album_recorrected is False
    assert _read_easy(mp3, "album") == "The Real Original"


@pytest.mark.parametrize("good_album", [
    "Synkronized", "Stadium Arcadium", "In Between Dreams",
    "Queen Of The Clouds", "On The 6",
])
def test_recorrect_accepts_good_resolved_targets(mp3, good_album):
    # The GOOD originals must STILL overwrite a marker-stamped present album, so
    # the feature keeps fixing the genuinely-wrong albums it was built for.
    _tag(mp3, artist="A", title="T", album="Some Live Album", date="2005")
    tags.stamp_charts_album_src(mp3, "resolver")
    res = _run_rc(mp3, ResolvedMeta(year="1998", album=good_album,
                                    album_year="1998", track=""))
    assert res.album_recorrected is True, good_album
    assert _read_easy(mp3, "album") == good_album, good_album


# --- 14. mtime preserved on a recorrect write (NFR1) ------------------------

def test_recorrect_write_preserves_mtime(mp3):
    _tag(mp3, artist="A", title="T", album="On The Night", date="1993")
    tags.stamp_charts_album_src(mp3, "resolver")
    _set_mtime(mp3, _OLD_TS)
    res = _run_rc(mp3, ResolvedMeta(year="1985", album="Brothers in Arms",
                                    album_year="1985", track=""))
    assert res.album_recorrected is True
    assert abs(os.stat(mp3).st_mtime - _OLD_TS) <= _TOL
