"""
Tests for the broadened song title match key (FIX 1) in charts-songrequest.

Covers library._title_key stripping a trailing run of bitrate/format/version
decoration BEFORE the lowercase + [a-z0-9] fold, so a decorated upload title
and the clean library title produce the SAME match key, and the match that
gives find_existing. Uses a real tmp_path music tree with empty audio files;
find_existing only lists names, it never decodes audio.

This is MATCH-key only: on-disk names/tags are untouched. The remix/mix fold is
a deliberate behaviour change (see _TITLE_DECORATION in library.py).
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import library


# --- Decorated titles fold to the clean base key ---------------------------

def test_bitrate_tail_folds_to_base():
    base = library._title_key("Inside My Soul")
    assert library._title_key("Inside My Soul (320 Kbps)") == base
    assert library._title_key("Inside My Soul [320]") == base
    assert library._title_key("Inside My Soul - 320 Kbps") == base
    assert library._title_key("Inside My Soul 320kbps") == base


def test_chained_version_and_bitrate_tail_folds_to_base():
    # The real upload the user hit: version tail + dashed bitrate tail chained.
    assert (library._title_key("Das Isch mini Schwiiz (Melodic Techno Version) - (320 Kbps)")
            == library._title_key("Das Isch mini Schwiiz"))


def test_mix_and_edit_tails_fold_to_base():
    base = library._title_key("Inside My Soul")
    for tail in ("(Extended Mix)", "(Original Mix)", "(Radio Edit)", "(Remix)"):
        assert library._title_key("Inside My Soul " + tail) == base, tail


def test_does_not_over_strip_part_number():
    # (Part 2) is NOT decoration -> it is kept, so the two stay distinct.
    assert (library._title_key("Inside My Soul (Part 2)")
            != library._title_key("Inside My Soul"))


# --- find_existing matches a decorated submitted title ---------------------

def _make_song(music_dir, artist_folder, album_folder, filename):
    """Create <music>/<artist>/<album>/<filename> as an empty audio file."""
    album = music_dir / artist_folder / album_folder
    album.mkdir(parents=True, exist_ok=True)
    (album / filename).write_bytes(b"")


def test_find_existing_matches_decorated_title(tmp_path):
    music = tmp_path / "Music"
    _make_song(music, "SK.music", "Inside My Soul", "Inside My Soul.mp3")
    # Submitted title carries bitrate decoration; the clean on-disk file matches.
    found = library.find_existing(str(music), "SK.music", "Inside My Soul (320 Kbps)")
    assert found is not None
    assert "Inside My Soul.mp3" in found
    # The clean submitted title still matches too.
    assert library.find_existing(str(music), "SK.music", "Inside My Soul")
