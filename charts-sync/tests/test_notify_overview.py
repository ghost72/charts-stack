"""
charts-sync Pushover message: overview line first, budget cut between whole
songs with one explicit "... and N more" line.
"""
import pathlib
import json
from types import SimpleNamespace

import pytest

from app import i18n, main, notify_budget

_LANG_DIR = pathlib.Path(i18n.__file__).resolve().parent.parent.parent / "lang"


class _Src:
    title = "Charts"
    name = "charts.m3u"
    last_stats = {}


def _send(monkeypatch, matched, missing, state=None, limit=500, language="en"):
    sent = {}
    monkeypatch.setattr(main.notify, "send",
                        lambda title, msg: sent.update(title=title, msg=msg))
    monkeypatch.setattr(main.config, "NOTIFY_ONLY_ON_CHANGE", False)
    monkeypatch.setattr(main.config, "NOTIFY_MISSING_LIMIT", limit)
    i18n.setup(lang=language, lang_dir=_LANG_DIR)
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
    assert "♪ #1 Artist 1 - Song 1" in lines
    assert lines.index("♪ #1 Artist 1 - Song 1") < len(lines) - 1
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


def test_apple_source_contribution_is_visible(monkeypatch):
    monkeypatch.setattr(_Src, "last_stats", {
        "deezer_raw": 8, "deezer_new": 5, "deezer_failed": 1,
        "apple_raw": 5, "apple_new": 3, "apple_failed": 0,
        "energy_raw": 5, "energy_new": 0, "energy_failed": 0,
        "lastfm_raw": 0, "lastfm_new": 0, "lastfm_failed": 1,
    })
    msg = _send(monkeypatch, _matched(2), [])
    assert "Deezer: 8 received · 5 additional" in msg
    assert "Apple Music: 5 received · 3 additional" in msg
    assert "Energy: 5 received · 0 additional" in msg
    assert "Last.fm: 0 received · 0 additional" in msg
    assert msg.count("Failed feeds: 1") == 2
    assert notify_budget.measure([msg]) <= 1024


@pytest.mark.parametrize("language", ["de", "en", "fr"])
def test_source_and_status_sections_have_spacing(monkeypatch, language):
    monkeypatch.setattr(_Src, "last_stats", {
        "deezer_raw": 148, "deezer_new": 132, "deezer_failed": 0,
        "energy_raw": 278, "energy_new": 265, "energy_failed": 0,
        "genre_filtered": 2,
    })
    msg = _send(monkeypatch, _matched(2), _missing(3), language=language)
    lines = msg.splitlines()
    header = lines.index(i18n.t("notify.source_header"))
    explanation = lines.index(i18n.t("notify.source_explanation"))
    filtered = lines.index(i18n.t("notify.genre_filtered", count=2))
    assert lines[header - 1] == ""
    assert lines[explanation - 1] == ""
    assert lines[filtered - 1] == ""
    assert notify_budget.measure([msg]) <= 1024


@pytest.mark.parametrize(("language", "received", "additional", "failure"), [
    ("de", "empfangen", "zusätzlich", "Fehlgeschlagene Abrufe: 1"),
    ("en", "received", "additional", "Failed feeds: 1"),
    ("fr", "reçus", "supplémentaires", "Flux en échec : 1"),
])
def test_source_overview_localized_and_within_budget(
        monkeypatch, language, received, additional, failure):
    monkeypatch.setattr(_Src, "last_stats", {
        f"{kind}_{field}": value
        for kind in ("deezer", "lastfm", "apple", "energy")
        for field, value in (("raw", 100), ("new", 0), ("failed", 1))
    })
    msg = _send(monkeypatch, _matched(8), _missing(40, pad=30),
                language=language)
    assert msg.count(f"100 {received}") == 4
    assert msg.count(f"0 {additional}") == 4
    assert msg.count(failure) == 4
    assert notify_budget.measure([msg]) <= 1024


def _enable_scene_playlists(tmp_path, monkeypatch):
    monkeypatch.setattr(main.config, "CHARTS_DIR", str(tmp_path))
    (tmp_path / ".charts-artist-selection.json").write_text(json.dumps({
        "version": 1, "filter_enabled": False, "target_scene": "My artists",
        "scene_playlists": True, "artists": {
            "deezer:123": {"deezer_id": "123", "enabled": True,
                           "scene": "Pop", "scenes": ["Pop", "Rock"]}},
    }))


def test_scene_messages_report_only_new_tracks_per_written_playlist(tmp_path, monkeypatch):
    _enable_scene_playlists(tmp_path, monkeypatch)
    i18n.setup(lang="en", lang_dir=_LANG_DIR)
    monkeypatch.setattr(main.config, "MUSIC_DIR", str(tmp_path))
    monkeypatch.setattr(main.config, "PLAYLIST_DIR", str(tmp_path))
    sent = []
    monkeypatch.setattr(main.notify, "send", lambda title, msg: sent.append(msg))
    first = SimpleNamespace(artist="First Artist", title="First Song")
    second = SimpleNamespace(artist="Second Artist", title="Second Song")

    def find_path(artist, title):
        return str(tmp_path / f"{title}.mp3")

    main._write_scene_playlists({"Pop": [first], "Rock": [first]}, find_path, {})
    assert len(sent) == 2
    assert "<b>Pop</b>" in sent[0] and "<b>Rock</b>" in sent[1]
    assert all("First Artist – First Song" in msg for msg in sent)
    sent.clear()
    main._write_scene_playlists({"Pop": [first], "Rock": [first]}, find_path, {})
    assert not sent
    main._write_scene_playlists({"Pop": [second, first], "Rock": [first]}, find_path, {})
    assert len(sent) == 1 and "<b>Pop</b>" in sent[0]
    assert "Second Artist – Second Song" in sent[0] and "First Song" not in sent[0]

    sent.clear()
    name = main.artist_catalog.scene_filename("Pop")
    (tmp_path / name).write_text("#EXTM3U\n#PLAYLIST:Unmanaged\n")
    main._write_scene_playlists({"Pop": [first]}, find_path, {})
    assert not sent
    assert (tmp_path / name).read_text() == "#EXTM3U\n#PLAYLIST:Unmanaged\n"


def test_scene_message_counts_titles_omitted_by_pushover_budget(tmp_path, monkeypatch):
    _enable_scene_playlists(tmp_path, monkeypatch)
    i18n.setup(lang="en", lang_dir=_LANG_DIR)
    monkeypatch.setattr(main.config, "MUSIC_DIR", str(tmp_path))
    monkeypatch.setattr(main.config, "PLAYLIST_DIR", str(tmp_path))
    sent = []
    monkeypatch.setattr(main.notify, "send", lambda title, msg: sent.append(msg))
    tracks = [SimpleNamespace(artist="Artist & Band", title=f"Song {i} " + "X" * 70)
              for i in range(1, 26)]
    main._write_scene_playlists({"Pop": tracks}, lambda artist, title: str(tmp_path / title), {})
    msg, = sent
    shown = sum(line.startswith("♪ ") for line in msg.splitlines())
    assert 0 < shown < 25
    assert "25 new in the playlist" in msg
    assert msg.splitlines()[-1] == i18n.t("notify.more", count=25 - shown)
    assert notify_budget.measure([msg]) <= 1024
