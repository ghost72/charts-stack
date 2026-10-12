"""
Unit-Tests fuer den FLASHBACK_ENABLED-Schalter (config._get_enabled) und die
beiden idle-Guards in main._guarded_run.

Gedeckt:
  - _get_enabled-Wertetabelle (leer -> default, true/1/yes/on -> True,
    false/0/no/off -> False, unverstandener Wert -> False + WARN, Gross/Klein).
  - FLASHBACK_ENABLED=False -> run_once wird NICHT aufgerufen, Status "idle"
    ("deaktiviert"), gegen ein tmp-STATUS_DIR geprueft.
  - FLASHBACK_ENABLED=True + leerer FLASHBACK_HASH -> run_once NICHT aufgerufen,
    Status "idle" mit Message "kein FLASHBACK_HASH ...".
  - FLASHBACK_ENABLED=True + gesetzter Hash -> Guard greift nicht, run_once wird
    aufgerufen.
"""

import json
import os
import pathlib

import pytest

from app import config, i18n, main, status


# Zentrale, ausgelieferte Sprachdateien (app -> charts-flashback -> charts-stack/lang).
_LANG_DIR = pathlib.Path(i18n.__file__).resolve().parent.parent.parent / "lang"


@pytest.fixture
def de_lang():
    """Setzt die aktive Sprache auf Deutsch (idle-Messages pruefen die
    deutschen Status-Texte). Nach dem Test den Import-Zustand wiederherstellen."""
    i18n.setup(lang="de", lang_dir=_LANG_DIR)
    yield
    i18n.setup()


# --- _get_enabled ---------------------------------------------------------

@pytest.mark.parametrize("raw,default,expected", [
    (None, True, True),      # nicht gesetzt -> default
    (None, False, False),
    ("", True, True),        # leer -> default
    ("", False, False),
    ("true", False, True),
    ("1", False, True),
    ("yes", False, True),
    ("on", False, True),
    ("false", True, False),
    ("0", True, False),
    ("no", True, False),
    ("off", True, False),
    ("TRUE", False, True),   # Gross/Klein egal
    ("False", True, False),
    ("On", False, True),
    ("maybe", True, False),  # unverstanden -> False (+ WARN)
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


# --- _guarded_run idle-Guards ---------------------------------------------

def _read_status(status_dir: str) -> dict:
    with open(os.path.join(status_dir, "charts-flashback.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _spy_run_once(monkeypatch, called):
    monkeypatch.setattr(
        main, "run_once",
        lambda: called.append(True) or ({"channels": 0, "new": 0,
                                         "matched": 0, "missing": 0}, [], 0, 0))


def test_guarded_run_disabled_skips(monkeypatch, tmp_path, de_lang):
    """ENABLED=False -> run_once NICHT aufgerufen, Status 'idle'/'deaktiviert'."""
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
    """ENABLED=True + leerer Hash -> run_once NICHT aufgerufen, Status 'idle'."""
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
    """ENABLED=True + Hash gesetzt -> Guard greift nicht, run_once laeuft."""
    called = []
    monkeypatch.setattr(config, "FLASHBACK_ENABLED", True)
    monkeypatch.setattr(config, "FLASHBACK_HASH", "deadbeef")
    _spy_run_once(monkeypatch, called)
    # _prev_status liest status.py -> gegen tmp lenken.
    monkeypatch.setattr(status, "STATUS_DIR", str(tmp_path))
    status.init("charts-flashback")

    main._guarded_run()

    assert called == [True]
    data = _read_status(str(tmp_path))
    # attempts==0 -> bestehender Zweig schreibt "ok"/"nichts zu tun" (unberuehrt).
    assert data["status"] == "ok"
