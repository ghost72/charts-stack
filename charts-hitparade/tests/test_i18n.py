"""
Unit tests for the i18n loader (app/i18n.py) and the shipped language tables
(centrally in charts-stack/lang/*.json, shared across all services).

Analogous to the charts-sync pilot: the central parity guarantee
(set(en)==set(de)==set(fr) plus placeholder parity per key) AND a de golden
check for a few representative hitparade.* keys. All tests run WITHOUT
network/Docker and set i18n.setup(lang=..., lang_dir=...) explicitly (no
CHARTS_LANG env leak).
"""

import json
import pathlib
import string

import pytest

from app import i18n


# Central shipped language files: app -> charts-hitparade -> charts-stack/lang.
_LANG_DIR = pathlib.Path(i18n.__file__).resolve().parent.parent.parent / "lang"
_SHIPPED = ("en", "de", "fr")


def _load_shipped(code):
    with open(_LANG_DIR / f"{code}.json", encoding="utf-8") as fh:
        return json.load(fh)


def _placeholders(template):
    """Set of named {placeholder} names used in a template."""
    return {name for _, name, _, _ in string.Formatter().parse(template)
            if name is not None}


# --- Loader base behavior (local tmp language files) ------------------------

@pytest.fixture
def lang_dir(tmp_path):
    d = tmp_path / "lang"
    d.mkdir()
    (d / "en.json").write_text(json.dumps({
        "greeting": "Hello {name}",
        "only_en": "english only",
    }), encoding="utf-8")
    (d / "de.json").write_text(json.dumps({
        "greeting": "Hallo {name}",
    }, ensure_ascii=False), encoding="utf-8")
    (d / "fr.json").write_text(json.dumps({
        "greeting": "Bonjour {name}",
        "only_en": "anglais seulement",
    }, ensure_ascii=False), encoding="utf-8")
    return d


def test_empty_language_uses_english(lang_dir):
    i18n.setup(lang="", lang_dir=lang_dir)
    assert i18n.t("greeting", name="Roger") == "Hello Roger"


def test_german_resolves(lang_dir):
    i18n.setup(lang="de", lang_dir=lang_dir)
    assert i18n.t("greeting", name="Roger") == "Hallo Roger"


def test_per_key_fallback_to_english(lang_dir):
    i18n.setup(lang="de", lang_dir=lang_dir)
    # 'only_en' is missing in de.json -> English via merge.
    assert i18n.t("only_en") == "english only"


def test_missing_key_returns_key_name(lang_dir):
    i18n.setup(lang="en", lang_dir=lang_dir)
    assert i18n.t("hitparade.does.not.exist") == "hitparade.does.not.exist"


# --- CENTRAL REGRESSION GUARD: key-set + placeholder parity -----------------

def test_shipped_key_sets_are_identical():
    tables = {c: _load_shipped(c) for c in _SHIPPED}
    key_sets = {c: set(t) for c, t in tables.items()}
    assert key_sets["en"] == key_sets["de"] == key_sets["fr"], (
        "key sets diverge: "
        f"en^de={key_sets['en'] ^ key_sets['de']}, "
        f"en^fr={key_sets['en'] ^ key_sets['fr']}"
    )


def test_shipped_no_empty_values():
    for c in _SHIPPED:
        for key, value in _load_shipped(c).items():
            assert value != "", f"empty value for {key} in {c}.json"


def test_shipped_placeholder_parity_per_key():
    tables = {c: _load_shipped(c) for c in _SHIPPED}
    for key in tables["en"]:
        ph = {c: _placeholders(tables[c][key]) for c in _SHIPPED}
        assert ph["en"] == ph["de"] == ph["fr"], (
            f"placeholder mismatch for key '{key}': {ph}")


def test_hitparade_keys_present_in_all_languages():
    """The hitparade.* namespace must exist in all three shipped files."""
    tables = {c: _load_shipped(c) for c in _SHIPPED}
    hp = {k for k in tables["en"] if k.startswith("hitparade.")}
    assert hp, "no hitparade.* keys shipped"
    for c in _SHIPPED:
        missing = hp - set(tables[c])
        assert not missing, f"hitparade.* keys missing in {c}: {missing}"


# --- de reproduces today's German strings (golden sample) -------------------

def test_de_golden_hitparade_strings():
    """CHARTS_LANG=de must reproduce the former hardcoded German byte-exact."""
    i18n.setup(lang="de", lang_dir=_LANG_DIR)
    # Run start marker.
    assert (i18n.t("hitparade.run.start", charts="singles")
            == "=== charts-hitparade-Lauf gestartet (Charts: singles) ===")
    # Service title (Swiss flag emoji + service name).
    assert (i18n.t("hitparade.notify.title", service="charts-hitparade")
            == "\U0001F1E8\U0001F1ED charts-hitparade")
    # Pushover header containing <b>.
    assert (i18n.t("hitparade.notify.summary_header", matched=5, missing=3)
            == "<b>5</b> in Library, 3 werden nachgeladen:")
    # Per-chart line (previously assembled hardcoded).
    assert (i18n.t("hitparade.run.chart_done", chart="singles", songs=100,
                   matched=70, missing=30)
            == "singles: 100 Songs, 70 in Library, 30 fehlen (missing geschrieben)")
    # idle status message (expected by the enabled_guard test).
    assert i18n.t("hitparade.status.disabled") == "deaktiviert"


def test_de_missing_file_header_byte_exact(tmp_path, monkeypatch):
    """The missing-file header written with de must match the former block
    (hitparade shares the playlist.missing_file.header key with charts-sync)."""
    import app.playlist as playlist_mod
    import app.config as config_mod

    i18n.setup(lang="de", lang_dir=_LANG_DIR)
    monkeypatch.setattr(config_mod, "CHARTS_DIR", str(tmp_path))

    class _FixedDateTime:
        @staticmethod
        def now():
            import datetime as _dt
            return _dt.datetime(2026, 1, 2, 15, 4)

    monkeypatch.setattr(playlist_mod, "datetime", _FixedDateTime)

    out = playlist_mod.write_missing([(1, "Artist", "Title")], "missing-test.txt")
    content = pathlib.Path(out).read_text(encoding="utf-8")
    expected = (
        "# Fehlende Chart-Songs (Stand 02.01.2026 15:04)\n"
        "# Diese Titel sind in den aktuellen Deezer-Charts, aber nicht in der\n"
        "# Library gefunden worden. Auf legalem Weg beschaffen (Kauf/eigene Rips)\n"
        "# und in die Library legen - beim nächsten Lauf landen sie automatisch\n"
        "# in der Playlist.\n"
        "\n"
        "  1. Artist - Title\n"
    )
    assert content == expected


# --- Integration round-trip through a converted module ----------------------

def test_library_scan_emits_german(tmp_path, monkeypatch, caplog):
    """With setup(lang='de'), library.build_index emits the German log lines."""
    import logging
    import app.library as library_mod
    import app.config as config_mod

    i18n.setup(lang="de", lang_dir=_LANG_DIR)
    monkeypatch.setattr(config_mod, "CHARTS_DIR", str(tmp_path / "charts"))

    music = tmp_path / "music"
    music.mkdir()

    with caplog.at_level(logging.INFO):
        lib = library_mod.build_index(str(music))

    assert lib.tracks == []
    messages = [r.getMessage() for r in caplog.records]
    assert any(m == f"Scanne Library: {music}" for m in messages)
    assert any(m.startswith("Library-Index fertig: 0 Audiodateien") for m in messages)


# Restore import-time state so test ordering never leaks a language.
@pytest.fixture(autouse=True)
def _restore_setup():
    yield
    i18n.setup()
