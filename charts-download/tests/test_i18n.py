"""
i18n tests for charts-download (Phase 2).

The i18n loader (app/i18n.py) is byte-identical to charts-sync; the central
key-set/placeholder parity across the shared lang/*.json files is already
covered in charts-sync/tests/test_i18n.py (and thereby also covers the new
download.* keys). This set adds a de golden check to the Phase 1 pilot: with
CHARTS_LANG=de the output of the new download.* keys must be byte-identical to
the former hardcoded output (regression anchor).

Additionally a local parity guarantee as a second line of defense, so the three
central files stay in sync even when this service is extended.

All tests run WITHOUT network/Docker.
"""

import json
import pathlib
import string

from app import i18n


# Central language files shared across all services:
# app -> charts-download -> charts-stack/lang.
_LANG_DIR = pathlib.Path(i18n.__file__).resolve().parent.parent.parent / "lang"
_SHIPPED = ("en", "de", "fr")


def _load_shipped(code):
    with open(_LANG_DIR / f"{code}.json", encoding="utf-8") as fh:
        return json.load(fh)


def _placeholders(template):
    return {name for _, name, _, _ in string.Formatter().parse(template)
            if name is not None}


# --- de golden: CHARTS_LANG=de reproduces the former hardcodes byte-exactly --

def test_de_golden_download_strings():
    i18n.setup(lang="de", lang_dir=_LANG_DIR)
    # Run start (formerly: "=== charts-download-Lauf gestartet (DRY_RUN=%s) ===").
    assert (i18n.t("download.run.start", dry_run="1")
            == "=== charts-download-Lauf gestartet (DRY_RUN=1) ===")
    # Search line (formerly: "Suche #%d [%s]: %s - %s").
    assert (i18n.t("download.song.searching", pos=1, source="charts",
                   artist="Artist", title="Title")
            == "Suche #1 [charts]: Artist - Title")
    # Live Pushover title with emoji (formerly: "\u2b07\ufe0f charts-download").
    assert i18n.t("download.notify.title_live") == "\u2b07\ufe0f charts-download"
    # missing total line (formerly: "missing gesamt: %d eindeutige Songs aus %d Datei(en)").
    assert (i18n.t("download.missing.total", unique=5, files=2)
            == "missing gesamt: 5 eindeutige Songs aus 2 Datei(en)")


# --- Local parity guarantee (second line of defense) ------------------------

def test_shipped_key_sets_are_identical():
    key_sets = {c: set(_load_shipped(c)) for c in _SHIPPED}
    assert key_sets["en"] == key_sets["de"] == key_sets["fr"], (
        f"key sets diverge: en^de={key_sets['en'] ^ key_sets['de']}, "
        f"en^fr={key_sets['en'] ^ key_sets['fr']}")


def test_shipped_placeholder_parity_per_key():
    tables = {c: _load_shipped(c) for c in _SHIPPED}
    for key in tables["en"]:
        ph = {c: _placeholders(tables[c][key]) for c in _SHIPPED}
        assert ph["en"] == ph["de"] == ph["fr"], (
            f"placeholder mismatch for key '{key}': {ph}")


def test_shipped_no_empty_values():
    for c in _SHIPPED:
        for key, value in _load_shipped(c).items():
            assert value != "", f"empty value for {key} in {c}.json"
