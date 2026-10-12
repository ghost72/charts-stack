"""
Last.fm chart client.

Last.fm provides the AudioScrobbler web API (2.0). For reading chart, geo, tag
and user queries a free API key is enough (no OAuth, no signature). We fetch
metadata only (artist + title), no audio data.

Methods used (all format=json):
  - chart.getTopTracks              global top tracks (listener/scrobble-based)
  - geo.getTopTracks   country=..   country top tracks (e.g. Switzerland)
  - tag.getTopTracks   tag=..       most-tagged tracks of a tag (e.g. pop, 80s)
  - user.getTopTracks  user=..      most-played tracks of a user (period=..)
  - track.getTopTags   artist,track genre tags of a song (for the genre filter)

The track objects are deliberately the same as in the Deezer client
(ChartTrack) so main.py/the matching works independently of the source.
"""

import logging

import httpx

from .deezer import ChartTrack
from .i18n import t

logger = logging.getLogger(__name__)

_BASE_URL = "https://ws.audioscrobbler.com/2.0/"

# Last.fm returns at most ~1000 entries per response; we paginate in fixed
# steps via the 'page' parameter.
_PAGE_SIZE = 200
_MAX_TRACKS = 100000

# Methods for which the respective required extra parameter is passed along.
# key = Last.fm method, value = (param name for extra, required)
_METHOD_PARAM = {
    "chart.gettoptracks": (None, False),
    "geo.gettoptracks": ("country", True),
    "tag.gettoptracks": ("tag", True),
    "user.gettoptracks": ("user", True),
}


def _tracks_from_payload(payload: dict) -> list[dict]:
    """Extract the track list from the different response wrappers."""
    if not isinstance(payload, dict):
        return []
    # The wrapper is named 'tracks' or 'toptracks' depending on the method.
    container = payload.get("tracks") or payload.get("toptracks") or {}
    items = container.get("track", []) if isinstance(container, dict) else []
    # With exactly one hit Last.fm returns an object instead of a list.
    if isinstance(items, dict):
        items = [items]
    return items


def _parse_tracks(items: list[dict], start_pos: int) -> list[ChartTrack]:
    """Convert Last.fm JSON entries into ChartTrack objects."""
    tracks: list[ChartTrack] = []
    for offset, item in enumerate(items):
        name = (item.get("name") or "").strip()
        artist = item.get("artist") or {}
        # 'artist' is an object {name, mbid, url} for getTopTracks.
        artist_name = (artist.get("name") if isinstance(artist, dict) else artist) or ""
        artist_name = artist_name.strip()
        if not artist_name or not name:
            continue
        # Chart/geo tracks provide @attr.rank; otherwise a running number.
        rank = 0
        attr = item.get("@attr")
        if isinstance(attr, dict):
            try:
                rank = int(attr.get("rank", 0))
            except (TypeError, ValueError):
                rank = 0
        position = rank or (start_pos + offset)
        tracks.append(ChartTrack(position, artist_name, name))
    return tracks


def _fetch_lastfm_feed(method: str, extra: dict, limit: int, api_key: str,
                       client: httpx.Client) -> list[ChartTrack]:
    """
    Fetch ONE Last.fm source (chart/geo/tag/user getTopTracks) with pagination.
    'method' is the Last.fm method (lowercased), 'extra' holds the
    method-specific extra parameters (country/tag/user/period).
    """
    method = method.lower()
    if method not in _METHOD_PARAM:
        raise ValueError(t("sync.lastfm.unknown_method", method=method))

    target = min(max(1, limit), _MAX_TRACKS)
    tracks: list[ChartTrack] = []
    page = 1

    while len(tracks) < target:
        want = min(_PAGE_SIZE, target - len(tracks))
        params = {
            "method": method,
            "api_key": api_key,
            "format": "json",
            "limit": want,
            "page": page,
        }
        params.update(extra)

        resp = client.get(_BASE_URL, params=params)
        resp.raise_for_status()
        payload = resp.json()

        # Last.fm reports errors as {"error": <code>, "message": ".."} (HTTP 200).
        if isinstance(payload, dict) and payload.get("error"):
            raise RuntimeError(t("sync.lastfm.api_error", method=method,
                                 error=payload.get('error'),
                                 message=payload.get('message', '')).strip())

        items = _tracks_from_payload(payload)
        if not items:
            break

        tracks.extend(_parse_tracks(items, start_pos=len(tracks) + 1))

        if len(items) < want:
            break  # last page reached
        page += 1

    logger.info(t("lastfm.feed.result",
                  method=method,
                  extra=f" ({extra})" if extra else "",
                  count=len(tracks)))
    return tracks[:target]


def fetch_feed(feed, api_key: str, client: httpx.Client) -> list[ChartTrack]:
    """
    Adapter for a feed of type 'lastfm'. Expects a Feed object with the
    attributes: method, params (dict with country/tag/user/period), limit.
    """
    if not api_key:
        raise RuntimeError(t("sync.lastfm.no_api_key"))
    return _fetch_lastfm_feed(feed.method, dict(feed.params or {}),
                              feed.limit, api_key, client)


def get_top_tags(artist: str, title: str, api_key: str,
                 client: httpx.Client) -> list[tuple[str, int]]:
    """
    Return a song's genre/top tags as [(tag_lowercase, count), ...], descending
    by count. For the genre exclusion filter.

    Returns an empty list on errors / without tags (fail-open: the song is then
    NOT excluded).
    """
    params = {
        "method": "track.gettoptags",
        "artist": artist,
        "track": title,
        "api_key": api_key,
        "format": "json",
        "autocorrect": 1,
    }
    try:
        resp = client.get(_BASE_URL, params=params)
        resp.raise_for_status()
        payload = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        logger.debug(t("lastfm.toptags.failed", artist=artist, title=title, error=e))
        return []

    if not isinstance(payload, dict) or payload.get("error"):
        return []

    tags = (payload.get("toptags") or {}).get("tag", [])
    if isinstance(tags, dict):
        tags = [tags]

    result: list[tuple[str, int]] = []
    for tag in tags:
        name = (tag.get("name") or "").strip().lower()
        if not name:
            continue
        try:
            count = int(tag.get("count", 0))
        except (TypeError, ValueError):
            count = 0
        result.append((name, count))
    result.sort(key=lambda x: x[1], reverse=True)
    return result
