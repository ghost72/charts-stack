"""
Tests for the charts-import "imported" Pushover message with multi-source
labels (Option B).

A song can belong to several playlists at once. charts-import reads the
combined label from the manifest (via tags.manifest_sources, joined with
"+") and must render it as ONE line per song under a SINGLE combined group
heading (e.g. "charts+hitparade") - never once per source.

Backward compat: an old manifest entry with only the single "source" string
still yields that one label.

All tests run WITHOUT network/Docker/Pushover (notify.send is stubbed).
"""

import json
import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import config, tags
from app import main as import_main


def _capture_notify(monkeypatch):
    """Stub notify.send and return a list that collects (title, body)."""
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(import_main.notify, "send",
                        lambda title, body: sent.append((title, body)))
    return sent


def test_scene_names_are_preserved_and_html_escaped(monkeypatch):
    monkeypatch.setattr(config, "MUSIC_DIR", "/music")
    sent = _capture_notify(monkeypatch)
    import_main._notify_imported([
        ("/music/Example/First.mp3", "Mundart: Rock & Roll+Mundart: <Pop>",
         "Example", "First", "", ""),
    ])
    body = sent[0][1]
    assert "Mundart: Rock &amp; Roll+Mundart: &lt;Pop&gt;" in body
    assert body.count("♪ Example – First") == 1
    assert "<Pop>" not in body


def test_combined_label_song_rendered_once(monkeypatch):
    """A song whose source is the combined 'charts+hitparade' appears in a
    single group, a single breakdown item, and is listed exactly once."""
    monkeypatch.setattr(config, "MUSIC_DIR", "/music/Music")
    sent = _capture_notify(monkeypatch)

    imported = [
        ("/music/Music/Ashe/Stop The Wedding!/Ashe - Stop The Wedding!.mp3",
         "charts+hitparade", "Ashe", "Stop The Wedding!",
         "Stop The Wedding!", ""),
    ]
    import_main._notify_imported(imported)

    assert len(sent) == 1
    _title, body = sent[0]
    # The combined label appears exactly once (one breakdown item + one group
    # heading both read "charts+hitparade"); the two sub-labels never appear as
    # SEPARATE groups.
    assert "charts+hitparade" in body
    # The song is listed exactly once (not once per source).
    assert body.count("♪ Ashe – Stop The Wedding!") == 1
    assert "Ashe/Stop The Wedding!" not in body
    # No standalone "hitparade" group heading split off from the combined label.
    assert "\nhitparade\n" not in body


def test_two_sources_do_not_split_into_two_groups(monkeypatch):
    """The overlap song must not be counted/listed under both 'charts' and
    'hitparade' separately - it is one song in one combined group."""
    monkeypatch.setattr(config, "MUSIC_DIR", "/music/Music")
    sent = _capture_notify(monkeypatch)

    imported = [
        ("/music/Music/A/T/T.mp3", "charts+hitparade", "A", "T", "T", ""),
        ("/music/Music/B/U/U.mp3", "charts", "B", "U", "U", ""),
    ]
    import_main._notify_imported(imported)

    _title, body = sent[0]
    # Header counts 2 imported songs, not 3 (no double counting).
    assert body.count("♪ A – T") == 1
    assert body.count("♪ B – U") == 1
    # "charts+hitparade" and "charts" are distinct group labels.
    assert "charts+hitparade" in body
    assert "charts" in body


def test_manifest_sources_backward_compat():
    """tags.manifest_sources prefers the new 'sources' list, falls back to the
    old single 'source' string, and returns [] when neither is present."""
    # New multi-source entry.
    assert tags.manifest_sources(
        {"sources": ["charts", "hitparade"]}) == ["charts", "hitparade"]
    # Old single-source entry (written by an older charts-download).
    assert tags.manifest_sources({"source": "charts"}) == ["charts"]
    # Neither present / empty.
    assert tags.manifest_sources({}) == []
    assert tags.manifest_sources(None) == []
    # Blank labels are dropped.
    assert tags.manifest_sources({"sources": ["charts", ""]}) == ["charts"]


