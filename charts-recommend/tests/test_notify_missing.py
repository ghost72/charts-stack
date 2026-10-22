"""
Tests for charts-recommend's missing-song listing in the Pushover notification.

Previously the message only showed a bare count plus the per-user source
breakdown. It now ALSO lists the songs it will download by artist/title,
mirroring charts-flashback, and respects a 15-item cap + "... and N more" tail.
The all_present path (nothing to download) deliberately lists no songs.

All tests run WITHOUT network/Docker.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import i18n, main


_LANG_DIR = pathlib.Path(main.__file__).resolve().parent.parent.parent / "lang"


def _capture(monkeypatch):
    """Stub notify.send to capture the (title, message) of the last call."""
    captured: dict[str, str] = {}

    def _fake_send(title, message):
        captured["title"] = title
        captured["message"] = message

    monkeypatch.setattr(main.notify, "send", _fake_send)
    return captured


def test_missing_songs_are_listed(monkeypatch):
    i18n.setup(lang="en", lang_dir=_LANG_DIR)
    captured = _capture(monkeypatch)

    main._notify(
        source_results=[{"user": "u1", "source": "stats", "n": 3}],
        counters={"sources": 1, "candidates": 3, "matched": 1, "missing": 2},
        missing=[(1, "Dua Lipa", "Levitating"),
                 (2, "The Weeknd", "Blinding Lights")])

    msg = captured["message"]
    assert i18n.t("recommend.notify.to_load_header", count=2) in msg
    assert "Dua Lipa - Levitating" in msg
    assert "The Weeknd - Blinding Lights" in msg


def test_all_present_lists_no_songs(monkeypatch):
    i18n.setup(lang="en", lang_dir=_LANG_DIR)
    captured = _capture(monkeypatch)

    main._notify(
        source_results=[{"user": "u1", "source": "stats", "n": 3}],
        counters={"sources": 1, "candidates": 3, "matched": 3, "missing": 0},
        missing=[])

    msg = captured["message"]
    assert "Dua Lipa - Levitating" not in msg
    assert "The Weeknd - Blinding Lights" not in msg
    assert i18n.t("recommend.notify.to_load_header", count=0) not in msg


def test_missing_list_respects_cap_and_more_tail(monkeypatch):
    i18n.setup(lang="en", lang_dir=_LANG_DIR)
    captured = _capture(monkeypatch)

    missing = [(i, f"Artist{i}", f"Title{i}") for i in range(18)]
    main._notify(
        source_results=[{"user": "u1", "source": "stats", "n": 20}],
        counters={"sources": 1, "candidates": 20, "matched": 2, "missing": 18},
        missing=missing)

    msg = captured["message"]
    assert i18n.t("recommend.notify.to_load_header", count=18) in msg
    shown = [line for line in msg.splitlines() if line.startswith("Artist")]
    assert len(shown) == 15
    assert i18n.t("recommend.notify.more", count=3) in msg
