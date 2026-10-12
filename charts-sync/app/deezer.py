"""
Deezer-Chart-Client.

Deezer stellt eine offene REST-API bereit, die fuer Chart-Listen keinen
Account und keinen API-Key benoetigt. Wir holen ausschliesslich Metadaten
(Kuenstler + Titel), keine Audiodaten.

Endpunkte:
  - Globale/laenderbezogene Charts:  https://api.deezer.com/chart/<id>/tracks
  - Beliebige Playlist:              https://api.deezer.com/playlist/<id>/tracks
"""

import logging
import re
import time

import httpx

from . import config
from .i18n import t

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.deezer.com"


class ChartTrack:
    """Ein einzelner Chart-Track mit den fuer das Matching relevanten Feldern."""

    def __init__(self, position: int, artist: str, title: str, deezer_id: int = 0,
                 contributors: list[str] | None = None):
        self.position = position
        self.artist = artist
        self.title = title
        self.deezer_id = deezer_id
        # Alle an diesem Track beteiligten Kuenstler (Deezer 'contributors[]'),
        # inkl. Feature-/Collab-Partnern, die NICHT der Haupt-Kuenstler sind.
        # Nur fuer den exclude_artists-Filter relevant; beim Matching spielt es
        # keine Rolle. Last.fm-Tracks haben keine Contributors -> leere Liste.
        self.contributors: list[str] = contributors or []

    def __repr__(self) -> str:
        return f"ChartTrack(#{self.position} {self.artist} - {self.title})"


def _parse_tracks(items: list[dict]) -> list[ChartTrack]:
    """Wandelt die Deezer-JSON-Eintraege in ChartTrack-Objekte um."""
    tracks: list[ChartTrack] = []
    for index, item in enumerate(items, start=1):
        artist = (item.get("artist") or {}).get("name", "").strip()
        # title_short ist ohne "(feat. ...)"-Zusaetze und matcht meist besser.
        title = (item.get("title_short") or item.get("title") or "").strip()
        if not artist or not title:
            continue
        position = item.get("position", index)
        # Contributors mitnehmen: Deezer fuehrt bei Collabs den Haupt-Kuenstler
        # in 'artist', die Feature-/Begleit-Acts aber (nur) in 'contributors[]'.
        # Der exclude_artists-Filter braucht sie, um z.B. "Goelae feat. Trauffer"
        # (artist=Goelae, contributor=Trauffer) ausschliessen zu koennen.
        contributors = [
            (c.get("name") or "").strip()
            for c in (item.get("contributors") or [])
            if isinstance(c, dict) and (c.get("name") or "").strip()
        ]
        tracks.append(ChartTrack(position, artist, title, item.get("id", 0),
                                 contributors))
    return tracks


# Deezer liefert pro Antwort maximal ~100 Eintraege, auch wenn ein hoeheres
# 'limit' angefragt wird. Um mehr (bis "all") zu holen, paginieren wir in
# festen Schritten ueber den 'index'-Parameter.
_PAGE_SIZE = 100
# Sicherheitsobergrenze, damit eine kaputte/riesige Quelle keine Endlosschleife
# verursacht (deckt jede real existierende Chart/Playlist locker ab).
_MAX_TRACKS = 100000


