"""
i18n + wiring tests for charts-config.

Mirrors the sister services' LOCAL parity guard (identical key sets, no empty
values, {name} placeholder parity via string.Formatter) over the shared central
lang/*.json files, so the charts-config CI run is green independently of
charts-sync. The authoritative central guard lives in charts-sync/tests/
test_i18n.py; this file does NOT weaken the {name} check.

Plus charts-config-specific wiring proofs (all WITHOUT network/Docker):
  - a TestClient render-handoff proof asserting the rendered / carries config.*
    markers and a parseable const I18N with config.ui.* canaries,
  - the server-rendered login page translation,
  - a fail-closed test (importing app.main with NO password raises RuntimeError),
  - a test that charts-config exposes NO /api/status route while the finder/
    energy routes DO exist.
"""

import importlib
import json
import pathlib
import re
import string

import pytest

from app import i18n


# Central shared lang files: app -> charts-config -> charts-stack/lang.
_LANG_DIR = pathlib.Path(i18n.__file__).resolve().parent.parent.parent / "lang"
_SHIPPED = ("en", "de", "fr")
_UI_PREFIX = "config.ui."


def _load_shipped(code):
    with open(_LANG_DIR / f"{code}.json", encoding="utf-8") as fh:
        return json.load(fh)


def _placeholders(template):
    return {name for _, name, _, _ in string.Formatter().parse(template)
            if name is not None}


# --- Local parity guard (second line of defence) ----------------------------

def test_shipped_key_sets_are_identical():
    key_sets = {c: set(_load_shipped(c)) for c in _SHIPPED}
    assert key_sets["en"] == key_sets["de"] == key_sets["fr"], (
        f"key sets diverge: en^de={key_sets['en'] ^ key_sets['de']}, "
        f"en^fr={key_sets['en'] ^ key_sets['fr']}")


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


# --- Additive config tests --------------------------------------------------

def test_ui_subset_is_json_safe():
    """Every config.ui.* value must be safe inside a <script> literal.

    Guards the three HTML-parser script/comment terminators (case-insensitive)
    that would break the injected const I18N. Inert <b>/<code> markup and {name}
    placeholders / UTF-8 accents stay allowed.
    """
    banned = ("</script", "<!--", "-->")
    for c in _SHIPPED:
        for key, value in _load_shipped(c).items():
            if not key.startswith(_UI_PREFIX):
                continue
            low = value.lower()
            for b in banned:
                assert b not in low, f"{key} in {c}.json contains banned {b!r}"


def test_config_ui_keys_present():
    """Canary set the frontend hard-depends on (full dotted keys)."""
    canaries = {
        "config.ui.section.finder",
        "config.ui.section.energy",
        "config.ui.finder.generic_error",
        "config.ui.finder.run_hint",
        "config.ui.energy.hint",
        "config.ui.energy.run_hint",
        "config.ui.title",
        "config.ui.logout",
    }
    for c in _SHIPPED:
        keys = set(_load_shipped(c))
        missing = canaries - keys
        assert not missing, f"missing UI canary keys in {c}.json: {missing}"


# --- TestClient render proof (server->frontend handoff) ----------------------

def _client(monkeypatch, lang, password="secret"):
    """Build a TestClient with a stub session and the chosen language.

    Reloads config + main so module-level constants (login users) and the i18n
    table pick up the per-test env. Returns (client, main_module).
    """
    from fastapi.testclient import TestClient
    monkeypatch.setenv("CONFIG_PASSWORD", password)
    monkeypatch.setenv("CONFIG_USERNAME", "admin")
    monkeypatch.setenv("CHARTS_LANG", lang)
    import app.config as config_mod
    importlib.reload(config_mod)
    i18n.setup(lang=lang, lang_dir=_LANG_DIR)
    import app.main as main_mod
    importlib.reload(main_mod)
    token = "test-token"
    main_mod._sessions[token] = "admin"
    client = TestClient(main_mod.app)
    client.cookies.set("session", token)
    return client, main_mod


def _extract_i18n(html):
    """Parse the injected `const I18N = {...};` object from the rendered page."""
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
    # config.* markers resolved via the config.ui. prefix in static markup.
    assert 'data-i18n="section.finder"' in html
    assert 'data-i18n="section.energy"' in html
    i18n_obj = _extract_i18n(html)
    # config.ui.* canaries present in the re-based client table.
    assert "finder.generic_error" in i18n_obj
    assert "energy.hint" in i18n_obj


def test_login_page_translated(monkeypatch):
    client, main_mod = _client(monkeypatch, "de")
    # Fresh client without session cookie to hit the login page directly.
    from fastapi.testclient import TestClient
    anon = TestClient(main_mod.app)
    r = anon.get("/login")
    assert r.status_code == 200
    assert '<html lang="de">' in r.text
    assert "Bitte anmelden" in r.text


def test_fail_closed_without_password(monkeypatch):
    """With NO credential configured, app.main refuses to import (RuntimeError)."""
    monkeypatch.delenv("CONFIG_PASSWORD", raising=False)
    monkeypatch.delenv("CONFIG_USERNAME", raising=False)
    monkeypatch.delenv("APP_USERS", raising=False)
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    import app.config as config_mod
    importlib.reload(config_mod)
    assert config_mod.APP_USERS == {}
    import app.main as main_mod
    with pytest.raises(RuntimeError):
        importlib.reload(main_mod)


def test_no_api_status_route_but_finder_energy_present(monkeypatch):
    """charts-config must NOT expose /api/status; the finder/energy routes do."""
    _client(monkeypatch, "en")
    import app.main as main_mod
    paths = {r.path for r in main_mod.app.routes}
    assert "/api/status" not in paths
    assert "/api/sources/deezer/playlists" in paths
    assert "/api/sources/deezer/artists" in paths
    assert "/api/sources/deezer/genres" in paths
    assert "/api/sources/lastfm/tag" in paths
    assert "/api/sources/energy/channels" in paths


# Restore import-time state so test ordering never leaks a language.
@pytest.fixture(autouse=True)
def _restore_setup():
    yield
    i18n.setup()
