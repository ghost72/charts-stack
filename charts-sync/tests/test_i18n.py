"""
Unit-Tests fuer den i18n-Loader (app/i18n.py) und die ausgelieferten
Sprachtabellen (zentral in charts-stack/lang/*.json, geteilt ueber alle Dienste).

Dies ist der Pilot-Testsatz: die zentrale Parity-Garantie (set(en)==set(de)==
set(fr) plus Platzhalter-Parity je Key) und der de-Golden-Check sind das
Muster, das die Schwester-Dienste spaeter 1:1 uebernehmen.

Alle Tests laufen OHNE Netzwerk/Docker. Statt die Umgebung zu veraendern wird
i18n.setup(lang=..., lang_dir=...) mit tmp_path-Sprachdateien bzw. dem echten
zentralen lang-Verzeichnis aufgerufen (kein CHARTS_LANG-Env-Leak).
"""

import json
import logging
import pathlib
import string

import pytest

from app import i18n


# Pfad zu den AUSGELIEFERTEN Sprachdateien. Sie liegen jetzt ZENTRAL (geteilt
# ueber alle Dienste, wie die .env) in charts-stack/lang/, nicht mehr pro Dienst
# unter app/lang/. Relativ zum i18n-Modul: app -> charts-sync -> charts-stack/lang.
_LANG_DIR = pathlib.Path(i18n.__file__).resolve().parent.parent.parent / "lang"
_SHIPPED = ("en", "de", "fr")


@pytest.fixture
def lang_dir(tmp_path):
    """Schreibt ein kleines, kontrolliertes Sprach-Set in ein tmp-Verzeichnis."""
    d = tmp_path / "lang"
    d.mkdir()
    (d / "en.json").write_text(json.dumps({
        "greeting": "Hello {name}",
        "only_en": "english only",
        "emoji.title": "\U0001f3b5 {service}",
    }), encoding="utf-8")
    (d / "de.json").write_text(json.dumps({
        "greeting": "Hallo {name}",
        "emoji.title": "\U0001f3b5 {service}",
    }, ensure_ascii=False), encoding="utf-8")
    (d / "fr.json").write_text(json.dumps({
        "greeting": "Bonjour {name}",
        "only_en": "anglais seulement",
        "emoji.title": "\U0001f3b5 {service}",
    }, ensure_ascii=False), encoding="utf-8")
    return d


# --- 1. Default/empty -> English --------------------------------------------

def test_empty_language_uses_english(lang_dir):
    i18n.setup(lang="", lang_dir=lang_dir)
    assert i18n.t("greeting", name="Roger") == "Hello Roger"


# --- 3. fr resolves French --------------------------------------------------

def test_french_resolves(lang_dir):
    i18n.setup(lang="fr", lang_dir=lang_dir)
    assert i18n.t("greeting", name="Roger") == "Bonjour Roger"


# --- 4. Unknown language -> en + one info log -------------------------------

def test_unknown_language_falls_back_with_info(lang_dir, caplog):
    with caplog.at_level(logging.INFO, logger="i18n"):
        i18n.setup(lang="xx", lang_dir=lang_dir)
    assert i18n.t("greeting", name="Roger") == "Hello Roger"
    infos = [r for r in caplog.records if r.levelno == logging.INFO]
    assert len(infos) == 1
    assert "xx" in infos[0].getMessage()


# --- 5. Missing key -> returns the key name ---------------------------------

def test_missing_key_returns_key_name(lang_dir):
    i18n.setup(lang="en", lang_dir=lang_dir)
    assert i18n.t("does.not.exist") == "does.not.exist"


# --- 6. Missing kwarg (KeyError) -> raw template + one warning --------------

def test_missing_kwarg_returns_raw_template(lang_dir, caplog):
    i18n.setup(lang="en", lang_dir=lang_dir)
    with caplog.at_level(logging.WARNING, logger="i18n"):
        result = i18n.t("greeting")  # {name} not supplied
    assert result == "Hello {name}"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1