def _fetch_deezer_feed(feed, client: httpx.Client) -> list[ChartTrack]:
    """
    Holt EINE Deezer-Quelle (Chart oder Playlist), mit Pagination.
    feed.limit ist die gewuenschte Gesamtzahl (bei "all" ein sehr hoher Wert);
    es wird in _PAGE_SIZE-Schritten geladen, bis limit erreicht ist oder keine
    weiteren Tracks mehr kommen.
    """
    feed_type = feed.deezer_type
    feed_id = feed.source
    if feed_type == "playlist":
        url = f"{_BASE_URL}/playlist/{feed_id}/tracks"
    elif feed_type == "artist":
        # Top-Tracks eines Kuenstlers. Dieselbe Track-Struktur (title + artist)
        # wie Chart/Playlist, daher kein eigener Parser noetig. Deezer deckelt
        # /artist/<id>/top intern bei ~100 - die Pagination unten bricht sauber
        # ab, sobald keine weiteren Tracks mehr kommen.
        url = f"{_BASE_URL}/artist/{feed_id}/top"
    else:
        url = f"{_BASE_URL}/chart/{feed_id}/tracks"

    target = min(max(1, feed.limit), _MAX_TRACKS)
    tracks: list[ChartTrack] = []
    index = 0

    while len(tracks) < target:
        page = min(_PAGE_SIZE, target - len(tracks))
        resp = client.get(url, params={"limit": page, "index": index})
        resp.raise_for_status()
        payload = resp.json()

        # Deezer meldet Fehler als {"error": {...}} mit HTTP 200.
        if isinstance(payload, dict) and payload.get("error"):
            raise RuntimeError(
                f"Deezer-API-Fehler ({feed_type}:{feed_id}): {payload['error']}")

        items = payload.get("data", []) if isinstance(payload, dict) else []
        if not items:
            break  # keine weiteren Tracks (Ende der Liste erreicht)

        tracks.extend(_parse_tracks(items))

        # Wenn Deezer weniger als angefragt liefert, ist die Liste zu Ende.
        if len(items) < page:
            break
        index += len(items)

    logger.info(t("deezer.feed.result", type=feed_type, id=feed_id, count=len(tracks)))
    return tracks


def _merge_key(track: ChartTrack) -> str:
    """Schluessel fuer die Deduplizierung ueber Quellen hinweg (Kuenstler+Titel)."""
    return f"{track.artist.strip().lower()}|{track.title.strip().lower()}"


def _name_hits_exclude(name: str, exclude_set: set[str]) -> bool:
    """
    True, wenn EIN Kuenstlername die Ausschlussliste trifft - entweder als
    ganzer Name ODER als einer der per "feat."/"&"/","/.. getrennten
    Einzelkuenstler (z.B. "Trauffer feat. Beatrice Egli" trifft wegen
    "Trauffer"). Gross-/Kleinschreibung egal.
    """
    n = (name or "").strip().lower()
    if not n:
        return False
    if n in exclude_set:
        return True
    parts = re.split(r"\s*(?:feat\.?|ft\.?|&|,|/|x|vs\.?)\s+", n)
    return any(p.strip() in exclude_set for p in parts if p.strip())


def _is_excluded_artist(artist: str, exclude_set: set[str],
                        contributors: list[str] | None = None) -> bool:
    """
    True, wenn der ausgeschlossene Kuenstler an diesem Track beteiligt ist (die
    Ausschlussliste gilt playlist-spezifisch). Geprueft werden:
      1. der Haupt-Kuenstler (artist) - ganzer Name oder ein per "feat."/"&"/
         "," getrennter Einzelkuenstler, und
      2. die Deezer-'contributors[]' (Feature-/Collab-Partner).
    Grund fuer (2): Bei Collabs fuehrt Deezer den Haupt-Act in 'artist', den
    Feature-Act aber nur in 'contributors[]' - z.B. "Goelae feat. Trauffer"
    hat artist="Goelae" und waere ohne Contributor-Pruefung durchgerutscht
    (an echten Mundart-Daten verifiziert). Kein API-Call, rein lokaler Vergleich.
    """
    if not exclude_set:
        return False
    if _name_hits_exclude(artist, exclude_set):
        return True
    return any(_name_hits_exclude(c, exclude_set) for c in (contributors or []))


def _fetch_one_feed(feed, client: httpx.Client) -> list[ChartTrack]:
    """Dispatcher: holt einen Feed je nach Typ von Deezer oder Last.fm."""
    if feed.kind == "lastfm":
        # Lazy-Import, damit Deezer-only-Setups Last.fm nicht brauchen.
        from . import lastfm
        return lastfm.fetch_feed(feed, config.LASTFM_API_KEY, client)
    return _fetch_deezer_feed(feed, client)


