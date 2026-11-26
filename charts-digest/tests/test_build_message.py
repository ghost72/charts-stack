"""
build_message tests: the overview line (all sources with counts) leads the
message and survives the Pushover budget cut; the screenshot scenario
(112 songs, 6 sources, Energy 59 first) is the regression case.
"""
import pathlib

from app import config, digest, i18n, notify_budget

_LANG_DIR = pathlib.Path(i18n.__file__).resolve().parent.parent.parent / "lang"


def _entries(prefix, n):
    return [{"artist": f"{prefix} Artist {i}", "title": f"{prefix} Song Title {i}"}
            for i in range(n)]


def test_scene_sources_keep_names_and_escape_html():
    _title, message, count = digest.build_message({
        "Mundart: Rock & Roll+Mundart: <Pop>": _entries("Scene", 1),
    }, 1)
    assert count == 1
    assert "Rock &amp; Roll+&lt;Pop&gt;" in message
    assert "🎸" in message
    assert "<Pop>" not in message
    assert message.count("Scene Artist 0") == 1


def test_old_technical_scene_source_has_readable_label():
    i18n.setup(lang="de", lang_dir=_LANG_DIR)
    _title, message, count = digest.build_message({
        ".charts-scene-missing": _entries("Legacy", 1),
        "Playlist: French Rock": _entries("French", 1),
    }, 2)
    assert count == 2
    assert ".charts-scene-missing" not in message
    assert "🎸 Szenen-Tophits" in message
    assert "🎸 French Rock" in message


def test_legacy_and_current_scene_sources_share_one_digest_group(monkeypatch):
    i18n.setup(lang="de", lang_dir=_LANG_DIR)
    records = [
        {"ts": "2026-11-23T09:00:00+0100", "source": source, **entry}
        for source, entries in (
            ("Mundart: Mundart-Rock", _entries("Old", 5)),
            ("Playlist: Mundart-Rock", _entries("New", 2) + _entries("Old", 1)),
            ("Playlist: Mundart-Pop", _entries("Pop", 1)),
            ("Playlist: Mundart-Rock+Playlist: Mundart-Pop", _entries("Both", 1)),
        )
        for entry in entries
    ]
    monkeypatch.setattr(digest, "_read_log", lambda: records)
    monkeypatch.setattr(digest, "_prune_log", lambda records: None)
    monkeypatch.setattr(digest.time, "time", lambda: 1795420800)
    monkeypatch.setattr(config, "DIGEST_WINDOW_HOURS", 48)

    groups, total = digest.collect()
    _, message, unique = digest.build_message(groups, total)

    assert total == 10
    assert unique == 9
    assert len(groups) == 3
    assert "🎸 Mundart-Rock · 7" in message
    assert "🎸 Mundart-Pop · 1" in message
    assert "🎸 Mundart-Rock+Mundart-Pop · 1" in message
    assert "<b>3</b> Quelle(n)" in message


def _screenshot_groups():
    # 59 + 25 + 12 + 8 + 5 + 3 = 112 songs from 6 sources.
    return {
        "energy-library": _entries("En", 59),
        "charts": _entries("Ch", 25),
        "manuell": _entries("Ma", 12),
        "upload": _entries("Up", 8),
        "mundart": _entries("Mu", 5),
        "youtube": _entries("Yt", 3),
    }


def test_screenshot_regression_overview_survives_budget():
    i18n.setup(lang="de", lang_dir=_LANG_DIR)
    groups = _screenshot_groups()
    title, msg, unique = digest.build_message(groups, 112)
    assert unique == 112
    assert len(title) <= 250
    # Within the Pushover limit as transmitted (escaped).
    assert notify_budget.measure([msg]) <= config.PUSHOVER_MESSAGE_LIMIT
    # All six counts in the first lines / first 400 chars.
    first = msg[:400]
    for text in ("⚡ Energy · 59", "🔥 Aktuelle Charts · 25", "🙋 Manuell angefordert · 12",
                 "📤 Manuell hochgeladen · 8", "🇨🇭 Schweizer Mundart · 5", "📺 YouTube · 3"):
        assert text in first, text
    lines = msg.split("\n")
    # Overview is separated from the heading and followed by a blank line.
    assert lines[2] == ""
    assert lines[3] == "⚡ Energy · 59"
    assert lines[8] == "📺 YouTube · 3"
    assert lines[9] == ""
    assert "➖" not in msg
    # Footer intact, last detail line is the global "more" line.
    assert lines[-1] == i18n.t("digest.notify.footer")
    more_line = lines[-3]
    assert lines[-2] == ""
    shown = sum(1 for ln in lines if ln.startswith("♪ "))
    assert more_line == i18n.t("digest.notify.more", count=112 - shown)
    assert shown < 112
    # No group header without an entry after it.
    for i, ln in enumerate(lines):
        if ln.startswith(tuple(e for e, _ in digest._SOURCE_LABELS.values())) and "<b>" in ln and " · " in ln:
            assert lines[i + 1].startswith("♪ ")


