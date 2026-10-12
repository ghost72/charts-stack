"""
Unit tests for the HITPARADE_ENABLED switch (config._get_enabled) and the
idle guard in main._guarded_run.

Covered:
  - _get_enabled value table (empty -> default, true/1/yes/on -> True,
    false/0/no/off -> False, unrecognized value -> False + WARN, case).
  - _guarded_run with HITPARADE_ENABLED=False -> run_once is NOT called and
    status.finish writes "idle" (checked against a tmp STATUS_DIR).
  - HITPARADE_ENABLED=True -> the guard does not trigger, run_once is called.
"""

import json
import os
import pathlib

import pytest

from app import config, i18n, main, status


# Central shipped language files (app -> charts-hitparade -> charts-stack/lang).
_LANG_DIR = pathlib.Path(i18n.__file__).resolve().parent.parent.parent / "lang"


@pytest.fixture
def de_lang():
    """Set the active language to German (the idle/WARN messages check the
    German texts). Restore the import state after the test."""
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
    ("TRUE", False, True),   # case does not matter
    ("False", True, False),
    ("On", False, True),
    ("maybe", True, False),  # unrecognized -> False (+ WARN)
    ("maybe", False, False),
])
def test_get_enabled(monkeypatch, raw, default, expected):
    if raw is None:
        monkeypatch.delenv("HITPARADE_ENABLED", raising=False)
    else:
        monkeypatch.setenv("HITPARADE_ENABLED", raw)
    assert config._get_enabled("HITPARADE_ENABLED", default) is expected


def test_get_enabled_warns_on_garbage(monkeypatch, caplog, de_lang):
    monkeypatch.setenv("HITPARADE_ENABLED", "maybe")
    with caplog.at_level("WARNING"):
        assert config._get_enabled("HITPARADE_ENABLED", True) is False
    assert any("nicht verstanden" in r.message for r in caplog.records)


# --- _guarded_run idle guard ----------------------------------------------

def _read_status(status_dir: str) -> dict:
    with open(os.path.join(status_dir, "charts-hitparade.json"), encoding="utf-8") as fh:
        return json.load(fh)


def test_guarded_run_disabled_skips(monkeypatch, tmp_path, de_lang):
    """ENABLED=False -> run_once NOT called, status 'idle'."""
    called = []
    monkeypatch.setattr(config, "HITPARADE_ENABLED", False)
    monkeypatch.setattr(main, "run_once", lambda: called.append(True))
    monkeypatch.setattr(status, "STATUS_DIR", str(tmp_path))
    status.init("charts-hitparade")

    main._guarded_run()

    assert called == []                 # run_once was NOT called
    data = _read_status(str(tmp_path))
    assert data["status"] == "idle"
    assert data["message"] == "deaktiviert"
    assert "last_success" not in data   # idle is not a success


def test_guarded_run_enabled_calls_run_once(monkeypatch, tmp_path):
    """ENABLED=True -> guard does not trigger, run_once is called."""
    called = []
    monkeypatch.setattr(config, "HITPARADE_ENABLED", True)
    monkeypatch.setattr(main, "run_once",
                        lambda: called.append(True) or {"songs": 3})
    monkeypatch.setattr(status, "STATUS_DIR", str(tmp_path))
    status.init("charts-hitparade")

    main._guarded_run()

    assert called == [True]             # run_once was called
    data = _read_status(str(tmp_path))
    assert data["status"] == "ok"
