"""
ListenBrainz client (multi-user, download-only).

Reads the PUBLIC listening taste of a ListenBrainz user from two data sources
and returns each as a (position, artist, title) list - source-agnostic, like
the Deezer/Last.fm client in charts-sync. Both sources return artist + title
DIRECTLY; NO MBID resolving happens and the CF recommendation endpoint
(cf/recommendation/user/<user>/recording) is DELIBERATELY NOT used (it only
returns raw recording_mbid + score, whose resolution would need MusicBrainz at
1 req/s or a fragile player endpoint).

Source 1 - "stats" (available immediately, robust):
  GET /1/stats/user/<user>/recordings?count=<limit>&range=<range>
     -> the most-played recordings of a user (artist_name + track_name).

Source 2 - "createdfor" (real discovery, may be empty at first):
  Two-step flow (ListenBrainz API, see
  https://listenbrainz.readthedocs.io/en/latest/users/api/playlist.html):
  1. GET /1/user/<user>/playlists/createdfor
     -> metadata of all "created for you" playlists incl. playlist_mbid. All
        public, no token needed. We pick the desired ones (Weekly Jams / Weekly
        Exploration) by title.
  2. GET /1/playlist/<mbid>
     -> the playlist in JSPF format WITH track metadata (title + creator).
        No separate MusicBrainz lookup needed.

IMPORTANT (ramp-up time): ListenBrainz only generates the createdfor playlists
after a few weeks of listening history. At first createdfor may return nothing -
that is not an error but missing data (the "stats" source has data immediately).
"""

import logging
import time

import httpx

from . import config
from .errors import FetchError
from .i18n import t

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.listenbrainz.org"


def _get_with_retry(client: httpx.Client, url: str, **kwargs) -> httpx.Response:
    """
    GET with retry on transport errors (timeout/HTTP/connection).

    Tries up to config.HTTP_RETRIES times with a short, exponential backoff.
    Only when the last attempt fails does the httpx.HTTPError propagate upward
    (where it is translated into a FetchError).
    """
    attempts = max(1, config.HTTP_RETRIES)
    for attempt in range(1, attempts + 1):
        try:
            resp = client.get(url, **kwargs)
            resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError as e:
            # 4xx client errors (except 429 rate limit) are permanent - a retry
            # does not help (e.g. 404 "user has no data"). Propagate immediately,
            # the caller handles it (404 = empty, not an error).
            code = e.response.status_code
            if 400 <= code < 500 and code != 429:
                raise
            if attempt >= attempts:
                raise
            backoff = min(2 ** (attempt - 1), 8)
            logger.warning(t("recommend.lb.retry", attempt=attempt,
                             attempts=attempts, error=e, backoff=backoff))
            time.sleep(backoff)
        except httpx.HTTPError as e:
            # Transport/timeout/connection error -> worth retrying.
            if attempt >= attempts:
                raise
            backoff = min(2 ** (attempt - 1), 8)
            logger.warning(t("recommend.lb.retry", attempt=attempt,
                             attempts=attempts, error=e, backoff=backoff))
            time.sleep(backoff)
    # Unreachable (the loop ends via return or raise), just for peace of mind:
    raise FetchError(t("recommend.lb.retry_aborted"))


# Title markers to recognize the desired "Created for you" playlists.
# ListenBrainz names them e.g. "Weekly Jams for <username>, week of ..." or
# "Weekly Exploration for <username>, week of ...". We match leniently.
PLAYLIST_KINDS = {
    "jams": ["weekly jams", "daily jams"],
    "exploration": ["weekly exploration", "weekly discovery", "exploration"],
}


def _headers(token: str) -> dict:
    """Authorization header if a token is set (optional for createdfor, but
    does no harm and allows higher rate limits)."""
    return {"Authorization": f"Token {token}"} if token else {}


def _list_created_for(user: str, token: str, client: httpx.Client) -> list[dict]:
    """Fetch the metadata of all 'createdfor' playlists of the user.

    A 404/204 does NOT mean 'error' but 'the user has no createdfor playlists
    (yet)' - ListenBrainz only generates Weekly Jams/Exploration after a few
    weeks of listening history, and a new/empty user returns 404 there. This is
    handled - as with the stats endpoint - as an empty result (no FetchError ->
    dashboard stays green/idle, no Pushover alarm)."""
    url = f"{_BASE_URL}/1/user/{user}/playlists/createdfor"
    try:
        resp = _get_with_retry(client, url, headers=_headers(token), params={"count": 25})
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (404, 204):
            logger.info(t("recommend.lb.createdfor_none", user=user,
                          status=e.response.status_code))
            return []
        raise
    data = resp.json()
    # Response: {"playlists": [{"playlist": {...JSPF...}}, ...], "count": ..}
    out = []
    for wrapper in data.get("playlists", []):
        pl = wrapper.get("playlist") if isinstance(wrapper, dict) else None
        if isinstance(pl, dict):
            out.append(pl)
    return out


def _playlist_identifier(pl: dict) -> str | None:
    """
    Extract the playlist_mbid from a JSPF playlist metadata object.
    JSPF stores it in 'identifier' (a URL ending in .../playlist/<mbid>).
    """
    ident = pl.get("identifier") or ""
    if isinstance(ident, list):
        ident = ident[0] if ident else ""
    if isinstance(ident, str) and "/" in ident:
        return ident.rstrip("/").rsplit("/", 1)[-1]
    return ident or None


