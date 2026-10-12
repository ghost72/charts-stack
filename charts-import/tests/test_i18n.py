"""
i18n tests for charts-import (Phase 2).

The i18n loader (app/i18n.py) is byte-identical to charts-sync; the central
key-set/placeholder parity across the shared lang/*.json files is already
covered in charts-sync/tests/test_i18n.py (and thereby also covers the new
import.* keys). This set adds a de golden check to the Phase 1 pilot: with
CHARTS_LANG=de the output of the new import.* keys must be byte-identical to
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
# app -> charts-import -> charts-stack/lang.
_LANG_DIR = pathlib.Path(i18n.__file__).resolve().parent.parent.parent / "lang"
_SHIPPED = ("en", "de", "fr")


def _load_shipped(code):
    with open(_LANG_DIR / f"{code}.json", encoding="utf-8") as fh:
        return json.load(fh)


def _placeholders(template):
    return {name for _, name, _, _ in string.Formatter().parse(template)
            if name is not None}


# --- de golden: CHARTS_LANG=de reproduces the former hardcodes byte-exactly --

def test_de_golden_import_strings():
    i18n.setup(lang="de", lang_dir=_LANG_DIR)
    # Run start (formerly: "=== charts-import-Lauf gestartet ===").
    assert i18n.t("import.run.start") == "=== charts-import-Lauf gestartet ==="
    # Run end (formerly: "Lauf fertig: %d Songs importiert, %d abgelehnt, "
    # "%d alte Downloads entfernt.").
    assert (i18n.t("import.run.done", imported=3, rejected=1, removed=5)
            == "Lauf fertig: 3 Songs importiert, 1 abgelehnt, 5 alte Downloads entfernt.")
    # Pushover title new songs (formerly: "📥 charts-import: %d neue Songs").
    assert (i18n.t("import.notify.imported_title", count=4)
            == "\U0001F4E5 charts-import: 4 neue Songs")
    # Playlist breakdown (formerly: "%d aus %s").
    assert (i18n.t("import.breakdown.item", count=3, label="charts")
            == "3 aus charts")
    # Reject reason too short (formerly: "zu kurz (%ds, min %ds)").
    assert (i18n.t("import.reject.too_short", duration="24", min=90)
            == "zu kurz (24s, min 90s)")


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
