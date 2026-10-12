"""
ListenBrainz-Client (Multi-User, Download-only).

Liest den OEFFENTLICHEN Hoergeschmack eines ListenBrainz-Users aus zwei
Datenquellen und liefert ihn jeweils als (position, artist, title)-Liste -
quellenunabhaengig, wie der Deezer-/Last.fm-Client in charts-sync. Beide Quellen
liefern Kuenstler + Titel DIREKT; es findet KEIN MBID-Resolving statt und der
CF-Empfehlungs-Endpoint (cf/recommendation/user/<user>/recording) wird BEWUSST
NICHT verwendet (der liefert nur rohe recording_mbid + score, deren Aufloesung
MusicBrainz mit 1 req/s oder einen fragilen Player-Endpoint braeuchte).

Quelle 1 - "stats" (sofort verfuegbar, robust):
  GET /1/stats/user/<user>/recordings?count=<limit>&range=<range>
     -> die meistgehoerten Recordings eines Users (artist_name + track_name).

Quelle 2 - "createdfor" (echte Discovery, kann anfangs leer sein):
  Zwei-Schritt-Ablauf (ListenBrainz-API, siehe
  https://listenbrainz.readthedocs.io/en/latest/users/api/playlist.html):
  1. GET /1/user/<user>/playlists/createdfor
     -> Metadaten aller "fuer dich erstellten" Playlists inkl. playlist_mbid.
        Alle public, kein Token noetig. Wir waehlen anhand des Titels die
        gewuenschten (Weekly Jams / Weekly Exploration) aus.
  2. GET /1/playlist/<mbid>
     -> die Playlist im JSPF-Format MIT Track-Metadaten (title + creator).
        Kein separater MusicBrainz-Lookup noetig.

WICHTIG (Anlaufzeit): ListenBrainz generiert die createdfor-Playlists erst nach
einigen Wochen Hoerverlauf. Anfangs liefert createdfor evtl. nichts - das ist
kein Fehler, sondern fehlende Daten (die "stats"-Quelle hat sofort Daten).
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
    GET mit Wiederholung bei Transport-Fehlern (Timeout/HTTP/Verbindung).

    Versucht bis config.HTTP_RETRIES-mal mit kurzem, exponentiellem Backoff.
    Erst wenn der letzte Versuch scheitert, fliegt der httpx.HTTPError weiter
    nach oben (dort wird er in einen FetchError uebersetzt).
    """
    attempts = max(1, config.HTTP_RETRIES)
    for attempt in range(1, attempts + 1):
        try:
            resp = client.get(url, **kwargs)
            resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError as e:
            # 4xx-Client-Fehler (ausser 429 Rate-Limit) sind dauerhaft - ein
            # Retry bringt nichts (z.B. 404 "User hat keine Daten"). Sofort
            # durchreichen, der Aufrufer behandelt es (404 = leer, kein Fehler).
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
            # Transport-/Timeout-/Verbindungsfehler -> wiederholen lohnt sich.
            if attempt >= attempts:
                raise
            backoff = min(2 ** (attempt - 1), 8)
            logger.warning(t("recommend.lb.retry", attempt=attempt,
                             attempts=attempts, error=e, backoff=backoff))
            time.sleep(backoff)
    # Unerreichbar (Schleife endet via return oder raise), nur zur Beruhigung:
    raise FetchError("ListenBrainz: unerwarteter Abbruch der Retry-Schleife")


# Titel-Marker, um die gewuenschten "Created for you"-Playlists zu erkennen.
# ListenBrainz benennt sie z.B. "Weekly Jams for <username>, week of ..." bzw.
# "Weekly Exploration for <username>, week of ...". Wir matchen tolerant.
PLAYLIST_KINDS = {
    "jams": ["weekly jams", "daily jams"],
    "exploration": ["weekly exploration", "weekly discovery", "exploration"],
}


def _headers(token: str) -> dict:
    """Authorization-Header, falls ein Token gesetzt ist (fuer createdfor
    optional, aber schadet nicht und erlaubt hoehere Rate-Limits)."""
    return {"Authorization": f"Token {token}"} if token else {}


