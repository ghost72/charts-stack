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
