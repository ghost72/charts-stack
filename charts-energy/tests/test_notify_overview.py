"""
charts-energy Pushover message: overview line (channels, feeds, library) first,
budget cut between whole entries with the neutral "and N more" line.
"""
import pathlib

from app import config, i18n, main, notify_budget
from app.i18n import t

_LANG_DIR = pathlib.Path(i18n.__file__).resolve().parent.parent.parent / "lang"


def _old_message(results, feed_results, library_results):
    """Frozen copy of the pre-change line builder (no truncation applied)."""
    lib = library_results or {}
    lib_present = len(lib.get("present") or [])
    lib_to_load = len(lib.get("to_load") or [])
    total_matched = sum(r["matched"] for r in results) + lib_present
    total_missing = sum(r["missing"] for r in results) + lib_to_load
    lines = [t("energy.notify.body",
               matched=total_matched, missing=total_missing), ""]
    for r in results:
        lines.append(f"<b>{r['title']}</b>")
        lines.append(t("energy.notify.own_done",
                       matched=r["matched"], missing=r["missing"]))
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
    for fr in feed_results:
        lines.append(t("energy.notify.feed_done",
                       theme=fr["theme"], count=fr["count"]))
    if library_results:
        to_load = library_results.get("to_load") or []
        present = library_results.get("present") or []
        lines.append("")
        lines.append(t("energy.notify.library_section"))
        lines.append(t("energy.notify.library_done",
                       matched=len(present), missing=len(to_load)))
        if to_load:
            lines.append("")
            lines.append(t("energy.notify.library_to_load_header",
                           count=len(to_load)))
            limit = config.NOTIFY_MISSING_LIMIT
            shown = to_load[:limit] if limit > 0 else to_load
            for _pos, artist, title, yr, *_rest in shown:
                lines.append(main._library_song_line(artist, title, yr))
            if limit > 0 and len(to_load) > limit:
                lines.append(t("notify.missing.more", count=len(to_load) - limit))
        if present:
            lines.append("")
            lines.append(t("energy.notify.library_present_header",
                           count=len(present)))
            for artist, title, yr in present:
                lines.append(main._library_song_line(artist, title, yr))
    return "\n".join(lines).strip()


def _capture(monkeypatch):
    sent = {}
    monkeypatch.setattr(main.notify, "send",
                        lambda title, msg: sent.update(title=title, msg=msg))
    i18n.setup(lang="en", lang_dir=_LANG_DIR)
    return sent


def _own(title, matched, n_missing, pad=0):
    return {"title": title, "matched": matched, "missing": n_missing,
            "missing_songs": [(i, f"Art {i}", "T" * pad + f"Song {i}")
                              for i in range(1, n_missing + 1)]}


def _library(n_load, n_present, pad=0):
    return {
        "to_load": [(i, f"LArt {i}", "T" * pad + f"LSong {i}", "1985")
                    for i in range(n_load)],
        "present": [(f"PArt {i}", f"PSong {i}", "") for i in range(n_present)],
    }


def _norm(text):
    """Compare ignoring runs of blank lines (whitespace-only differences)."""
    out, prev_blank = [], False
    for ln in text.split("\n"):
        blank = not ln.strip()
        if blank and prev_blank:
            continue
        out.append(ln)
        prev_blank = blank
    return "\n".join(out)


def test_golden_uncut_message_matches_old_except_overview(monkeypatch):
    sent = _capture(monkeypatch)
    monkeypatch.setattr(config, "NOTIFY_MISSING_LIMIT", 40)
    results = [_own("Channel A", 3, 2), _own("Channel B", 1, 1)]
    feeds = [{"theme": "mundart", "count": 7}]
    lib = _library(2, 2)
    main._notify(results, feeds, lib)
    new_lines = sent["msg"].split("\n")
    # Drop the overview (4 items -> lines 2..5, one per line); everything else
    # equals the old message.
    assert new_lines[1:5] == ["Channel A ✅3 ⬇2", "Channel B ✅1 ⬇1",
                              "mundart ➡7", "📚 Library ✅2 ⬇2"]
    without_overview = "\n".join(new_lines[:1] + new_lines[5:])
    assert _norm(without_overview) == _norm(_old_message(results, feeds, lib))


