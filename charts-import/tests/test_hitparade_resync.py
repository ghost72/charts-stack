"""
Tests for the charts-import hitparade resync producer (app/main.py).

charts-import drops a dedicated hitparade signal file ONLY when a
hitparade-relevant song is imported (membership of HITPARADE_SOURCE_LABEL in
the "+"-joined source string). Covered:
  - _is_hitparade_source membership table (not substring / whole-string).
  - run_once() with a hitparade-relevant import creates the signal; a
    charts-only import does not.
All tests run WITHOUT network/Docker/Pushover (notify/lidarr side effects are
stubbed).
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pytest

from app import config
from app import main as import_main


# --- _is_hitparade_source table --------------------------------------------

@pytest.mark.parametrize("source,expected", [
    ("hitparade", True),
    ("charts", False),
    ("charts+hitparade", True),
    ("hitparade+charts", True),
    ("", False),
    ("hit", False),
    ("hitparadexx", False),
    (" hitparade ", True),
    ("charts + hitparade", True),
])
def test_is_hitparade_source(source, expected):
    assert import_main._is_hitparade_source(source) is expected


# --- run_once producer wiring ----------------------------------------------

def _stub_side_effects(monkeypatch):
    """Silence all notify/lidarr/release side effects so no network is hit."""
    monkeypatch.setattr(import_main, "_notify_imported", lambda *_a, **_k: None)
    monkeypatch.setattr(import_main, "_notify_manual_available", lambda *_a, **_k: None)
    monkeypatch.setattr(import_main, "_unmonitor_in_lidarr", lambda *_a, **_k: None)
    monkeypatch.setattr(import_main, "_request_resync", lambda: None)


def _wire_import(monkeypatch, imported):
    """Make import_downloads return `imported` and cleanup a no-op."""
    monkeypatch.setattr(import_main.importer, "import_downloads",
                        lambda: (imported, [], []))
    monkeypatch.setattr(import_main.cleanup, "cleanup_downloads", lambda: [])


def test_run_once_sets_signal_for_hitparade_song(monkeypatch, tmp_path):
    """A hitparade-relevant import drops the hitparade signal file."""
    signal = tmp_path / ".hitparade-resync-requested"
    monkeypatch.setattr(config, "HITPARADE_RESYNC_SIGNAL", str(signal))
    _stub_side_effects(monkeypatch)
    _wire_import(monkeypatch, [
        ("/music/Music/A/T/T.mp3", "charts+hitparade", "A", "T", "T", ""),
    ])

    import_main.run_once()

    assert signal.exists()


def test_run_once_no_signal_for_charts_only(monkeypatch, tmp_path):
    """A charts-only import does NOT drop the hitparade signal file."""
    signal = tmp_path / ".hitparade-resync-requested"
    monkeypatch.setattr(config, "HITPARADE_RESYNC_SIGNAL", str(signal))
    _stub_side_effects(monkeypatch)
    _wire_import(monkeypatch, [
        ("/music/Music/B/U/U.mp3", "charts", "B", "U", "U", ""),
    ])

    import_main.run_once()

    assert not signal.exists()