# --- 7. Malformed template (ValueError) -> raw template + warning -----------

def test_malformed_template_returns_raw(tmp_path, caplog):
    d = tmp_path / "lang"
    d.mkdir()
    # A stray single brace makes str.format raise ValueError.
    (d / "en.json").write_text(json.dumps({"bad": "oops {"}), encoding="utf-8")
    i18n.setup(lang="en", lang_dir=d)
    with caplog.at_level(logging.WARNING, logger="i18n"):
        result = i18n.t("bad")
    assert result == "oops {"
    assert any(r.levelno == logging.WARNING for r in caplog.records)


# --- 8. Missing <lang>.json -> degrade to English + one warning -------------

def test_missing_lang_file_degrades_to_english(tmp_path, caplog):
    d = tmp_path / "lang"
    d.mkdir()
    (d / "en.json").write_text(json.dumps({"greeting": "Hello {name}"}),
                               encoding="utf-8")
    # No de.json present.
    with caplog.at_level(logging.INFO, logger="i18n"):
        i18n.setup(lang="de", lang_dir=d)
    assert i18n.t("greeting", name="X") == "Hello X"
    # One log entry about the missing language (info, per the loader).
    assert any("de" in r.getMessage() for r in caplog.records)


# --- 9. Broken JSON in a lang file -> ignored, English used, no crash -------

def test_broken_chosen_json_falls_back(tmp_path, caplog):
    d = tmp_path / "lang"
    d.mkdir()
    (d / "en.json").write_text(json.dumps({"greeting": "Hello {name}"}),
                               encoding="utf-8")
    (d / "de.json").write_text("{ this is not valid json", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="i18n"):
        i18n.setup(lang="de", lang_dir=d)
    assert i18n.t("greeting", name="X") == "Hello X"
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_broken_en_json_keeps_running(tmp_path, caplog):
    d = tmp_path / "lang"
    d.mkdir()
    (d / "en.json").write_text("}{ broken", encoding="utf-8")
    with caplog.at_level(logging.ERROR, logger="i18n"):
        i18n.setup(lang="en", lang_dir=d)
    # No usable table -> keys degrade to key name, but no crash.
    assert i18n.t("anything") == "anything"
    assert any(r.levelno == logging.ERROR for r in caplog.records)


# --- 10. Per-key fallback: key only in en still resolves under de -----------

def test_per_key_fallback_to_english(lang_dir):
    i18n.setup(lang="de", lang_dir=lang_dir)
    # 'only_en' is absent from de.json -> English text via the merge.
    assert i18n.t("only_en") == "english only"
    # And a key present in de uses German.
    assert i18n.t("greeting", name="X") == "Hallo X"


# --- 11. CENTRAL REGRESSION GUARD: key-set + placeholder parity -------------

def _load_shipped(code):
    with open(_LANG_DIR / f"{code}.json", encoding="utf-8") as fh:
        return json.load(fh)


def _placeholders(template):
    """Set of named {placeholder} names used in a template."""
    return {name for _, name, _, _ in string.Formatter().parse(template)
            if name is not None}


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