def _list_created_for(user: str, token: str, client: httpx.Client) -> list[dict]:
    """Holt die Metadaten aller 'createdfor'-Playlists des Users.

    Ein 404/204 bedeutet NICHT 'Fehler', sondern 'der User hat (noch) keine
    createdfor-Playlists' - ListenBrainz generiert Weekly Jams/Exploration erst
    nach einigen Wochen Hoerverlauf, und ein neuer/leerer User liefert dort 404.
    Das wird - wie beim stats-Endpoint - als leeres Ergebnis behandelt (kein
    FetchError -> Dashboard bleibt gruen/idle, kein Pushover-Alarm)."""
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
    # Antwort: {"playlists": [{"playlist": {...JSPF...}}, ...], "count": ..}
    out = []
    for wrapper in data.get("playlists", []):
        pl = wrapper.get("playlist") if isinstance(wrapper, dict) else None
        if isinstance(pl, dict):
            out.append(pl)
    return out


def _playlist_identifier(pl: dict) -> str | None:
    """
    Extrahiert die playlist_mbid aus einem JSPF-Playlist-Metadaten-Objekt.
    JSPF speichert sie im 'identifier' (URL, die auf .../playlist/<mbid> endet).
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
    Holt eine Playlist per MBID und liefert (position, artist, title).
    JSPF-Track: {"title": <Songtitel>, "creator": <Kuenstler>, ...}.
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
    Holt die gewuenschten Empfehlungs-Playlists.

    kinds: Teilmenge von PLAYLIST_KINDS.keys() ("jams", "exploration").
    Rueckgabe: {kind: [(pos, artist, title), ...]}. Fehlt eine Playlist (noch
    nicht generiert), fehlt der Key bzw. ist leer.
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
                # Neueste passende Playlist dieses Typs waehlen (Liste ist i.d.R.
                # nach Datum sortiert; wir nehmen die erste Uebereinstimmung).
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
        # Transport-Fehler NACH allen Wiederholungen -> als Fehler nach oben
        # melden (frueher wurde hier still ein leeres Ergebnis zurueckgegeben,
        # was der Aufrufer nicht von "noch keine Playlists" unterscheiden
        # konnte). Ein leeres, aber erfolgreiches Ergebnis (kein Exception)
        # bleibt davon unberuehrt und ist weiterhin KEIN Fehler.
        logger.error(t("recommend.lb.api_error", error=e))
        raise FetchError(f"ListenBrainz nicht erreichbar: {e}") from e
    return result


def fetch_stats_recordings(user: str, token: str, range_: str,
                           limit: int = 100) -> list[tuple[int, str, str]]:
    """
    Holt die meistgehoerten Recordings eines Users (stats-Quelle).

      GET /1/stats/user/<user>/recordings?count=<limit>&range=<range_>
      -> payload.recordings[].{artist_name, track_name}

    Rueckgabe: [(pos, artist, title), ...] - dieselbe 3-Tupel-Form wie
    fetch_recommendations und lastfm.fetch_recommendations.

    404/204 (kein Profil / noch keine Daten) wird wie "noch keine Daten"
    behandelt: leere Liste, KEIN FetchError. Jeder andere HTTP-Fehler (nach
    allen Wiederholungen in _get_with_retry) wird als FetchError gemeldet.
    Hinweis: Da _get_with_retry erst NACH der Retry-Schleife wirft, wird ein
    404 pro Lauf HTTP_RETRIES-mal abgefragt, bevor [] zurueckkommt - bewusst in
    Kauf genommen, um die 5xx-Robustheit fuer diesen Endpoint zu erhalten.
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
        raise FetchError(f"ListenBrainz stats '{user}' nicht erreichbar: {e}") from e
    except httpx.HTTPError as e:
        logger.error(t("recommend.lb.stats_error", user=user, error=e))
        raise FetchError(f"ListenBrainz stats '{user}' nicht erreichbar: {e}") from e

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
