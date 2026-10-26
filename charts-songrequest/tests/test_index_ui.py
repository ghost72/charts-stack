"""
DOM/string regression tests for the served Song Request index page.

These lock in two things the user needs:
  1. No raw i18n key token leaks into the rendered upload section. The upload
     strings must resolve to real text (e.g. "Upload a song"), never to the
     literal key name. A pre-fix / stale-deploy page would show the raw keys,
     so the English-text assertions below FAIL against that and PASS now.
  2. The tabbed single-card layout: both tab controls are present, both forms'
     element IDs are intact, Song Request is the default-active pane and the
     upload pane starts hidden.

No real browser is available here, so this is DOM/string verification of the
served markup and the injected i18n table - NOT a visual screenshot.

All tests run WITHOUT network/Docker, following the TestClient pattern from
tests/test_i18n.py.
"""

import importlib
import json
import pathlib

import pytest

from app import i18n


_LANG_DIR = pathlib.Path(i18n.__file__).resolve().parent.parent.parent / "lang"


def _client(monkeypatch, lang):
    """Build a TestClient with a stub session and the chosen language.

    Mirrors tests/test_i18n.py: reload config + main so module-level constants
    and the i18n table pick up the per-test env, then seat a session token.
    """
    from fastapi.testclient import TestClient
    monkeypatch.setenv("SONGREQUEST_USERS", "roger:secret")
    monkeypatch.setenv("CHARTS_LANG", lang)
    import app.config as config_mod
    importlib.reload(config_mod)
    i18n.setup(lang=lang, lang_dir=_LANG_DIR)
    import app.main as main_mod
    importlib.reload(main_mod)
    main_mod._sessions["test-token"] = "roger"
    client = TestClient(main_mod.app)
    client.cookies.set("session", "test-token")
    return client, main_mod


def _visible_markup(html):
    """Return only the body markup, with the <script> block removed.

    Key-token assertions must inspect what the user can actually see, not the
    legitimate client translator: the injected `const I18N = {...}` table holds
    short keys, and the surrounding English code comments mention the dotted
    `songrequest.ui.` prefix for documentation. Neither is user-visible."""
    import re
    return re.sub(r"<script>.*?</script>", "", html, flags=re.S)


# --- Issue 1: no raw i18n key tokens, real text served ----------------------

def test_no_raw_upload_key_tokens_in_served_page(monkeypatch):
    import re
    client, _ = _client(monkeypatch, "en")
    html = client.get("/").text
    visible = _visible_markup(html)
    # No raw upload key token leaks into the user-visible markup. The dotted
    # prefix and the short key names only legitimately live inside the <script>
    # block (injected I18N table + English code comments), which is stripped
    # here; they must never appear in the rendered body.
    assert "songrequest.ui." not in visible
    for key in ("upload_heading", "upload_btn", "upload_hint", "js_upload_accepted"):
        # The key name must not surface as visible text. data-i18n="<key>"
        # ATTRIBUTES stay in the static markup (filled at runtime), so match the
        # key only when NOT immediately preceded by a data-i18n* attribute.
        assert not re.search(r'(?<!i18n=")(?<!i18n-ph=")(?<!i18n-html=")\b'
                             + re.escape(key) + r'\b', visible), \
            f"raw key {key!r} leaks into visible markup"


def test_upload_section_shows_real_english_text(monkeypatch):
    """FAILS against stale pre-key-merge markup (raw keys), PASSES after fix."""
    client, _ = _client(monkeypatch, "en")
    i18n_obj = _extract_i18n(client.get("/").text)
    # These resolve to real English copy, not the literal key names.
    assert i18n_obj["upload_heading"] == "Upload a song"
    assert i18n_obj["upload_btn"] == "Upload & check"
    assert i18n_obj["upload_hint"] == 'MP3 only \u00b7 named "Artist - Title.mp3"'
    assert i18n_obj["js_upload_accepted"] == "<b>Uploaded</b>"
    # Guard that the values are not just echoing the key name.
    for key in ("upload_heading", "upload_btn", "upload_hint", "js_upload_accepted"):
        assert i18n_obj[key] != key


# --- Issue 2: tabbed single-card layout -------------------------------------

def test_both_tab_controls_present(monkeypatch):
    client, _ = _client(monkeypatch, "en")
    html = client.get("/").text
    assert 'data-tab="request"' in html
    assert 'data-tab="upload"' in html
    i18n_obj = _extract_i18n(html)
    assert i18n_obj["tab_request"] == "Song Request"
    assert i18n_obj["tab_upload"] == "Song Upload"


def test_both_forms_element_ids_present_once(monkeypatch):
    client, _ = _client(monkeypatch, "en")
    html = client.get("/").text
    for element_id in ('id="form"', 'id="upform"', 'id="artist"', 'id="title"',
                       'id="up_artist"', 'id="up_title"', 'id="upfile"'):
        assert html.count(element_id) == 1, f"{element_id} not present exactly once"


def test_single_card_layout(monkeypatch):
    client, _ = _client(monkeypatch, "en")
    html = client.get("/").text
    # One card wraps both panes (the login page is a separate route).
    assert html.count('class="card"') == 1


