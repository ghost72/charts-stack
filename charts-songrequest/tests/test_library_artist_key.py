"""
Tests for the deterministic artist match key (FIX B) in charts-songrequest.

Covers library._artist_key (folds dot/underscore/whitespace/case, never
collapses genuinely different artists) and the folding it gives find_existing so
a song whose library folder differs from the typed spelling only by dot/space/
case is still recognized. Uses a real tmp_path music tree with empty audio
files; find_existing only lists names, it never decodes audio.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import library


# --- _artist_key folding ---------------------------------------------------

def test_artist_key_folds_variants():
    # Dot, space, double space and underscore spellings of the SAME artist all
    # map to one key.
    variants = ["DJ.ILHAM", "DJ ILHAM", "dj  ilham", "DJ_ILHAM"]
    keys = {library._artist_key(v) for v in variants}
    assert keys == {"djilham"}


def test_artist_key_keeps_distinct_artists_distinct():
    # Genuinely different artists stay distinct - no fuzzy/typo collapse.
    assert library._artist_key("DJ ILHAM") != library._artist_key("DJ ADONI")
    assert library._artist_key("Adele") != library._artist_key("Adel")


# --- find_existing folds dot vs space on the artist folder -----------------

def _make_song(music_dir, artist_folder, album_folder, filename):
    """Create <music>/<artist>/<album>/<filename> as an empty audio file."""
    album = music_dir / artist_folder / album_folder
    album.mkdir(parents=True, exist_ok=True)
    (album / filename).write_bytes(b"")


def test_find_existing_folds_dot_vs_space(tmp_path):
    music = tmp_path / "Music"
    # Library folder spelled with a dot; user types a space (and vice versa).
    _make_song(music, "DJ.ILHAM", "Digidi Digidi (2026)",
               "DJ.ILHAM - Digidi Digidi.mp3")
    # Typed "DJ ILHAM" (space) must find the dot folder.
    found = library.find_existing(str(music), "DJ ILHAM", "Digidi Digidi")
    assert found is not None
    assert "DJ.ILHAM" in found
    # And the exact spelling still finds it too.
    assert library.find_existing(str(music), "DJ.ILHAM", "Digidi Digidi")

    # Reverse: a space-spelled folder is found when the user types the dot form.
    music2 = tmp_path / "Music2"
    _make_song(music2, "DJ ILHAM", "DIGIDI DIGIDI", "DIGIDI DIGIDI.mp3")
    found2 = library.find_existing(str(music2), "DJ.ILHAM", "Digidi Digidi")
    assert found2 is not None
    assert "DJ ILHAM" in found2


def test_find_existing_does_not_collapse_other_artist(tmp_path):
    music = tmp_path / "Music"
    _make_song(music, "DJ ADONI", "Some Album", "DJ ADONI - Digidi Digidi.mp3")
    # Searching a genuinely different artist must NOT return DJ ADONI's file.
    assert library.find_existing(str(music), "DJ ILHAM", "Digidi Digidi") is None
