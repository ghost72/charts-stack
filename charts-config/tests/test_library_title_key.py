"""
Tests for the decoration-stripping song title match key in charts-config.

charts-config's Manual Tag Editor search index keys titles via
library._title_key. P3 lifts the full _strip_title_decoration (dash-bitrate
tails, balanced "(...)"/"[...]" decoration groups, open tails) into this copy so
the key is identical to charts-songrequest / charts-import / presence.py and a
decorated title groups with its clean base. MATCH key only: on-disk names/tags
are untouched (those use _clean_component on the raw title).

Mirrors charts-songrequest/tests/test_library_title_key.py.
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
    assert (library._title_key("Das Isch mini Schwiiz (Melodic Techno Version) - (320 Kbps)")
            == library._title_key("Das Isch mini Schwiiz"))


def test_mix_and_edit_tails_fold_to_base():
    base = library._title_key("Inside My Soul")
    for tail in ("(Extended Mix)", "(Original Mix)", "(Radio Edit)", "(Remix)"):
        assert library._title_key("Inside My Soul " + tail) == base, tail


def test_does_not_over_strip_part_number():
    # (Part 2) is NOT decoration -> kept, so the two stay distinct.
    assert (library._title_key("Inside My Soul (Part 2)")
            != library._title_key("Inside My Soul"))


def test_open_tail_siblings_fold_to_base():
    base = library._title_key("Song")
    for tail in ("(live", "(feat. X", "(radio edit", "(extended mix",
                 "(official video", "(remix", "[original"):
        assert library._title_key("Song " + tail) == base, tail


def test_open_tail_does_not_over_strip_part():
    base = library._title_key("Song")
    assert library._title_key("Song (Part 2") != base
    assert library._title_key("Song (Part 2)") != base


def test_open_tail_keeps_trailing_number():
    assert library._title_key("Area 51") == "area51"


# --- accent folding still holds (regression anchor) ------------------------

def test_umlaut_title_key_equivalence():
    base = library._title_key("ewigi liabi")
    assert library._title_key("ewigi liäbi") == base
    assert library._title_key("EWIGI LIÄBI") == base
