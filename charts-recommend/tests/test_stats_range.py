"""
Tests for the ListenBrainz stats-range handling in charts-recommend:
empty / invalid bodies, the period name in the Pushover source text and the
startup warning for unknown ranges. HTTP is mocked, NO network.
"""
import logging
import pathlib
import sys
import httpx
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from app import config, i18n, listenbrainz, main
_LANG_DIR = pathlib.Path(main.__file__).resolve().parent.parent.parent / "lang"
def _mock_get(monkeypatch, status=200, content=b"", json_body=None):
    """Make listenbrainz._get_with_retry return a canned response."""
    if json_body is not None:
        resp = httpx.Response(status, json=json_body)
    else:
        resp = httpx.Response(status, content=content)
    monkeypatch.setattr(listenbrainz, "_get_with_retry",
                        lambda client, url, **kw: resp)
def _capture(monkeypatch):
    captured: dict[str, str] = {}
    monkeypatch.setattr(main.notify, "send",
                        lambda title, message: captured.update(message=message))
    return captured
def test_empty_body_200_returns_empty(monkeypatch, caplog):
    i18n.setup(lang="en", lang_dir=_LANG_DIR)
    _mock_get(monkeypatch, 200, b"")
    with caplog.at_level(logging.INFO):
        assert listenbrainz.fetch_stats_recordings("u", "", "year", 100) == []
    assert "empty response body" in caplog.text
def test_invalid_json_returns_empty_with_warning(monkeypatch, caplog):
    i18n.setup(lang="en", lang_dir=_LANG_DIR)
    _mock_get(monkeypatch, 200, b"<html>nope</html>")
    with caplog.at_level(logging.INFO):
        assert listenbrainz.fetch_stats_recordings("u", "", "month", 100) == []
    rec = [r for r in caplog.records if "not valid JSON" in r.getMessage()]
    assert rec and rec[0].levelno == logging.WARNING
def test_valid_payload_parsed_and_period_logged(monkeypatch, caplog):
    i18n.setup(lang="en", lang_dir=_LANG_DIR)
    body = {"payload": {"from_ts": 1788220800, "to_ts": 1790812800,
                        "last_updated": 1790000000,
                        "recordings": [{"artist_name": "A", "track_name": "T"}]}}
    _mock_get(monkeypatch, 200, json_body=body)
    with caplog.at_level(logging.INFO):
        out = listenbrainz.fetch_stats_recordings("u", "", "this_month", 100)
    assert out == [(1, "A", "T")]
    assert "range 'this_month'" in caplog.text
    assert "period 2026-" in caplog.text
def test_http_error_still_raises_fetch_error(monkeypatch):
    i18n.setup(lang="en", lang_dir=_LANG_DIR)
    def _boom(client, url, **kw):
        req = httpx.Request("GET", url)
        raise httpx.HTTPStatusError("boom", request=req,
                                    response=httpx.Response(500, request=req))
    monkeypatch.setattr(listenbrainz, "_get_with_retry", _boom)
    try:
        listenbrainz.fetch_stats_recordings("u", "", "month", 100)
    except main.FetchError:
        return
    raise AssertionError("FetchError expected")
def test_range_in_source_text_single_user(monkeypatch):
    i18n.setup(lang="en", lang_dir=_LANG_DIR)
    captured = _capture(monkeypatch)
    main._notify(
        source_results=[
            {"user": "ghost72", "source": "stats", "n": 100, "range": "this_month"},
            {"user": "ghost72", "source": "createdfor", "n": 5}],
        counters={"sources": 2, "candidates": 105, "matched": 100, "missing": 5},
        missing=[(1, "A", "T")])
    msg = captured["message"]
    assert "ghost72 (stats this_month 100, createdfor 5)" in msg
def test_range_in_source_text_multiple_users(monkeypatch):
    i18n.setup(lang="en", lang_dir=_LANG_DIR)
    captured = _capture(monkeypatch)
    main._notify(
        source_results=[
            {"user": "u1", "source": "stats", "n": 10, "range": "week"},
            {"user": "u2", "source": "stats", "n": 20, "range": "week"}],
        counters={"sources": 2, "candidates": 30, "matched": 30, "missing": 0},
        missing=[])
    msg = captured["message"]
    assert "u1 (stats week 10)" in msg
    assert "u2 (stats week 20)" in msg
def test_unknown_range_logs_warning(monkeypatch, caplog):
    i18n.setup(lang="en", lang_dir=_LANG_DIR)
    monkeypatch.setattr(config, "RECOMMEND_STATS_RANGE", "monht")
    with caplog.at_level(logging.WARNING):
        main._warn_unknown_stats_range()
    assert any(r.levelno == logging.WARNING and "monht" in r.getMessage()
               for r in caplog.records)
def test_known_ranges_log_no_warning(monkeypatch, caplog):
    i18n.setup(lang="en", lang_dir=_LANG_DIR)
    for value in config.STATS_RANGES:
        monkeypatch.setattr(config, "RECOMMEND_STATS_RANGE", value)
        with caplog.at_level(logging.WARNING):
            main._warn_unknown_stats_range()
    assert caplog.records == []
