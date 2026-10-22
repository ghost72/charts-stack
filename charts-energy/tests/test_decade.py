"""
Unit tests for config.decade_from_slug and the explicit-decade override.
"""

from app import config


def test_decade_from_known_slugs():
    assert config.decade_from_slug("energy80s") == "1980"
    assert config.decade_from_slug("energy90s") == "1990"
    assert config.decade_from_slug("energy00s") == "2000"
    assert config.decade_from_slug("energy10s") == "2010"
    assert config.decade_from_slug("energy20s") == "2020"


def test_decade_from_slug_is_case_insensitive():
    assert config.decade_from_slug("Energy80s") == "1980"


def test_decade_from_slug_no_decade():
    assert config.decade_from_slug("energypop") == ""
    assert config.decade_from_slug("random") == ""


def test_explicit_decade_overrides_slug_parse():
    ch = config.Channel(slug="energy80s", mode="library", decade="1975")
    assert ch.decade == "1975"


def test_slug_decade_used_when_no_explicit():
    ch = config.Channel(slug="energy90s", mode="library")
    assert ch.decade == "1990"
