"""
Unit tests for the discovery CLI (app/discover.py).

The static list output needs no network; the probe is stubbed (no real HTTP).
"""

from contextlib import contextmanager

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

    @contextmanager
    def stream(self, method, url, headers=None):
        if self._exc:
            raise self._exc
        yield self._response


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


# --- probe_result (pure bool, no printing) ---------------------------------

def test_probe_result_true_on_200_with_icy_name(monkeypatch):
    _patch_client(monkeypatch,
                  response=_FakeResponse(200, {"icy-name": "Energy Pop"}))
    assert discover.probe_result("energypop") is True


def test_probe_result_false_on_404(monkeypatch):
    _patch_client(monkeypatch, response=_FakeResponse(404, {}))
    assert discover.probe_result("energynope") is False


def test_probe_result_false_without_icy_name(monkeypatch):
    _patch_client(monkeypatch, response=_FakeResponse(200, {}))
    assert discover.probe_result("energypop") is False


def test_probe_result_false_on_http_error(monkeypatch):
    _patch_client(monkeypatch, exc=httpx.ConnectError("boom"))
    assert discover.probe_result("energypop") is False


def test_probe_result_does_not_print(monkeypatch, capsys):
    _patch_client(monkeypatch,
                  response=_FakeResponse(200, {"icy-name": "Energy Pop"}))
    discover.probe_result("energypop")
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("probe", [discover.probe, discover.probe_result])
@pytest.mark.parametrize("status,headers,alive", [
    (200, {"icy-name": "Energy Pop"}, True),
    (200, {}, False),
    (404, {}, False),
])
def test_probe_closes_stream_without_reading_audio(monkeypatch, probe,
                                                  status, headers, alive):
    class AudioStream(httpx.SyncByteStream):
        closed = False

        def __iter__(self):
            raise AssertionError("Liveness must not read the continuous audio body")

        def close(self):
            self.closed = True

    stream = AudioStream()

    def handle(request):
        assert request.method == "GET"
        assert request.headers["Icy-MetaData"] == "1"
        return httpx.Response(status, headers=headers, stream=stream)

    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(
        transport=httpx.MockTransport(handle), **kwargs))

    result = probe("energypop")

    if probe is discover.probe:
        assert result == (0 if alive else 1)
    else:
        assert result is alive
    assert stream.closed
