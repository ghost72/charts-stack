from datetime import date

import httpx

from app import hitparade


def test_fetch_chart_preserves_utf8_titles_from_response(monkeypatch):
    """UTF-8 declared by hitparade.ch must not be reinterpreted as Latin-1."""
    html = (
        '<div class="chart_title"><a href="/song/example">'
        "<b>Ayliva</b><br>F\u00fcr immer</a></div>"
    ).encode("utf-8")
    response = httpx.Response(
        200,
        headers={"content-type": "text/html; charset=utf-8"},
        content=html,
        request=httpx.Request("GET", "https://hitparade.ch/charts/singles/08-11-2026"),
    )
    monkeypatch.setattr(hitparade.httpx, "get", lambda *args, **kwargs: response)

    songs = hitparade.fetch_chart("singles", date(2026, 11, 8), retries=1)

    assert songs == [(1, "Ayliva", "Für immer")]
