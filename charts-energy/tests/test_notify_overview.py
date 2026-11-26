"""
charts-energy Pushover message: overview line (channels, feeds, library) first,
budget cut between whole entries with the neutral "and N more" line.
"""
import pathlib

import pytest

from app import config, i18n, main, notify_budget
from app.i18n import t

_LANG_DIR = pathlib.Path(i18n.__file__).resolve().parent.parent.parent / "lang"


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


def test_screenshot_sized_overview_is_clear_and_not_repeated(monkeypatch):
    sent = _capture(monkeypatch)
    i18n.setup(lang="de", lang_dir=_LANG_DIR)
    monkeypatch.setattr(config, "NOTIFY_MISSING_LIMIT", 40)
    main._notify([], [{"theme": "mundart", "count": 280},
                      {"theme": "charts", "count": 69}], _library(388, 1302))
    msg = sent["msg"]
    assert msg.startswith(
        "<b>📚 Library</b>\n✅ 1302 vorhanden\n🔎 388 fehlen\n\n"
        "<b>⚡ Energy-Titel für charts-sync bereitgestellt</b>\n"
        "📻 Thema mundart: 280 Titel\n📻 Thema charts: 69 Titel\n\n"
        "<b>🎵 Fehlende Library-Titel:</b>\n")
    assert msg.count("1302") == 1
    assert msg.count("388") == 1
    assert msg.count("mundart: 280") == 1
    assert msg.count("charts: 69") == 1
    shown = sum(line.startswith("♪ LArt ") for line in msg.splitlines())
    assert 0 < shown < 388
    assert msg.endswith(t("notify.more", count=388 - shown))
    assert notify_budget.measure([msg]) <= 1024


def test_own_playlist_counts_and_missing_songs_remain_separate(monkeypatch):
    sent = _capture(monkeypatch)
    monkeypatch.setattr(config, "NOTIFY_MISSING_LIMIT", 40)
    main._notify([_own("Only Channel", 3, 2)],
                 [{"theme": "charts", "count": 69}], _library(1, 1))
    lines = sent["msg"].split("\n")
    assert "<b>📻 Own Energy playlists</b>" in lines
    assert "Only Channel ✅3 ⬇2" in lines
    assert "<b>Only Channel</b>" in lines
    assert "♪ #1 Art 1 - Song 1" in lines
    assert "📻 Theme charts: 69 tracks" in lines
    assert "♪ LArt 0 - LSong 0 (1985)" in lines


@pytest.mark.parametrize(("language", "feed_text", "missing_text"), [
    ("de", "Energy-Titel für charts-sync bereitgestellt", "🔎 405 fehlen"),
    ("en", "Energy tracks available to charts-sync", "🔎 405 missing"),
    ("fr", "Titres Energy disponibles pour charts-sync", "🔎 405 manquants"),
])
def test_feed_and_library_use_truthful_status(monkeypatch, language, feed_text, missing_text):
    sent = _capture(monkeypatch)
    i18n.setup(lang=language, lang_dir=_LANG_DIR)
    main._notify([], [{"theme": "mundart", "count": 69}], _library(405, 1))
    assert feed_text in sent["msg"]
    assert missing_text in sent["msg"]
    assert notify_budget.measure([sent["msg"]]) <= 1024


@pytest.mark.parametrize("feed_count", [1, 2])
def test_feed_only_does_not_claim_library_was_checked(monkeypatch, feed_count):
    sent = _capture(monkeypatch)
    feeds = [{"theme": f"theme{i}", "count": i + 1} for i in range(feed_count)]
    main._notify([], feeds, None)
    assert sent["msg"].startswith(t("energy.notify.feed_overview_header"))
    assert t("energy.notify.library_overview_header") not in sent["msg"]
    assert all(f"theme{i}" in sent["msg"] for i in range(feed_count))