def test_watchdog_keys_shell_safe():
    """Additiver Guard fuer die watchdog.*-Keys (shell-i18n, charts-watchdog).

    Die watchdog.*-Keys nutzen positionale %s statt {name}, daher sieht der
    string.Formatter-Guard oben sie nicht. Dieser Zusatztest sichert zwei
    Vertraege der sh-printf-/sed-Mechanik ab (siehe design.md, Entscheidung 5):
      (a) value.count("%s") ist ueber en/de/fr identisch (printf-Argument-
          Paritaet analog zur {name}-Paritaet der Python-Keys).
      (b) kein " und kein \\ im Wert (Vorbedingung des greedy-sed-Extraktors in
          t()). Nicht-ASCII (Umlaute/Akzente) ist ausdruecklich erlaubt.
    Prueft NUR watchdog.*-Keys; die {name}-Pruefungen bleiben unberuehrt.
    """
    tables = {c: _load_shipped(c) for c in _SHIPPED}
    wd_keys = [k for k in tables["en"] if k.startswith("watchdog.")]
    assert wd_keys, "no watchdog.* keys found"
    for key in wd_keys:
        counts = {c: tables[c][key].count("%s") for c in _SHIPPED}
        assert counts["en"] == counts["de"] == counts["fr"], (key, counts)
        for c in _SHIPPED:
            v = tables[c][key]
            # Nur Quotes/Backslash verboten; Nicht-ASCII (Umlaute/Akzente) OK.
            assert '"' not in v and "\\" not in v, (c, key, v)


# --- 2. de reproduces today's German strings (golden sample) ----------------

def test_de_golden_strings():
    """CHARTS_LANG=de must reproduce the former hardcoded German byte-exact."""
    i18n.setup(lang="de", lang_dir=_LANG_DIR)
    # Plain line.
    assert i18n.t("library.scan", dir="/music/Music") == "Scanne Library: /music/Music"
    # Emoji + title (shared template, service kwarg).
    assert i18n.t("notify.title", service="charts-sync") == "\U0001f3b5 charts-sync"
    # <b>-wrapped line with the 🆕 emoji.
    assert (i18n.t("notify.new_in_playlist", count=3)
            == "<b>\U0001f195 3 neu in der Playlist:</b>")
    # Summary sentence.
    assert (i18n.t("notify.summary", matched=5, total=10)
            == "5 von 10 Chart-Songs sind vorhanden.")
    # Run start marker.
    assert (i18n.t("sync.run.start", sources=2)
            == "=== Charts-Sync-Lauf gestartet (2 Quellen) ===")
    # Device-none-valid with en-dash and <b>-free quoted title.
    assert (i18n.t("notify.devices.none_valid", devices="x", title="T")
            == "Keine g\u00fcltigen Pushover-Ger\u00e4te \u00fcbrig (x) \u2013 "
               "Nachricht 'T' nicht gesendet")


def test_de_missing_file_header_byte_exact(tmp_path, monkeypatch):
    """The missing-file header written with de must match the former block."""
    import app.playlist as playlist_mod
    import app.config as config_mod

    i18n.setup(lang="de", lang_dir=_LANG_DIR)
    monkeypatch.setattr(config_mod, "CHARTS_DIR", str(tmp_path))

    # Freeze the timestamp so we can compare byte-for-byte.
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


def test_de_missing_file_header_empty_body(tmp_path, monkeypatch):
    """Empty missing list reproduces the former trailing-newline layout."""
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

    out = playlist_mod.write_missing([], "missing-empty.txt")
    content = pathlib.Path(out).read_text(encoding="utf-8")
    expected = (
        "# Fehlende Chart-Songs (Stand 02.01.2026 15:04)\n"
        "# Diese Titel sind in den aktuellen Deezer-Charts, aber nicht in der\n"
        "# Library gefunden worden. Auf legalem Weg beschaffen (Kauf/eigene Rips)\n"
        "# und in die Library legen - beim nächsten Lauf landen sie automatisch\n"
        "# in der Playlist.\n"
        "\n"
    )
    assert content == expected


# --- 12. Integration round-trip through a real converted module -------------

def test_library_scan_emits_german(tmp_path, monkeypatch, caplog):
    """With setup(lang='de'), library.build_index emits the German log lines."""
    import app.library as library_mod
    import app.config as config_mod

    i18n.setup(lang="de", lang_dir=_LANG_DIR)
    # Point the charts dir away from the scanned tree so nothing is skipped.
    monkeypatch.setattr(config_mod, "CHARTS_DIR", str(tmp_path / "charts"))
    monkeypatch.setattr(config_mod, "IGNORE_COMPILATIONS", False)

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
