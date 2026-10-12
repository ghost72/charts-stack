"""
i18n tests for charts-songrequest (Phase 2).

The i18n loader (app/i18n.py) is byte-identical to charts-sync; the central
key-set/placeholder parity over the shared lang/*.json files is already covered
in charts-sync/tests/test_i18n.py (and thereby also covers the new
songrequest.* keys). This set extends the Phase 1 pilot with a de golden check:
with CHARTS_LANG=de the output of representative songrequest.* keys must be
byte-identical to the former hardcoded output (regression anchor) - including
the web UI response strings.

In addition a local parity guarantee as a second line of defense, so the three
central files also stay in sync when this service is extended.

All tests run WITHOUT network/Docker.
"""

import importlib
import json
import pathlib
import re
import string

import pytest

from app import i18n


# Central language files shared across all services:
# app -> charts-songrequest -> charts-stack/lang.
_LANG_DIR = pathlib.Path(i18n.__file__).resolve().parent.parent.parent / "lang"
_SHIPPED = ("en", "de", "fr")


def _load_shipped(code):
    with open(_LANG_DIR / f"{code}.json", encoding="utf-8") as fh:
        return json.load(fh)


def _placeholders(template):
    return {name for _, name, _, _ in string.Formatter().parse(template)
            if name is not None}


# --- de golden: CHARTS_LANG=de reproduces the former hardcodes byte-exactly --

def test_de_golden_songrequest_strings():
    i18n.setup(lang="de", lang_dir=_LANG_DIR)
    # Request log (formerly: "Request von %s: %s - %s").
    assert (i18n.t("songrequest.request.received", user="roger",
                   artist="Artist", title="Title")
            == "Request von roger: Artist - Title")
    # Web UI "nothing better found" (honest backend message, formerly an
    # f-string with {existing_bitrate}/{MIN_BITRATE}).
    assert (i18n.t("songrequest.ui.nothing_better", bitrate=192, min=256)
            == "Vorhanden mit 192 kbps. Aktuell keine bessere Version "
               "(\u2265 256 kbps) im Netz gefunden \u2013 sp\u00e4ter erneut versuchen.")
    # Download-started UI message (formerly a fixed string).
    assert (i18n.t("songrequest.ui.download_started")
            == "Gefunden & Download gestartet. Landet in K\u00fcrze automatisch "
               "in der Library.")


def test_de_golden_shared_keys_reused():
    """The copied shared modules (slsk/status/manifest) use the already
    translated download.*/status.* keys - here as an anchor that they still
    deliver the former German hardcodes unchanged."""
    i18n.setup(lang="de", lang_dir=_LANG_DIR)
    assert (i18n.t("download.slsk.rejected_dj_edit", filename="x.mp3")
            == "  verworfen (DJ-Edit/BPM-Endung): x.mp3")
    assert (i18n.t("status.write_failed", path="/p", error="boom")
            == "Status konnte nicht geschrieben werden (/p): boom")


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


# --- TestClient render proof (server->frontend handoff) ----------------------

def _client(monkeypatch, lang):
    """Build a TestClient with a stub session and the chosen language.

    Reloads config + main so module-level constants (login users) and the i18n
    table pick up the per-test env. Returns (client, main_module).
    """
    from fastapi.testclient import TestClient
    # A user so config.APP_USERS is non-empty (main raises otherwise).
    monkeypatch.setenv("SONGREQUEST_USERS", "roger:secret")
    monkeypatch.setenv("CHARTS_LANG", lang)
    import app.config as config_mod
    importlib.reload(config_mod)
    i18n.setup(lang=lang, lang_dir=_LANG_DIR)
    import app.main as main_mod
    importlib.reload(main_mod)
    token = "test-token"
    main_mod._sessions[token] = "roger"
    client = TestClient(main_mod.app)
    client.cookies.set("session", token)
    return client, main_mod


def _extract_i18n(html):
    """Parse the injected `const I18N = {...};` object from the rendered page."""
    # json.dumps emits the whole object on ONE line, so anchor the capture to
    # that line (greedy up to the trailing };) rather than a non-greedy match
    # that could stop at a '};' inside a value.
    m = re.search(r"const I18N = (\{.*\});\s*$", html, re.M)
    assert m, "const I18N object not found in rendered HTML"
    return json.loads(m.group(1))


@pytest.mark.parametrize("lang", ["de", "en", "fr"])
def test_index_render_handoff(monkeypatch, lang):
    client, _ = _client(monkeypatch, lang)
    r = client.get("/")
    assert r.status_code == 200
    html = r.text
    assert f'<html lang="{lang}">' in html
    i18n_obj = _extract_i18n(html)
    # a known UI key is present
    assert "subtitle" in i18n_obj
    # language-correct sample string
    expected = {
        "de": "Song suchen, laden und in die Library holen",
        "en": "Search for a song, download it and add it to the library",
        "fr": "Rechercher un morceau, le t\u00e9l\u00e9charger et "
              "l'ajouter \u00e0 la biblioth\u00e8que",
    }[lang]
    assert i18n_obj["subtitle"] == expected


def test_index_de_byte_identical_subtitle(monkeypatch):
    client, _ = _client(monkeypatch, "de")
    i18n_obj = _extract_i18n(client.get("/").text)
    # de must be byte-identical to today's German UI
    assert i18n_obj["subtitle"] == "Song suchen, laden und in die Library holen"


# Restore import-time state so test ordering never leaks a language.
@pytest.fixture(autouse=True)
def _restore_setup():
    yield
    i18n.setup()