def test_legacy_single_source_renders_one_label(monkeypatch):
    """A legacy entry with only 'source' produces a single-label group."""
    monkeypatch.setattr(config, "MUSIC_DIR", "/music/Music")
    sent = _capture_notify(monkeypatch)

    # Simulate the importer having read a legacy entry: manifest_sources
    # collapses it to one label, joined to the same 'source' string shape.
    source = "+".join(tags.manifest_sources({"source": "charts"}))
    imported = [("/music/Music/A/T/T.mp3", source, "A", "T", "T", "")]
    import_main._notify_imported(imported)

    _title, body = sent[0]
    assert "charts" in body
    assert body.count("♪ A – T") == 1


# --- Golden no-cut tests: new output == frozen copy of the old builders -------

from app import i18n, notify, notify_budget  # noqa: E402
from app.i18n import t  # noqa: E402

_LANG_DIR = pathlib.Path(i18n.__file__).resolve().parent.parent.parent / "lang"


@pytest.mark.parametrize("language", ["de", "en", "fr"])
def test_song_notification_templates_use_single_note(language):
    with (_LANG_DIR / f"{language}.json").open(encoding="utf-8") as file:
        messages = json.load(file)
    keys = (
        "digest.notify.song", "download.notify.retried_entry",
        "energy.notify.library_song_entry", "energy.notify.library_song_entry_no_year",
        "flashback.notify.song_entry", "flashback.notify.song_entry_no_year",
        "import.notify.rejected_entry", "import.notify.flagged_entry",
        "import.notify.imported_entry", "import.notify.imported_file",
        "import.notify.manual_song", "notify.scene_song_entry", "notify.song_entry",
        "recommend.notify.song_entry", "upgrade.notify.song_entry",
    )
    assert all(messages[key].startswith("♪ ") for key in keys)


def _old_rejected(rejected):
    count = len(rejected)
    lines = [t("import.notify.rejected_header", count=count), ""]
    for entry in rejected[:10]:
        base = os.path.basename(entry.get("path", ""))
        reason = entry.get("reason", "?")
        if len(lines) > 2:
            lines.append("")
        lines.append(t("import.notify.rejected_entry", base=base, reason=reason))
    if count > 10:
        lines.append("")
        lines.append(t("import.notify.more", count=count - 10))
    return "\n".join(lines)


def _old_flagged(flagged):
    count = len(flagged)
    lines = [t("import.notify.flagged_header", count=count), ""]
    for artist, song_title in flagged[:10]:
        lines.append(t("import.notify.flagged_entry", artist=artist, title=song_title))
    if count > 10:
        lines.append("")
        lines.append(t("import.notify.more", count=count - 10))
    return "\n".join(lines)


def _expected_imported(imported):
    count = len(imported)
    lines = [t("import.notify.imported_header", count=count)]
    groups = {}
    for path, source, artist, song_title, _album, _dev in imported:
        label = source or t("import.notify.unknown_source")
        if artist and artist.strip() and song_title and song_title.strip():
            item = t("import.notify.imported_entry", artist=artist, title=song_title)
        else:
            item = t("import.notify.imported_file", filename=os.path.basename(path))
        groups.setdefault(label, []).append(item)
    ordered = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    items = [t("import.breakdown.item", count=len(songs), label=label)
             for label, songs in ordered]
    # Uniform overview rule: 1 item omitted, 2 items on one line, 3+ one each.
    if len(items) == 2:
        items = [" · ".join(items)]
    elif len(items) == 1:
        items = []
    for item in items:
        lines.append(t("import.notify.imported_breakdown", breakdown=item))
    lines.append("")
    shown = 0
    remaining = count
    for i, (label, songs) in enumerate(ordered):
        if shown >= 10:
            break
        if i > 0:
            lines.append("")
        lines.append(t("import.notify.imported_group", label=label))
        for item in songs:
            if shown >= 10:
                break
            lines.append(item)
            shown += 1
            remaining -= 1
    if remaining > 0:
        lines.append("")
        lines.append(t("import.notify.more", count=remaining))
    return "\n".join(lines)


def _imported_batch(spec):
    """spec = [(source, n_songs)] -> imported tuples with short rel paths."""
    out = []
    for source, n in spec:
        for i in range(n):
            out.append((f"/music/Music/{source}/A{i}/T{i}.mp3", source,
                        f"A{i}", f"T{i}", f"T{i}", ""))
    return out


def _setup_en(monkeypatch):
    monkeypatch.setattr(config, "MUSIC_DIR", "/music/Music")
    i18n.setup(lang="en", lang_dir=_LANG_DIR)
    return _capture_notify(monkeypatch)


