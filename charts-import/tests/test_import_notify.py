"""
Tests for the charts-import "imported" Pushover message with multi-source
labels (Option B).

A song can belong to several playlists at once. charts-import reads the
combined label from the manifest (via tags.manifest_sources, joined with
"+") and must render it as ONE line per song under a SINGLE combined group
heading (e.g. "charts+hitparade") - never once per source.

Backward compat: an old manifest entry with only the single "source" string
still yields that one label.

All tests run WITHOUT network/Docker/Pushover (notify.send is stubbed).
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import config, tags
from app import main as import_main


def _capture_notify(monkeypatch):
    """Stub notify.send and return a list that collects (title, body)."""
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(import_main.notify, "send",
                        lambda title, body: sent.append((title, body)))
    return sent


def test_combined_label_song_rendered_once(monkeypatch):
    """A song whose source is the combined 'charts+hitparade' appears in a
    single group, a single breakdown item, and is listed exactly once."""
    monkeypatch.setattr(config, "MUSIC_DIR", "/music/Music")
    sent = _capture_notify(monkeypatch)

    imported = [
        ("/music/Music/Ashe/Stop The Wedding!/Ashe - Stop The Wedding!.mp3",
         "charts+hitparade", "Ashe", "Stop The Wedding!",
         "Stop The Wedding!", ""),
    ]
    import_main._notify_imported(imported)

    assert len(sent) == 1
    _title, body = sent[0]
    # The combined label appears exactly once (one breakdown item + one group
    # heading both read "charts+hitparade"); the two sub-labels never appear as
    # SEPARATE groups.
    assert "charts+hitparade" in body
    # The song file is listed exactly once (not once per source).
    assert body.count("Ashe - Stop The Wedding!.mp3") == 1
    # No standalone "hitparade" group heading split off from the combined label.
    assert "\nhitparade\n" not in body


def test_two_sources_do_not_split_into_two_groups(monkeypatch):
    """The overlap song must not be counted/listed under both 'charts' and
    'hitparade' separately - it is one song in one combined group."""
    monkeypatch.setattr(config, "MUSIC_DIR", "/music/Music")
    sent = _capture_notify(monkeypatch)

    imported = [
        ("/music/Music/A/T/T.mp3", "charts+hitparade", "A", "T", "T", ""),
        ("/music/Music/B/U/U.mp3", "charts", "B", "U", "U", ""),
    ]
    import_main._notify_imported(imported)

    _title, body = sent[0]
    # Header counts 2 imported songs, not 3 (no double counting).
    assert body.count("T.mp3") == 1
    assert body.count("U.mp3") == 1
    # "charts+hitparade" and "charts" are distinct group labels.
    assert "charts+hitparade" in body
    assert "charts" in body


def test_manifest_sources_backward_compat():
    """tags.manifest_sources prefers the new 'sources' list, falls back to the
    old single 'source' string, and returns [] when neither is present."""
    # New multi-source entry.
    assert tags.manifest_sources(
        {"sources": ["charts", "hitparade"]}) == ["charts", "hitparade"]
    # Old single-source entry (written by an older charts-download).
    assert tags.manifest_sources({"source": "charts"}) == ["charts"]
    # Neither present / empty.
    assert tags.manifest_sources({}) == []
    assert tags.manifest_sources(None) == []
    # Blank labels are dropped.
    assert tags.manifest_sources({"sources": ["charts", ""]}) == ["charts"]


def test_legacy_single_source_renders_one_label(monkeypatch):
    """A legacy entry with only 'source' produces a single-label group."""
    monkeypatch.setattr(config, "MUSIC_DIR", "/music/Music")
    sent = _capture_notify(monkeypatch)

    # Simulate the importer having read a legacy entry: manifest_sources
    # collapses it to one label, joined to the same 'source' string shape.
    source = "+".join(tags.manifest_sources({"source": "charts"}))
    imported = [("/music/Music/A/T/T.mp3", source, "A", "T", "T", "")]
    import_main._notify_imported(imported)

    _title, body = sent[0]
    assert "charts" in body
    assert body.count("T.mp3") == 1
