"""
i18n-Tests fuer charts-songrequest (Phase 2).

Der i18n-Loader (app/i18n.py) ist byte-identisch zu charts-sync; die zentrale
Key-Set-/Platzhalter-Parity ueber die geteilten lang/*.json-Dateien ist bereits
in charts-sync/tests/test_i18n.py abgedeckt (und deckt damit auch die neuen
songrequest.*-Keys mit ab). Dieser Satz ergaenzt den Phase-1-Pilot um einen
de-Golden-Check: mit CHARTS_LANG=de muss die Ausgabe repraesentativer
songrequest.*-Keys byte-identisch zur frueheren Hardcode-Ausgabe sein
(Regressions-Anker) - inklusive der Web-UI-Antwortstrings.

Zusaetzlich eine lokale Parity-Garantie als zweite Verteidigungslinie, damit
die drei zentralen Dateien auch beim Erweitern dieses Dienstes synchron bleiben.

Alle Tests laufen OHNE Netzwerk/Docker.
"""

import json
import pathlib
import string

from app import i18n


# Zentrale, ueber alle Dienste geteilte Sprachdateien:
# app -> charts-songrequest -> charts-stack/lang.
_LANG_DIR = pathlib.Path(i18n.__file__).resolve().parent.parent.parent / "lang"
_SHIPPED = ("en", "de", "fr")


def _load_shipped(code):
    with open(_LANG_DIR / f"{code}.json", encoding="utf-8") as fh:
        return json.load(fh)


def _placeholders(template):
    return {name for _, name, _, _ in string.Formatter().parse(template)
            if name is not None}


# --- de-Golden: CHARTS_LANG=de reproduziert die frueheren Hardcodes byte-exakt --

def test_de_golden_songrequest_strings():
    i18n.setup(lang="de", lang_dir=_LANG_DIR)
    # Request-Log (frueher: "Request von %s: %s - %s").
    assert (i18n.t("songrequest.request.received", user="roger",
                   artist="Artist", title="Title")
            == "Request von roger: Artist - Title")
    # Web-UI "nichts Besseres gefunden" (ehrliche Backend-Meldung, frueher
    # f-String mit {existing_bitrate}/{MIN_BITRATE}).
    assert (i18n.t("songrequest.ui.nothing_better", bitrate=192, min=256)
            == "Vorhanden mit 192 kbps. Aktuell keine bessere Version "
               "(\u2265 256 kbps) im Netz gefunden \u2013 sp\u00e4ter erneut versuchen.")
    # Download-gestartet UI-Meldung (frueher fester String).
    assert (i18n.t("songrequest.ui.download_started")
            == "Gefunden & Download gestartet. Landet in K\u00fcrze automatisch "
               "in der Library.")


def test_de_golden_shared_keys_reused():
    """Die kopierten Shared-Module (slsk/status/manifest) nutzen die bereits
    uebersetzten download.*/status.*-Keys - hier als Anker, dass sie unveraendert
    die frueheren deutschen Hardcodes liefern."""
    i18n.setup(lang="de", lang_dir=_LANG_DIR)
    assert (i18n.t("download.slsk.rejected_dj_edit", filename="x.mp3")
            == "  verworfen (DJ-Edit/BPM-Endung): x.mp3")
    assert (i18n.t("status.write_failed", path="/p", error="boom")
            == "Status konnte nicht geschrieben werden (/p): boom")


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