def test_single_group_has_no_breakdown_line(monkeypatch):
    sent = _setup_en(monkeypatch)
    batch = _imported_batch([("charts", 3)])
    import_main._notify_imported(batch)
    body = sent[0][1]
    assert "<i>" not in body
    assert body == _expected_imported(batch)


def test_three_groups_one_breakdown_item_per_line(monkeypatch):
    sent = _setup_en(monkeypatch)
    batch = _imported_batch([("charts", 3), ("mundart", 2), ("manuell", 1)])
    import_main._notify_imported(batch)
    lines = sent[0][1].split("\n")
    assert lines[1:4] == ["<i>3 from charts</i>", "<i>2 from mundart</i>",
                          "<i>1 from manuell</i>"]


def test_golden_imported_below_cap(monkeypatch):
    sent = _setup_en(monkeypatch)
    batch = _imported_batch([("charts", 3), ("mundart", 2)])
    import_main._notify_imported(batch)
    assert sent[0][1] == _expected_imported(batch)


def test_golden_imported_above_cap_within_budget(monkeypatch):
    sent = _setup_en(monkeypatch)
    # 14 songs: cap reached inside the 2nd group; the 3rd group never reached.
    batch = _imported_batch([("charts", 6), ("mundart", 6), ("manuell", 2)])
    import_main._notify_imported(batch)
    body = sent[0][1]
    assert body == _expected_imported(batch)
    assert body.endswith("\n\n" + t("import.notify.more", count=4))
    # The third group was never reached: no group heading for it.
    assert t("import.notify.imported_group", label="manuell") not in body


def test_golden_imported_cap_at_group_boundary(monkeypatch):
    sent = _setup_en(monkeypatch)
    batch = _imported_batch([("charts", 10), ("mundart", 3)])
    import_main._notify_imported(batch)
    assert sent[0][1] == _expected_imported(batch)
    assert sent[0][1].endswith(t("import.notify.more", count=3))


def test_golden_rejected_and_flagged(monkeypatch):
    sent = _setup_en(monkeypatch)
    for n in (3, 10, 14):
        rejected = [{"path": f"/charts/f{i}.mp3", "reason": "too short (24s)"}
                    for i in range(n)]
        flagged = [(f"ARTIST {i}", f"TITLE {i}") for i in range(n)]
        sent.clear()
        import_main._notify_rejected(rejected)
        import_main._notify_flagged_upper(flagged)
        assert sent[0][1] == _old_rejected(rejected)
        assert sent[1][1] == _old_flagged(flagged)


@pytest.mark.parametrize("language", ["de", "en", "fr"])
def test_imported_and_flagged_use_readable_song_names(monkeypatch, language):
    for name in ("_lang", "_messages", "_fallback"):
        monkeypatch.setattr(i18n, name, getattr(i18n, name))
    i18n.setup(lang=language, lang_dir=_LANG_DIR)
    sent = _capture_notify(monkeypatch)
    import_main._notify_imported([
        ("/music/A/Long Album/A - Song.mp3", "charts+energy",
         "A & Band", "Song <Live>", "Long Album", ""),
        ("/music/B/Unknown.mp3", "", "", " ", "", ""),
    ])
    import_main._notify_flagged_upper([("ARTIST", "TITLE")])

    body = sent[0][1]
    assert "charts+energy" in body
    assert "♪ A & Band – Song <Live>" in body
    assert "Long Album" not in body
    assert "♪ Unknown.mp3" in body
    assert "A &amp; Band – Song &lt;Live&gt;" in notify_budget.escaped(body)
    assert "♪ ARTIST — TITLE" in sent[1][1]
    assert notify_budget.measure([body]) <= 1024
    assert notify_budget.measure([sent[1][1]]) <= 1024


