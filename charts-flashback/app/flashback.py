"""
FLASHBACK FM GraphQL client (LastPlayed).

Data source (public, no token/cookie, CORS open):
  GET https://www.flashbackfm.ch/api/pub/gql/flashbackfm/LastPlayed/<HASH>
      ?variables={"streamName":"<stream>","limitTo":<N>}
Response: data.audioPlayer.stream.played[] with {interpret, title, playtime, ...}.

IMPORTANT: the "played" history is only kept for the LIVE MAIN STREAM
(streamName "flashbackfm.ffm"). The thematic decade/artist channels
(flashbackfm-80er, queen, ...) exist as streams but return played=null. That is
why the default stream is the main stream - it plays the whole rotation mixed
across all decades.

The release year sits directly in the title as a suffix "(JJJJ)", e.g.
"THE WILD BOYS (1984)". That is the RELIABLE year source (FLASHBACK itself);
MusicBrainz only serves as a fallback (year.py) when there is no (JJJJ) in the
title. parse_title() splits off the year and returns the cleaned title.

Error handling (watchdog-relevant): a GraphQL "errors" block, a missing
data.audioPlayer.stream.played structure (played=null on the wrong stream/hash)
OR a transport error after all retries are reported as a FetchError (= dead
hash / API changed -> status=error -> dashboard red + Pushover). An EMPTY played
list ([]) is a valid success.
"""

import json
import logging
import re
import time

import httpx

from . import config
from .errors import FetchError
from .i18n import t

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.flashbackfm.ch/api/pub/gql/flashbackfm/LastPlayed"

# Year suffix at the title end: "THE WILD BOYS (1984)" -> title + year.
_TITLE_YEAR_RE = re.compile(r"^(.*\S)\s*\((\d{4})\)\s*$")


def parse_title(raw_title: str) -> tuple[str, str]:
    """
    Split off an optional '(JJJJ)' year suffix from the title.
    Returns: (cleaned_title, year) - year "" when there is no plausible
    4-digit year at the end.
    """
    title = (raw_title or "").strip()
    m = _TITLE_YEAR_RE.match(title)
    if m:
        y = m.group(2)
        # Plausibility (rough): 1900..2099.
        if 1900 <= int(y) <= 2099:
            return m.group(1).strip(), y
    return title, ""


def _get_with_retry(client: httpx.Client, url: str, **kwargs) -> httpx.Response:
    """
    GET with retry on transport errors (timeout/HTTP/connection).
    Mirrors charts-recommend/listenbrainz._get_with_retry.
    """
    attempts = max(1, config.HTTP_RETRIES)
    for attempt in range(1, attempts + 1):
        try:
            resp = client.get(url, **kwargs)
            resp.raise_for_status()
            return resp
        except httpx.HTTPError as e:
            if attempt >= attempts:
                raise
            backoff = min(2 ** (attempt - 1), 8)
            logger.warning(t("flashback.fetch.retry", attempt=attempt,
                             attempts=attempts, error=e, backoff=backoff))
            time.sleep(backoff)
    raise FetchError(t("flashback.err.retry_aborted"))


def _url(hash_: str) -> str:
    return f"{_BASE_URL}/{hash_}"


def _check_graphql_errors(data: dict) -> None:
    """Raise FetchError when the GraphQL response has an errors block."""
    errs = data.get("errors")
    if errs:
        msg = "; ".join(
            (e.get("message") if isinstance(e, dict) else str(e))
            for e in errs
        ) if isinstance(errs, list) else str(errs)
        raise FetchError(t("flashback.err.graphql", msg=msg))


def fetch_played(channel: str, hash_: str,
                 played_limit: int) -> list[tuple[str, str, str]]:
    """
    Fetch the "last played" list of a stream.
    Returns: [(interpret, cleaned_title, year), ...]. year is the release year
    split off from the title suffix "(JJJJ)" (or "").
    An empty played list ([]) is a valid success.
    FetchError on an errors block, played=null (wrong stream/hash), broken
    structure or transport error.
    """
    variables = json.dumps(
        {"streamName": channel, "limitTo": played_limit},
        separators=(",", ":"),
    )
    try:
        with httpx.Client(timeout=config.HTTP_TIMEOUT, follow_redirects=True) as client:
            resp = _get_with_retry(
                client, _url(hash_),
                params={"variables": variables},
                headers={"accept": "application/json"},
            )
            data = resp.json()
    except httpx.HTTPError as e:
        raise FetchError(t("flashback.err.unreachable", error=e)) from e
    except ValueError as e:
        raise FetchError(t("flashback.err.bad_json", error=e)) from e

    _check_graphql_errors(data)

    player = (data.get("data") or {}).get("audioPlayer")
    if not isinstance(player, dict):
        raise FetchError(t("flashback.err.no_audioplayer"))
    stream = player.get("stream")
    if not isinstance(stream, dict):
        raise FetchError(t("flashback.err.no_stream", channel=channel))
    played = stream.get("played")
    # played=null means: this stream keeps no history (only the main stream
    # flashbackfm.ffm does). That is a config/stream error, NOT an empty success
    # -> report it as a FetchError.
    if played is None:
        raise FetchError(t("flashback.err.played_null", channel=channel))

    out: list[tuple[str, str, str]] = []
    for tr in played:
        if not isinstance(tr, dict):
            continue
        interpret = (tr.get("interpret") or "").strip()
        raw_title = (tr.get("title") or "").strip()
        if not interpret or not raw_title:
            continue
        title, year = parse_title(raw_title)
        out.append((interpret, title, year))
    return out


def fetch_streams(hash_: str) -> list[dict]:
    """
    Optional: list of the available channels (data.audioPlayer.streams[]).
    Only for diagnostics/dashboard. FetchError like fetch_played.
    """
    variables = json.dumps(
        {"streamName": config.FLASHBACK_CHANNELS[0] if config.FLASHBACK_CHANNELS
         else "flashbackfm.ffm", "limitTo": 1},
        separators=(",", ":"),
    )
    try:
        with httpx.Client(timeout=config.HTTP_TIMEOUT, follow_redirects=True) as client:
            resp = _get_with_retry(
                client, _url(hash_),
                params={"variables": variables},
                headers={"accept": "application/json"},
            )
            data = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        raise FetchError(t("flashback.err.unreachable", error=e)) from e
    _check_graphql_errors(data)
    player = (data.get("data") or {}).get("audioPlayer") or {}
    streams = player.get("streams") or []
    return [s for s in streams if isinstance(s, dict)]
