"""
Served-CSS regression test for the attention pulse (FIX 2).

The confirm-button pulse is the primary cross-platform "a decision is waiting"
signal (haptics stay best-effort). It was previously too subtle: box-shadow
only, 0.28 alpha (dimmer than the resting 0.35 glow), 0.6s, 1 iteration. This
test asserts the served style.css now animates a visibly stronger effect - a
scale/transform or a background/brightness change - and runs 2-3 iterations.

No real browser: this is a string assertion on the served CSS, following the
TestClient served-markup pattern in tests/test_index_ui.py.
"""

import importlib
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


def _css(monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setenv("SONGREQUEST_USERS", "roger:secret")
    import app.config as config_mod
    importlib.reload(config_mod)
    import app.main as main_mod
    importlib.reload(main_mod)
    client = TestClient(main_mod.app)
    r = client.get("/static/style.css")
    assert r.status_code == 200
    return r.text


def test_keyframes_animates_stronger_property(monkeypatch):
    css = _css(monkeypatch)
    m = re.search(r"@keyframes\s+attention-pulse\s*\{(.*?)\}\s*\}", css, re.S)
    assert m, "@keyframes attention-pulse rule not found"
    block = m.group(1)
    # A visibly stronger property than a bare box-shadow flash: a scale/transform
    # pulse AND/OR a brightness/background shift.
    assert ("scale" in block or "transform" in block), \
        "@keyframes attention-pulse must animate a transform/scale"
    assert ("brightness" in block or "filter" in block or "background" in block), \
        "@keyframes attention-pulse must animate a brightness/background change"


def test_pulse_runs_two_or_three_iterations(monkeypatch):
    css = _css(monkeypatch)
    m = re.search(r"\.pulse\s*\{([^}]*)\}", css)
    assert m, ".pulse rule not found"
    rule = m.group(1)
    assert "attention-pulse" in rule, ".pulse must use the attention-pulse animation"
    # Iteration count must be 2 or 3 (a clearly repeated pulse), not 1.
    assert re.search(r"attention-pulse[^;]*\b(?:2|3)\b", rule), \
        ".pulse animation must run 2 or 3 iterations, not 1"
