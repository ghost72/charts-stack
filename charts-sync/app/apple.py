"""Public Apple Music country charts (metadata only, no account or API key)."""

import logging
import time

import httpx

from .config import Feed
from .deezer import ChartTrack
from .i18n import t

logger = logging.getLogger(__name__)


def fetch_feed(feed: Feed, client: httpx.Client) -> list[ChartTrack]:
    country = feed.country
    if (len(country) != 2 or not country.isascii() or not country.isalpha()
            or not 1 <= feed.limit <= 100):
        raise ValueError(t("sync.apple.invalid_feed", country=country, limit=feed.limit))
    url = (f"https://rss.marketingtools.apple.com/api/v2/{country}"
           "/music/most-played/100/songs.json")
    attempt = 1
    while True:
        try:
            response = client.get(url)
            response.raise_for_status()
            break
        except (httpx.TransportError, httpx.HTTPStatusError) as error:
            if (attempt >= 3 or
                    (isinstance(error, httpx.HTTPStatusError)
                     and not 500 <= error.response.status_code < 600)):
                raise
            logger.warning(t("sync.apple.retry", country=country,
                             attempt=attempt, attempts=3, error=error,
                             backoff=attempt))
            time.sleep(attempt)
            attempt += 1
    payload = response.json()
    chart = payload.get("feed") if isinstance(payload, dict) else None
    items = chart.get("results") if isinstance(chart, dict) else None
    if not isinstance(items, list) or not items:
        raise RuntimeError(t("sync.apple.bad_payload", country=country))

    tracks = []
    for item in items[:feed.limit]:
        artist = item.get("artistName") if isinstance(item, dict) else None
        title = item.get("name") if isinstance(item, dict) else None
        if (not isinstance(artist, str) or not artist.strip()
                or not isinstance(title, str) or not title.strip()):
            raise RuntimeError(t("sync.apple.bad_payload", country=country))
        tracks.append(ChartTrack(len(tracks) + 1, artist.strip(), title.strip()))

    logger.info(t("sync.apple.feed_result", country=country, count=len(tracks)))
    return tracks
