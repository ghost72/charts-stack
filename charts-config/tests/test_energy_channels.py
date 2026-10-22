"""
Unit tests for the Energy channel browser in collector.py.

No network and no Docker: energy_channels() only reads the static channel list
plus the charts-energy status entry, so the tests mock collector._read_status_
files directly. Covers the default-mode mapping, the (playlist, missing_file)
derivation, the per-group snippet JSON (incl. byte-exact feed theme 'mundart'
and key order), the exact active-status badge predicate (fail-open), and a
HARD-FAILING slug drift guard against charts-energy/app/discover.py.

Run (from charts-config/):
    /tmp/csvenv/bin/python -m pytest tests/ -q
"""

import ast
import importlib
import json
import pathlib

import pytest

from app import collector, i18n


# The collector emits its fail-open detail via i18n (English by default). Anchor
# a known language so the few detail assertions are deterministic.
_LANG_DIR = pathlib.Path(i18n.__file__).resolve().parent.parent.parent / "lang"


@pytest.fixture(autouse=True)
def _english_lang():
    i18n.setup(lang="en", lang_dir=_LANG_DIR)
    yield
    i18n.setup()


# --- _energy_default_mode ---------------------------------------------------

def test_default_mode_decades_are_library():
    for slug in ("energy80s", "energy90s", "energy00s", "energy10s", "energy20s"):
        assert collector._energy_default_mode(slug) == "library"


def test_default_mode_swiss_is_feed():
    assert collector._energy_default_mode("energymadeinswitzerland") == "feed"


def test_default_mode_others_are_own():
    for slug in ("energypop", "energyhits", "energyrock", "energyxmas"):
        assert collector._energy_default_mode(slug) == "own"


# --- _energy_default_names + per-channel missing_file -----------------------

def test_default_names_strips_leading_energy():
    assert collector._energy_default_names("energypop") == (
        "energy-pop.m3u", "missing-energy-pop.txt")
    assert collector._energy_default_names("energyworkout") == (
        "energy-workout.m3u", "missing-energy-workout.txt")


def test_missing_file_per_mode_rule():
    """own -> derived txt, library -> shared file, feed -> ''."""
    by_slug = {c["slug"]: c for c in collector.energy_channels()["channels"]}
    assert by_slug["energypop"]["missing_file"] == "missing-energy-pop.txt"
    assert by_slug["energy80s"]["missing_file"] == "missing-energy-library.txt"
    assert by_slug["energymadeinswitzerland"]["missing_file"] == ""


# --- _energy_snippet --------------------------------------------------------

def test_snippet_library_object_and_order():
    snip = collector._energy_snippet("Energy 80s", "energy80s")
    assert json.loads(snip) == {
        "slug": "energy80s", "mode": "library", "enabled": True}
    # Key order: slug, mode, enabled.
    assert snip == '{"slug":"energy80s","mode":"library","enabled":true}'


def test_snippet_feed_object_and_byte_exact_theme():
    snip = collector._energy_snippet(
        "Energy Made In Switzerland", "energymadeinswitzerland")
    assert json.loads(snip) == {
        "slug": "energymadeinswitzerland", "mode": "feed",
        "theme": "mundart", "enabled": True}
    # theme is byte-for-byte 'mundart'.
    assert '"theme":"mundart"' in snip
    assert snip == ('{"slug":"energymadeinswitzerland","mode":"feed",'
                    '"theme":"mundart","enabled":true}')


def test_snippet_own_object_and_order():
    snip = collector._energy_snippet("Energy Pop", "energypop")
    assert json.loads(snip) == {
        "slug": "energypop", "title": "Energy Pop", "mode": "own",
        "playlist": "energy-pop.m3u", "enabled": True}
    # Key order: slug, title, mode, playlist, enabled.
    assert snip == ('{"slug":"energypop","title":"Energy Pop","mode":"own",'
                    '"playlist":"energy-pop.m3u","enabled":true}')


# --- energy_channels() badge predicate (mock only the status read) ----------

def _patch_status(monkeypatch, data):
    """Replace _read_status_files so it returns {'charts-energy': data} (or {})."""
    def fake():
        return {"charts-energy": data} if data is not None else {}
    monkeypatch.setattr(collector, "_read_status_files", fake)


def _by_slug(out):
    return {c["slug"]: c for c in out["channels"]}


def test_active_when_ok_and_enabled(monkeypatch):
    _patch_status(monkeypatch, {
        "status": "ok",
        "channels": [{"slug": "energypop", "mode": "feed", "enabled": True}],
    })
    out = collector.energy_channels()
    assert out["ok"] is True
    pop = _by_slug(out)["energypop"]
    assert pop["active"] is True
    # active_mode reflects the ACTUAL running mode, not the default.
    assert pop["active_mode"] == "feed"


def test_active_when_running(monkeypatch):
    _patch_status(monkeypatch, {
        "status": "running",
        "channels": [{"slug": "energypop", "mode": "own", "enabled": True}],
    })
    assert _by_slug(collector.energy_channels())["energypop"]["active"] is True


