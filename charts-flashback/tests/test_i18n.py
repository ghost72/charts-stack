"""
Unit-Tests fuer den i18n-Loader (app/i18n.py) und die ausgelieferten
Sprachtabellen (zentral in charts-stack/lang/*.json, geteilt ueber alle Dienste).

Analog zum charts-sync-Pilot: die zentrale Parity-Garantie (set(en)==set(de)==
set(fr) plus Platzhalter-Parity je Key) UND ein de-Golden-Check fuer ein paar
repraesentative flashback.*-Keys. Alle Tests laufen OHNE Netzwerk/Docker und
setzen i18n.setup(lang=..., lang_dir=...) explizit (kein CHARTS_LANG-Env-Leak).
"""

import json
import pathlib
import string

import pytest

from app import i18n


# Zentrale, ausgelieferte Sprachdateien: app -> charts-flashback -> charts-stack/lang.
_LANG_DIR = pathlib.Path(i18n.__file__).resolve().parent.parent.parent / "lang"
_SHIPPED = ("en", "de", "fr")


def _load_shipped(code):
    with open(_LANG_DIR / f"{code}.json", encoding="utf-8") as fh:
        return json.load(fh)


def _placeholders(template):
    """Set of named {placeholder} names used in a template."""
    return {name for _, name, _, _ in string.Formatter().parse(template)
            if name is not None}


# --- Loader-Grundverhalten (lokale tmp-Sprachdateien) -----------------------

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
    # 'only_en' fehlt in de.json -> English via Merge.
    assert i18n.t("only_en") == "english only"


def test_missing_key_returns_key_name(lang_dir):
    i18n.setup(lang="en", lang_dir=lang_dir)
    assert i18n.t("flashback.does.not.exist") == "flashback.does.not.exist"


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


def test_flashback_keys_present_in_all_languages():
    """The flashback.* namespace must exist in all three shipped files."""
    tables = {c: _load_shipped(c) for c in _SHIPPED}
    fb = {k for k in tables["en"] if k.startswith("flashback.")}
    assert fb, "no flashback.* keys shipped"
    for c in _SHIPPED:
        missing = fb - set(tables[c])
        assert not missing, f"flashback.* keys missing in {c}: {missing}"


# --- de reproduces today's German strings (golden sample) -------------------

def test_de_golden_flashback_strings():
    """CHARTS_LANG=de must reproduce the former hardcoded German byte-exact."""
    i18n.setup(lang="de", lang_dir=_LANG_DIR)
    # Run-Start-Marker.
    assert i18n.t("flashback.run.start") == "=== charts-flashback-Lauf gestartet ==="
    # Dienst-Titel (eigenes Radio-Emoji + Dienstname).
    assert (i18n.t("flashback.notify.title", service="charts-flashback")
            == "\U0001F4FB charts-flashback")
    # <b>-haltige Pushover-Kopfzeile mit Umlauten (FLASHBACK-Rotation).
    assert (i18n.t("flashback.notify.new_to_load", missing=3, new=10)
            == "<b>3</b> neue Songs zum Nachladen aus der FLASHBACK-Rotation "
               "(von <b>10</b> neu gesehenen).")
    # idle-Status-Meldungen (von den Guards + enabled_guard-Test erwartet).
    assert i18n.t("flashback.status.disabled") == "deaktiviert"
    assert (i18n.t("flashback.status.no_hash")
            == "kein FLASHBACK_HASH gesetzt - nichts zu tun")


def test_de_missing_file_header_byte_exact(tmp_path, monkeypatch):
    """The missing-file header written with de must match the former block."""
    import app.main as main_mod
    import app.config as config_mod

    i18n.setup(lang="de", lang_dir=_LANG_DIR)
    monkeypatch.setattr(config_mod, "CHARTS_DIR", str(tmp_path))

    class _FixedDateTime:
        @staticmethod
        def now():
            import datetime as _dt
            return _dt.datetime(2026, 1, 2, 15, 4)

    monkeypatch.setattr(main_mod, "datetime", _FixedDateTime)

    out = main_mod.write_missing([(1, "Artist", "Title", "1984")], "missing-test.txt")
    content = pathlib.Path(out).read_text(encoding="utf-8")
    expected = (
        "# Fehlende FLASHBACK-Songs (Stand 02.01.2026 15:04)\n"
        "# Rotation von FLASHBACK FM, die noch nicht in der Library ist.\n"
        "# charts-download laedt sie nach, charts-import sortiert sie ein. Das\n"
        "# Erscheinungsjahr kommt separat ueber die Jahr-Map (nur als Tag, NICHT\n"
        "# im Datei-/Ordnernamen) - so wachsen sie in die gemeinsame Library und\n"
        "# erscheinen ueber die Navidrome Smart Playlists in der richtigen Dekade.\n"
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