def _match_exclude_tag(top_tags: list[tuple[str, int]]):
    """
    Prueft, ob einer der uebergebenen (Tag, count)-Paare auf die Ausschlussliste
    passt. Match ist ein WORT-basierter Teilstring: der Ausschluss-Tag 'country'
    trifft auch 'contemporary country', 'modern country', 'country rap' usw.
    (aber nicht ein zufaellig enthaltenes Fragment in einem anderen Wort).

    Gibt das erste Treffer-Paar (name, count) zurueck oder None.
    """
    for name, cnt in top_tags:
        if cnt < config.EXCLUDE_TAG_MIN_COUNT:
            continue
        words = set(name.replace("-", " ").split())
        for ex in config.EXCLUDE_TAGS:
            ex_words = ex.replace("-", " ").split()
            # Ein-Wort-Ausschluss (z.B. "country"): trifft, wenn das Wort im Tag
            # vorkommt. Mehr-Wort-Ausschluss (z.B. "hard rock"): als Phrase.
            if len(ex_words) == 1:
                if ex_words[0] in words:
                    return (name, cnt)
            elif ex in name:
                return (name, cnt)
    return None


def _apply_genre_filter(tracks: list[ChartTrack],
                        client: httpx.Client,
                        active: bool) -> list[ChartTrack]:
    """
    Filtert Tracks anhand ihrer Last.fm-Genre-Tags. Ein Track fliegt raus, wenn
    einer seiner staerksten EXCLUDE_TAG_TOP_N Tags in EXCLUDE_TAGS steht und
    dessen count >= EXCLUDE_TAG_MIN_COUNT ist.

    `active` ist der pro-Playlist wirksame Genre-Filter-Schalter
    (ChartSource.effective_genre_filter). Ist er False, wird nicht gefiltert.

    Fail-open: liefert Last.fm keine Tags (oder kein API-Key), bleibt der Track
    drin. Braucht LASTFM_API_KEY; ohne Key wird der Filter uebersprungen.
    """
    if not active:
        return tracks
    if not config.LASTFM_API_KEY:
        logger.warning(t("deezer.genre.no_key"))
        return tracks
    if not config.EXCLUDE_TAGS:
        return tracks

    from . import lastfm

    kept: list[ChartTrack] = []
    dropped = 0
    for track in tracks:
        tags = lastfm.get_top_tags(track.artist, track.title, config.LASTFM_API_KEY, client)
        top = tags[:config.EXCLUDE_TAG_TOP_N]
        hit = _match_exclude_tag(top)
        if hit:
            dropped += 1
            logger.info(t("deezer.genre.dropped",
                          artist=track.artist, title=track.title,
                          tag=hit[0], count=hit[1]))
        else:
            kept.append(track)
        if config.GENRE_FILTER_SLEEP_MS > 0:
            time.sleep(config.GENRE_FILTER_SLEEP_MS / 1000.0)

    logger.info(t("deezer.genre.summary", dropped=dropped, total=len(tracks)))
    return kept


