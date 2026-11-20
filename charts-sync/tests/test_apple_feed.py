import httpx
import pytest

from app import apple, config, deezer, energy_feed


def _payload():
    return {"feed": {"results": [
        {"artistName": " Artist ", "name": " Shared "},
        {"artistName": "Other", "name": "Apple only"},
        {"artistName": "Blocked", "name": "Excluded"},
    ]}}


@pytest.fixture
def retry_delays(monkeypatch):
    delays = []
    monkeypatch.setattr("time.sleep", delays.append)
    return delays


def test_transient_apple_failures_retry_and_keep_the_same_feed(retry_delays, caplog):
    calls = []

    def handle(request):
        calls.append(str(request.url))
        if len(calls) == 1:
            raise httpx.ReadTimeout("test timeout", request=request)
        if len(calls) == 2:
            return httpx.Response(502)
        return httpx.Response(200, json=_payload())

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        tracks = apple.fetch_feed(config.Feed("apple", 2, country="ch"), client)

    assert len(calls) == 3
    assert len(set(calls)) == 1
    assert retry_delays == [1, 2]
    assert [(track.artist, track.title) for track in tracks] == [
        ("Artist", "Shared"), ("Other", "Apple only")]
    assert len(caplog.records) == 2


def test_persistent_apple_timeout_stops_after_three_attempts(retry_delays):
    calls = []

    def handle(request):
        calls.append(request)
        raise httpx.ReadTimeout("test timeout", request=request)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(httpx.ReadTimeout):
            apple.fetch_feed(config.Feed("apple", 100, country="ch"), client)

    assert len(calls) == 3
    assert retry_delays == [1, 2]


@pytest.mark.parametrize("status", [400, 403, 404, 429])
def test_apple_client_errors_are_not_retried(status, retry_delays):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(status)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            apple.fetch_feed(config.Feed("apple", 100, country="ch"), client)

    assert len(calls) == 1
    assert retry_delays == []


def test_fetch_apple_uses_public_country_chart_and_limit():
    def handle(request):
        assert str(request.url) == (
            "https://rss.marketingtools.apple.com/api/v2/ch/music/most-played/100/songs.json")
        return httpx.Response(200, json=_payload())

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        tracks = apple.fetch_feed(config.Feed("apple", 2, country="ch"), client)

    assert [(t.position, t.artist, t.title, t.deezer_id) for t in tracks] == [
        (1, "Artist", "Shared", 0), (2, "Other", "Apple only", 0)]


@pytest.mark.parametrize("payload", [
    {}, {"feed": {"results": []}}, {"feed": {"results": {}}},
    {"feed": {"results": [{"artistName": "", "name": "Title"}]}},
    {"feed": {"results": [{"artistName": "Artist", "name": 42}]}},
])
def test_invalid_apple_payload_is_an_explicit_error(payload):
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=payload)
    )) as client:
        with pytest.raises(RuntimeError):
            apple.fetch_feed(config.Feed("apple", 100, country="ch"), client)


def test_apple_http_failure_is_not_an_empty_success(retry_delays):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(503)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            apple.fetch_feed(config.Feed("apple", 100, country="ch"), client)
    assert len(calls) == 3
    assert retry_delays == [1, 2]


@pytest.mark.parametrize("country,limit", [("../ch", 100), ("ch", 0), ("ch", 101)])
def test_invalid_direct_feed_does_not_make_a_request(country, limit):
    def unexpected_request(request):
        pytest.fail("Invalid feed reached the network")

    with httpx.Client(transport=httpx.MockTransport(unexpected_request)) as client:
        with pytest.raises(ValueError):
            apple.fetch_feed(config.Feed("apple", limit, country=country), client)


def test_apple_merges_after_deezer_before_energy_and_counts_contribution(monkeypatch, caplog):
    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=_payload())),
        **kwargs))
    monkeypatch.setattr(deezer, "_fetch_deezer_feed", lambda feed, client: [
        deezer.ChartTrack(1, "Artist", "Shared", deezer_id=12)])
    monkeypatch.setattr(energy_feed, "load", lambda theme: [
        deezer.ChartTrack(1, "Other", "Apple only"),
        deezer.ChartTrack(2, "Radio", "Energy only")])
    monkeypatch.setattr(config, "GENRE_FILTER", True)
    source = config.ChartSource([
        config.Feed("energy", 100, theme="charts"),
        config.Feed("deezer", 100),
        config.Feed("apple", 100, country="ch"),
    ], "charts.m3u", "Test", "missing.txt", 100,
        exclude_artists={"blocked"}, genre_filter=False)

    with caplog.at_level("INFO"):
        tracks = deezer.fetch_chart(source)

    assert [(t.position, t.artist, t.title) for t in tracks] == [
        (1, "Artist", "Shared"), (2, "Other", "Apple only"),
        (3, "Radio", "Energy only")]
    assert tracks[0].deezer_id == 12
    assert source.last_stats["apple_new"] == 1
    assert source.last_stats["deezer_new"] == 1
    assert source.last_stats["energy_new"] == 1
    assert source.last_stats["genre_filtered"] == 0
    assert any("apple:" in record.message for record in caplog.records)