def test_default_tab_is_request(monkeypatch):
    client, _ = _client(monkeypatch, "en")
    html = client.get("/").text
    # The request tab button carries `active`.
    assert '<button type="button" class="tab active" data-tab="request"' in html
    # The upload pane starts hidden.
    assert '<div class="tabpane" data-pane="upload" hidden>' in html


# --- Feature 1: guarded-fetch (survive a mobile app-switch) wiring ----------

def test_guarded_fetch_scaffolding_present(monkeypatch):
    """The fetchGuarded helper, the ABORTED sentinel and the visibility/
    pageshow recovery sweep must be present in the served <script>.

    These markers live inside the inline <script> (not the visible body), so
    assert against the full served HTML, not `_visible_markup`.
    """
    client, _ = _client(monkeypatch, "en")
    html = client.get("/").text
    assert "fetchGuarded" in html, "fetchGuarded helper missing from served script"
    assert "const ABORTED = { aborted: true };" in html, \
        "plain-object ABORTED sentinel missing (must not be a Symbol)"
    assert "visibilitychange" in html, "visibilitychange recovery listener missing"
    assert "pageshow" in html, "pageshow (bfcache) recovery listener missing"


def test_exactly_four_guarded_fetch_call_sites(monkeypatch):
    """Exactly four in-flight fetches are guarded: /api/request (1),
    /api/upload initial + /api/upload/confirm (2), /api/youtube (1).

    Count `await fetchGuarded(` to count CALL SITES only - the single
    `async function fetchGuarded(` definition also contains the `fetchGuarded(`
    substring and must not inflate the count. The debounced /api/youtube/fetch
    suggestion stays on a plain, unguarded `fetch(`.
    """
    client, _ = _client(monkeypatch, "en")
    html = client.get("/").text
    assert html.count("await fetchGuarded(") == 4, \
        "expected exactly 4 guarded fetch call sites"
    # The suggestion fetch is deliberately NOT guarded.
    assert "fetch('/api/youtube/fetch'" in html, \
        "/api/youtube/fetch must stay a plain unguarded fetch"


# --- Feature 2: haptics scaffolding -----------------------------------------

def test_haptic_switch_element_present_once(monkeypatch):
    """The hidden iOS Taptic switch must exist exactly once, carry a localized
    aria-label via data-i18n-aria, have NO aria-hidden (so the label stays
    announceable), and NOT be display:none (string-assert the class, since CSS
    is not executed in these tests)."""
    client, _ = _client(monkeypatch, "en")
    html = client.get("/").text
    assert html.count('id="haptic-switch"') == 1, \
        "#haptic-switch must appear exactly once"
    import re
    m = re.search(r"<input[^>]*id=\"haptic-switch\"[^>]*>", html)
    assert m, "#haptic-switch element not found"
    switch = m.group(0)
    assert 'data-i18n-aria="haptic_switch_label"' in switch, \
        "#haptic-switch must carry data-i18n-aria=\"haptic_switch_label\""
    assert "aria-hidden" not in switch, \
        "#haptic-switch must NOT set aria-hidden (keeps the label announceable)"
    assert 'class="haptic-switch"' in switch, \
        "#haptic-switch must use the off-screen-clip class, never display:none"


def test_haptic_and_attention_wiring_present(monkeypatch):
    """The haptic() helper and the single non-user-initiated attention() wiring
    must be present in the served script."""
    client, _ = _client(monkeypatch, "en")
    html = client.get("/").text
    assert "haptic(" in html, "haptic() helper missing from served script"
    # Exactly one attention() CALL site (on the needs_confirm confirm button);
    # `function attention(` is the definition and is excluded by this match.
    assert html.count("attention(document.getElementById('upconfirm'))") == 1, \
        "expected exactly one attention() wiring on the needs_confirm button"


# --- New i18n keys resolve to real text -------------------------------------

def test_new_i18n_keys_resolve_to_real_text(monkeypatch):
    """Both new songrequest.ui.* keys land in the injected I18N table and
    resolve to real copy, not their key names."""
    client, _ = _client(monkeypatch, "en")
    i18n_obj = _extract_i18n(client.get("/").text)
    for key in ("js_connection_lost", "haptic_switch_label"):
        assert key in i18n_obj, f"{key} missing from injected I18N table"
        assert i18n_obj[key] != key, f"{key} must resolve to real text, not its name"
    assert i18n_obj["js_connection_lost"] == "Connection interrupted – please try again."
    assert i18n_obj["haptic_switch_label"] == "Haptic feedback (internal)"


def _extract_i18n(html):
    """Parse the injected `const I18N = {...};` object from the rendered page."""
    import re
    m = re.search(r"const I18N = (\{.*\});\s*$", html, re.M)
    assert m, "const I18N object not found in rendered HTML"
    return json.loads(m.group(1))


# Restore import-time state so test ordering never leaks a language.
@pytest.fixture(autouse=True)
def _restore_setup():
    yield
    i18n.setup()
