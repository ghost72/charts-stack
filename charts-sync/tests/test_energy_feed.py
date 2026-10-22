"""
Unit tests for the charts-sync 'energy' feed integration (FEAT-002).

Covers:
  (a) energy_feed.load reading a canned handoff JSON -> ChartTrack list, and its
      tolerance of missing / corrupt / stale files (empty + the right WARN),
      opening no socket.
  (b) config._build_feed with service:'energy' -> kind=='energy' carrying theme;
      a missing theme disables the feed.
  (c) active_feeds stable partition: an energy-first config still returns the
      Deezer feed first, and a full fetch_chart keeps Deezer position priority
      for an overlapping track (which appears exactly once).
  (d) a synthetic decade theme (achtziger-neunziger) produces exactly one merged
      deduped list.
  (e) stats: last_stats carries energy_new.
  (f) PLAYLIST_ORDER=newest-first: an energy-only matched track sorts to
      position 1; an energy track overlapping a Deezer match keeps its
      carried-over first_seen (not re-dated as "new").

All network is stubbed: Deezer via monkeypatching deezer._fetch_deezer_feed,
energy via a real canned handoff JSON written under a tmp CHARTS_DIR. These
tests assert on BEHAVIOUR (enabled flag, counts, ordering), not on exact German
text, so they stay green before FEAT-003 adds the sync.* energy lang keys (an
unknown key falls back to the key name via i18n.t).
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone

import pytest

from app import config, deezer, energy_feed, main
from app.deezer import ChartTrack


# --- helpers ----------------------------------------------------------------

def _canonical_now(delta_hours: float = 0.0) -> str:
    """Canonical UTC timestamp (isoformat, seconds), optionally offset."""
    return (datetime.now(timezone.utc)
            + timedelta(hours=delta_hours)).isoformat(timespec="seconds")


def _write_handoff(charts_dir, theme, tracks, generated=None):
    """Write a canned .charts-energy-feed-<theme>.json and return its path."""
    if generated is None:
        generated = _canonical_now()
    payload = {
        "version": 1,
        "theme": theme,
        "generated": generated,
        "tracks": tracks,
    }
    path = os.path.join(str(charts_dir), f".charts-energy-feed-{theme}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
    return path


@pytest.fixture
def charts_dir(tmp_path, monkeypatch):
    """Point config.CHARTS_DIR at a tmp folder for the handoff reader."""
    d = tmp_path / "charts"
    d.mkdir()
    monkeypatch.setattr(config, "CHARTS_DIR", str(d))
    return d


def _parse(monkeypatch, data):
    monkeypatch.setenv("CHARTS", json.dumps(data))
    return config._parse_charts()


def _only_feed(sources):
    assert len(sources) == 1
    assert len(sources[0].feeds) == 1
    return sources[0].feeds[0]


class _StubSource:
    """Minimal ChartSource stand-in for fetch_chart with ordered feeds."""

    def __init__(self, feeds, exclude_artists=None):
        self._feeds = feeds
        self.title = "Test Playlist"
        self.exclude_artists = exclude_artists or set()
        self.last_stats = {}

    @property
    def active_feeds(self):
        # Mirror ChartSource.active_feeds: energy feeds merged last.
        return ([f for f in self._feeds if f.enabled and f.kind != "energy"]
                + [f for f in self._feeds if f.enabled and f.kind == "energy"])

    @property
    def feeds(self):
        return self._feeds

    @property
    def effective_genre_filter(self):
        return deezer.config.GENRE_FILTER


# --- (a) energy_feed.load ----------------------------------------------------

def test_load_reads_canned_handoff(charts_dir):
    _write_handoff(charts_dir, "mundart", [
        {"artist": "Lo & Leduc", "title": "LOYALTY", "first_seen": _canonical_now(-1)},
        {"artist": "Bligg", "title": "0815", "first_seen": _canonical_now(-2)},
    ])
    tracks = energy_feed.load("mundart")
    assert [(t.artist, t.title) for t in tracks] == [
        ("Lo & Leduc", "LOYALTY"), ("Bligg", "0815")]
    # position is a 1-based enumerate index preserving file order.
    assert [t.position for t in tracks] == [1, 2]


def test_load_missing_file_is_empty(charts_dir):
    assert energy_feed.load("does-not-exist") == []


def test_load_corrupt_json_is_empty_with_warn(charts_dir, caplog):
    path = os.path.join(str(charts_dir), ".charts-energy-feed-mundart.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("{ not valid json")
    with caplog.at_level(logging.WARNING):
        tracks = energy_feed.load("mundart")
    assert tracks == []
    # Assert on behaviour + the key reference (key-name fallback before FEAT-003).
    assert any("energy_feed_bad_json" in r.getMessage() for r in caplog.records)


def test_load_bad_shape_is_empty_with_warn(charts_dir, caplog):
    path = os.path.join(str(charts_dir), ".charts-energy-feed-mundart.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"version": 1, "theme": "mundart"}, fh)  # no tracks list
    with caplog.at_level(logging.WARNING):
        tracks = energy_feed.load("mundart")
    assert tracks == []
    assert any("energy_feed_bad_json" in r.getMessage() for r in caplog.records)


def test_load_stale_file_is_empty_with_warn(charts_dir, caplog, monkeypatch):
    monkeypatch.setattr(config, "ENERGY_FEED_MAX_AGE_HOURS", 48)
    _write_handoff(charts_dir, "mundart",
                   [{"artist": "A", "title": "B", "first_seen": _canonical_now(-100)}],
                   generated=_canonical_now(-72))
    with caplog.at_level(logging.WARNING):
        tracks = energy_feed.load("mundart")
    assert tracks == []
    assert any("energy_feed_stale" in r.getMessage() for r in caplog.records)


def test_load_fresh_within_ttl_is_kept(charts_dir, monkeypatch):
    monkeypatch.setattr(config, "ENERGY_FEED_MAX_AGE_HOURS", 48)
    _write_handoff(charts_dir, "mundart",
                   [{"artist": "A", "title": "B", "first_seen": _canonical_now(-1)}],
                   generated=_canonical_now(-1))
    tracks = energy_feed.load("mundart")
    assert len(tracks) == 1


def test_load_skips_entries_without_artist_or_title(charts_dir):
    _write_handoff(charts_dir, "mundart", [
        {"artist": "A", "title": "B", "first_seen": _canonical_now()},
        {"artist": "", "title": "no artist", "first_seen": _canonical_now()},
        {"artist": "no title", "title": "", "first_seen": _canonical_now()},
    ])
    tracks = energy_feed.load("mundart")
    assert [(t.artist, t.title) for t in tracks] == [("A", "B")]


# --- (b) _build_feed service:'energy' ---------------------------------------

def test_build_feed_energy_carries_theme(monkeypatch):
    data = [{"name": "mundart.m3u", "sources": [
        {"service": "energy", "theme": "mundart", "enabled": True}]}]
    feed = _only_feed(_parse(monkeypatch, data))
    assert feed.kind == "energy"
    assert feed.theme == "mundart"
    assert feed.enabled is True


def test_build_feed_energy_missing_theme_disabled(monkeypatch, caplog):
    data = [{"name": "mundart.m3u", "sources": [
        {"service": "energy", "enabled": True}]}]
    with caplog.at_level(logging.WARNING):
        feed = _only_feed(_parse(monkeypatch, data))
    assert feed.kind == "energy"
    assert feed.enabled is False
    assert any("energy_no_theme" in r.getMessage() for r in caplog.records)


def test_build_feed_energy_theme_not_leaked_as_lastfm_param():
    """'theme' is in _LASTFM_META_KEYS, so it is never forwarded as a param."""
    assert "theme" in config._LASTFM_META_KEYS


# --- (c) active_feeds stable partition + ordering ---------------------------

def test_active_feeds_energy_last_despite_config_order(monkeypatch):
    """Energy source listed FIRST still returns Deezer first in active_feeds."""
    data = [{"name": "mundart.m3u", "sources": [
        {"service": "energy", "theme": "mundart", "enabled": True},
        {"service": "deezer", "type": "playlist", "source": "658561775", "enabled": True},
    ]}]
    sources = _parse(monkeypatch, data)
    active = sources[0].active_feeds
    assert [f.kind for f in active] == ["deezer", "energy"]


def test_active_feeds_is_pure_permutation(monkeypatch):
    """Partition changes order only; membership + length are unchanged."""
    data = [{"name": "x.m3u", "sources": [
        {"service": "energy", "theme": "mundart", "enabled": True},
        {"service": "deezer", "type": "chart", "source": "0", "enabled": True},
        {"service": "deezer", "type": "playlist", "source": "658561775", "enabled": True},
    ]}]
    sources = _parse(monkeypatch, data)
    active = sources[0].active_feeds
    enabled = [f for f in sources[0].feeds if f.enabled]
    assert len(active) == len(enabled)
    assert set(id(f) for f in active) == set(id(f) for f in enabled)


def test_fetch_chart_deezer_keeps_priority_over_energy(charts_dir, monkeypatch):
    """An overlapping track appears once and keeps the Deezer position."""
    monkeypatch.setattr(config, "GENRE_FILTER", False)

    # Deezer feed delivers two tracks; the energy handoff overlaps on one.
    def _stub_deezer(feed, client):
        return [ChartTrack(1, "Bligg", "0815"), ChartTrack(2, "Gölä", "Schocken")]
    monkeypatch.setattr(deezer, "_fetch_deezer_feed", _stub_deezer)

    _write_handoff(charts_dir, "mundart", [
        {"artist": "Bligg", "title": "0815", "first_seen": _canonical_now()},   # overlap
        {"artist": "Lo & Leduc", "title": "LOYALTY", "first_seen": _canonical_now()},
    ])

    energy_feed_obj = config.Feed(kind="energy", limit=100, theme="mundart")
    deezer_feed = config.Feed(kind="deezer", limit=100, deezer_type="playlist",
                              source="658561775")
    # Energy listed FIRST in config; active_feeds must still merge it last.
    src = _StubSource([energy_feed_obj, deezer_feed])

    result = deezer.fetch_chart(src)
    pairs = [(t.artist, t.title) for t in result]
    # Overlap appears exactly once.
    assert pairs.count(("Bligg", "0815")) == 1
    # Deezer tracks keep the top positions; the unique energy track is appended.
    assert pairs == [("Bligg", "0815"), ("Gölä", "Schocken"), ("Lo & Leduc", "LOYALTY")]
    assert [t.position for t in result] == [1, 2, 3]


# --- (d) decade theme merges into one deduped list --------------------------

def test_decade_theme_one_merged_list(charts_dir, monkeypatch):
    monkeypatch.setattr(config, "GENRE_FILTER", False)

    def _stub_deezer(feed, client):
        return [ChartTrack(1, "a-ha", "Take On Me")]
    monkeypatch.setattr(deezer, "_fetch_deezer_feed", _stub_deezer)

    _write_handoff(charts_dir, "achtziger-neunziger", [
        {"artist": "a-ha", "title": "Take On Me", "first_seen": _canonical_now()},  # overlap
        {"artist": "Europe", "title": "The Final Countdown", "first_seen": _canonical_now()},
    ])

    deezer_feed = config.Feed(kind="deezer", limit=100, deezer_type="playlist",
                              source="123")
    energy_feed_obj = config.Feed(kind="energy", limit=100, theme="achtziger-neunziger")
    src = _StubSource([deezer_feed, energy_feed_obj])

    result = deezer.fetch_chart(src)
    pairs = [(t.artist, t.title) for t in result]
    assert pairs == [("a-ha", "Take On Me"), ("Europe", "The Final Countdown")]
    assert pairs.count(("a-ha", "Take On Me")) == 1


# --- (e) last_stats carries energy_new --------------------------------------

def test_last_stats_includes_energy_new(charts_dir, monkeypatch):
    monkeypatch.setattr(config, "GENRE_FILTER", False)

    def _stub_deezer(feed, client):
        return [ChartTrack(1, "Bligg", "0815")]
    monkeypatch.setattr(deezer, "_fetch_deezer_feed", _stub_deezer)

    _write_handoff(charts_dir, "mundart", [
        {"artist": "Bligg", "title": "0815", "first_seen": _canonical_now()},   # overlap -> 0 new
        {"artist": "Lo & Leduc", "title": "LOYALTY", "first_seen": _canonical_now()},  # new
    ])

    deezer_feed = config.Feed(kind="deezer", limit=100, deezer_type="playlist",
                              source="658561775")
    energy_feed_obj = config.Feed(kind="energy", limit=100, theme="mundart")
    src = _StubSource([deezer_feed, energy_feed_obj])

    deezer.fetch_chart(src)
    assert src.last_stats["energy_new"] == 1
    assert src.last_stats["deezer_new"] == 1


def test_handle_notify_shows_energy_bit(monkeypatch):
    """The Pushover summary can surface the energy contribution."""
    sent = {}
    monkeypatch.setattr(main.notify, "send",
                        lambda title, msg: sent.update(title=title, msg=msg))
    monkeypatch.setattr(main.config, "NOTIFY_ONLY_ON_CHANGE", False)
    monkeypatch.setattr(main.config, "NOTIFY_MISSING_LIMIT", 0)

    class _Src:
        title = "Schweizer Mundart"
        name = "mundart.m3u"
        last_stats = {"deezer_new": 2, "lastfm_new": 0, "energy_new": 3,
                      "genre_filtered": 0}

    state = {}
    main._handle_notify(_Src(), matched=[(1, "A", "B", "/music/a.mp3")],
                        missing=[], state=state)
    assert "Energy 3" in sent["msg"]
    assert "Deezer 2" in sent["msg"]


# --- (f) PLAYLIST_ORDER=newest-first ----------------------------------------

def test_newest_first_energy_only_match_sorts_to_top(monkeypatch):
    """An energy-only matched track gets a first_seen and sorts to position 1."""
    monkeypatch.setattr(config, "MUSIC_DIR", "/music")
    monkeypatch.setattr(config, "PLAYLIST_ORDER", "newest-first")

    # The energy track matches this library path; an older Deezer track exists.
    matched = [
        (1, "Old Hit", "Classic", "/music/old/classic.mp3"),
        (2, "Energy Act", "Fresh", "/music/new/fresh.mp3"),
    ]
    # Pre-existing state: the old hit has a first_seen, the energy one does not.
    state = {"mundart.m3u": {"first_seen": {"old/classic.mp3": _canonical_now(-48)}}}

    class _Src:
        name = "mundart.m3u"

    ordered = main._order_matched(_Src(), matched, state)
    # The newly-seen energy path sorts to the top.
    assert ordered[0][3] == "/music/new/fresh.mp3"


def test_newest_first_overlap_keeps_carried_over_first_seen(monkeypatch):
    """An energy track overlapping a Deezer match keeps its original date."""
    monkeypatch.setattr(config, "MUSIC_DIR", "/music")
    monkeypatch.setattr(config, "PLAYLIST_ORDER", "newest-first")

    old_date = _canonical_now(-72)
    matched = [
        (1, "Overlap Act", "Shared", "/music/shared/song.mp3"),
        (2, "Brand New", "Newest", "/music/new/newest.mp3"),
    ]
    state = {"mundart.m3u": {"first_seen": {"shared/song.mp3": old_date}}}

    class _Src:
        name = "mundart.m3u"

    # _order_matched stashes the maintained first_seen map on the source object.
    src = _Src()
    ordered = main._order_matched(src, matched, state)
    # The carried-over date is preserved (not re-dated as "new").
    assert src._first_seen["shared/song.mp3"] == old_date
    # The brand-new path got "now" and sorts above the old overlap.
    assert ordered[0][3] == "/music/new/newest.mp3"
