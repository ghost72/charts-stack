"""
i18n tests for charts-dashboard (Phase 4).

Mirrors the sister services' LOCAL parity guard (identical key sets, no empty
values, {name} placeholder parity via string.Formatter) over the shared central
lang/*.json files, so the dashboard CI run is green independently of charts-sync.
The authoritative central guard lives in charts-sync/tests/test_i18n.py and
already covers the new dashboard.* keys; this file does NOT weaken the {name}
check.

Added ADDITIVE dashboard tests (design Decision 3):
  - test_ui_subset_is_json_safe: every dashboard.ui.* value is safe to inject
    into a <script> element (no </script, <!--, --> terminators). Inert
    <b>/<code> markup is allowed on purpose.
  - test_dashboard_ui_keys_present: canary set of UI keys the frontend depends on.
  - test_de_golden_dashboard: byte-identical German anchors incl. the
    entity/path and ellipsis canaries.

Plus a FastAPI TestClient render proof of the server->frontend handoff for
de/en/fr (<html lang>, parseable const I18N, a known placeholder key, and the
Donate footer on/off per DASHBOARD_DONATE). All tests run WITHOUT network/Docker.
"""

import importlib
import json
import pathlib
import re
import string

import pytest

from app import i18n


# Central shared lang files: app -> charts-dashboard -> charts-stack/lang.
_LANG_DIR = pathlib.Path(i18n.__file__).resolve().parent.parent.parent / "lang"
_SHIPPED = ("en", "de", "fr")
_UI_PREFIX = "dashboard.ui."


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


# --- Additive dashboard tests -----------------------------------------------

def test_ui_subset_is_json_safe():
    """Every dashboard.ui.* value must be safe inside a <script> literal.

    Guards the three HTML-parser script/comment terminators (case-insensitive)
    that would break the injected const I18N. Inert <b>/<code> markup and {name}
    placeholders / UTF-8 accents stay allowed (see Injection safety).
    """
    banned = ("</script", "<!--", "-->")
    for c in _SHIPPED:
        for key, value in _load_shipped(c).items():
            if not key.startswith(_UI_PREFIX):
                continue
            low = value.lower()
            for b in banned:
                assert b not in low, f"{key} in {c}.json contains banned {b!r}"


def test_dashboard_ui_keys_present():
    """Canary set the frontend hard-depends on (full dotted keys)."""
    canaries = {
        "dashboard.ui.updated.loading",
        "dashboard.ui.updated.refreshed",
        "dashboard.ui.time.never",
        "dashboard.ui.time.unit.min",
        "dashboard.ui.time.in_prefix",
        "dashboard.ui.tile.reachable",
        "dashboard.ui.tile.not_reachable",
        "dashboard.ui.donate.label",
    }
    for c in _SHIPPED:
        keys = set(_load_shipped(c))
        missing = canaries - keys
        assert not missing, f"missing UI canary keys in {c}.json: {missing}"


def test_de_golden_dashboard():
    """CHARTS_LANG=de reproduces today's German byte-exact (incl. entity/path
    and ellipsis canaries that lock Decision 1b / rev-2 Findings 1-2)."""
    i18n.setup(lang="de", lang_dir=_LANG_DIR)
    # textContent path -> DECODED '&'
    assert (i18n.t("dashboard.ui.brand.subtitle")
            == "Charts-Automatik \u00b7 Status & Verbindungen")
    # innerHTML path -> ENCODED '&amp;'
    assert i18n.t("dashboard.ui.stage.report") == "\u00dcBERWACHUNG &amp; REPORT"
    # ellipsis preserved inside the no-since running key
    assert i18n.t("dashboard.ui.svc.running") == "\u23f3 l\u00e4uft gerade\u2026"
    # representative prose anchors
    assert (i18n.t("dashboard.ui.tile.slskd.connected")
            == "verbunden & eingeloggt")
    assert (i18n.t("dashboard.conn.flashback.bad_hash")
            == "Hash ung\u00fcltig (API ge\u00e4ndert?)")
    # shared template keeps its {code} placeholder
    assert i18n.t("dashboard.conn.http_error", code=503) == "Fehler (HTTP 503)"


# --- TestClient render proof (server->frontend handoff) ----------------------

def _client(monkeypatch, lang):
    """Build a TestClient with a stub session and the chosen language.

    Reloads main so module-level constants (login users, donate footer) and the
    i18n table pick up the per-test env. Returns (client, main_module, token).
    """
    from fastapi.testclient import TestClient
    # A password so config.APP_USERS is non-empty (main raises otherwise).
    monkeypatch.setenv("DASHBOARD_PASSWORD", "secret")
    monkeypatch.setenv("DASHBOARD_USERNAME", "admin")
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
    # json.dumps emits the whole object on ONE line, so anchor the capture to
    # that line (greedy up to the trailing };) rather than a non-greedy match
    # that could stop at a '};' inside a value.
    m = re.search(r"const I18N = (\{.*\});\s*$", html, re.M)
    assert m, "const I18N object not found in rendered HTML"
    return json.loads(m.group(1))


@pytest.mark.parametrize("lang", ["de", "en", "fr"])
def test_index_render_handoff(monkeypatch, lang):
    client, main_mod = _client(monkeypatch, lang)
    r = client.get("/")
    assert r.status_code == 200
    html = r.text
    assert f'<html lang="{lang}">' in html
    i18n_obj = _extract_i18n(html)
    # a surviving dashboard.ui.* key is present in the client handoff
    assert "tile.slskd.connected" in i18n_obj
    # language-correct sample strings
    expected = {
        "de": "verbunden & eingeloggt",
        "en": "connected & logged in",
        "fr": "connect\u00e9 et authentifi\u00e9",
    }[lang]
    assert i18n_obj["tile.slskd.connected"] == expected


def test_index_de_byte_identical_sample(monkeypatch):
    client, _ = _client(monkeypatch, "de")
    i18n_obj = _extract_i18n(client.get("/").text)
    # de must be byte-identical to today's German UI
    assert i18n_obj["brand.subtitle"] == "Charts-Automatik \u00b7 Status & Verbindungen"
    assert i18n_obj["svc.running"] == "\u23f3 l\u00e4uft gerade\u2026"


def test_donate_footer_on_by_default(monkeypatch):
    client, _ = _client(monkeypatch, "de")
    html = client.get("/").text
    assert 'class="site-footer"' in html
    assert "https://paypal.me/rogerwyss" in html
    assert "https://revolut.me/rogerwyss" in html
    assert 'rel="noopener noreferrer"' in html
    assert 'target="_blank"' in html


def test_donate_footer_off_when_disabled(monkeypatch):
    monkeypatch.setenv("DASHBOARD_DONATE", "0")
    client, _ = _client(monkeypatch, "de")
    html = client.get("/").text
    assert 'class="site-footer"' not in html
    assert "paypal.me/rogerwyss" not in html


def test_login_page_translated(monkeypatch):
    client, _ = _client(monkeypatch, "de")
    # fresh client without session cookie to hit the login page directly
    from fastapi.testclient import TestClient
    import app.main as main_mod
    anon = TestClient(main_mod.app)
    r = anon.get("/login")
    assert r.status_code == 200
    assert '<html lang="de">' in r.text
    assert "Bitte anmelden" in r.text


# Restore import-time state so test ordering never leaks a language.
@pytest.fixture(autouse=True)
def _restore_setup():
    yield
    i18n.setup()
