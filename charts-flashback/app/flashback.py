"""
FLASHBACK FM GraphQL-Client (LastPlayed).

Datenquelle (oeffentlich, kein Token/Cookie, CORS offen):
  GET https://www.flashbackfm.ch/api/pub/gql/flashbackfm/LastPlayed/<HASH>
      ?variables={"streamName":"<stream>","limitTo":<N>}
Antwort: data.audioPlayer.stream.played[] mit {interpret, title, playtime, ...}.

WICHTIG: Die "played"-Historie wird nur fuer den LIVE-HAUPTSTREAM gefuehrt
(streamName "flashbackfm.ffm"). Die thematischen Dekaden-/Kuenstler-Channels
(flashbackfm-80er, queen, ...) existieren als streams, liefern aber
played=null. Darum ist der Default-Stream der Hauptstream - er spielt die ganze
Rotation ueber alle Dekaden gemischt.

Das Erscheinungsjahr steht direkt im Titel als Suffix "(JJJJ)", z.B.
"THE WILD BOYS (1984)". Das ist die VERLAESSLICHE Jahr-Quelle (FLASHBACK selbst);
MusicBrainz dient nur noch als Fallback (year.py), falls kein (JJJJ) im Titel
steht. parse_title() spaltet das Jahr ab und liefert den bereinigten Titel.

Fehlerbehandlung (watchdog-relevant): ein GraphQL-"errors"-Block, eine fehlende
data.audioPlayer.stream.played-Struktur (played=null bei falschem Stream/Hash)
ODER ein Transport-Fehler nach allen Wiederholungen werden als FetchError
gemeldet (= toter Hash / API geaendert -> status=error -> Dashboard rot +
Pushover). Eine LEERE played-Liste ([]) ist ein gueltiger Erfolg.
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

# Jahr-Suffix am Titelende: "THE WILD BOYS (1984)" -> Titel + Jahr.
_TITLE_YEAR_RE = re.compile(r"^(.*\S)\s*\((\d{4})\)\s*$")


def parse_title(raw_title: str) -> tuple[str, str]:
    """
    Spaltet ein optionales '(JJJJ)'-Jahr-Suffix vom Titel ab.
    Rueckgabe: (bereinigter_titel, jahr) - jahr "" wenn kein plausibles
    4-stelliges Jahr am Ende steht.
    """
    title = (raw_title or "").strip()
    m = _TITLE_YEAR_RE.match(title)
    if m:
        y = m.group(2)
        # Plausibilitaet (grob): 1900..2099.
        if 1900 <= int(y) <= 2099:
            return m.group(1).strip(), y
    return title, ""


def _get_with_retry(client: httpx.Client, url: str, **kwargs) -> httpx.Response:
    """
    GET mit Wiederholung bei Transport-Fehlern (Timeout/HTTP/Verbindung).
    Spiegelt charts-recommend/listenbrainz._get_with_retry.
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
    raise FetchError("FLASHBACK: unerwarteter Abbruch der Retry-Schleife")


def _url(hash_: str) -> str:
    return f"{_BASE_URL}/{hash_}"


def _check_graphql_errors(data: dict) -> None:
    """Wirft FetchError, wenn die GraphQL-Antwort einen errors-Block hat."""
    errs = data.get("errors")
    if errs:
        msg = "; ".join(
            (e.get("message") if isinstance(e, dict) else str(e))
            for e in errs
        ) if isinstance(errs, list) else str(errs)
        raise FetchError(f"GraphQL-Fehler (Hash tot?): {msg}")


def fetch_played(channel: str, hash_: str,
                 played_limit: int) -> list[tuple[str, str, str]]:
    """
    Holt die "zuletzt gespielt"-Liste eines Streams.
    Rueckgabe: [(interpret, bereinigter_titel, jahr), ...]. jahr ist das aus dem
    Titel-Suffix "(JJJJ)" abgespaltene Erscheinungsjahr (oder "").
    Eine leere played-Liste ([]) ist ein gueltiger Erfolg.
    FetchError bei errors-Block, played=null (falscher Stream/Hash), kaputter
    Struktur oder Transportfehler.
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
        raise FetchError(f"FLASHBACK nicht erreichbar: {e}") from e
    except ValueError as e:
        raise FetchError(f"FLASHBACK: ungueltiges JSON: {e}") from e

    _check_graphql_errors(data)

    player = (data.get("data") or {}).get("audioPlayer")
    if not isinstance(player, dict):
        raise FetchError("FLASHBACK: data.audioPlayer fehlt (Hash tot / API geaendert?)")
    stream = player.get("stream")
    if not isinstance(stream, dict):
        raise FetchError(
            f"FLASHBACK: stream fehlt fuer '{channel}' (Hash tot / API geaendert?)")
    played = stream.get("played")
    # played=null bedeutet: dieser Stream fuehrt keine Historie (nur der
    # Hauptstream flashbackfm.ffm tut das). Das ist ein Konfig-/Stream-Fehler,
    # KEIN leerer Erfolg -> als FetchError melden.
    if played is None:
        raise FetchError(
            f"FLASHBACK: stream.played=null fuer '{channel}' - nur der "
            "Hauptstream (flashbackfm.ffm) fuehrt eine Historie.")

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
    Optional: Liste der verfuegbaren Channels (data.audioPlayer.streams[]).
    Nur fuer Diagnose/Dashboard. FetchError wie fetch_played.
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
        raise FetchError(f"FLASHBACK nicht erreichbar: {e}") from e
    _check_graphql_errors(data)
    player = (data.get("data") or {}).get("audioPlayer") or {}
    streams = player.get("streams") or []
    return [s for s in streams if isinstance(s, dict)]