def test_two_channels_have_individual_overview_lines(monkeypatch):
    sent = _capture(monkeypatch)
    monkeypatch.setattr(config, "NOTIFY_MISSING_LIMIT", 40)
    main._notify([_own("A", 1, 0), _own("B", 2, 1)], [], None)
    lines = sent["msg"].split("\n")
    assert lines[0] == t("energy.notify.own_overview_header")
    assert lines[1:3] == ["A ✅1 ⬇0", "B ✅2 ⬇1"]


def test_twenty_channels_every_missing_count_in_head(monkeypatch):
    sent = _capture(monkeypatch)
    monkeypatch.setattr(config, "NOTIFY_MISSING_LIMIT", 40)
    results = [_own(f"Energy Channel Number {i}", 100 + i, i, pad=20)
               for i in range(20)]
    main._notify(results, [], None)
    msg = sent["msg"]
    assert notify_budget.measure([msg]) <= 1024
    overview = msg.split("\n\n", 1)[0]
    for i in range(20):
        assert f"⬇{i}" in overview
        assert f"✅{100 + i}" in overview


def test_large_escaped_overviews_keep_counts_and_budget(monkeypatch):
    sent = _capture(monkeypatch)
    results = [_own(f"Channel & {i} " + "x" * 50, 100 + i, 0)
               for i in range(20)]
    feeds = [{"theme": f"feed & {i} " + "y" * 50, "count": 200 + i}
             for i in range(20)]
    main._notify(results, feeds, _library(2, 1))
    msg = sent["msg"]
    assert notify_budget.measure([msg]) <= 1024
    assert "✅ 1 present\n🔎 2 missing" in msg
    assert all(f"✅{100 + i}" in msg for i in range(20))
    assert all(f"📻 {200 + i}" in msg for i in range(20))
    assert "♪ LArt 0 - LSong 0" in msg or t("notify.more", count=2) in msg


def test_html_escaped_overview_falls_back_to_complete_counts(monkeypatch):
    sent = _capture(monkeypatch)
    feeds = [{"theme": "&" * 50 + str(i), "count": 10 + i} for i in range(2)]
    main._notify([], feeds, None)
    assert "📻 10\n📻 11" in sent["msg"]
    assert notify_budget.measure([sent["msg"]]) <= 1024


def test_omitted_present_library_songs_are_not_reported_as_missing(monkeypatch):
    sent = _capture(monkeypatch)
    monkeypatch.setattr(config, "NOTIFY_MISSING_LIMIT", 40)
    main._notify([], [], _library(0, 120))
    msg = sent["msg"]
    lines = msg.split("\n")
    shown = sum(1 for ln in lines if ln.startswith("\u266a "))
    assert 0 < shown < 120
    assert notify_budget.measure([msg]) <= 1024
    assert not any(line.startswith("… and ") for line in lines)
    assert t("energy.notify.library_done", matched=120, missing=0) in msg


def test_library_more_count_excludes_present_songs(monkeypatch):
    sent = _capture(monkeypatch)
    monkeypatch.setattr(config, "NOTIFY_MISSING_LIMIT", 40)
    library = _library(504, 1698)
    main._notify([], [], library)
    lines = sent["msg"].split("\n")
    shown_missing = sum(
        1 for line in lines if line.startswith("\u266a LArt "))

    assert t("energy.notify.library_missing_list") in lines
    assert shown_missing < 504
    assert lines[-1] == t("notify.more", count=504 - shown_missing)
    assert notify_budget.measure([sent["msg"]]) <= 1024


def test_missing_limit_zero_no_song_lines_no_more(monkeypatch):
    sent = _capture(monkeypatch)
    monkeypatch.setattr(config, "NOTIFY_MISSING_LIMIT", 0)
    main._notify([_own("Ch", 1, 5)], [], None)
    msg = sent["msg"]
    assert "Art 1" not in msg
    assert "more" not in msg


def test_truncate_helper_removed():
    assert not hasattr(main, "_truncate_for_pushover")
