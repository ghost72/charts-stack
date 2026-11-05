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
    shown = [line for line in msg.splitlines() if line.startswith("\u266a ")]
    assert len(shown) == 15
    assert i18n.t("recommend.notify.more", count=3) in msg


# --- Golden no-cut tests + budget (notify_budget) ------------------------------

from app import notify_budget  # noqa: E402


def _old_message(body, missing):
    """Frozen copy of the pre-change message builder (message only)."""
    t = i18n.t
    lines = [body]
    if missing:
        lines.append("")
        lines.append(t("recommend.notify.to_load_header", count=len(missing)))
        shown = missing[:15]
        for _pos, artist, title in shown:
            lines.append(t("recommend.notify.song_entry", artist=artist, title=title))
        if len(missing) > 15:
            lines.append("")
            lines.append(t("recommend.notify.more", count=len(missing) - 15))
    return "\n".join(lines)


def _body(n_missing, cand):
    t = i18n.t
    head = t("recommend.notify.new_to_load", missing=n_missing, users=1,
             candidates=cand)
    return head + t("recommend.notify.sources", sources="u1 (stats 20)")


def _run(monkeypatch, n_missing, pad=0):
    i18n.setup(lang="en", lang_dir=_LANG_DIR)
    captured = _capture(monkeypatch)
    missing = [(i, "A" * pad + f"Artist{i}", f"Title{i}") for i in range(n_missing)]
    main._notify(
        source_results=[{"user": "u1", "source": "stats", "n": 20}],
        counters={"sources": 1, "candidates": 20, "matched": 20 - n_missing,
                  "missing": n_missing},
        missing=missing)
    return captured["message"], missing


def test_golden_below_cap(monkeypatch):
    msg, missing = _run(monkeypatch, 5)
    assert msg == _old_message(_body(5, 20), missing)


def test_golden_above_cap_within_budget(monkeypatch):
    msg, missing = _run(monkeypatch, 18)
    assert msg == _old_message(_body(18, 20), missing)
    assert msg.endswith("\n\n" + i18n.t("recommend.notify.more", count=3))


def test_golden_exactly_at_cap(monkeypatch):
    msg, missing = _run(monkeypatch, 15)
    assert msg == _old_message(_body(15, 20), missing)


def test_budget_cut_many_long_songs(monkeypatch):
    msg, _missing = _run(monkeypatch, 30, pad=60)
    assert notify_budget.measure([msg]) <= 1024
    lines = msg.split("\n")
    shown = sum(1 for ln in lines if "Artist" in ln)
    assert 0 < shown < 15
    assert lines[-2] == ""
    assert lines[-1] == i18n.t("recommend.notify.more", count=30 - shown)


def _sources_line(monkeypatch, users):
    i18n.setup(lang="en", lang_dir=_LANG_DIR)
    captured = _capture(monkeypatch)
    main._notify(
        source_results=[{"user": u, "source": "stats", "n": 3} for u in users],
        counters={"sources": len(users), "candidates": 3, "matched": 1,
                  "missing": 2},
        missing=[(1, "Dua Lipa", "Levitating")])
    return captured["message"].split("\n")


def test_sources_one_user_stays_on_one_line(monkeypatch):
    lines = _sources_line(monkeypatch, ["u1"])
    assert "Sources: u1 (stats 3)" in lines


def test_sources_two_users_one_line(monkeypatch):
    lines = _sources_line(monkeypatch, ["u1", "u2"])
    assert "Sources: u1 (stats 3) \u00b7 u2 (stats 3)" in lines


def test_sources_three_users_one_per_line(monkeypatch):
    lines = _sources_line(monkeypatch, ["u1", "u2", "u3"])
    i = lines.index("Sources: ")
    assert lines[i + 1:i + 4] == ["u1 (stats 3)", "u2 (stats 3)", "u3 (stats 3)"]
