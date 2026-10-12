"""
Hybrid-Jahr-Aufloesung fuer charts-flashback.

Ziel: jedem Song ein moeglichst korrektes Erscheinungsjahr geben, damit er in
der richtigen Dekaden-Smart-Playlist (NSP) in Navidrome landet.

Strategie:
  1. MusicBrainz-Lookup (genaues Jahr, best effort): recording-Suche
     artist+title, fruehestes Studio-Erstrelease (Live/Remix/Karaoke/
     Instrumental/Demo/Cover werden ausgeschlossen, Score >= MB_SCORE_MIN).
     Rate-Limit (MB_RATE_SLEEP) + Cache (auch Misses).
  2. Plausibilisierung ueber die Channel-Dekade:
       - MB-Jahr liegt in der Dekade  -> MB-Jahr nehmen.
       - MB-Jahr widerspricht/leer/MB aus -> Dekaden-Mitte (von+5).
       - keine Dekade (Theme/Kuenstler) + MB-Jahr -> MB-Jahr.
       - keine Dekade + MB leer -> "" (kein Jahr-Tag).

EHRLICH: Das exakte Jahr ist best effort - MusicBrainz liefert es nicht immer
richtig. Verlaesslich ist die DEKADE (durch den Channel + Clamp). Fuer die
Dekaden-NSP ist das genau das, was zaehlt.
"""

import json
import logging
import os
import time
from datetime import datetime

import httpx

from . import config
from .i18n import t

logger = logging.getLogger(__name__)

_MB_URL = "https://musicbrainz.org/ws/2/recording"
_EXCLUDE = ("live", "remix", "karaoke", "instrumental", "demo", "cover")

# Prozess-weiter Cache (persistiert in MB_CACHE_FILE).
_cache: dict | None = None


def _lucene_phrase(s: str) -> str:
    """Escaped Backslash und Anfuehrungszeichen fuer eine Lucene-Phrase."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _load_cache() -> dict:
    global _cache
    if _cache is not None:
        return _cache
    try:
        with open(config.MB_CACHE_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        _cache = data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        _cache = {}
    return _cache


def _save_cache() -> None:
    if _cache is None:
        return
    try:
        os.makedirs(os.path.dirname(config.MB_CACHE_FILE), exist_ok=True)
        tmp = config.MB_CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(_cache, fh, ensure_ascii=False, indent=0)
        os.replace(tmp, config.MB_CACHE_FILE)
    except OSError as e:
        logger.warning(t("flashback.mb.cache_write_failed", error=e))


def _parse_year(value: str) -> int | None:
    """Zieht ein plausibles 4-stelliges Jahr aus einem Datum-String."""
    if not value or len(value) < 4:
        return None
    head = value[:4]
    if not head.isdigit():
        return None
    y = int(head)
    now = datetime.now().year
    if 1900 <= y <= now + 1:
        return y
    return None


def _mb_year(artist: str, title: str) -> "tuple[str, bool]":
    """
    Fragt MusicBrainz nach dem fruehesten Studio-Erscheinungsjahr.
    Rueckgabe: (jahr, ok). jahr = "YYYY" oder "" (kein brauchbarer Treffer).
    ok = True, wenn MusicBrainz sauber geantwortet hat (Ergebnis belastbar, auch
    ein leeres); ok = False bei Netz-/HTTP-/Parse-Fehler (transient -> NICHT
    cachen, damit der naechste Lauf erneut fragt).
    """
    q = f'recording:"{_lucene_phrase(title)}" AND artist:"{_lucene_phrase(artist)}"'
    try:
        time.sleep(config.MB_RATE_SLEEP)
        with httpx.Client(timeout=config.HTTP_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(
                _MB_URL,
                params={"query": q, "fmt": "json", "limit": 25},
                headers={"User-Agent": config.MB_USER_AGENT},
            )
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        logger.debug(t("flashback.mb.lookup_failed", artist=artist, title=title, error=e))
        return "", False

    years: list[int] = []
    for rec in data.get("recordings", []):
        if not isinstance(rec, dict):
            continue
        if (rec.get("score") or 0) < config.MB_SCORE_MIN:
            continue
        dis = (rec.get("disambiguation") or "").lower()
        if any(w in dis for w in _EXCLUDE):
            continue
        # (i) Recording-Top-Level first-release-date.
        y = _parse_year(rec.get("first-release-date") or "")
        if y:
            years.append(y)
        # (ii) frueheste release.date dieses Recordings.
        for rel in rec.get("releases", []) or []:
            if isinstance(rel, dict):
                ry = _parse_year(rel.get("date") or "")
                if ry:
                    years.append(ry)
                # (iii) release-group first-release-date, falls gefuellt.
                rg = rel.get("release-group") if isinstance(rel, dict) else None
                if isinstance(rg, dict):
                    gy = _parse_year(rg.get("first-release-date") or "")
                    if gy:
                        years.append(gy)
    # API sauber beantwortet -> ok=True, auch wenn kein Jahr gefunden wurde
    # (echter Miss, darf gecacht werden).
    if not years:
        return "", True
    return str(min(years)), True


def resolve_year(artist: str, title: str, title_year: str) -> str:
    """
    Liefert ein Jahr "YYYY" (oder "").

    Strategie:
      1. title_year (aus dem FLASHBACK-Titel-Suffix "(JJJJ)") - VERLAESSLICH,
         wird bevorzugt. FLASHBACK gibt das Erscheinungsjahr selbst an.
      2. Nur falls kein Titel-Jahr vorhanden ist: MusicBrainz-Fallback
         (best effort, mit Rate-Limit + Cache).
    """
    if title_year:
        return title_year
    if config.MB_ENABLED and artist and title:
        cache = _load_cache()
        key = f"{artist.lower()}|{title.lower()}"
        if key in cache:
            return cache[key] or ""
        mb, ok = _mb_year(artist, title)
        # Nur belastbare Ergebnisse cachen (auch den echten Miss ""). Bei einem
        # transienten Netz-/HTTP-Fehler (ok=False) NICHT cachen, sonst wuerde
        # der Song wegen des Miss-Cache + seen-State nie wieder nach dem echten
        # Jahr gefragt - der naechste Lauf soll es erneut versuchen.
        if ok:
            cache[key] = mb
            _save_cache()
        return mb
    return ""
