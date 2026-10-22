"""
Unit tests for the collision guard in config._parse_channels.

All assertions run against the (channels, warnings) tuple _parse_channels
returns. Invariant: never let two writers target the same .m3u (or the same
missing file).
"""

from app import config


def _names(channels):
    return [c.playlist for c in channels]


def test_own_non_energy_playlist_is_refused():
    channels, warnings = config._parse_channels([
        {"slug": "energypop", "mode": "own", "playlist": "mundart.m3u"},
    ])
    assert channels == []
    assert any("own_playlist_reserved" in w or "reserved namespace" in w
               or "reservierter" in w or "réservé" in w for w in warnings)


def test_own_mixed_case_playlist_is_normalized_and_kept():
    channels, warnings = config._parse_channels([
        {"slug": "energypop", "mode": "own", "playlist": "Energy-Pop.m3u"},
    ])
    assert len(channels) == 1
    assert channels[0].playlist == "energy-pop.m3u"


def test_own_empty_playlist_derives_default():
    channels, warnings = config._parse_channels([
        {"slug": "energypop", "mode": "own"},
    ])
    assert len(channels) == 1
    assert channels[0].playlist == "energy-pop.m3u"


def test_own_capitalized_slug_derives_pattern_valid_name():
    channels, warnings = config._parse_channels([
        {"slug": "Energy80s", "mode": "own"},
    ])
    assert len(channels) == 1
    # Slug lowercased first -> pattern-valid default, channel kept.
    assert channels[0].playlist == "energy-80s.m3u"


def test_duplicate_playlist_keeps_only_first():
    channels, warnings = config._parse_channels([
        {"slug": "energypop", "mode": "own", "playlist": "energy-pop.m3u"},
        {"slug": "energyrock", "mode": "own", "playlist": "energy-pop.m3u"},
    ])
    assert _names(channels) == ["energy-pop.m3u"]
    assert any("own_playlist_duplicate" in w or "already used" in w
               or "bereits" in w or "déjà" in w for w in warnings)


def test_duplicate_missing_file_keeps_only_first():
    # Distinct .m3u names but the SAME resolved missing file -> second dropped.
    channels, warnings = config._parse_channels([
        {"slug": "energypop", "mode": "own", "playlist": "energy-pop.m3u",
         "missing": "missing-energy-shared.txt"},
        {"slug": "energyrock", "mode": "own", "playlist": "energy-rock.m3u",
         "missing": "missing-energy-shared.txt"},
    ])
    assert _names(channels) == ["energy-pop.m3u"]
    assert any("own_playlist_duplicate" in w or "already used" in w
               or "bereits" in w or "déjà" in w for w in warnings)


def test_library_and_feed_add_no_collision_warning():
    channels, warnings = config._parse_channels([
        {"slug": "energy80s", "mode": "library"},
        {"slug": "energy90s", "mode": "library"},
        {"slug": "energymadeinswitzerland", "mode": "feed", "theme": "mundart"},
    ])
    assert len(channels) == 3
    assert warnings == []
