"""
Deezer-Casing-Aufloesung fuer charts-import (letzte Instanz).

charts-flashback kanonisiert Artist/Titel bereits ueber Deezer, bevor es sie in
die missing-Liste schreibt. Trotzdem koennen GROSS-Rohwerte durchrutschen (z.B.
wenn flashback vor dem Matching-Fix lief, oder ein Lauf den Deezer-Treffer knapp
verwarf). Damit NIE ein GROSS-Name in der Library landet, loest charts-import
beim Einsortieren eines Chart-Songs die kanonische Schreibweise HIER noch einmal
auf - als letzte, zentrale Instanz direkt vor dem Schreiben.

Self-contained (eigene Normalisierung, kein library-Modul noetig), damit es im
charts-import-Container ohne Zusatz-Abhaengigkeit laeuft. Logik identisch zu
charts-flashback/app/deezer.py (dort gegen die Live-API verifiziert):

  GET https://api.deezer.com/search?q=<artist> <title>  (Plain-Query; die
  strukturierte Advanced-Query liefert bei Deezer 0 Treffer).
  Treffer-Validierung ueber normalize()/similarity() mit asymmetrischer Schwelle
  (_passes): strikt beide >= MATCH_MIN, ODER ein Feld >= STRONG und das andere
  >= RELAX. Zusaetzlich Klammer-Zusatz-Strip beim Titelvergleich. Kein
  belastbarer Treffer -> unveraenderte Rohwerte (lieber GROSS als falsch).
  Cache (auch Misses) in DEEZER_CACHE_FILE.
"""

import json
import logging
import os
import re
import unicodedata
from difflib import SequenceMatcher

import httpx

from . import config
from .i18n import t

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://api.deezer.com/search"

# --- Normalisierung (identisch zu charts-flashback/charts-sync library.py) ---
_NOISE_PATTERNS = [
    r"\(feat\.?.*?\)", r"\[feat\.?.*?\]",
    r"\(ft\.?.*?\)", r"\[ft\.?.*?\]",
    r"\(with.*?\)",
    r"\(remaster.*?\)", r"\[remaster.*?\]",
    r"\(.*?version\)", r"\(.*?edit\)",
    r"\(official.*?\)", r"\(lyric.*?\)", r"\(audio\)", r"\(video\)",
]


