"""
charts-sync Pushover message: overview line first, budget cut between whole
songs with one explicit "... and N more" line.
"""
import pathlib


from app import i18n, main, notify_budget

_LANG_DIR = pathlib.Path(i18n.__file__).resolve().parent.parent.parent / "lang"


class _Src:
    title = "Charts"
    name = "charts.m3u"
    last_stats = {}


def _send(monkeypatch, matched, missing, state=None, limit=500):
    sent = {}
    monkeypatch.setattr(main.notify, "send",
                        lambda title, msg: sent.update(title=title, msg=msg))
    monkeypatch.setattr(main.config, "NOTIFY_ONLY_ON_CHANGE", False)
    monkeypatch.setattr(main.config, "NOTIFY_MISSING_LIMIT", limit)
    i18n.setup(lang="en", lang_dir=_LANG_DIR)
    main._handle_notify(_Src(), matched, missing, state if state is not None else {})
    return sent["msg"]


def _matched(n):
    return [(i, f"Artist {i}", f"Song {i}", f"/music/{i}.mp3") for i in range(1, n + 1)]


def _missing(n, pad=0):
    return [(i, f"Miss {i}", "T" * pad + f"Song {i}") for i in range(1, n + 1)]


def test_overview_new_and_missing(monkeypatch):
    msg = _send(monkeypatch, _matched(3), _missing(2))
    lines = msg.split("\n")
    assert "🆕 3 new · ⬇ 2 missing" in lines
    # Overview sits in the head, before the first detail block.
    assert lines.index("🆕 3 new · ⬇ 2 missing") < lines.index("")


def test_no_overview_when_nothing_new_or_missing(monkeypatch):
    state = {"charts.m3u": {"paths": ["/music/1.mp3"], "missing": []}}
    msg = _send(monkeypatch, [(1, "A", "B", "/music/1.mp3")], [], state=state)
    assert "🆕" not in msg and "⬇" not in msg


def test_overview_only_missing(monkeypatch):
    state = {"charts.m3u": {"paths": ["/music/1.mp3"], "missing": []}}
    msg = _send(monkeypatch, [(1, "A", "B", "/music/1.mp3")], _missing(4),
                state=state)
    # One category only: no overview line; the block header carries the count.
    assert "⬇ 4 missing" not in msg and "🆕" not in msg
    assert "<b>4 still missing:</b>" in msg


def test_budget_overflow_neutral_more_line(monkeypatch):
    msg = _send(monkeypatch, _matched(8), _missing(40, pad=30), limit=100)
    assert notify_budget.measure([msg]) <= 1024
    assert "🆕 8 new · ⬇ 40 missing" in msg.split("\n")
    shown_missing = sum(1 for ln in msg.split("\n") if "Miss " in ln)
    shown_new = sum(1 for ln in msg.split("\n") if "Artist " in ln)
    omitted = (8 - shown_new) + (40 - shown_missing)
    assert msg.split("\n")[-1] == i18n.t("notify.more", count=omitted)
    # The neutral global line does not carry the "see missing file" hint.
    assert "(see missing file)" not in msg.split("\n")[-1]


def test_missing_limit_zero_none_listed_last(monkeypatch):
    msg = _send(monkeypatch, _matched(3), _missing(5), limit=0)
    lines = msg.split("\n")
    assert lines[-1] == i18n.t("notify.missing.none_listed", count=5)
    assert "#1 Artist 1 - Song 1" in lines
    assert lines.index("#1 Artist 1 - Song 1") < len(lines) - 1
    assert "more" not in msg


def test_more_than_eight_new_within_budget_no_more_line(monkeypatch):
    msg = _send(monkeypatch, _matched(12), [])
    assert msg.count("#") == 8
    assert "more" not in msg


def test_missing_cap_note_kept(monkeypatch):
    msg = _send(monkeypatch, [], _missing(5), limit=2)
    assert i18n.t("notify.missing.more", count=3) in msg


def test_truncate_helper_removed():
    assert not hasattr(main, "_truncate_for_pushover")
