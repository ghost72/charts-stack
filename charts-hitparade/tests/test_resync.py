"""
Unit tests for the charts-hitparade resync trigger (app/main.py).

Covered:
  - Signal helpers: _hitparade_resync_requested() false then true after the
    file is created; _clear_hitparade_resync_signal() removes it and is a no-op
    when already absent.
  - run_resync() with a populated cache re-renders hitparade.m3u, NEVER fetches
    hitparade.ch, and returns the counter dict.
  - run_resync() with an empty cache returns all-zero counters and logs
    hitparade.resync.no_cache.
All tests run WITHOUT network/Docker; hitparade.fetch_chart is stubbed to raise
if it is ever called on the trigger path.
"""

import logging
import os

from app import chart_cache, config, i18n, main


# --- Signal helpers --------------------------------------------------------

def test_signal_probe_and_clear(monkeypatch, tmp_path):
    """Probe is false, then true after creating the file; clear removes it and
    is a no-op when the file is already gone."""
    signal = tmp_path / ".hitparade-resync-requested"
    monkeypatch.setattr(config, "HITPARADE_RESYNC_SIGNAL", str(signal))

    assert main._hitparade_resync_requested() is False
    signal.write_text("resync", encoding="utf-8")
    assert main._hitparade_resync_requested() is True

    main._clear_hitparade_resync_signal()
    assert not signal.exists()
    # Idempotent: clearing an already-absent signal does not raise.
    main._clear_hitparade_resync_signal()


# --- run_resync (populated cache) ------------------------------------------

class _StubTrack:
    def __init__(self, path):
        self.path = path


class _StubLibrary:
    """Minimal library stub: matches every song to a fixed path."""

    def find_match(self, artist, title, threshold):
        return _StubTrack(f"{artist}/{title}/{title}.mp3"), 100


def test_run_resync_renders_from_cache_without_network(monkeypatch, tmp_path):
    """A populated cache is re-matched and the playlist re-rendered WITHOUT
    calling hitparade.fetch_chart; returns the counter dict."""
    charts_dir = tmp_path / "charts"
    music_dir = tmp_path / "music"
    monkeypatch.setattr(config, "CHARTS_DIR", str(charts_dir))
    monkeypatch.setattr(config, "PLAYLIST_DIR", str(music_dir))
    monkeypatch.setattr(config, "MUSIC_DIR", str(music_dir))

    # Populate the cache with one chart.
    chart_cache.save({"singles": [(1, "Artist A", "Title A"),
                                   (2, "Artist B", "Title B")]})

    # The trigger must NOT fetch: make fetch_chart blow up if touched.
    def _boom(*_a, **_k):
        raise AssertionError("run_resync must not fetch hitparade.ch")

    monkeypatch.setattr(main.hitparade, "fetch_chart", _boom)
    monkeypatch.setattr(main.library, "build_index", lambda _d: _StubLibrary())

    result = main.run_resync()

    assert result == {"charts": 1, "songs": 2, "matched": 2, "missing": 0}
    # The playlist was rendered.
    playlist_file = music_dir / config.CHART_META["singles"]["playlist"]
    assert playlist_file.exists()
    content = playlist_file.read_text(encoding="utf-8")
    assert "#EXTM3U" in content
    assert "Artist A - Title A" in content


# --- run_resync (empty cache) ----------------------------------------------

def test_run_resync_no_cache_is_noop(monkeypatch, tmp_path, caplog):
    """An empty cache returns all-zero counters and logs the no_cache key; no
    playlist is written and no fetch happens."""
    charts_dir = tmp_path / "charts"
    music_dir = tmp_path / "music"
    monkeypatch.setattr(config, "CHARTS_DIR", str(charts_dir))
    monkeypatch.setattr(config, "PLAYLIST_DIR", str(music_dir))
    monkeypatch.setattr(config, "MUSIC_DIR", str(music_dir))

    def _boom(*_a, **_k):
        raise AssertionError("run_resync must not fetch hitparade.ch")

    monkeypatch.setattr(main.hitparade, "fetch_chart", _boom)
    # build_index must not even be called when there is no cache.
    monkeypatch.setattr(
        main.library, "build_index",
        lambda _d: (_ for _ in ()).throw(AssertionError("no scan without cache")))

    # English active table so the log message is deterministic.
    i18n.setup(lang="en")
    with caplog.at_level(logging.INFO):
        result = main.run_resync()

    assert result == {"charts": 0, "songs": 0, "matched": 0, "missing": 0}
    expected = i18n.t("hitparade.resync.no_cache")
    assert any(expected in r.getMessage() for r in caplog.records)
    # No playlist written.
    assert not os.path.exists(
        os.path.join(str(music_dir), config.CHART_META["singles"]["playlist"]))
    # Restore the import-time language state.
    i18n.setup()