def _normalize(text: str) -> str:
    if not text:
        return ""
    text = text.lower()
    for pattern in _NOISE_PATTERNS:
        text = re.sub(pattern, " ", text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _similarity(a: str, b: str) -> int:
    if not a or not b:
        return 0
    return round(SequenceMatcher(None, a, b).ratio() * 100)


# --- Cache ------------------------------------------------------------------
_cache: dict | None = None


def _key(artist: str, title: str) -> str:
    """Cache-Key lowercase 'artist|title' (wie flashback/charts-import)."""
    return f"{artist.lower()}|{title.lower()}"


def _load_cache() -> dict:
    global _cache
    if _cache is not None:
        return _cache
    try:
        with open(config.DEEZER_CACHE_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        _cache = data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        _cache = {}
    return _cache


def _save_cache() -> None:
    if _cache is None:
        return
    try:
        os.makedirs(os.path.dirname(config.DEEZER_CACHE_FILE), exist_ok=True)
        tmp = config.DEEZER_CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(_cache, fh, ensure_ascii=False, indent=0)
        os.replace(tmp, config.DEEZER_CACHE_FILE)
    except OSError as e:
        logger.warning(t("import.deezer.cache_write_failed", error=e))


# --- Matching ---------------------------------------------------------------
_TRAILING_PAREN = re.compile(r"\s*[\(\[][^\(\)\[\]]*[\)\]]\s*$")


def _strip_trailing_paren(text: str) -> str:
    """Schneidet einen abschliessenden (…)/[…]-Zusatz ab (einmal)."""
    return _TRAILING_PAREN.sub("", text or "").strip()


def _passes(a_score: int, t_score: int) -> bool:
    """
    Validierung: strikt (beide >= MATCH_MIN) ODER asymmetrisch (ein Feld
    >= STRONG, anderes >= RELAX). Faengt Alias-Kuenstler (Titel sicher, Artist
    niedrig) und Klammer-Titel (Artist sicher, Titel gedrueckt), ohne
    beidseitig schwache Fehltreffer durchzulassen.
    """
    mn = config.DEEZER_MATCH_MIN
    strong = config.DEEZER_MATCH_STRONG
    relax = config.DEEZER_MATCH_RELAX
    if a_score >= mn and t_score >= mn:
        return True
    if a_score >= strong and t_score >= relax:
        return True
    if t_score >= strong and a_score >= relax:
        return True
    return False


def _best_canonical(data: list, raw_artist: str, raw_title: str):
    """Bester belastbarer Deezer-Treffer (nach _passes) oder None."""
    n_raw_artist = _normalize(raw_artist)
    n_raw_title = _normalize(raw_title)
    n_raw_title_stripped = _normalize(_strip_trailing_paren(raw_title))
    best = None
    best_score = -1
    for item in data:
        if not isinstance(item, dict):
            continue
        cand_artist = ((item.get("artist") or {}).get("name") or "").strip()
        cand_title = (item.get("title_short") or item.get("title") or "").strip()
        if not cand_artist or not cand_title:
            continue
        a_score = _similarity(_normalize(cand_artist), n_raw_artist)
        n_cand_title = _normalize(cand_title)
        n_cand_title_stripped = _normalize(_strip_trailing_paren(cand_title))
        t_score = max(
            _similarity(n_cand_title, n_raw_title),
            _similarity(n_cand_title_stripped, n_raw_title_stripped),
            _similarity(n_cand_title_stripped, n_raw_title),
            _similarity(n_cand_title, n_raw_title_stripped),
        )
        if _passes(a_score, t_score):
            combined = a_score + t_score
            if combined > best_score:
                best_score = combined
                best = (cand_artist, cand_title)
    return best


def resolve_canonical(artist: str, title: str) -> tuple[str, str]:
    """
    (artist, title) in kanonischer Schreibweise ueber die Deezer-Suche, sonst
    die unveraenderten Rohwerte (Schalter aus / kein sicherer Treffer /
    Netzfehler). Gecacht werden nur belastbare Ergebnisse (API erreichbar, mit
    oder ohne Treffer); Netzfehler/Timeout/API-Error werden NICHT gecacht, damit
    ein transienter Ausfall nicht dauerhaft den Rohwert einfriert.
    """
    if not config.DEEZER_CANONICAL_ENABLED:
        return artist, title
    if not artist or not title:
        return artist, title

    cache = _load_cache()
    k = _key(artist, title)
    if k in cache:
        cached = cache[k]
        if isinstance(cached, list) and len(cached) == 2:
            return cached[0], cached[1]

    query = f"{artist} {title}"
    result = (artist, title)
    # Nur ECHTE Ergebnisse cachen (API erreichbar - mit oder ohne Treffer).
    # Bei Netzfehler/Timeout/API-Error NICHT cachen, sonst wuerde ein nur
    # transienter Ausfall den Rohwert (GROSS-Name) dauerhaft einfrieren und der
    # naechste Lauf fragt nie wieder nach. So heilt sich der Fall von selbst,
    # sobald Deezer wieder erreichbar ist.
    cacheable = False
    try:
        with httpx.Client(timeout=config.HTTP_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(_SEARCH_URL, params={"q": query})
            resp.raise_for_status()
            payload = resp.json()
        if isinstance(payload, dict) and payload.get("error"):
            # Deezer meldet einen API-Fehler (z.B. Rate-Limit/Quota) - das ist
            # KEIN belastbares "kennt den Song nicht", also nicht cachen.
            logger.debug(t("import.deezer.api_error", artist=artist,
                           title=title, error=payload["error"]))
        else:
            data = payload.get("data", []) if isinstance(payload, dict) else []
            best = _best_canonical(data, artist, title)
            # API hat sauber geantwortet -> Ergebnis ist belastbar (Treffer ODER
            # echter Miss), beides darf gecacht werden.
            cacheable = True
            if best:
                result = best
                logger.debug(t("import.deezer.casing_resolved", artist=artist,
                               title=title, new_artist=best[0], new_title=best[1]))
            else:
                logger.debug(t("import.deezer.casing_no_match", artist=artist, title=title))
    except (httpx.HTTPError, ValueError) as e:
        logger.debug(t("import.deezer.lookup_failed", artist=artist, title=title, error=e))

    if cacheable:
        cache[k] = [result[0], result[1]]
        _save_cache()
    return result
