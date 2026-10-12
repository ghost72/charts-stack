"""
Unit tests for the playlist-specific exclude_artists filter
(deezer._is_excluded_artist / deezer.fetch_chart).

Background (bug from NAS operation): in the "Schweizer Mundart" playlist with
exclude_artists=["Trauffer","Nemo"], Trauffer/Nemo tracks still remained in
missing-mundart.txt. Verified against real Deezer data these were collab tracks
where the main artist is NOT the excluded act (e.g. artist="Goelae",
contributors=[..., "Trauffer"]) - the old filter only checked artist.name and
let them through.

Additionally guarded: the dedup bypass path. If the same song (same _merge_key)
appears first under a NOT excluded name, that must not sneak the later,
correctly excluded variant past the filter via 'key in seen'.
"""

import pytest

from app import deezer
from app.deezer import ChartTrack


EXCLUDE = {"trauffer", "nemo"}


# --- _is_excluded_artist: direct unit tests ---------------------------------

def test_main_artist_exact_match():
    assert deezer._is_excluded_artist("Trauffer", EXCLUDE) is True
    assert deezer._is_excluded_artist("Nemo", EXCLUDE) is True


def test_main_artist_case_insensitive():
    assert deezer._is_excluded_artist("TRAUFFER", EXCLUDE) is True


def test_main_artist_in_feat_combo():
    # "Trauffer feat. Beatrice Egli" -> main act is Trauffer.
    assert deezer._is_excluded_artist(
        "Trauffer feat. Beatrice Egli", EXCLUDE) is True


def test_not_excluded_artist_passes():
    assert deezer._is_excluded_artist("Gölä", EXCLUDE) is False
    assert deezer._is_excluded_artist("Bligg", EXCLUDE) is False


def test_excluded_as_contributor_only():
    # The actual bug: main artist is Goelae, Trauffer only as contributor.
    assert deezer._is_excluded_artist(
        "Gölä", EXCLUDE,
        contributors=["Gölä", "Trauffer"]) is True


def test_contributor_not_excluded_passes():
    assert deezer._is_excluded_artist(
        "Gölä", EXCLUDE,
        contributors=["Gölä", "Beatrice Egli"]) is False


def test_empty_exclude_set_never_excludes():
    assert deezer._is_excluded_artist(
        "Trauffer", set(), contributors=["Trauffer"]) is False


# --- fetch_chart: integration with stubbed feeds ----------------------------

class _StubFeed:
    kind = "deezer"


class _StubSource:
    """Minimal ChartSource stand-in for fetch_chart (one playlist, N feeds)."""

    def __init__(self, feeds, exclude_artists):
        self._feeds = feeds
        self.title = "Schweizer Mundart (Test)"
        self.exclude_artists = exclude_artists
        self.last_stats = {}

    @property
    def active_feeds(self):
        return self._feeds

    @property
    def feeds(self):
        return self._feeds

    @property
    def effective_genre_filter(self):
        # No per-playlist override -> global GENRE_FILTER (like ChartSource).
        return deezer.config.GENRE_FILTER


def _result_artists(tracks):
    return [(t.artist, t.title) for t in tracks]


def test_fetch_chart_excludes_contributor_collab(monkeypatch):
    """Collab track (Trauffer only contributor) must NOT land in the result."""
    feed = _StubFeed()
    tracks = [
        ChartTrack(1, "Bligg", "0815"),
        ChartTrack(2, "Gölä", "Büetzer Buebe", contributors=["Gölä", "Trauffer"]),
        ChartTrack(3, "Trauffer", "Heubode"),
    ]
    monkeypatch.setattr(deezer, "_fetch_one_feed", lambda f, c: tracks)
    # Genre filter off (otherwise real Last.fm calls).
    monkeypatch.setattr(deezer.config, "GENRE_FILTER", False)

    src = _StubSource([feed], EXCLUDE)
    result = deezer.fetch_chart(src)

    artists = {a.lower() for a, _ in _result_artists(result)}
    assert "trauffer" not in artists
    # Only Bligg remains.
    assert _result_artists(result) == [("Bligg", "0815")]


def test_fetch_chart_dedup_does_not_bypass_exclude(monkeypatch):
    """
    Order bug: the same song (same _merge_key) appears first under a NOT
    excluded artist name, then under the correctly excluded one. The excluded
    artist must NOT slip through.
    """
    feed_a = _StubFeed()
    feed_b = _StubFeed()
    # Feed A: song "Heubode" wrongly attributed to another act (same title).
    # Feed B: correctly Trauffer.
    tracks_a = [ChartTrack(1, "Trauffer", "Heubode", contributors=[])]
    # Simulate that the same _merge_key was already "seen" by having feed A
    # deliver a Trauffer hit -> must be excluded immediately and must not even
    # fill 'seen'.
    tracks_b = [ChartTrack(5, "Trauffer", "Heubode", contributors=[])]

    feeds_map = {id(feed_a): tracks_a, id(feed_b): tracks_b}
    monkeypatch.setattr(deezer, "_fetch_one_feed",
                        lambda f, c: feeds_map[id(f)])
    monkeypatch.setattr(deezer.config, "GENRE_FILTER", False)

    src = _StubSource([feed_a, feed_b], EXCLUDE)
    result = deezer.fetch_chart(src)

    assert result == [] or all("trauffer" != t.artist.lower() for t in result)


def test_fetch_chart_keeps_allowed_artists(monkeypatch):
    """Non-excluded artists are kept (no false exclusions)."""
    feed = _StubFeed()
    tracks = [
        ChartTrack(1, "Gölä", "Schwan"),
        ChartTrack(2, "Lo & Leduc", "079", contributors=["Lo & Leduc"]),
        ChartTrack(3, "Bligg", "Rosmarie"),
    ]
    monkeypatch.setattr(deezer, "_fetch_one_feed", lambda f, c: tracks)
    monkeypatch.setattr(deezer.config, "GENRE_FILTER", False)

    src = _StubSource([feed], EXCLUDE)
    result = deezer.fetch_chart(src)
    assert len(result) == 3
