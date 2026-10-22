"""
Tests for charts-download's re-downloaded ("retried") song listing in the
Pushover notification.

Previously the message only showed a bare count of re-downloaded songs
(download.notify.retried_header). It now ALSO lists the affected songs by
artist/title as a dedicated group, mirroring the enqueued block, and respects
the shared 15-item cap + "... and N more" tail. These tests drive
_summary_and_notify directly (no network/slskd needed) and capture the Pushover
body via a stubbed notify.send.

All tests run WITHOUT network/Docker.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import config, i18n, main


_LANG_DIR = pathlib.Path(main.__file__).resolve().parent.parent.parent / "lang"


def _capture(monkeypatch):
    """Stub notify.send to capture the (title, message) of the last call."""
    captured: dict[str, str] = {}

    def _fake_send(title, message):
        captured["title"] = title
        captured["message"] = message

    monkeypatch.setattr(main.notify, "send", _fake_send)
    return captured


def _setup(monkeypatch):
    """Force live mode and a deterministic (English) language."""
    monkeypatch.setattr(config, "DRY_RUN", False)
    i18n.setup(lang="en", lang_dir=_LANG_DIR)


def test_retried_songs_are_listed(monkeypatch):
    _setup(monkeypatch)
    captured = _capture(monkeypatch)

    main._summary_and_notify(
        planned=[], enqueued=[], retried=2,
        retried_songs=[("ABBA", "Waterloo"), ("Queen", "Bohemian Rhapsody")])

    msg = captured["message"]
    # The group heading and BOTH songs appear (not just a count).
    assert i18n.t("download.notify.retried_group", count=2) in msg
    assert "ABBA - Waterloo" in msg
    assert "Queen - Bohemian Rhapsody" in msg


def test_retried_list_respects_cap_and_more_tail(monkeypatch):
    _setup(monkeypatch)
    captured = _capture(monkeypatch)

    songs = [(f"Artist{i}", f"Title{i}") for i in range(18)]
    main._summary_and_notify(planned=[], enqueued=[], retried=18,
                             retried_songs=songs)

    msg = captured["message"]
    # The group header reflects the FULL retried total even when truncated.
    assert i18n.t("download.notify.retried_group", count=18) in msg
    # Exactly 15 song lines are shown, then the "... and 3 more" tail.
    shown = [line for line in msg.splitlines() if line.startswith("Artist")]
    assert len(shown) == 15
    assert i18n.t("download.notify.more", count=3) in msg


def test_requeue_failed_returns_songs(monkeypatch):
    """_requeue_failed returns (count, [(artist, title), ...]) for failures."""
    _setup(monkeypatch)

    st = {
        "requested": {
            "abba|waterloo": {"artist": "ABBA", "title": "Waterloo",
                              "username": "u1", "filename": "a.mp3"},
            "queen|bohemian rhapsody": {"artist": "Queen",
                                        "title": "Bohemian Rhapsody",
                                        "username": "u2", "filename": "b.mp3"},
        }
    }

    def _fake_check(client, requested):
        # Only the first entry failed.
        return ["abba|waterloo"], [("u1", {"filename": "a.mp3"})]

    monkeypatch.setattr(main.slsk, "check_download_states", _fake_check)
    monkeypatch.setattr(main.slsk, "cleanup_failed", lambda *a, **k: None)
    monkeypatch.setattr(main.state, "save", lambda *a, **k: None)

    count, songs = main._requeue_failed(client=None, st=st)
    assert count == 1
    assert songs == [("ABBA", "Waterloo")]
    # The failed key was removed from the state.
    assert "abba|waterloo" not in st["requested"]
