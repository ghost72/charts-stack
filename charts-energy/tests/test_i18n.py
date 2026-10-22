"""
Unit tests for the i18n loader (app/i18n.py) and the shipped language tables
(centrally in charts-stack/lang/*.json, shared across all services).

Mirrors the charts-hitparade pilot: the central parity guarantee
(set(en)==set(de)==set(fr) plus placeholder parity per key) AND a check that
every energy.* key the app references resolves in all three shipped languages.

NOTE: the energy.* keys are added to lang/{en,de,fr}.json by FEAT-003, so
test_energy_keys_present_in_all_languages goes green at the integration pass.
All tests run WITHOUT network/Docker and set i18n.setup(lang=..., lang_dir=...)
explicitly (no CHARTS_LANG env leak).
"""

import json
import pathlib
import string

import pytest

from app import i18n


# Central shipped language files: app -> charts-energy -> charts-stack/lang.
_LANG_DIR = pathlib.Path(i18n.__file__).resolve().parent.parent.parent / "lang"
_SHIPPED = ("en", "de", "fr")


# The CLOSED set of energy.* keys the charts-energy app references (design
# section 10, 34 keys). FEAT-003 adds EXACTLY these to all three lang files.
_ENERGY_KEYS = {
    # energy.config.* (5)
    "energy.config.enabled_unparsed",
    "energy.config.channel_no_slug",
    "energy.config.channel_bad_mode",
    "energy.config.feed_no_theme",
    "energy.config.no_channels",
    # energy.status.* (2)
    "energy.status.disabled",
    "energy.status.nothing_to_do",
    # energy.scheduler.* / run (6)
    "energy.disabled_skip",
    "energy.scheduler.started",
    "energy.run.start",
    "energy.run.done",
    "energy.run.error",
    "energy.channel.done",
    # energy.icy.* (3)
    "energy.icy.no_metaint",
    "energy.icy.no_separator",
    "energy.icy.read_failed",
    # energy.counter.* (4)
    "energy.counter.channels",
    "energy.counter.tracks",
    "energy.counter.matched",
    "energy.counter.missing",
    # energy.notify.* (4)
    "energy.notify.title",
    "energy.notify.body",
    "energy.notify.own_done",
    "energy.notify.feed_done",
    # energy.state.* (2)
    "energy.state.yes",
    "energy.state.no",
    # energy.discover.* (5)
    "energy.discover.list_header",
    "energy.discover.columns",
    "energy.discover.probe_ok",
    "energy.discover.probe_fail",
    "energy.discover.probe_unreachable",
    # energy.feed.* (3)
    "energy.feed.written",
    "energy.feed.merged",
    "energy.feed.skipped",
}


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
    assert i18n.t("only_en") == "english only"


def test_missing_key_returns_key_name(lang_dir):
    i18n.setup(lang="en", lang_dir=lang_dir)
    assert i18n.t("energy.does.not.exist") == "energy.does.not.exist"


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


def test_energy_keys_present_in_all_languages():
    """Every energy.* key the app references must exist in en/de/fr.

    FEAT-003 adds exactly the _ENERGY_KEYS set to the shipped language files;
    until then this test reports which keys are still missing.
    """
    tables = {c: _load_shipped(c) for c in _SHIPPED}
    for c in _SHIPPED:
        missing = _ENERGY_KEYS - set(tables[c])
        assert not missing, f"energy.* keys missing in {c}: {sorted(missing)}"


def test_de_golden_energy_disabled():
    """CHARTS_LANG=de must render the idle message as 'deaktiviert'
    (the enabled_guard test depends on this)."""
    i18n.setup(lang="de", lang_dir=_LANG_DIR)
    assert i18n.t("energy.status.disabled") == "deaktiviert"


# Restore import-time state so test ordering never leaks a language.
@pytest.fixture(autouse=True)
def _restore_setup():
    yield
    i18n.setup()
