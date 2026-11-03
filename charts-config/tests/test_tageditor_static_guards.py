"""
Static-asset regression guards for the Manual Tag Editor's two prefill-driven /
cover-overlay elements that silently failed to render on the deployed NAS
instance:

1. The round glassmorphism water-drop play button (.te-play-overlay) rendered as
   the old flat blue rectangle because the generic `.finder-box form button`
   rule (specificity 0,1,2) outranked the single-class `.te-play-overlay`
   (0,1,0) on border / background / border-radius. The fix scopes the overlay
   selectors under `#te-form` (1,1,0) so they win. These tests pin that scoping
   so a future edit that drops it turns red instead of silently flattening the
   button.

2. The Convert-to-MP3 button never appeared for a non-MP3 song when the server
   payload omitted `is_mp3` (version/deploy skew), because the guard keyed on
   `song.is_mp3 !== false` and `undefined !== false` is true. The fix derives
   convertibility with a `song.ext` fallback. This test pins that the guard
   consults `song.ext`, not `song.is_mp3` alone.

These are text-level assertions on the committed static files (no browser);
they freeze the exact invariant each fix establishes.
"""

import os
import re

_STATIC = os.path.join(os.path.dirname(__file__), "..", "app", "static")
_CSS = os.path.join(_STATIC, "style.css")
_HTML = os.path.join(_STATIC, "index.html")


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


# --- Root cause 1: play-overlay CSS specificity -------------------------------

def test_play_overlay_base_rule_scoped_to_te_form():
    """The base .te-play-overlay rule must be scoped under #te-form so its
    specificity (1,1,0) beats the generic `.finder-box form button` (0,1,2)."""
    css = _read(_CSS)
    assert "#te-form .te-play-overlay {" in css, (
        "base .te-play-overlay rule must be scoped under #te-form to outrank "
        ".finder-box form button"
    )
    # The un-scoped base rule (which would lose the cascade) must NOT exist.
    assert not re.search(r"(?m)^\.te-play-overlay\s*\{", css), (
        "un-scoped `.te-play-overlay {` rule found; it would be overridden by "
        "`.finder-box form button` and flatten the water-drop button"
    )


def test_play_overlay_state_rules_scoped_to_te_form():
    """Every stateful .te-play-overlay companion rule (hover/active/hidden/
    disabled) must also be scoped under #te-form, so it keeps matching the
    more-specific base rule and does not get clobbered."""
    css = _read(_CSS)
    for state in (":hover", ":active", "[hidden]", ":disabled"):
        scoped = "#te-form .te-play-overlay" + state
        assert scoped in css, f"missing scoped selector: {scoped}"
        # No un-scoped sibling for the same state.
        assert not re.search(
            r"(?m)^\.te-play-overlay" + re.escape(state), css
        ), f"un-scoped `.te-play-overlay{state}` found; must be #te-form-scoped"


def test_cache_busting_token_bumped():
    """The style.css link must carry a bumped cache-busting token so Safari/iOS
    fetch the corrected CSS outside a private window."""
    html = _read(_HTML)
    m = re.search(r"/static/style\.css\?v=(\w+)", html)
    assert m, "style.css link must carry a ?v= cache-busting token"
    assert m.group(1) != "config1", (
        "cache-busting token must be bumped past the broken 'config1' build"
    )


# --- Root cause 2: convert guard extension fallback ---------------------------

def test_convert_guard_falls_back_to_ext():
    """The Convert-to-MP3 guard must consult song.ext as a fallback, so a
    payload that omits is_mp3 still reveals Convert for a non-MP3 file."""
    html = _read(_HTML)
    # The guard reads song.ext (fallback) alongside song.is_mp3.
    assert "song.ext" in html, (
        "convert guard must fall back to song.ext when is_mp3 is absent"
    )
    # The fragile sole guard `song.is_mp3 !== false` must be gone.
    assert "song.is_mp3 !== false" not in html, (
        "fragile `song.is_mp3 !== false` convert guard still present; it hides "
        "Convert when the server omits is_mp3"
    )
