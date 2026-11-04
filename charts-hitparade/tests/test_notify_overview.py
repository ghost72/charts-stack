"""
charts-hitparade Pushover message: overview line (all charts) first, budget cut
between whole entries, run-failed message cut on the escaped length.
"""
import pathlib

from app import config, i18n, main, notify_budget
from app.i18n import t

_LANG_DIR = pathlib.Path(i18n.__file__).resolve().parent.parent.parent / "lang"


def _old_message(results):
    """Frozen copy of the pre-change line builder (no truncation applied)."""
    total_matched = sum(r["matched"] for r in results)
    total_missing = sum(r["missing"] for r in results)
    lines = [t("hitparade.notify.summary_header",
               matched=total_matched, missing=total_missing), ""]
    for r in results:
        lines.append(f"<b>{r['title']}</b>")
        lines.append(t("hitparade.notify.chart_line",
                       matched=r["matched"], missing=r["missing"], total=r["total"]))
        missing_songs = r.get("missing_songs") or []
        if missing_songs and config.NOTIFY_MISSING_LIMIT > 0:
            limit = config.NOTIFY_MISSING_LIMIT
            lines.append("")
            for pos, artist, title in missing_songs[:limit]:
                lines.append(t("notify.song_entry", pos=pos, artist=artist, title=title))
            if len(missing_songs) > limit:
                rest = len(missing_songs) - limit
                lines.append(t("notify.missing.more", count=rest))
        lines.append("")
    return "\n".join(lines).strip()


def _chart(title, matched, n_missing, total=100, pad=0):
    return {"title": title, "matched": matched, "missing": n_missing,
            "total": total,
            "missing_songs": [(i, f"Art {i}", "T" * pad + f"Song {i}")
                              for i in range(1, n_missing + 1)]}


def _capture(monkeypatch):
    sent = {}
    monkeypatch.setattr(main.notify, "send",
                        lambda title, msg: sent.update(title=title, msg=msg))
    i18n.setup(lang="en", lang_dir=_LANG_DIR)
    return sent


def test_golden_uncut_matches_old_except_overview(monkeypatch):
    sent = _capture(monkeypatch)
    monkeypatch.setattr(config, "NOTIFY_MISSING_LIMIT", 40)
    results = [_chart("Chart One", 5, 2), _chart("Chart Two", 7, 1)]
    main._notify(results)
    lines = sent["msg"].split("\n")
    assert lines[1] == "Chart One ✅5 ⬇2 · Chart Two ✅7 ⬇1"
    assert "\n".join(lines[:1] + lines[2:]) == _old_message(results)


def test_single_chart_has_no_overview_line(monkeypatch):
    # Real message: 1 chart, 89 present, 11 missing of 100. The overview used to
    # repeat the block header with less information; now the block header
    # ("89 present, 11 missing (of 100)") is the only place with the numbers.
    sent = _capture(monkeypatch)
    monkeypatch.setattr(config, "NOTIFY_MISSING_LIMIT", 40)
    results = [_chart("Swiss Hitparade", 89, 11)]
    main._notify(results)
    msg = sent["msg"]
    lines = msg.split("\n")
    assert lines[0] == t("hitparade.notify.summary_header", matched=89, missing=11)
    assert lines[1] == ""
    assert lines[2] == "<b>Swiss Hitparade</b>"
    assert lines[3] == t("hitparade.notify.chart_line", matched=89, missing=11,
                         total=100)
    assert "Swiss Hitparade ✅89" not in msg
    assert msg == _old_message(results)


def test_german_single_chart_example(monkeypatch):
    sent = {}
    monkeypatch.setattr(main.notify, "send",
                        lambda title, msg: sent.update(title=title, msg=msg))
    i18n.setup(lang="de", lang_dir=_LANG_DIR)
    monkeypatch.setattr(config, "NOTIFY_MISSING_LIMIT", 40)
    main._notify([_chart("Schweizer Hitparade 🇨🇭", 89, 11)])
    msg = sent["msg"]
    assert "89 vorhanden, 11 fehlen (von 100)" in msg
    assert "✅89" not in msg and "⬇️11" not in msg


def test_chart_two_visible_when_chart_one_has_40_missing(monkeypatch):
    sent = _capture(monkeypatch)
    monkeypatch.setattr(config, "NOTIFY_MISSING_LIMIT", 40)
    results = [_chart("Chart One", 10, 40, pad=25), _chart("Chart Two", 77, 3)]
    main._notify(results)
    msg = sent["msg"]
    assert notify_budget.measure([msg]) <= 1024
    assert "Chart Two ✅77 ⬇3" in msg.split("\n")[1]
    lines = msg.split("\n")
    shown = sum(1 for ln in lines if ln.startswith("#"))
    assert lines[-1] == t("notify.more", count=43 - shown)


def test_missing_limit_zero_no_song_lines_no_more(monkeypatch):
    sent = _capture(monkeypatch)
    monkeypatch.setattr(config, "NOTIFY_MISSING_LIMIT", 0)
    main._notify([_chart("Chart One", 1, 5)])
    msg = sent["msg"]
    assert "Art 1" not in msg
    assert "more" not in msg
    assert "1 present, 5 missing" in msg


def test_run_failed_hard_cut_escaped_length(monkeypatch):
    sent = _capture(monkeypatch)
    main._notify_run_failed("<err> & " * 400)
    assert notify_budget.measure([sent["msg"]]) <= 1024
    assert sent["msg"].endswith("…")


def test_truncate_helper_removed():
    assert not hasattr(main, "_truncate_for_pushover")
