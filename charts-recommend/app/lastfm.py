"""
Last.fm recommendation client (optional, usable right away with an existing
API key).

Two recommendation modes (selectable via config, both need only the API key -
no OAuth):

  1. "top" (user.getTopTracks): the most-played tracks of a Last.fm user.
     Needs a Last.fm USERNAME (LASTFM_USER) with listening history. This is
     closest to "your rotation".

  2. "similar" (track.getSimilar): songs similar to seed songs (your top tracks
     or a configured seed list). Yields discoveries in the style of what you
     listen to - even WITHOUT Last.fm listening history when seeds are given.

All queries return only metadata (artist + title).
"""

import logging
import time

import httpx

from . import config
from .errors import FetchError
from .i18n import t

logger = logging.getLogger(__name__)

_BASE_URL = "https://ws.audioscrobbler.com/2.0/"


def _get(client: httpx.Client, params: dict) -> dict:
    # GET with retry on transport errors (timeout/HTTP/connection).
    # Only when the last attempt fails does the httpx.HTTPError propagate
    # (translated into a FetchError above). Last.fm error payloads
    # (payload["error"]) are NOT transport errors and are not retried.
    attempts = max(1, config.HTTP_RETRIES)
    resp = None
    for attempt in range(1, attempts + 1):
        try:
            resp = client.get(_BASE_URL, params={**params, "format": "json"})
            resp.raise_for_status()
            break
        except httpx.HTTPError as e:
            if attempt >= attempts:
                raise
            backoff = min(2 ** (attempt - 1), 8)
            logger.warning(t("recommend.lastfm.retry", attempt=attempt,
                             attempts=attempts, error=e, backoff=backoff))
            time.sleep(backoff)
    payload = resp.json()
    if isinstance(payload, dict) and payload.get("error"):
        raise RuntimeError(t("recommend.lastfm.api_error_raise",
                             error=payload.get('error'),
                             message=payload.get('message', '')).strip())
    return payload


def get_top_tracks(user: str, api_key: str, limit: int,
                   client: httpx.Client) -> list[tuple[int, str, str]]:
    """Most-played tracks of a Last.fm user -> (pos, artist, title)."""
    payload = _get(client, {
        "method": "user.gettoptracks", "user": user, "api_key": api_key,
        "limit": min(max(1, limit), 1000), "period": "3month",
    })
    container = payload.get("toptracks") or {}
    items = container.get("track", []) if isinstance(container, dict) else []
    if isinstance(items, dict):
        items = [items]
    out: list[tuple[int, str, str]] = []
    for i, it in enumerate(items, start=1):
        title = (it.get("name") or "").strip()
        artist = it.get("artist") or {}
        artist_name = (artist.get("name") if isinstance(artist, dict) else artist) or ""
        artist_name = artist_name.strip()
        if title and artist_name:
            out.append((i, artist_name, title))
    return out


def get_similar_tracks(seed_artist: str, seed_title: str, api_key: str,
                       limit: int, client: httpx.Client) -> list[tuple[str, str]]:
    """Tracks similar to a seed song -> [(artist, title), ...]."""
    try:
        payload = _get(client, {
            "method": "track.getsimilar", "artist": seed_artist,
            "track": seed_title, "api_key": api_key,
            "limit": min(max(1, limit), 100), "autocorrect": 1,
        })
    except (httpx.HTTPError, RuntimeError) as e:
        logger.debug(t("recommend.lastfm.similar_failed",
                       artist=seed_artist, title=seed_title, error=e))
        return []
    container = payload.get("similartracks") or {}
    items = container.get("track", []) if isinstance(container, dict) else []
    if isinstance(items, dict):
        items = [items]
    out: list[tuple[str, str]] = []
    for it in items:
        title = (it.get("name") or "").strip()
        artist = it.get("artist") or {}
        artist_name = (artist.get("name") if isinstance(artist, dict) else artist) or ""
        artist_name = artist_name.strip()
        if title and artist_name:
            out.append((artist_name, title))
    return out


def fetch_recommendations(mode: str, user: str, api_key: str,
                          seeds: list[tuple[str, str]],
                          limit: int) -> list[tuple[int, str, str]]:
    """
    Return Last.fm recommendations as (pos, artist, title).

    mode "top":     user.getTopTracks (needs LASTFM_USER with listening history).
    mode "similar": track.getSimilar based on the seeds (or, if user is set, on
                    that user's top tracks as seeds). Deduplicated.
    """
    if not api_key:
        logger.info(t("recommend.lastfm.no_api_key"))
        return []

    try:
        with httpx.Client(timeout=config.HTTP_TIMEOUT, follow_redirects=True) as client:
            if mode == "top":
                if not user:
                    logger.info(t("recommend.lastfm.top_no_user"))
                    return []
                return get_top_tracks(user, api_key, limit, client)

            # mode == "similar"
            seed_list = list(seeds)
            if not seed_list and user:
                # Derive seeds from the user's top tracks.
                seed_list = [(a, t) for _p, a, t in
                             get_top_tracks(user, api_key, 20, client)]
            if not seed_list:
                logger.info(t("recommend.lastfm.similar_no_seeds"))
                return []

            seen: set[str] = set()
            out: list[tuple[int, str, str]] = []
            pos = 1
            for s_artist, s_title in seed_list:
                for artist, title in get_similar_tracks(
                        s_artist, s_title, api_key, 30, client):
                    key = f"{artist.lower()}|{title.lower()}"
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append((pos, artist, title))
                    pos += 1
                    if len(out) >= limit:
                        return out
            return out
    except httpx.HTTPError as e:
        # Transport error AFTER all retries -> report upward as an error
        # (previously [] was returned silently here). An empty but successful
        # result (no exception) remains NOT an error.
        logger.error(t("recommend.lastfm.api_error", error=e))
        raise FetchError(t("recommend.lastfm.fetch_failed", error=e)) from e
