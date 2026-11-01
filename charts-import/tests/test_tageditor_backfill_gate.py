"""
AC9 cross-service proof for the Manual Tag Editor: once the editor clears both
provenance markers on a song whose fields are ALL present, the conservative
metadata backfill leaves it untouched.

This runs in charts-import's provisioned venv (mutagen + Pillow) because
charts-import owns missing_fields/read_state. It drives the real automation gate
with the real clear functions: write a tmp mp3 with every backfill-relevant
field present (artist, title, a PLAIN non-secondary album, a year, a positive
track), stamp both markers (+ a youtube source), clear BOTH via
tags.clear_charts_source / tags.clear_charts_album_src, then assert
missing_fields(read_state(path), recorrect_albums=True) flags nothing:
replace_year/recorrect_album False and fill_year/fill_album/fill_track all
False.

The album MUST be a plain non-secondary value ('Greatest Album') so
resolve.is_secondary_edition_title is False: with the markers cleared
(album_src == ''), missing_fields' heuristic branch would otherwise fire for a
live/deluxe/anniversary title and mislead the "cleared markers -> not
recorrected" assertion. This pins the real invariant the editor relies on.
"""

import pytest

from app import backfill_metadata, config, tags


def _write_dummy_mp3(path: str) -> None:
    header = b"\xff\xfb\x90\x40"
    frame = header + b"\x00" * (417 - 4)
    with open(path, "wb") as fh:
        fh.write(frame * 30)


@pytest.fixture(autouse=True)
def _preserve_on(monkeypatch):
    monkeypatch.setattr(config, "PRESERVE_MTIME", True)


@pytest.fixture
def mp3(tmp_path):
    p = tmp_path / "song.mp3"
    _write_dummy_mp3(str(p))
    return str(p)


def _tag(path, **easy):
    from mutagen import File as MutagenFile
    audio = MutagenFile(path, easy=True)
    for k, v in easy.items():
        audio[k] = v
    audio.save()


def test_marker_cleared_song_with_all_fields_is_untouched(mp3):
    # All backfill-relevant fields present; a PLAIN non-secondary album.
    _tag(mp3, artist="The Band", albumartist="The Band", title="A Song",
         album="Greatest Album", date="1995", tracknumber="4")
    # Stamp both markers + a youtube source, as a stack-written song would carry.
    tags.stamp_charts_source(mp3, "youtube")
    tags.stamp_charts_album_src(mp3, "resolver")
    assert tags.read_charts_source(mp3) == "youtube"
    assert tags.read_charts_album_src(mp3) == "resolver"

    # The editor clears BOTH markers on every successful save.
    tags.clear_charts_source(mp3)
    tags.clear_charts_album_src(mp3)
    assert tags.read_charts_source(mp3) == ""
    assert tags.read_charts_album_src(mp3) == ""

    state = backfill_metadata.read_state(mp3)
    plan = backfill_metadata.missing_fields(state, recorrect_albums=True)

    # Marker-gated paths: both off now that the markers are gone.
    assert plan.replace_year is False
    assert plan.recorrect_album is False
    # Every field present -> nothing missing to fill.
    assert plan.fill_year is False
    assert plan.fill_album is False
    assert plan.fill_track is False
