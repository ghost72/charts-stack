"""
Last.fm-Chart-Client.

Last.fm stellt die AudioScrobbler-Web-API (2.0) bereit. Fuer lesende Chart-,
Geo-, Tag- und User-Abfragen genuegt ein kostenloser API-Key (kein OAuth, keine
Signatur). Wir holen ausschliesslich Metadaten (Kuenstler + Titel), keine
Audiodaten.

Genutzte Methoden (alle format=json):
  - chart.getTopTracks              globale Top-Tracks (hoerer-/scrobble-basiert)
  - geo.getTopTracks   country=..   laenderbezogene Top-Tracks (z.B. Switzerland)
  - tag.getTopTracks   tag=..       meistgetaggte Tracks eines Tags (z.B. pop, 80s)
  - user.getTopTracks  user=..      meistgehoerte Tracks eines Users (period=..)
  - track.getTopTags   artist,track Genre-Tags eines Songs (fuer den Genre-Filter)

Die Track-Objekte sind bewusst dieselben wie beim Deezer-Client (ChartTrack),
damit main.py/das Matching quellenunabhaengig funktioniert.
"""

import logging

import httpx

from .deezer import ChartTrack
from .i18n import t

logger = logging.getLogger(__name__)

_BASE_URL = "https://ws.audioscrobbler.com/2.0/"

# Last.fm liefert pro Antwort maximal ~1000 Eintraege; wir paginieren in
# festen Schritten ueber den 'page'-Parameter.
_PAGE_SIZE = 200
_MAX_TRACKS = 100000

# Methoden, bei denen der jeweils benoetigte Zusatzparameter mitgegeben wird.
# key = Last.fm-Methode, value = (paramname_fuer_extra, required)
_METHOD_PARAM = {
    "chart.gettoptracks": (None, False),
    "geo.gettoptracks": ("country", True),
    "tag.gettoptracks": ("tag", True),
    "user.gettoptracks": ("user", True),
}


def _tracks_from_payload(payload: dict) -> list[dict]:
    """Extrahiert die Track-Liste aus den unterschiedlichen Antwort-Wrappern."""
    if not isinstance(payload, dict):
        return []
    # Wrapper heisst je nach Methode 'tracks' oder 'toptracks'.
    container = payload.get("tracks") or payload.get("toptracks") or {}
    items = container.get("track", []) if isinstance(container, dict) else []
    # Bei genau einem Treffer liefert Last.fm ein Objekt statt einer Liste.
    if isinstance(items, dict):
        items = [items]
    return items


def _parse_tracks(items: list[dict], start_pos: int) -> list[ChartTrack]:
    """Wandelt Last.fm-JSON-Eintraege in ChartTrack-Objekte um."""
    tracks: list[ChartTrack] = []
    for offset, item in enumerate(items):
        name = (item.get("name") or "").strip()
        artist = item.get("artist") or {}
        # 'artist' ist bei getTopTracks ein Objekt {name, mbid, url}.
        artist_name = (artist.get("name") if isinstance(artist, dict) else artist) or ""
        artist_name = artist_name.strip()
        if not artist_name or not name:
            continue
        # Chart-/geo-Tracks liefern @attr.rank; sonst laufende Nummer.
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
    Holt EINE Last.fm-Quelle (chart/geo/tag/user getTopTracks) mit Pagination.
    'method' ist die Last.fm-Methode (kleingeschrieben), 'extra' enthaelt die
    methodenspezifischen Zusatzparameter (country/tag/user/period).
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

        # Last.fm meldet Fehler als {"error": <code>, "message": ".."} (HTTP 200).
        if isinstance(payload, dict) and payload.get("error"):
            raise RuntimeError(t("sync.lastfm.api_error", method=method,
                                 error=payload.get('error'),
                                 message=payload.get('message', '')).strip())

        items = _tracks_from_payload(payload)
        if not items:
            break

        tracks.extend(_parse_tracks(items, start_pos=len(tracks) + 1))

        if len(items) < want:
            break  # letzte Seite erreicht
        page += 1

    logger.info(t("lastfm.feed.result",
                  method=method,
                  extra=f" ({extra})" if extra else "",
                  count=len(tracks)))
    return tracks[:target]


def fetch_feed(feed, api_key: str, client: httpx.Client) -> list[ChartTrack]:
    """
    Adapter fuer einen Feed vom Typ 'lastfm'. Erwartet ein Feed-Objekt mit den
    Attributen: method, params (dict mit country/tag/user/period), limit.
    """
    if not api_key:
        raise RuntimeError(t("sync.lastfm.no_api_key"))
    return _fetch_lastfm_feed(feed.method, dict(feed.params or {}),
                              feed.limit, api_key, client)


def get_top_tags(artist: str, title: str, api_key: str,
                 client: httpx.Client) -> list[tuple[str, int]]:
    """
    Liefert die Genre-/Top-Tags eines Songs als [(tag_lowercase, count), ...],
    absteigend nach count. Fuer den Genre-Ausschlussfilter.

    Gibt bei Fehlern / ohne Tags eine leere Liste zurueck (fail-open: der Song
    wird dann NICHT ausgeschlossen).
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
