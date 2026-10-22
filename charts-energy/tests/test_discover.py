"""
Unit tests for the discovery CLI (app/discover.py).

The static list output needs no network; the probe is stubbed (no real HTTP).
"""

import httpx
import pytest

from app import discover


# --- list ------------------------------------------------------------------

def test_list_prints_confirmed_channels(capsys):
    discover.list_channels()
    out = capsys.readouterr().out
    # A couple of confirmed slugs appear in the table.
    assert "energymadeinswitzerland" in out
    assert "energypop" in out
    # One line per channel (plus two header lines).
    assert out.strip().count("\n") >= len(discover.CONFIRMED_CHANNELS)


def test_main_defaults_to_list(capsys):
    rc = discover.main([])
    assert rc == 0
    assert "energypop" in capsys.readouterr().out


# --- probe (stubbed) -------------------------------------------------------

class _FakeResponse:
    def __init__(self, status_code, headers):
        self.status_code = status_code
        self.headers = headers


class _FakeClient:
    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, headers=None):
        if self._exc:
            raise self._exc
        return self._response


def _patch_client(monkeypatch, response=None, exc=None):
    monkeypatch.setattr(httpx, "Client",
                        lambda *a, **k: _FakeClient(response=response, exc=exc))


def test_probe_real_channel(monkeypatch, capsys):
    _patch_client(monkeypatch,
                  response=_FakeResponse(200, {"icy-name": "Energy Pop"}))
    rc = discover.probe("energypop")
    assert rc == 0
    # Something is printed (the exact text depends on the energy.* lang keys
    # added by FEAT-003; here we only assert a non-empty line + the exit code).
    assert capsys.readouterr().out.strip() != ""


def test_probe_not_a_channel(monkeypatch, capsys):
    _patch_client(monkeypatch, response=_FakeResponse(404, {}))
    rc = discover.probe("energynope")
    assert rc != 0


def test_probe_unreachable(monkeypatch, capsys):
    _patch_client(monkeypatch, exc=httpx.ConnectError("boom"))
    rc = discover.probe("energypop")
    assert rc != 0
