"""
Unit tests for the FLASHBACK_ENABLED switch (config._get_enabled) and the two
idle guards in main._guarded_run.

Covered:
  - _get_enabled value table (empty -> default, true/1/yes/on -> True,
    false/0/no/off -> False, unrecognized value -> False + WARN, upper/lower).
  - FLASHBACK_ENABLED=False -> run_once is NOT called, status "idle"
    ("disabled"), checked against a tmp STATUS_DIR.
  - FLASHBACK_ENABLED=True + empty FLASHBACK_HASH -> run_once NOT called, status
    "idle" with message "no FLASHBACK_HASH ...".
  - FLASHBACK_ENABLED=True + hash set -> the guard does not kick in, run_once is
    called.
"""

import json
import os
import pathlib

import pytest

from app import config, i18n, main, status


# Central, shipped language files (app -> charts-flashback -> charts-stack/lang).
_LANG_DIR = pathlib.Path(i18n.__file__).resolve().parent.parent.parent / "lang"


@pytest.fixture
def de_lang():
    """Set the active language to German (idle messages check the German
    status texts). Restore the import state after the test."""
    i18n.setup(lang="de", lang_dir=_LANG_DIR)
    yield
    i18n.setup()


# --- _get_enabled ---------------------------------------------------------

@pytest.mark.parametrize("raw,default,expected", [
    (None, True, True),      # not set -> default
    (None, False, False),
    ("", True, True),        # empty -> default
    ("", False, False),
    ("true", False, True),
    ("1", False, True),
    ("yes", False, True),
    ("on", False, True),
    ("false", True, False),
    ("0", True, False),
    ("no", True, False),
    ("off", True, False),
    ("TRUE", False, True),   # upper/lower irrelevant
    ("False", True, False),
    ("On", False, True),
    ("maybe", True, False),  # unrecognized -> False (+ WARN)
    ("maybe", False, False),
])
def test_get_enabled(monkeypatch, raw, default, expected):
    if raw is None:
        monkeypatch.delenv("FLASHBACK_ENABLED", raising=False)
    else:
        monkeypatch.setenv("FLASHBACK_ENABLED", raw)
    assert config._get_enabled("FLASHBACK_ENABLED", default) is expected


def test_get_enabled_warns_on_garbage(monkeypatch, caplog, de_lang):
    monkeypatch.setenv("FLASHBACK_ENABLED", "maybe")
    with caplog.at_level("WARNING"):
        assert config._get_enabled("FLASHBACK_ENABLED", True) is False
    assert any("nicht verstanden" in r.message for r in caplog.records)


# --- _guarded_run idle guards ---------------------------------------------

def _read_status(status_dir: str) -> dict:
    with open(os.path.join(status_dir, "charts-flashback.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _spy_run_once(monkeypatch, called):
    monkeypatch.setattr(
        main, "run_once",
        lambda: called.append(True) or ({"channels": 0, "new": 0,
                                         "matched": 0, "missing": 0}, [], 0, 0))


def test_guarded_run_disabled_skips(monkeypatch, tmp_path, de_lang):
    """ENABLED=False -> run_once NOT called, status 'idle'/'disabled'."""
    called = []
    monkeypatch.setattr(config, "FLASHBACK_ENABLED", False)
    monkeypatch.setattr(config, "FLASHBACK_HASH", "deadbeef")
    _spy_run_once(monkeypatch, called)
    monkeypatch.setattr(status, "STATUS_DIR", str(tmp_path))
    status.init("charts-flashback")

    main._guarded_run()

    assert called == []
    data = _read_status(str(tmp_path))
    assert data["status"] == "idle"
    assert data["message"] == "deaktiviert"
    assert "last_success" not in data


def test_guarded_run_enabled_empty_hash_idle(monkeypatch, tmp_path, de_lang):
    """ENABLED=True + empty hash -> run_once NOT called, status 'idle'."""
    called = []
    monkeypatch.setattr(config, "FLASHBACK_ENABLED", True)
    monkeypatch.setattr(config, "FLASHBACK_HASH", "")
    _spy_run_once(monkeypatch, called)
    monkeypatch.setattr(status, "STATUS_DIR", str(tmp_path))
    status.init("charts-flashback")

    main._guarded_run()

    assert called == []
    data = _read_status(str(tmp_path))
    assert data["status"] == "idle"
    assert "kein FLASHBACK_HASH" in data["message"]
    assert "last_success" not in data


def test_guarded_run_enabled_with_hash_calls_run_once(monkeypatch, tmp_path):
    """ENABLED=True + hash set -> the guard does not kick in, run_once runs."""
    called = []
    monkeypatch.setattr(config, "FLASHBACK_ENABLED", True)
    monkeypatch.setattr(config, "FLASHBACK_HASH", "deadbeef")
    _spy_run_once(monkeypatch, called)
    # _prev_status reads status.py -> point it at tmp.
    monkeypatch.setattr(status, "STATUS_DIR", str(tmp_path))
    status.init("charts-flashback")

    main._guarded_run()

    assert called == [True]
    data = _read_status(str(tmp_path))
    # attempts==0 -> existing branch writes "ok"/"nothing to do" (untouched).
    assert data["status"] == "ok"