def fetch_chart(chart_source) -> list[ChartTrack]:
    """
    Holt die Chart-Liste(n) fuer eine ChartSource und fuehrt mehrere Feeds
    (Deezer und/oder Last.fm) zu einer deduplizierten Liste zusammen. Deaktivierte
    Feeds (enabled=False) werden uebersprungen. Danach optional Genre-Filter.

    Reihenfolge: Feeds werden in Konfigurationsreihenfolge abgefragt. Erscheint
    ein Song in mehreren Feeds, gewinnt das erste Vorkommen (also die frueher
    gelistete Quelle). Die Positionen werden nach dem Merge neu durchnummeriert,
    damit die Playlist-Reihenfolge stabil ist.
    """
    active = chart_source.active_feeds
    # Pro-Playlist wirksamer Genre-Filter (explizites genre_filter > global).
    genre_active = chart_source.effective_genre_filter
    skipped = len(chart_source.feeds) - len(active)
    skipped_suffix = t("deezer.chart.fetch_skipped", skipped=skipped) if skipped else ""
    logger.info(t(
        "deezer.chart.fetch",
        name=chart_source.title, active=len(active), skipped_suffix=skipped_suffix,
    ))

    merged: list[ChartTrack] = []
    seen: set[str] = set()
    # Zaehler fuer per exclude_artists (playlist-spezifisch) entfernte Tracks.
    excluded_artists = 0
    # Beitrags-Statistik je Quelle: roh geliefert und davon NEU (noch nicht
    # durch einen frueheren Feed abgedeckt).
    stats: dict[str, dict[str, int]] = {}
    # Merken, von welcher Quelle ein Song urspruenglich kam (fuer die Filter-Bilanz).
    origin: dict[str, str] = {}

    with httpx.Client(timeout=30.0, headers={"User-Agent": "charts-sync/1.0"},
                      follow_redirects=True) as client:
        for feed in active:
            try:
                tracks = _fetch_one_feed(feed, client)
            except Exception as e:
                logger.error(t("deezer.feed.failed", feed=repr(feed), error=e))
                continue
            s = stats.setdefault(feed.kind, {"raw": 0, "new": 0})
            s["raw"] += len(tracks)
            for track in tracks:
                # Kuenstler-Ausschluss (playlist-spezifisch) VOR der Dedup
                # pruefen. Reihenfolge ist wichtig: Taeten wir erst 'seen', dann
                # Ausschluss, koennte ein ausgeschlossener Kuenstler durchrutschen,
                # wenn derselbe Song (gleicher _merge_key) zuerst von einem Feed
                # unter einem NICHT ausgeschlossenen Namen kam - der zweite,
                # korrekt ausgeschlossene Treffer liefe dann in 'continue' bei
                # 'key in seen', bevor der Filter greift. Erst ausschliessen,
                # dann dedupen, schliesst diesen Umgehungspfad aus. Kein API-Call.
                if _is_excluded_artist(track.artist, chart_source.exclude_artists,
                                       track.contributors):
                    excluded_artists += 1
                    logger.info(t("deezer.artist.excluded",
                                  artist=track.artist, title=track.title))
                    continue
                key = _merge_key(track)
                if key in seen:
                    continue
                seen.add(key)
                origin[key] = feed.kind
                s["new"] += 1
                merged.append(track)

        if excluded_artists:
            logger.info(t("deezer.exclude.summary",
                          name=chart_source.title, count=excluded_artists))

        before_filter = len(merged)
        # Genre-Filter (optional) auf die gemergte Liste anwenden.
        merged = _apply_genre_filter(merged, client, genre_active)

    # Positionen nach dem Merge neu vergeben (1..N in Merge-Reihenfolge).
    for i, track in enumerate(merged, start=1):
        track.position = i

    # Beitrags-Statistik loggen (was bringt jede Quelle NETTO dazu).
    if stats:
        parts = []
        for kind in ("deezer", "lastfm"):
            if kind in stats:
                s = stats[kind]
                dup = s["raw"] - s["new"]
                parts.append(t("deezer.source.contribution_item",
                               kind=kind, new=s["new"], raw=s["raw"], dup=dup))
        filtered = before_filter - len(merged)
        logger.info(t("deezer.source.contribution",
                      name=chart_source.title, parts=" | ".join(parts)))
        if genre_active and filtered:
            logger.info(t("deezer.genre.filter_removed",
                          name=chart_source.title, count=filtered))
    else:
        filtered = 0

    # Statistik fuer die spaetere Meldung (Pushover) am chart_source ablegen.
    chart_source.last_stats = {
        "deezer_new": stats.get("deezer", {}).get("new", 0),
        "lastfm_new": stats.get("lastfm", {}).get("new", 0),
        "genre_filtered": filtered if genre_active else 0,
    }

    logger.info(t("deezer.merge.result", name=chart_source.title, count=len(merged)))
    return merged