def _match_kind(title: str, markers: list[str]) -> bool:
    lowered = (title or "").lower()
    return any(m in lowered for m in markers)


def _fetch_playlist_tracks(mbid: str, token: str,
                           client: httpx.Client) -> list[tuple[int, str, str]]:
    """
    Fetch a playlist by MBID and return (position, artist, title).
    JSPF track: {"title": <song title>, "creator": <artist>, ...}.
    """
    url = f"{_BASE_URL}/1/playlist/{mbid}"
    resp = _get_with_retry(client, url, headers=_headers(token))
    data = resp.json()
    playlist = data.get("playlist", {}) if isinstance(data, dict) else {}
    tracks = playlist.get("track", []) if isinstance(playlist, dict) else []

    out: list[tuple[int, str, str]] = []
    for i, tr in enumerate(tracks, start=1):
        if not isinstance(tr, dict):
            continue
        title = (tr.get("title") or "").strip()
        creator = (tr.get("creator") or "").strip()
        if title and creator:
            out.append((i, creator, title))
    return out


def fetch_recommendations(user: str, token: str, kinds: list[str],
                          limit: int = 100) -> dict[str, list[tuple[int, str, str]]]:
    """
    Fetch the desired recommendation playlists.

    kinds: subset of PLAYLIST_KINDS.keys() ("jams", "exploration").
    Returns: {kind: [(pos, artist, title), ...]}. If a playlist is missing (not
    yet generated), the key is absent or empty.
    """
    result: dict[str, list[tuple[int, str, str]]] = {}
    try:
        with httpx.Client(timeout=config.HTTP_TIMEOUT, follow_redirects=True) as client:
            metas = _list_created_for(user, token, client)
            if not metas:
                logger.info(t("recommend.lb.createdfor_empty", user=user))
                return result

            for kind in kinds:
                markers = PLAYLIST_KINDS.get(kind, [])
                # Pick the newest matching playlist of this kind (the list is
                # usually sorted by date; we take the first match).
                chosen = None
                for pl in metas:
                    title = pl.get("title") or ""
                    if _match_kind(title, markers):
                        chosen = pl
                        break
                if not chosen:
                    logger.info(t("recommend.lb.kind_not_found", kind=kind))
                    continue
                mbid = _playlist_identifier(chosen)
                if not mbid:
                    logger.warning(t("recommend.lb.kind_no_mbid", kind=kind))
                    continue
                tracks = _fetch_playlist_tracks(mbid, token, client)[:limit]
                logger.info(t("recommend.lb.kind_tracks", kind=kind,
                              count=len(tracks), title=chosen.get("title", "")))
                result[kind] = tracks
    except httpx.HTTPError as e:
        # Transport error AFTER all retries -> report upward as an error
        # (previously an empty result was returned silently here, which the
        # caller could not distinguish from "no playlists yet"). An empty but
        # successful result (no exception) is unaffected by this and remains
        # NOT an error.
        logger.error(t("recommend.lb.api_error", error=e))
        raise FetchError(t("recommend.lb.unreachable", error=e)) from e
    return result


def fetch_stats_recordings(user: str, token: str, range_: str,
                           limit: int = 100) -> list[tuple[int, str, str]]:
    """
    Fetch the most-played recordings of a user (stats source).

      GET /1/stats/user/<user>/recordings?count=<limit>&range=<range_>
      -> payload.recordings[].{artist_name, track_name}

    Returns: [(pos, artist, title), ...] - the same 3-tuple form as
    fetch_recommendations and lastfm.fetch_recommendations.

    404/204 (no profile / no data yet) is handled like "no data yet": empty
    list, NO FetchError. Any other HTTP error (after all retries in
    _get_with_retry) is reported as a FetchError.
    Note: since _get_with_retry only raises AFTER the retry loop, a 404 is
    requested HTTP_RETRIES times per run before [] comes back - deliberately
    accepted to keep the 5xx robustness for this endpoint.
    """
    url = f"{_BASE_URL}/1/stats/user/{user}/recordings"
    try:
        with httpx.Client(timeout=config.HTTP_TIMEOUT, follow_redirects=True) as client:
            resp = _get_with_retry(client, url, headers=_headers(token),
                                   params={"count": limit, "range": range_})
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (404, 204):
            logger.info(t("recommend.lb.stats_none", user=user,
                          status=e.response.status_code))
            return []
        logger.error(t("recommend.lb.stats_error", user=user, error=e))
        raise FetchError(t("recommend.lb.stats_unreachable", user=user, error=e)) from e
    except httpx.HTTPError as e:
        logger.error(t("recommend.lb.stats_error", user=user, error=e))
        raise FetchError(t("recommend.lb.stats_unreachable", user=user, error=e)) from e

    payload = (resp.json() or {}).get("payload", {})
    recs = payload.get("recordings", []) or []
    out: list[tuple[int, str, str]] = []
    for i, r in enumerate(recs, start=1):
        if not isinstance(r, dict):
            continue
        artist = (r.get("artist_name") or "").strip()
        title = (r.get("track_name") or "").strip()
        if artist and title:
            out.append((i, artist, title))
    return out
