"""
Last.fm-Empfehlungs-Client (optional, sofort nutzbar mit vorhandenem API-Key).

Zwei Empfehlungs-Modi (per config waehlbar, beide brauchen nur den API-Key -
kein OAuth):

  1. "top" (user.getTopTracks): die meistgehoerten Tracks eines Last.fm-Users.
     Braucht einen Last.fm-USERNAME (LASTFM_USER) mit Hoerverlauf. Das ist am
     naechsten an "deine Rotation".

  2. "similar" (track.getSimilar): ausgehend von Seed-Songs (deinen Top-Tracks
     oder einer konfigurierten Seed-Liste) aehnliche Songs. Liefert Entdeckungen
     im Stil dessen, was du hoerst - auch OHNE Last.fm-Hoerverlauf, wenn Seeds
     vorgegeben werden.

Alle Abfragen liefern nur Metadaten (Kuenstler + Titel).
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
    # GET mit Wiederholung bei Transport-Fehlern (Timeout/HTTP/Verbindung).
    # Erst wenn der letzte Versuch scheitert, fliegt der httpx.HTTPError weiter
    # (wird oben in einen FetchError uebersetzt). Last.fm-Fehler-Payloads
    # (payload["error"]) sind KEINE Transport-Fehler und werden nicht wiederholt.
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
        raise RuntimeError(f"Last.fm-Fehler {payload.get('error')}: "
                           f"{payload.get('message', '')}".strip())
    return payload


def get_top_tracks(user: str, api_key: str, limit: int,
                   client: httpx.Client) -> list[tuple[int, str, str]]:
    """Meistgehoerte Tracks eines Last.fm-Users -> (pos, artist, title)."""
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
    """Aehnliche Tracks zu einem Seed-Song -> [(artist, title), ...]."""
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
    Liefert Last.fm-Empfehlungen als (pos, artist, title).

    mode "top":     user.getTopTracks (braucht LASTFM_USER mit Hoerverlauf).
    mode "similar": track.getSimilar ausgehend von den Seeds (oder, wenn user
                    gesetzt, von dessen Top-Tracks als Seeds). Dedupliziert.
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
                # Seeds aus den Top-Tracks des Users ableiten.
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
        # Transport-Fehler NACH allen Wiederholungen -> als Fehler nach oben
        # melden (frueher wurde hier still [] zurueckgegeben). Ein leeres, aber
        # erfolgreiches Ergebnis (kein Exception) bleibt KEIN Fehler.
        logger.error(t("recommend.lastfm.api_error", error=e))
        raise FetchError(f"Last.fm-Abruf fehlgeschlagen: {e}") from e
