"""
i18n-Tests fuer charts-upgrade (Phase 2).

Der i18n-Loader (app/i18n.py) ist byte-identisch zu charts-sync; die zentrale
Key-Set-/Platzhalter-Parity ueber die geteilten lang/*.json-Dateien ist bereits
in charts-sync/tests/test_i18n.py abgedeckt (und deckt damit auch die neuen
upgrade.*-Keys mit ab). Dieser Satz ergaenzt den Phase-1-Pilot um einen
de-Golden-Check: mit CHARTS_LANG=de muss die Ausgabe der neuen upgrade.*-Keys
byte-identisch zur frueheren Hardcode-Ausgabe sein (Regressions-Anker).

Zusaetzlich eine lokale Parity-Garantie als zweite Verteidigungslinie, damit
die drei zentralen Dateien auch beim Erweitern dieses Dienstes synchron bleiben.

Alle Tests laufen OHNE Netzwerk/Docker.
"""

import json
import pathlib
import string

from app import i18n


# Zentrale, ueber alle Dienste geteilte Sprachdateien:
# app -> charts-upgrade -> charts-stack/lang.
_LANG_DIR = pathlib.Path(i18n.__file__).resolve().parent.parent.parent / "lang"
_SHIPPED = ("en", "de", "fr")


def _load_shipped(code):
    with open(_LANG_DIR / f"{code}.json", encoding="utf-8") as fh:
        return json.load(fh)


def _placeholders(template):
    return {name for _, name, _, _ in string.Formatter().parse(template)
            if name is not None}


# --- de-Golden: CHARTS_LANG=de reproduziert die frueheren Hardcodes byte-exakt --

def test_de_golden_upgrade_strings():
    i18n.setup(lang="de", lang_dir=_LANG_DIR)
    # Lauf-Start (frueher: "=== charts-upgrade-Lauf gestartet (DRY_RUN=%s) ===").
    assert (i18n.t("upgrade.run.start", dry_run="1")
            == "=== charts-upgrade-Lauf gestartet (DRY_RUN=1) ===")
    # Schlecht-Zeile (frueher: "Schlecht [%s]: %s - %s").
    assert (i18n.t("upgrade.song.bad", reason="niedrige Bitrate (128kbps)",
                   artist="Artist", title="Title")
            == "Schlecht [niedrige Bitrate (128kbps)]: Artist - Title")
    # Grund "niedrige Bitrate" (frueher: f"niedrige Bitrate ({bitrate}kbps)").
    assert (i18n.t("upgrade.reason.low_bitrate", bitrate=128)
            == "niedrige Bitrate (128kbps)")
    # Grund "upgesampelt" mit Float-Formatspecs (frueher:
    # f"upgesampelt (behauptet {b}kbps, echter Cutoff {m:.1f}kHz, erwartet ab {e:.0f}kHz)").
    assert (i18n.t("upgrade.reason.upsampled", bitrate=320, measured=16.0, expected=19.0)
            == "upgesampelt (behauptet 320kbps, echter Cutoff 16.0kHz, erwartet ab 19kHz)")
    # Live-Pushover-Titel mit Emoji (frueher: "\u2b06\ufe0f charts-upgrade").
    assert i18n.t("upgrade.notify.title_live") == "\u2b06\ufe0f charts-upgrade"


# --- Lokale Parity-Garantie (zweite Verteidigungslinie) ---------------------

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