def test_inactive_when_error(monkeypatch):
    _patch_status(monkeypatch, {
        "status": "error",
        "channels": [{"slug": "energypop", "mode": "own", "enabled": True}],
    })
    pop = _by_slug(collector.energy_channels())["energypop"]
    assert pop["active"] is False
    assert pop["active_mode"] == ""


def test_inactive_when_idle(monkeypatch):
    _patch_status(monkeypatch, {
        "status": "idle",
        "channels": [{"slug": "energypop", "mode": "own", "enabled": True}],
    })
    assert all(not c["active"] for c in collector.energy_channels()["channels"])


def test_inactive_when_disabled_entry(monkeypatch):
    _patch_status(monkeypatch, {
        "status": "ok",
        "channels": [{"slug": "energypop", "mode": "own", "enabled": False}],
    })
    assert _by_slug(collector.energy_channels())["energypop"]["active"] is False


def test_inactive_when_slug_absent(monkeypatch):
    _patch_status(monkeypatch, {
        "status": "ok",
        "channels": [{"slug": "energyhits", "mode": "own", "enabled": True}],
    })
    assert _by_slug(collector.energy_channels())["energypop"]["active"] is False


def test_failopen_missing_status_file(monkeypatch):
    _patch_status(monkeypatch, None)   # no charts-energy entry at all
    out = collector.energy_channels()
    assert out["ok"] is True
    assert all(not c["active"] for c in out["channels"])


def test_failopen_missing_channels_key(monkeypatch):
    _patch_status(monkeypatch, {"status": "ok"})   # no 'channels' key
    out = collector.energy_channels()
    assert out["ok"] is True
    assert all(not c["active"] for c in out["channels"])


def test_channel_count_and_shape(monkeypatch):
    _patch_status(monkeypatch, {"status": "ok", "channels": []})
    out = collector.energy_channels()
    assert len(out["channels"]) == 18
    for c in out["channels"]:
        assert set(c) == {"display", "slug", "genre", "mode", "snippet",
                          "missing_file", "active", "active_mode"}


# --- HARD-FAILING slug drift guard ------------------------------------------

def test_slug_set_matches_discover_non_regional():
    """The static set must equal discover.py's non-regional slugs.

    Reads charts-energy/app/discover.py as TEXT (never imports it: separate
    container/package) and extracts the CONFIRMED_CHANNELS literal via ast.
    A missing file, an unparseable literal, or any drift FAILS (never skips) so
    an upstream rename cannot slip through unnoticed.
    """
    discover = (pathlib.Path(collector.__file__).resolve().parents[2]
                / "charts-energy" / "app" / "discover.py")
    assert discover.is_file(), f"discover.py not found at {discover}"

    tree = ast.parse(discover.read_text(encoding="utf-8"))
    value_node = None
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        for tgt in targets:
            if isinstance(tgt, ast.Name) and tgt.id == "CONFIRMED_CHANNELS":
                value_node = node.value
                break
        if value_node is not None:
            break
    assert value_node is not None, "CONFIRMED_CHANNELS assignment not found"

    confirmed = ast.literal_eval(value_node)
    upstream_non_regional = {slug for _display, slug, genre in confirmed
                             if genre != "Regional"}
    config_slugs = {slug for _display, slug, _genre
                    in collector._ENERGY_CHANNELS}
    assert config_slugs == upstream_non_regional, (
        "slug drift between collector._ENERGY_CHANNELS and discover.py "
        f"CONFIRMED_CHANNELS (non-regional): "
        f"missing={upstream_non_regional - config_slugs}, "
        f"extra={config_slugs - upstream_non_regional}")


# --- FastAPI render proof (server -> frontend handoff) ----------------------

def test_index_renders_energy_section(monkeypatch):
    """The served '/' markup carries the Energy section markers.

    Reuses the TestClient/handoff pattern from test_i18n.py: a stub session so
    the auth guard passes, then assert the sibling section's i18n markers + the
    results container id are present in the rendered HTML.
    """
    from fastapi.testclient import TestClient
    monkeypatch.setenv("CONFIG_PASSWORD", "secret")
    monkeypatch.setenv("CONFIG_USERNAME", "admin")
    monkeypatch.setenv("CHARTS_LANG", "en")
    import app.config as config_mod
    importlib.reload(config_mod)
    i18n.setup(lang="en", lang_dir=_LANG_DIR)
    import app.main as main_mod
    importlib.reload(main_mod)
    main_mod._sessions["test-token"] = "admin"
    client = TestClient(main_mod.app)
    client.cookies.set("session", "test-token")

    html = client.get("/").text
    assert 'data-i18n="section.energy"' in html
    assert 'data-i18n-html="energy.hint"' in html
    assert 'id="energy-channels"' in html
    # Reset i18n import-time state so later tests do not leak the language.
    i18n.setup()
