"""Unit tests for the shared notify_budget helper (reference copy: charts-sync)."""
import random

import pytest

from test_shared_helper_parity import _load

nb = _load("charts-sync", "notify_budget.py")
Entry, Block = nb.Entry, nb.Block


def more(n):
    return f"... and {n} more"


def song(i, pad=30):
    return f"♪ Artist {i} - " + "x" * pad


def make_blocks(sizes, pad=30, **kw):
    return [Block(entries=[Entry([song(f"{b}-{i}", pad)]) for i in range(n)],
                  header=["", f"<b>G{b}</b>"], **kw)
            for b, n in enumerate(sizes)]


# --- fit_message ------------------------------------------------------------

def test_fits_all_no_more_line():
    out = nb.fit_message(["head"], make_blocks([3, 2]), ["", "foot"], more)
    assert out.count("♪") == 5
    assert "more" not in out
    assert out.startswith("head\n") and out.endswith("foot")


def test_stops_between_entries_exact_more_count():
    blocks = make_blocks([20, 20], pad=60)
    out = nb.fit_message(["head"], blocks, ["", "foot"], more, limit=500)
    shown = out.count("♪")
    assert 0 < shown < 40
    assert nb.measure([out]) <= 500
    assert out.count("... and") == 1
    assert f"... and {40 - shown} more" in out
    assert out.endswith("foot")
    # Every shown line is a whole entry.
    for ln in out.split("\n"):
        if ln.startswith("♪"):
            assert ln.endswith("x" * 60)


def test_blocks_without_shown_entry_have_no_header():
    blocks = make_blocks([30, 5], pad=60)
    out = nb.fit_message(["head"], blocks, [], more, limit=400)
    assert "G1" not in out
    assert "G0" in out


def test_weight_zero_not_counted():
    blocks = [Block(entries=[Entry(["info"], weight=0)]
                    + [Entry(["s" * 20]) for _ in range(6)])]
    assert nb.fit_message(["h"], blocks, [], more, limit=1000).count("s" * 20) == 6
    out = nb.fit_message(["h"], blocks, [], more, limit=90)
    shown = out.count("s" * 20)
    assert 0 < shown < 6
    assert "info" in out
    assert out.endswith(more(6 - shown))
    # A block of only weight-0 entries never produces a "more" line.
    only_info = [Block(entries=[Entry(["info" * 30], weight=0)])]
    assert "more" not in nb.fit_message(["h"], only_info, [], more, limit=40)


def test_multiline_entry_never_split():
    blocks = [Block(entries=[Entry(["a", "reason a", ""]) for _ in range(10)])]
    out = nb.fit_message(["h"], blocks, [], more, limit=120)
    lines = out.split("\n")
    assert lines.count("a") == lines.count("reason a") > 0
def test_randomized_limits_always_within():
    rnd = random.Random(1)
    blocks = make_blocks([30, 12, 7], pad=45)
    for limit in range(200, 1101, 7):
        out = nb.fit_message(["head line", "overview"], blocks,
                             ["", "footer"], more, limit=limit)
        assert nb.measure([out]) <= limit, limit
        assert out.startswith("head line\noverview")
        assert out.endswith("footer") or nb.measure(["head line", "overview",
                                                      "", "footer"]) > limit
    assert rnd  # keep the seeded Random referenced


def test_escaped_chars_stay_within_limit():
    ent = [Entry([f"♪ A & B <{i}> - T & U"]) for i in range(80)]
    out = nb.fit_message(["h"], [Block(entries=ent)], ["", "f"], more, limit=300)
    assert nb.measure([out]) <= 300


def test_head_plus_tail_too_long_hard_cut():
    out = nb.fit_message(["h" * 400], make_blocks([3]), ["t" * 400], more, limit=300)
    assert nb.measure([out]) <= 300
    assert out.endswith("…")


def test_empty_inputs():
    assert nb.fit_message([], [], [], more) == ""
    assert nb.fit_message(["h"], [], [], more) == "h"
    assert nb.fit_message(["h"], [Block(entries=[])], [], more) == "h"
    assert nb.fit_message(["h"], [], ["t"], more, limit=0) == ""


def test_cap_note_block_fully_shown_prints_note_no_double_count():
    blocks = [Block(entries=[Entry(["s1"]), Entry(["s2"])], hidden=3,
                    cap_note="... and 3 more (note)")]
    out = nb.fit_message(["h"], blocks, [], more)
    assert out == "h\ns1\ns2\n... and 3 more (note)"
    assert "... and 3 more\n" not in out


