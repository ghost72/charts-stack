"""
Self-contained i18n loader for the charts-* services (Phase 1 pilot).

This module is a copy-into-every-service module (like status.py / slsk.py /
manifest.py): stdlib-only, no runtime dependency on any other service module,
no import of ``config``. It is driven by the ``CHARTS_LANG`` environment
variable.

Language files are SHARED across all services (one central set, like the
central ``.env``): the stack mounts ``charts-stack/lang/`` read-only into every
container at ``/lang``. The loader therefore resolves the language directory in
this priority order:
    1. the ``lang_dir`` argument (tests only), else
    2. the ``CHARTS_LANG_DIR`` environment variable, else
    3. ``/lang`` if it exists (the central mount inside the container), else
    4. ``<repo>/lang`` relative to this file (``app/../../lang``), else
    5. ``app/lang`` next to this file (legacy / standalone fallback).
Keys are service-prefixed (``sync.*``, ``deezer.*``, ...) so one shared file
set never has cross-service collisions.

Public API:
    t(key, **kwargs) -> str   translate a key and apply {placeholder} formatting
    setup(lang=None, lang_dir=None) -> None   (re)load the tables; auto-run at import

Formatting invariant (IMPORTANT, read before adding translations):
    ONLY the template is parsed by ``str.format``; kwarg VALUES are inserted
    literally and never re-parsed, so braces in song titles / dict-reprs /
    joined fragments are safe without escaping. Only hardcoded ``{`` / ``}`` IN
    a template must be doubled (``{{`` / ``}}``).

Robustness invariant:
    ``t`` never raises. Logging must never bring processing down, so every
    failure mode (missing key, missing kwarg, malformed template, missing or
    broken language file) degrades visibly in the log and returns something
    meaningful (the raw template, or the key name as a last resort).
"""

import json
import logging
import os
import pathlib

# Module state (all private). Populated by setup() at import time.
_lang: str = "en"
_messages: dict[str, str] = {}
_fallback: dict[str, str] = {}

_logger = logging.getLogger("i18n")


def _load_json(path: pathlib.Path) -> dict:
    """Load a flat {key: template} JSON object from ``path`` as UTF-8."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"language file is not a JSON object: {path}")
    return data


def _resolve_lang_dir() -> pathlib.Path:
    """Find the shared language directory (see module docstring for the order).

    Falls back through the candidates and returns the first that exists; if none
    exists, returns the central ``/lang`` path anyway so the error path logs a
    sensible location (and the service degrades to key names, never crashes).
    """
    env_dir = os.environ.get("CHARTS_LANG_DIR", "").strip()
    here = pathlib.Path(__file__).resolve().parent  # .../charts-sync/app
    candidates = [
        pathlib.Path(env_dir) if env_dir else None,
        pathlib.Path("/lang"),           # central read-only mount in the container
        here.parent.parent / "lang",     # <repo>/charts-stack/lang (dev/test tree)
        here / "lang",                   # legacy per-service location / standalone
    ]
    for cand in candidates:
        if cand is not None and cand.is_dir():
            return cand
    return pathlib.Path("/lang")


def setup(lang: str | None = None, lang_dir: str | pathlib.Path | None = None) -> None:
    """(Re)load the language tables.

    ``lang`` / ``lang_dir`` exist only to make the module unit-testable; normal
    callers never pass them (the import-time auto-setup uses ``CHARTS_LANG`` and
    the ``app/lang`` directory next to this file). Idempotent.
    """
    global _lang, _messages, _fallback

    # 1. Resolve the requested language.
    if lang is None:
        lang = os.environ.get("CHARTS_LANG", "")
    lang = (lang or "").strip().lower()

    # 2. Resolve the language directory. Shared central dir in production
    #    (/lang mount); the candidate chain keeps the module portable across
    #    services and runnable from the test tree.
    if lang_dir is None:
        lang_dir = _resolve_lang_dir()
    lang_dir = pathlib.Path(lang_dir)

    # 3. Load en.json as the fallback table. On any failure the service keeps
    #    running; keys then degrade to the key name itself.
    try:
        _fallback = _load_json(lang_dir / "en.json")
    except (OSError, ValueError, json.JSONDecodeError) as e:
        _fallback = {}
        _logger.error("Could not load fallback language 'en' (%s): %s",
                      lang_dir / "en.json", e)

    # 4. Build the active table.
    if lang and lang != "en":
        chosen_path = (lang_dir / f"{lang}.json").resolve()
        # Defensive guard: only load if the resolved path exists AND sits inside
        # lang_dir (the value is operator-set, not request-derived, but this is
        # cheap). ``is_relative_to`` is available on Python 3.9+.
        inside = False
        try:
            inside = chosen_path.is_relative_to(lang_dir.resolve())
        except (OSError, ValueError):
            inside = False

        if inside and chosen_path.exists():
            try:
                chosen = _load_json(chosen_path)
                # Chosen overlays English, so a key missing from <lang>.json
                # transparently shows the English text (per-key fallback).
                _messages = {**_fallback, **chosen}
                _lang = lang
                return
            except (OSError, ValueError, json.JSONDecodeError) as e:
                _logger.warning("Could not load language '%s' (%s): using English: %s",
                                lang, chosen_path, e)
                _messages = _fallback
                _lang = "en"
                return
        else:
            # Non-empty but unknown language code (e.g. a typo): one info log,
            # fall back to English. A typo must never crash a background service.
            _logger.info("Language '%s' not found, using English.", lang)
            _messages = _fallback
            _lang = "en"
            return

    # 5. en / empty -> English active table.
    _messages = _fallback
    _lang = "en"


def _log_format_error(key: str, raw: str, kwargs: dict) -> None:
    """Log a single warning when str.format fails; never raises."""
    _logger.warning("i18n format error for key '%s' (template=%r, kwargs=%r): "
                    "returning raw template", key, raw, kwargs)


def t(key: str, **kwargs) -> str:
    """Translate ``key`` to the active language and apply {placeholder} formatting.

    Fallback chain: active language -> English -> the key name itself. On a
    format error (missing kwarg, malformed spec) one warning is logged and the
    raw template is returned. This function NEVER raises.
    """
    raw = _messages.get(key)
    if raw is None:
        raw = _fallback.get(key)
    if raw is None:
        return key
    try:
        return raw.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        _log_format_error(key, raw, kwargs)
        return raw


# Import-time auto-setup: the first ``from .i18n import t`` populates the tables
# using CHARTS_LANG + app/lang/. Tests re-invoke setup(...) with explicit args.
setup()
