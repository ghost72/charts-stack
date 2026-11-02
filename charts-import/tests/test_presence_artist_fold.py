"""
Tests for charts-import's presence check gaining the _artist_key fold (P5).

_song_present_for_artist now resolves the artist folder via _find_artist_dir,
which folds dot / underscore / whitespace / case AND transliterates accents via
_artist_key - identical to charts-songrequest / presence.py. So an existing copy
is recognized even when the on-disk artist folder differs only by
case / punctuation / accent, and import stops re-importing a duplicate.

Pure filesystem test: a tmp Music/<Artist>/<Album>/ tree with an empty audio
file; no network / mutagen / ffmpeg.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import importer


def _make_song(root: pathlib.Path, artist: str, album: str, filename: str):
    d = root / artist / album
    d.mkdir(parents=True, exist_ok=True)
    f = d / filename
    f.write_bytes(b"")  # empty, only the name is inspected
    return f


# --- _artist_key folding (same canonical form as presence.py) ---------------

def test_artist_key_folds_punct_and_case():
    assert importer._artist_key("DJ.ILHAM") == importer._artist_key("DJ ILHAM")
    assert importer._artist_key("DJ_ILHAM") == importer._artist_key("dj ilham")


def test_artist_key_transliterates_accents():
    assert importer._artist_key("Motörhead") == importer._artist_key("Motorhead")


# --- _song_present_for_artist resolves a folded artist folder ---------------

def test_present_when_artist_folder_differs_by_punct(tmp_path):
    music = tmp_path / "Music"
    _make_song(music, "DJ.ILHAM", "Some Album", "DJ.ILHAM - Some Album - 01 - My Song.mp3")
    # Search with a punctuation/space variant of the artist.
    hit = importer._song_present_for_artist(str(music), "DJ ILHAM", "My Song")
    assert hit is not None


def test_present_when_artist_folder_differs_by_accent(tmp_path):
    music = tmp_path / "Music"
    _make_song(music, "Motorhead", "Album", "Motorhead - Album - 02 - Ace of Spades.mp3")
    hit = importer._song_present_for_artist(str(music), "Motörhead", "Ace of Spades")
    assert hit is not None


def test_exact_folder_fast_path_still_works(tmp_path):
    music = tmp_path / "Music"
    _make_song(music, "Beyonce", "Album", "Beyonce - Album - 03 - Halo.mp3")
    hit = importer._song_present_for_artist(str(music), "Beyonce", "Halo")
    assert hit is not None


def test_absent_song_returns_none(tmp_path):
    music = tmp_path / "Music"
    _make_song(music, "Beyonce", "Album", "Beyonce - Album - 03 - Halo.mp3")
    assert importer._song_present_for_artist(str(music), "Beyonce", "Nope") is None


def test_empty_artist_returns_none_no_unknown_bucket(tmp_path):
    # An empty artist resolves to None (no "Unknown Artist" bucket probe),
    # matching the presence-side empty fallback.
    music = tmp_path / "Music"
    _make_song(music, "Unknown Artist", "Album", "x - Album - 01 - Ghost.mp3")
    assert importer._song_present_for_artist(str(music), "", "Ghost") is None