def test_cap_note_block_cut_counts_hidden_in_global_line():
    blocks = [Block(entries=[Entry(["s%d" % i * 10]) for i in range(5)],
                    hidden=3, cap_note="NOTE")]
    out = nb.fit_message(["h"], blocks, [], more, limit=40)
    shown = out.count("s")
    assert "NOTE" not in out
    assert out.endswith(more(5 - shown // 10 + 3))


def test_global_cap_hidden_with_more_sep_fits_path():
    blocks = [Block(entries=[Entry(["s1"]), Entry(["s2"])], hidden=4)]
    out = nb.fit_message(["h"], blocks, [], more, more_sep=("",))
    assert out == "h\ns1\ns2\n\n... and 4 more"


def test_global_cap_more_sep_budget_cut_path():
    blocks = [Block(entries=[Entry(["s" * 20]) for _ in range(10)], hidden=4)]
    out = nb.fit_message(["h"], blocks, [], more, limit=80, more_sep=("",))
    shown = out.count("s" * 20)
    assert out.endswith(f"\n\n... and {10 - shown + 4} more")
    assert nb.measure([out]) <= 80


# --- hard_cut ---------------------------------------------------------------

def test_hard_cut_limit_zero():
    assert nb.hard_cut("abc", 0) == ""
    assert nb.hard_cut("abc", -3) == ""


def test_hard_cut_fits_unchanged():
    assert nb.hard_cut("abc & d", 100) == "abc & d"


def test_hard_cut_amp_heavy():
    out = nb.hard_cut("&" * 100, 50)
    assert out.endswith("…")
    assert nb.measure([out]) <= 50


def test_hard_cut_never_splits_tags_or_entities():
    out = nb.hard_cut("<b>" + "a&b " * 40, 60)
    assert nb.measure([out]) <= 60
    assert "&am" not in out.replace("&amp;", "")
    # Raw text is cut, so escaping the result gives whole entities only.
    esc = nb.escaped(out)
    assert esc.count("&") == esc.count("&amp;") + esc.count("&lt;") + esc.count("&gt;")


# --- overview_line ----------------------------------------------------------

def test_overview_empty_and_single_omitted():
    assert nb.overview_line([]) == ""
    # One item: the detail block below carries the same numbers -> omitted.
    assert nb.overview_line(["a 1"]) == ""


def test_overview_two_items_one_line():
    assert nb.overview_line(["a 1", "b 2"]) == "a 1 · b 2"
    assert nb.overview_line(["a 1", "b 2"], sep=", ") == "a 1, b 2"


def test_overview_three_or_more_one_per_line():
    assert nb.overview_line(["a 1", "b 2", "c 3"]) == "a 1\nb 2\nc 3"
    six = [f"label {i} · {i}" for i in range(6)]
    assert nb.overview_line(six).split("\n") == six


def test_overview_compact_fallback_is_one_line():
    parts = ["x" * 30 + f" {i}" for i in range(20)]
    compact = [f"c{i}" for i in range(20)]
    out = nb.overview_line(parts, compact)
    assert out == " · ".join(compact)
    assert "\n" not in out


def test_overview_ellipsis_fallback_multiline():
    parts = ["y" * 30 for _ in range(30)]
    out = nb.overview_line(parts)
    assert out.endswith("\n…")
    assert len(out) <= nb.OVERVIEW_MAX
    # Only whole items are kept.
    assert all(line in ("y" * 30, "…") for line in out.split("\n"))


def test_overview_ellipsis_fallback_two_items_single_line():
    parts = ["q" * 200, "r" * 200]
    out = nb.overview_line(parts)
    assert out.endswith(" …")
    assert len(out) <= nb.OVERVIEW_MAX


def test_overview_mismatched_compact_ignored():
    parts = ["z" * 40 for _ in range(10)]
    out = nb.overview_line(parts, ["a", "b"])
    assert out.endswith("\n…")
    assert len(out) <= nb.OVERVIEW_MAX


def test_multiline_overview_in_head_is_never_cut_and_fits():
    six = ["📺 YouTube · 44", "⚡ Energy · 39", "🇨🇭 Schweizer Mundart · 5",
           "🔥 Aktuelle Charts · 1", "📻 FLASHBACK FM · 1",
           "🙋 Manuell angefordert · 1"]
    overview = nb.overview_line(six)
    blocks = [nb.Block(entries=[nb.Entry([f"♪ Artist {i} - Song {i}"])
                                for i in range(200)], header=["", "<b>x</b>"])]
    out = nb.fit_message(["head", overview], blocks, ["", "footer"],
                         more=lambda n: f"... {n} more")
    assert nb.measure([out]) <= nb.LIMIT
    for line in six:
        assert line in out.split("\n")


# --- escaped / measure ------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("a & b", "a &amp; b"),
    ("<b>x</b>", "<b>x</b>"),
    ("<x>", "&lt;x&gt;"),
])
def test_escaped(text, expected):
    assert nb.escaped(text) == expected
    assert nb.measure([text]) == len(expected)
