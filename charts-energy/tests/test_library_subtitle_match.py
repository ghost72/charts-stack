"""
Unit tests for the subtitle-tail tolerance in library matching.

Regression: the ICY radio stream feeds a SHORT title ("Against All Odds") while
the metadata-backfilled library carries the EXTENDED Deezer-canonical title
("Against All Odds (Take a Look at Me Now)"). The plain normalize()/similarity
path scored ~59 title_sim -> weighted ~75 < MATCH_THRESHOLD (87), so an OWNED
song read as "missing" and was re-queued every run.

The fix strips a trailing DESCRIPTIVE '(subtitle)' to form a "core title" key,
used ONLY for scoring. Numeric / "part N" / roman tails stay distinct so two
genuinely different songs cannot collapse onto one key.
"""

from app import library


def _track(artist, title, path=None):
    return library.LibraryTrack(
        path or f"/music/Music/{artist}/{title}/{title}.mp3", artist, title)


# --- _strip_subtitle_tail / normalize_core -------------------------------

def test_strip_descriptive_subtitle():
    assert (library.normalize_core("Against All Odds (Take a Look at Me Now)")
            == library.normalize("Against All Odds"))


def test_strip_is_symmetric_short_and_extended_fold_equal():
    short = library.normalize_core("Against All Odds")
    extended = library.normalize_core("Against All Odds (Take a Look at Me Now)")
    assert short == extended


def test_keeps_part_and_numeric_tails_distinct():
    # A numeric / part / roman tail distinguishes genuinely different songs and
    # must NOT be stripped (stays != the bare title).
    base = library.normalize_core("Song")
    for tail in ("(Part 2)", "(Pt. 2)", "(Part II)", "(2)", "(Vol. 3)",
                 "(Disc 2)", "(CD 1)", "(No. 4)"):
        assert library.normalize_core("Song " + tail) != base, tail


def test_no_tail_title_unchanged():
    # A bare short title (no parenthetical) is left exactly as normalize().
    assert library.normalize_core("Area 51") == library.normalize("Area 51")
    assert library.normalize_core("Shy Guy") == library.normalize("Shy Guy")


def test_whole_title_subtitle_not_stripped_to_nothing():
    # When the title is ONLY a parenthetical, _strip_subtitle_tail must not
    # collapse it to an empty core key (guard: "stripped or t"). "(Take a Look
    # at Me Now)" is not a normalize() noise phrase, so the core survives.
    assert library.normalize_core("(Take a Look at Me Now)") != ""
    assert (library.normalize_core("(Take a Look at Me Now)")
            == library.normalize("(Take a Look at Me Now)"))


# --- find_match: the concrete cases from the investigation ----------------

def test_cover_artist_extended_only_now_matches():
    # Mariah's only on-disk copy is the EXTENDED-title album track; the stream
    # feeds the short title. Was None/~75 -> must now be a real match.
    lib = library.Library([
        _track("Mariah Carey", "Against All Odds (Take A Look At Me Now)"),
    ])
    track, score = lib.find_match("Mariah Carey", "Against All Odds", 87)
    assert track is not None
    assert score == 100


def test_original_short_single_still_matches_100():
    # The 80s original still has the short-title single -> stays a perfect match.
    lib = library.Library([
        _track("Phil Collins", "Against All Odds (Take a Look at Me Now)"),
        _track("Phil Collins", "Against All Odds"),
    ])
    track, score = lib.find_match("Phil Collins", "Against All Odds", 87)
    assert track is not None
    assert score == 100


def test_reverse_extended_stream_short_library():
    # Symmetry: a stream that feeds the extended title still matches a library
    # that only holds the short title.
    lib = library.Library([_track("Phil Collins", "Against All Odds")])
    track, score = lib.find_match(
        "Phil Collins", "Against All Odds (Take a Look at Me Now)", 87)
    assert track is not None
    assert score == 100


def test_part_tail_does_not_collide_across_different_songs():
    # "Song (Part 2)" streamed must NOT read as a perfect match of the owned
    # "Song (Part 1)" - the numeric/part tail keeps them distinct.
    lib = library.Library([_track("A", "Song (Part 1)")])
    track, score = lib.find_match("A", "Song (Part 2)", 87)
    # Either no match, or at least not a perfect subtitle-collapsed 100.
    assert score < 100


def test_existing_healthy_exact_match_unaffected():
    # A plain exact title/artist still scores 100 as before.
    lib = library.Library([_track("a-ha", "Take On Me")])
    track, score = lib.find_match("a-ha", "Take On Me", 87)
    assert track is not None
    assert score == 100


def test_near_miss_different_song_stays_below_threshold():
    # A genuinely different title for the same artist must stay below 87 and so
    # read as missing (no false positive from the subtitle tolerance).
    lib = library.Library([_track("Tina Turner", "I Might Have Been Queen")])
    track, score = lib.find_match("Tina Turner", "Private Dancer", 87)
    assert track is None
    assert score < 87


def test_wrong_artist_still_gated_out():
    # The artist gate is unchanged: a perfect title with a wrong artist is no
    # match, even with the subtitle tolerance.
    lib = library.Library([_track("Van Halen", "Jump")])
    track, score = lib.find_match("BLACKPINK", "Jump", 87)
    assert track is None
