"""
Unit tests for the Pushover notification body built in main._notify.

Covers the song-list behavior: _notify now lists the actual songs that will be
downloaded (the `missing` set), one line per song, with the resolved release
year when available (and no empty parentheses when the year is missing). The
existing summary line stays as line 1. All tests run WITHOUT network/Docker and
set i18n.setup(lang=..., lang_dir=...) explicitly.
"""

import pathlib

import pytest

from app import i18n, main


# Central, shipped language files (app -> charts-flashback -> charts-stack/lang).
_LANG_DIR = pathlib.Path(i18n.__file__).resolve().parent.parent.parent / "lang"


@pytest.fixture
def captured(monkeypatch):
    """Capture the (title, message) handed to notify.send instead of sending."""
    sent = {}
    monkeypatch.setattr(main.notify, "send",
                        lambda title, message: sent.update(title=title, message=message))
    return sent


@pytest.fixture
def de_lang():
    i18n.setup(lang="de", lang_dir=_LANG_DIR)
    yield
    i18n.setup()


def test_notify_lists_missing_songs_with_year(captured, de_lang):
    """The body contains the German header plus one line per song in the
    '<artist> - <title> (<year>)' format."""
    counters = {"channels": 1, "new": 2, "matched": 0, "missing": 2}
    missing = [
        (1, "Céline Dion", "That's the Way It Is", "1999"),
        (2, "Duran Duran", "The Wild Boys", "1984"),
    ]
    main._notify(counters, missing)

    msg = captured["message"]
    # Summary line (lead) stays intact.
    assert "FLASHBACK-Rotation" in msg
    # German header for the list.
    assert "zum Nachladen:" in msg
    # Per-song lines with year.
    assert "Céline Dion - That's the Way It Is (1999)" in msg
    assert "Duran Duran - The Wild Boys (1984)" in msg


def test_notify_omits_empty_parentheses_without_year(captured, de_lang):
    """A song without a resolved year is rendered as '<artist> - <title>' with
    no empty parentheses."""
    counters = {"channels": 1, "new": 1, "matched": 0, "missing": 1}
    missing = [(1, "Unknown Artist", "Mystery Song", "")]
    main._notify(counters, missing)

    msg = captured["message"]
    assert "Unknown Artist - Mystery Song" in msg
    assert "Mystery Song ()" not in msg


def test_notify_no_missing_sends_summary_only(captured, de_lang):
    """When nothing is missing (all present), only the summary line goes out -
    no header, no song lines."""
    counters = {"channels": 1, "new": 3, "matched": 3, "missing": 0}
    main._notify(counters, [])

    msg = captured["message"]
    assert "bereits in der Library" in msg
    assert "zum Nachladen:" not in msg


def test_notify_no_new_songs_does_not_send(captured, de_lang):
    """new == 0 logs and sends nothing (unchanged behavior)."""
    counters = {"channels": 0, "new": 0, "matched": 0, "missing": 0}
    main._notify(counters, [])
    assert captured == {}


def test_notify_truncates_large_batch(captured, de_lang):
    """A very large batch is cut to Pushover's 1024-char cap with a note."""
    counters = {"channels": 1, "new": 50, "matched": 0, "missing": 50}
    missing = [(i, f"Artist Number {i}", f"A Fairly Long Song Title {i}", "1985")
               for i in range(1, 51)]
    main._notify(counters, missing)

    msg = captured["message"]
    assert len(msg) <= 1024
    # The shared truncation note is appended.
    assert "gekürzt" in msg


def test_notify_lists_present_songs(captured, de_lang):
    """When newly-seen songs are already in the library, _notify lists them
    under the 'already in the library' header (in addition to the summary),
    using the same '<artist> - <title> (<year>)' per-song format."""
    counters = {"channels": 1, "new": 2, "matched": 2, "missing": 0}
    present = [
        ("Queen", "Don't Stop Me Now", "1978"),
        ("ABBA", "Lay All Your Love On Me", ""),
    ]
    main._notify(counters, [], present)

    msg = captured["message"]
    # The "already in the library" list header is present.
    assert "bereits in der Library:" in msg
    # Per-song lines: with year, and without empty parentheses when no year.
    assert "Queen - Don't Stop Me Now (1978)" in msg
    assert "ABBA - Lay All Your Love On Me" in msg
    assert "ABBA - Lay All Your Love On Me ()" not in msg
    # Nothing to download in this case.
    assert "zum Nachladen:" not in msg
