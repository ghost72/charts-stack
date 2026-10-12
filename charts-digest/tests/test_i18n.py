"""
i18n-Tests fuer charts-digest (Phase 2).

Der i18n-Loader (app/i18n.py) ist byte-identisch zu charts-sync; die zentrale
Key-Set-/Platzhalter-Parity ueber die geteilten lang/*.json-Dateien ist bereits
in charts-sync/tests/test_i18n.py abgedeckt (und deckt damit auch die neuen
digest.*-Keys mit ab). Dieser Satz ergaenzt den Phase-1-Pilot um einen
de-Golden-Check: mit CHARTS_LANG=de muss die Ausgabe der neuen digest.*-Keys
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
# app -> charts-digest -> charts-stack/lang.
_LANG_DIR = pathlib.Path(i18n.__file__).resolve().parent.parent.parent / "lang"
_SHIPPED = ("en", "de", "fr")


def _load_shipped(code):
    with open(_LANG_DIR / f"{code}.json", encoding="utf-8") as fh:
        return json.load(fh)


def _placeholders(template):
    return {name for _, name, _, _ in string.Formatter().parse(template)
            if name is not None}


# --- de-Golden: CHARTS_LANG=de reproduziert die frueheren Hardcodes byte-exakt --

def test_de_golden_digest_strings():
    i18n.setup(lang="de", lang_dir=_LANG_DIR)
    # Lauf-Start mit Fenster + optionalem "manuell"-Suffix (frueher:
    # "=== charts-digest-Lauf gestartet (Fenster: %dh%s) ===").
    assert (i18n.t("digest.run.start", hours=24,
                   manual=i18n.t("digest.run.manual_suffix"))
            == "=== charts-digest-Lauf gestartet (Fenster: 24h, manuell) ===")
    # Pushover-Titel mit Emoji + Song-Zahl (frueher:
    # f"{head_emoji} {unique_total} neue Songs in deiner Library").
    assert (i18n.t("digest.notify.title", emoji="\U0001f389", count=7)
            == "\U0001f389 7 neue Songs in deiner Library")
    # Gruppen-Kopf im Summary-Body (frueher: f"{emoji} <b>{label}</b> \u00b7 {n}";
    # label kam aus _SOURCE_LABELS, hier "Aktuelle Charts").
    assert (i18n.t("digest.notify.group_header", emoji="\U0001f525",
                   label=i18n.t("digest.source.charts"), count=12)
            == "\U0001f525 <b>Aktuelle Charts</b> \u00b7 12")
    # Scheduler-Start mit Zeit-Formatspecs (frueher: "charts-digest gestartet |
    # taeglich %02d:%02d | Fenster %dh | Log: %s").
    assert (i18n.t("digest.scheduler.started", hour=9, minute=0, hours=24,
                   log="/charts/.charts-imported-log.jsonl")
            == "charts-digest gestartet | taeglich 09:00 | Fenster 24h | "
               "Log: /charts/.charts-imported-log.jsonl")


def test_de_golden_more_line():
    # "... und N weitere" (frueher f"\u2026 und {n} weitere") - der Ueberlauf-
    # Marker in der budgetbewussten Nachricht.
    i18n.setup(lang="de", lang_dir=_LANG_DIR)
    assert i18n.t("digest.notify.more", count=5) == "\u2026 und 5 weitere"


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
