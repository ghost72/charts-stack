"""
Tests for the presence guard (Option A) and the Lidarr "(YYYY)" compilation
short-circuit (Option B) in charts-energy.

Covers:
  - presence.find_existing over a REAL tmp music tree (incl. a Lidarr CD
    multi-disc subfolder) - the on-disk counterpart to the monkeypatched
    run_match test in test_run_match_library.py.
  - Option B: a (YYYY) album track is no longer flagged is_compilation, while a
    non-year Bravo Hits / Various Artists folder still is.
  - A drift guard pinning presence._title_key / _artist_key to the same keys
    the charts-songrequest reference produces.
"""

import os

import pytest

from app import library, presence


def _touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("")


# --- Option A: presence.find_existing over a real tmp tree -------------------

def test_find_existing_in_cd_subfolder(tmp_path):
    """The only copy survives in a Lidarr CD multi-disc subfolder."""
    music = tmp_path / "Music"
    _touch(str(music / "Sandra" / "Greatest Hits (2008)" / "CD 01"
                / "Sandra - Greatest Hits - 05 - Everlasting Love.mp3"))
    hit = presence.find_existing(str(music), "Sandra", "Everlasting Love")
    assert hit is not None and hit.endswith(".mp3")


def test_find_existing_artist_key_fold(tmp_path):
    music = tmp_path / "Music"
    _touch(str(music / "Sandra" / "Greatest Hits (2008)"
                / "Sandra - Greatest Hits - 05 - Everlasting Love.mp3"))
    # Lowercase / punctuation-folded artist query still resolves the folder.
    assert presence.find_existing(str(music), "sandra", "Everlasting Love")


def test_find_existing_absent(tmp_path):
    music = tmp_path / "Music"
    music.mkdir()
    assert presence.find_existing(str(music), "Nobody", "Unknown") is None


# --- Option B: (YYYY) album not a compilation; Bravo Hits still is -----------

def test_option_b_year_album_not_compilation():
    assert library._looks_like_compilation(
        "/music/Music/Sandra/Greatest Hits (2008)/CD 01/x.mp3",
        "Greatest Hits", "Sandra") is False


def test_option_b_non_year_sampler_still_compilation():
    assert library._looks_like_compilation(
        "/music/Music/Various Artists/Bravo Hits 42/x.mp3",
        "Bravo Hits 42", "Various Artists") is True


# --- Drift guard: keys match the charts-songrequest reference ----------------

def test_title_key_reference_values():
    # Hard-coded expected keys so the copied helper cannot silently drift from
    # the charts-songrequest reference semantics.
    assert presence._title_key("Everlasting Love") == "everlastinglove"
    assert presence._title_key("Inside My Soul (320 Kbps)") == "insidemysoul"
    assert presence._title_key("Ewigi Liäbi") == "ewigiliabi"
    # A numeric/part tail is preserved (not decoration).
    assert presence._title_key("Song (Part 2)") == "songpart2"


def test_artist_key_reference_values():
    assert presence._artist_key("DJ.ILHAM") == "djilham"
    assert presence._artist_key("DJ ILHAM") == "djilham"
    assert presence._artist_key("Sandra") == "sandra"