def test_overview_order_channels_feeds_library(monkeypatch):
    sent = _capture(monkeypatch)
    main._notify([_own("Ch", 1, 0)], [{"theme": "t", "count": 4}],
                 _library(1, 0))
    assert sent["msg"].split("\n")[1:4] == ["Ch ✅1 ⬇0", "t ➡4",
                                             "📚 Library ✅0 ⬇1"]


def test_two_items_one_line(monkeypatch):
    sent = _capture(monkeypatch)
    main._notify([_own("Ch", 1, 0)], [{"theme": "t", "count": 4}], None)
    assert sent["msg"].split("\n")[1] == "Ch ✅1 ⬇0 · t ➡4"


def test_single_channel_has_no_overview_line(monkeypatch):
    sent = _capture(monkeypatch)
    monkeypatch.setattr(config, "NOTIFY_MISSING_LIMIT", 40)
    main._notify([_own("Only Channel", 3, 2)], [], None)
    lines = sent["msg"].split("\n")
    # Head line, blank line, then the bold block header: the channel's own
    # counts live in the block (own_done), nowhere twice.
    assert lines[1] == ""
    assert lines[2] == "<b>Only Channel</b>"
    assert lines[3] == t("energy.notify.own_done", matched=3, missing=2)
    assert "Only Channel ✅3" not in sent["msg"]
    # No overview follows the head line -> no dangling trailing colon.
    assert lines[0] == t("energy.notify.body_plain", matched=3, missing=2)
    assert not lines[0].endswith(":")


def test_two_items_keep_colon_before_overview(monkeypatch):
    sent = _capture(monkeypatch)
    monkeypatch.setattr(config, "NOTIFY_MISSING_LIMIT", 40)
    main._notify([_own("A", 1, 0), _own("B", 2, 1)], [], None)
    lines = sent["msg"].split("\n")
    assert lines[0] == t("energy.notify.body", matched=3, missing=1)
    assert lines[0].endswith(":")
    assert lines[1] == "A ✅1 ⬇0 · B ✅2 ⬇1"


def test_twenty_channels_every_missing_count_in_head(monkeypatch):
    sent = _capture(monkeypatch)
    monkeypatch.setattr(config, "NOTIFY_MISSING_LIMIT", 40)
    results = [_own(f"Energy Channel Number {i}", 100 + i, i, pad=20)
               for i in range(20)]
    main._notify(results, [], None)
    msg = sent["msg"]
    assert notify_budget.measure([msg]) <= 1024
    overview = msg.split("\n")[1]
    for i in range(20):
        assert f"⬇{i}" in overview
        assert f"✅{100 + i}" in overview


def test_library_present_list_cut_with_exact_more_count(monkeypatch):
    sent = _capture(monkeypatch)
    monkeypatch.setattr(config, "NOTIFY_MISSING_LIMIT", 40)
    main._notify([], [], _library(0, 120))
    msg = sent["msg"]
    lines = msg.split("\n")
    shown = sum(1 for ln in lines if ln.startswith("\u266a "))
    assert 0 < shown < 120
    assert notify_budget.measure([msg]) <= 1024
    assert lines[-1] == t("notify.more", count=120 - shown)
    # Library is the only item -> no overview line; its section carries the count.
    assert lines[1] == ""
    assert t("energy.notify.library_done", matched=120, missing=0) in msg


def test_missing_limit_zero_no_song_lines_no_more(monkeypatch):
    sent = _capture(monkeypatch)
    monkeypatch.setattr(config, "NOTIFY_MISSING_LIMIT", 0)
    main._notify([_own("Ch", 1, 5)], [], None)
    msg = sent["msg"]
    assert "Art 1" not in msg
    assert "more" not in msg


def test_truncate_helper_removed():
    assert not hasattr(main, "_truncate_for_pushover")