@pytest.mark.parametrize("language", ["de", "en", "fr"])
def test_manual_song_uses_single_note_and_keeps_target(monkeypatch, language):
    for name in ("_lang", "_messages", "_fallback"):
        monkeypatch.setattr(i18n, name, getattr(i18n, name))
    i18n.setup(lang=language, lang_dir=_LANG_DIR)
    sent = []
    monkeypatch.setattr(import_main.notify, "send",
                        lambda title, body, **kwargs: sent.append((title, body, kwargs)))
    import_main._notify_manual_available([
        ("/music/a.mp3", "charts", "Artist", "Song", "Album", "phone"),
        ("/music/b.mp3", "charts", "Other", "Song", "", ""),
    ])
    assert len(sent) == 1
    title, body, options = sent[0]
    assert title.startswith("🎵 ")
    assert body.startswith("♪ <b>Artist - Song</b>")
    assert options == {"device": "phone", "sound": config.PUSHOVER_SOUND_MANUAL}


@pytest.mark.parametrize("language", ["de", "en", "fr"])
def test_rejected_entries_have_readable_atomic_lines(monkeypatch, language):
    for name in ("_lang", "_messages", "_fallback"):
        monkeypatch.setattr(i18n, name, getattr(i18n, name))
    i18n.setup(lang=language, lang_dir=_LANG_DIR)
    sent = _capture_notify(monkeypatch)
    reason = t("import.reject.upsampled", claimed="320", measured="16.7", expected="19")

    import_main._notify_rejected([
        {"path": "/charts/Artist & Band - First.mp3", "reason": reason},
        {"path": "/charts/Second.mp3", "reason": "24s < 90s"},
    ])

    body = sent[0][1]
    assert f"♪ Artist & Band - First.mp3\n↳ {reason}\n\n♪ Second.mp3\n↳ 24s < 90s" in body
    escaped = notify_budget.escaped(body)
    assert "Artist &amp; Band" in escaped and "24s &lt; 90s" in escaped
    assert escaped == notify._escape_html_keep_tags(body)
    assert notify_budget.measure([body]) <= 1024


def test_rejected_long_entries_fit_budget_and_count_hidden(monkeypatch):
    sent = _setup_en(monkeypatch)
    rejected = [
        {"path": f"/charts/{i:02d} - " + "Long & Artist " * 7 + ".mp3",
         "reason": "upsampled (claimed 320kbps, real cutoff 16.7kHz, expected from 19kHz)"}
        for i in range(14)
    ]
    import_main._notify_rejected(rejected)
    body = sent[0][1]
    shown = body.count("♪ ")
    assert 0 < shown < len(rejected)
    assert body.count("\n↳ upsampled") == shown
    assert body.endswith(t("import.notify.more", count=len(rejected) - shown))
    assert notify_budget.measure([body]) <= 1024


def test_five_rejected_files_keep_all_quality_details(monkeypatch):
    sent = _setup_en(monkeypatch)
    reason = "upsampled (claimed 320kbps, real cutoff 16.7kHz, expected from 19kHz)"
    names = [
        "The Cardigans Erase Rewind.mp3",
        "SARAH CONNOR-From Sarah with love.mp3",
        "The Waitresses - Christmas Wrapping (single edit).mp3",
        "Paul McCartney - Wonderful Christmastime.mp3",
        "Zach John King - Get To Drinkin'.mp3",
    ]
    import_main._notify_rejected([
        {"path": "/charts/" + name, "reason": reason} for name in names
    ])
    body = sent[0][1]
    assert all(f"♪ {name}\n↳ {reason}" in body for name in names)
    assert body.count("\n\n♪ ") == 5
    assert notify_budget.measure([body]) <= 1024


def test_imported_many_long_songs_stays_within_budget(monkeypatch):
    sent = _setup_en(monkeypatch)
    batch = [(f"/music/Music/Some Long Artist Name {i}/A Very Long Album Name "
              f"That Goes On And On {i}/{i:02d} - A Quite Long Song Title For "
              f"Testing Purposes {i}.mp3", f"playlist-{i % 4}", "a", "t", "al", "")
             for i in range(60)]
    import_main._notify_imported(batch)
    body = sent[0][1]
    assert notify_budget.measure([body]) <= 1024
    lines = body.split("\n")
    # Breakdown (overview) stays complete in the head; exactly one "more" line.
    # 4 groups -> one breakdown item per line, directly under the header.
    for i in range(4):
        assert f"playlist-{i}" in lines[1 + i]
    shown = sum(1 for ln in lines if ln.startswith("♪ "))
    assert body.count(t("import.notify.more", count=60 - shown)) == 1
    assert lines[-2] == "" and lines[-1] == t("import.notify.more", count=60 - shown)
