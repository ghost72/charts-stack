"""
Tests for the song title match key (importer._title_key) in charts-import.

Covers FIX 1: an NFKD accent fold (ä -> a, é -> e) runs BEFORE the lowercase +
[a-z0-9] fold, so an umlaut is transliterated instead of dropped. This keeps
charts-import in lock-step with charts-songrequest's _title_key and the
charts-sync normalize, so all subsystems agree on the same key for the umlaut.
Also pins the existing decoration-fold behaviour (feat/remaster stripping) as a
regression anchor - charts-import had no prior title-key test.

MATCH key only: on-disk names/tags are untouched by _title_key.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import importer


# --- FIX 1: umlaut/accent folding ------------------------------------------

def test_umlaut_title_key_equivalence():
    base = importer._title_key("ewigi liabi")
    assert importer._title_key("ewigi liäbi") == base
    assert importer._title_key("Ewigi Liäbi") == base
    assert importer._title_key("EWIGI LIÄBI") == base


def test_liaebi_equals_liabi():
    assert importer._title_key("liäbi") == importer._title_key("liabi")


def test_accent_pairs_fold():
    assert importer._title_key("Café") == importer._title_key("cafe")
    assert importer._title_key("Über") == importer._title_key("uber")


# --- Regression: the existing decoration fold still holds -------------------

def test_remaster_fold_unchanged():
    # 'Into You' matches 'Into You (Remastered)' as before the accent fix.
    assert (importer._title_key("Into You")
            == importer._title_key("Into You (Remastered)"))


def test_feat_fold_unchanged():
    assert (importer._title_key("Into You")
            == importer._title_key("Into You (feat. Someone)"))


# --- FIX 3: trailing UNBALANCED/open opener-tail folds to base -------------

def test_open_tail_folds_to_base():
    # Mirrors songrequest: a re-request with an unbalanced open tail keys the
    # same as the clean title, composing with the NFKD fold.
    base = importer._title_key("ewigi liäbi")
    assert importer._title_key("ewigi liäbi (original") == base
    assert importer._title_key("ewigi liabi") == base


def test_open_tail_siblings_fold_to_base():
    base = importer._title_key("Song")
    for tail in ("(live", "(feat. X", "(radio edit", "(extended mix",
                 "(official video", "(remix", "[original"):
        assert importer._title_key("Song " + tail) == base, tail


def test_open_tail_does_not_over_strip_part():
    # Numeric/part tails stay distinct from 'Song' (balanced and unbalanced).
    base = importer._title_key("Song")
    assert importer._title_key("Song (Part 2") != base
    assert importer._title_key("Song (Part 2)") != base


def test_open_tail_keeps_trailing_number():
    assert importer._title_key("Area 51") == "area51"


# --- P3: full _strip_title_decoration (balanced bitrate/version groups) -----

def test_bitrate_decoration_folds_to_base():
    # A decorated title ("... (320 Kbps)", "... - 320k") now folds onto the
    # clean library key, matching songrequest / presence.py.
    base = importer._title_key("Inside My Soul")
    assert importer._title_key("Inside My Soul (320 Kbps)") == base
    assert importer._title_key("Inside My Soul - 320k") == base
    assert importer._title_key("Inside My Soul [320]") == base


def test_version_decoration_folds_to_base():
    base = importer._title_key("Song")
    assert importer._title_key("Song (Extended Mix)") == base
    assert importer._title_key("Song (Original Mix)") == base


def test_chained_decoration_collapses():
    base = importer._title_key("Das Isch Mini Schwiiz")
    assert (importer._title_key(
        "Das Isch Mini Schwiiz (Melodic Techno Version) - (320 Kbps)") == base)