def test_overview_order_by_size_then_canonical_rank():
    i18n.setup(lang="en", lang_dir=_LANG_DIR)
    groups = {"youtube": _entries("Y", 2), "charts": _entries("C", 2),
              "zzz-raw": _entries("Z", 2), "energy-library": _entries("E", 5)}
    _, msg, _ = digest.build_message(groups, 11)
    overview = msg.split("\n")[3:7]
    assert overview == ["⚡ Energy · 5", "🔥 Current charts · 2",
                        "📺 YouTube · 2", "🎵 zzz-raw · 2"]


def test_single_source_has_no_overview_and_no_separator():
    i18n.setup(lang="en", lang_dir=_LANG_DIR)
    _, msg, unique = digest.build_message({"charts": _entries("C", 2)}, 2)
    lines = msg.split("\n")
    assert unique == 2
    # One source: no overview line; the blank line and the group header (with
    # label + count) follow the count header directly.
    assert lines[2] == ""
    assert "Current charts" in lines[3] and "2" in lines[3]
    assert "➖" not in msg


def test_two_sources_overview_on_one_line():
    i18n.setup(lang="en", lang_dir=_LANG_DIR)
    _, msg, _ = digest.build_message({"charts": _entries("C", 3),
                                      "youtube": _entries("Y", 2)}, 5)
    assert msg.split("\n")[3] == "🔥 Current charts · 3 · 📺 YouTube · 2"


def test_real_message_91_songs_six_sources_one_line_each():
    # Real digest from the phone: used to be ONE wrapped line that broke
    # inside "Schweizer / Mundart 5" plus a separator line.
    i18n.setup(lang="de", lang_dir=_LANG_DIR)
    groups = {"youtube": _entries("Y", 44), "energy-library": _entries("E", 39),
              "mundart": _entries("M", 5), "charts": _entries("C", 1),
              "flashback": _entries("F", 1), "manuell": _entries("A", 1)}
    _, msg, unique = digest.build_message(groups, 91)
    assert unique == 91
    lines = msg.split("\n")
    assert lines[3:9] == [
        "📺 YouTube · 44", "⚡ Energy · 39", "🇨🇭 Schweizer Mundart · 5",
        "🔥 Aktuelle Charts · 1", "📻 FLASHBACK FM · 1",
        "🙋 Manuell angefordert · 1"]
    assert "➖" not in msg
    assert notify_budget.measure([msg]) <= 1024


def test_below_budget_all_songs_shown_no_more_line():
    i18n.setup(lang="en", lang_dir=_LANG_DIR)
    _, msg, _ = digest.build_message({"charts": _entries("C", 3),
                                      "upload": _entries("U", 2)}, 5)
    assert msg.count("♪ ") == 5
    assert i18n.t("digest.notify.more", count=1) not in msg
    assert msg.endswith(i18n.t("digest.notify.footer"))


def test_per_source_cap_keeps_group_note(monkeypatch):
    i18n.setup(lang="en", lang_dir=_LANG_DIR)
    monkeypatch.setattr(config, "MAX_PER_SOURCE", 2)
    _, msg, _ = digest.build_message({"charts": _entries("C", 5)}, 5)
    assert msg.count("♪ ") == 2
    assert i18n.t("digest.notify.more", count=3) in msg


def test_no_songs_edge():
    i18n.setup(lang="en", lang_dir=_LANG_DIR)
    _, msg, unique = digest.build_message({}, 0)
    assert unique == 0
    assert "➖" not in msg
