"""
Unit tests for the `library` mode acceptance in config._parse_channels and the
_VALID_MODES tuple.
"""

from app import config


def test_valid_modes_includes_library():
    assert config._VALID_MODES == ("own", "feed", "library")


def test_library_channel_parses_with_slug_and_decade():
    channels, warnings = config._parse_channels([
        {"slug": "energy80s", "mode": "library", "enabled": True},
        {"slug": "energypop", "mode": "library", "decade": "1975", "enabled": True},
    ])
    assert warnings == []
    assert [c.slug for c in channels] == ["energy80s", "energypop"]
    assert channels[0].mode == "library"
    # Decade derived from the slug for the first channel ...
    assert channels[0].decade == "1980"
    # ... and taken verbatim from the explicit field for the second.
    assert channels[1].decade == "1975"


def test_library_channel_needs_no_playlist_or_theme():
    channels, warnings = config._parse_channels([
        {"slug": "energy90s", "mode": "library"},
    ])
    assert warnings == []
    assert len(channels) == 1
    assert channels[0].mode == "library"
