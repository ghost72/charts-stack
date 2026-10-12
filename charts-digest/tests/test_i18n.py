"""
i18n tests for charts-digest (Phase 2).

The i18n loader (app/i18n.py) is byte-identical to charts-sync; the central
key-set/placeholder parity over the shared lang/*.json files is already covered
in charts-sync/tests/test_i18n.py (and thereby also covers the new digest.*
keys). This set extends the Phase 1 pilot with a de golden check: with
CHARTS_LANG=de the output of the new digest.* keys must be byte-identical to
the former hardcoded output (regression anchor).

In addition a local parity guarantee as a second line of defense, so the three
central files also stay in sync when this service is extended.

All tests run WITHOUT network/Docker.
"""

import json
import pathlib
import string

from app import i18n


# Central language files shared across all services:
# app -> charts-digest -> charts-stack/lang.
_LANG_DIR = pathlib.Path(i18n.__file__).resolve().parent.parent.parent / "lang"
_SHIPPED = ("en", "de", "fr")


def _load_shipped(code):
    with open(_LANG_DIR / f"{code}.json", encoding="utf-8") as fh:
        return json.load(fh)


def _placeholders(template):
    return {name for _, name, _, _ in string.Formatter().parse(template)
            if name is not None}


# --- de golden: CHARTS_LANG=de reproduces the former hardcodes byte-exactly --

def test_de_golden_digest_strings():
    i18n.setup(lang="de", lang_dir=_LANG_DIR)
    # Run start with window + optional "manuell" suffix (formerly:
    # "=== charts-digest-Lauf gestartet (Fenster: %dh%s) ===").
    assert (i18n.t("digest.run.start", hours=24,
                   manual=i18n.t("digest.run.manual_suffix"))
            == "=== charts-digest-Lauf gestartet (Fenster: 24h, manuell) ===")
    # Pushover title with emoji + song count (formerly:
    # f"{head_emoji} {unique_total} neue Songs in deiner Library").
    assert (i18n.t("digest.notify.title", emoji="\U0001f389", count=7)
            == "\U0001f389 7 neue Songs in deiner Library")
    # Group header in the summary body (formerly: f"{emoji} <b>{label}</b> \u00b7 {n}";
    # label came from _SOURCE_LABELS, here "Aktuelle Charts").
    assert (i18n.t("digest.notify.group_header", emoji="\U0001f525",
                   label=i18n.t("digest.source.charts"), count=12)
            == "\U0001f525 <b>Aktuelle Charts</b> \u00b7 12")
    # Scheduler start with time format specs (formerly: "charts-digest gestartet |
    # taeglich %02d:%02d | Fenster %dh | Log: %s").
    assert (i18n.t("digest.scheduler.started", hour=9, minute=0, hours=24,
                   log="/charts/.charts-imported-log.jsonl")
            == "charts-digest gestartet | täglich 09:00 | Fenster 24h | "
               "Log: /charts/.charts-imported-log.jsonl")


def test_de_golden_more_line():
    # "... und N weitere" (formerly f"\u2026 und {n} weitere") - the overflow
    # marker in the budget-aware message.
    i18n.setup(lang="de", lang_dir=_LANG_DIR)
    assert i18n.t("digest.notify.more", count=5) == "\u2026 und 5 weitere"


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
