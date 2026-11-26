"""
Unit tests for the FEED-mode handoff writer (app/feed.py).

No network: the handoff file is written under a tmp CHARTS_DIR and read back.
"""

import json

import pytest

from app import config, feed, main, state


@pytest.fixture(autouse=True)
def tmp_charts(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CHARTS_DIR", str(tmp_path))
    return tmp_path


def _t(artist, title, first_seen):
    return {"artist": artist, "title": title, "first_seen": first_seen}


def test_schema_and_atomic_write(tmp_charts):
    tracks = [
        _t("Lo & Leduc", "LOYALTY x MANY MEN", "2026-10-22T07:16:40+00:00"),
    ]
    path = feed.write("mundart", [tracks])
    assert path == str(tmp_charts / ".charts-energy-feed-mundart.json")
    # No leftover temp file.
    assert not (tmp_charts / ".charts-energy-feed-mundart.json.tmp").exists()

    payload = json.loads((tmp_charts / ".charts-energy-feed-mundart.json").read_text("utf-8"))
    assert payload["version"] == feed.FEED_VERSION
    assert payload["theme"] == "mundart"
    assert payload["generated"].endswith("+00:00")
    assert payload["tracks"] == [
        {"artist": "Lo & Leduc", "title": "LOYALTY x MANY MEN",
         "first_seen": "2026-10-22T07:16:40+00:00"},
    ]


def test_two_channels_same_theme_merge_dedupe_order(tmp_charts):
    """Two FEED channels on one theme merge + dedupe into one file,
    ordered first_seen desc."""
    ch80 = [
        _t("Madonna", "Rain", "2026-07-01T10:00:00+00:00"),
        _t("Duplicate Act", "Same Song", "2026-07-01T09:00:00+00:00"),
    ]
    ch90 = [
        # Overlaps ch80 (normalized artist|title) -> deduped to one entry.
        _t("duplicate act", "same song", "2026-07-02T10:00:00+00:00"),
        _t("Bon Jovi", "Bed Of Roses", "2026-07-03T10:00:00+00:00"),
    ]
    feed.write("achtziger-neunziger", [ch80, ch90])

    payload = json.loads(
        (tmp_charts / ".charts-energy-feed-achtziger-neunziger.json").read_text("utf-8"))
    titles = [t["title"] for t in payload["tracks"]]
    # Dedupe: "Same Song" appears once (first occurrence, from ch80).
    assert titles.count("Same Song") == 1
    # Ordered first_seen descending.
    assert titles == ["Bed Of Roses", "Rain", "Same Song"]


def test_notification_reports_unique_handoff_count(tmp_charts, monkeypatch):
    channels = [config.Channel(slug="energyhits", mode="feed", theme="charts")]
    monkeypatch.setattr(config, "active_channels", lambda: channels)
    monkeypatch.setattr(state, "load", lambda: {})
    monkeypatch.setattr(state, "tracks_for", lambda *_: [
        _t("Artist", "Song", "2026-07-01T10:00:00+00:00"),
        _t("ARTIST", "SONG", "2026-07-01T11:00:00+00:00"),
    ])
    monkeypatch.setattr(main.library, "build_index", lambda *_: object())
    notifications = []
    monkeypatch.setattr(main, "_notify",
                        lambda own, feeds, library: notifications.extend(feeds))

    main.run_match()

    payload = json.loads((tmp_charts / ".charts-energy-feed-charts.json").read_text())
    assert len(payload["tracks"]) == 1
    assert notifications == [{"theme": "charts", "count": 1}]
