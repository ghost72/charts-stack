"""
Deezer-Casing-Aufloesung fuer charts-flashback.

FLASHBACK FM liefert Artist/Titel in GROSSBUCHSTABEN (z.B.
``U2 / WHERE THE STREETS HAVE NO NAME``). Diese Rohwerte wuerden ungefiltert in
die missing-Liste und damit in Ordner-/Dateinamen der Library wandern. Dieses
Modul loest zu jedem Song die ECHTE, offizielle Schreibweise ueber die offene
Deezer-Such-API auf (kein API-Key) - konsistent zu charts-sync/charts-hitparade.

Endpunkt:
  GET https://api.deezer.com/search?q=<artist> <title>
  Antwort-JSON: {"data": [{"title": ..., "artist": {"name": ...}}, ...]}
  Fehler meldet Deezer als {"error": {...}} MIT HTTP 200 (wie in
  charts-sync/app/deezer.py behandelt) -> dann Fallback.

WICHTIG (verifiziert gegen die Live-API): Die strukturierte Advanced-Search-
Query ``artist:"<a>" track:"<t>"`` liefert bei Deezer konsequent 0 Treffer
(auch fuer bekannte Songs, unabhaengig vom Casing) - das Feature waere damit ein
No-op. Die PLAIN-Query ``q="<artist> <title>"`` liefert dagegen korrekte
Treffer. Die lockerere Plain-Query wird durch die strikte normalize()/
similarity()-Validierung (DEEZER_MATCH_MIN) gegen Fehltreffer abgesichert.

Design (Stil von year.py):
  - resolve_canonical(artist, title) -> (artist, title): kanonische Werte oder
    - bei Abschaltung/kein Treffer/Netzfehler/zu grosser Abweichung - die
    unveraenderten FLASHBACK-Rohwerte (lieber GROSS als falsch geraten).
  - Prozess-weiter JSON-Cache (auch Misses), damit wiederkehrende Songs die API
    nicht erneut belasten.
  - Treffer-VALIDIERUNG ueber library.normalize/library.similarity (dieselbe
    Logik wie das Library-Matching), nicht neu erfunden.
  - Alle Netz-/HTTP-/JSON-Fehler sind nie fatal (Log DEBUG + Fallback).
"""

import json
import logging
import os

import httpx

from . import config, library
from .i18n import t

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://api.deezer.com/search"

# Prozess-weiter Cache (persistiert in DEEZER_CACHE_FILE).
_cache: dict | None = None


def _key(artist: str, title: str) -> str:
    """Cache-Key identisch zum Matching-/Jahr-Map-Key (lowercase 'artist|title')."""
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
        logger.warning(t("flashback.deezer.cache_write_failed", error=e))


import re as _re

# Entfernt EINEN runden Klammer-Zusatz am Titelende (z.B. "There Must Be an
# Angel (Playing With My Heart)" -> "There Must Be an Angel"). library.normalize
# strippt nur bekannte Zusaetze (feat/version/remaster/...); ein genereller
# Untertitel in Klammern bleibt sonst stehen und drueckt die Titel-Similarity.
_TRAILING_PAREN = _re.compile(r"\s*[\(\[][^\(\)\[\]]*[\)\]]\s*$")


def _strip_trailing_paren(text: str) -> str:
    """Schneidet einen abschliessenden (…)/[…]-Zusatz ab (einmal)."""
    return _TRAILING_PAREN.sub("", text or "").strip()


def _passes(a_score: int, t_score: int) -> bool:
    """
    Validierungs-Regel fuer einen Treffer. Primaer strikt (beide >= MATCH_MIN).
    Zusaetzlich ASYMMETRISCH: ist EIN Feld sehr sicher (>= STRONG), reicht beim
    anderen die gelockerte Schwelle (>= RELAX). Faengt Alias-Kuenstler (Titel
    100, Artist niedrig) und Klammer-Titel (Artist 100, Titel gedrueckt), ohne
    bei beidseitig schwachen Treffern Fehltreffer zuzulassen.
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
    """
    Waehlt aus den Deezer-Treffern (data[]) den BESTEN belastbaren Kandidaten.
    Belastbar = Validierung nach _passes() (strikt ODER asymmetrisch, s.o.).
    Fuer den Titel-Score wird zusaetzlich die klammerbereinigte Variante geprueft
    und der bessere der beiden Werte genommen (Deezer-Untertitel wie
    "(Playing With My Heart)" sollen nicht gegen den Treffer zaehlen). Unter den
    belastbaren gewinnt der mit der hoechsten Summe (artist+title). Rueckgabe:
    (artist, title) oder None (kein gueltiger Treffer -> Rohwerte).
    """
    n_raw_artist = library.normalize(raw_artist)
    n_raw_title = library.normalize(raw_title)
    n_raw_title_stripped = library.normalize(_strip_trailing_paren(raw_title))
    best = None
    best_score = -1
    for item in data:
        if not isinstance(item, dict):
            continue
        cand_artist = ((item.get("artist") or {}).get("name") or "").strip()
        # title_short laesst "(feat. ...)"-Zusaetze weg und matcht meist besser.
        cand_title = (item.get("title_short") or item.get("title") or "").strip()
        if not cand_artist or not cand_title:
            continue
        a_score = library.similarity(library.normalize(cand_artist), n_raw_artist)
        # Titel-Score: besserer Wert aus vollem und klammerbereinigtem Vergleich,
        # beidseitig (Kandidat UND Rohwert koennen den Zusatz tragen).
        n_cand_title = library.normalize(cand_title)
        n_cand_title_stripped = library.normalize(_strip_trailing_paren(cand_title))
        t_score = max(
            library.similarity(n_cand_title, n_raw_title),
            library.similarity(n_cand_title_stripped, n_raw_title_stripped),
            library.similarity(n_cand_title_stripped, n_raw_title),
            library.similarity(n_cand_title, n_raw_title_stripped),
        )
        if _passes(a_score, t_score):
            combined = a_score + t_score
            if combined > best_score:
                best_score = combined
                best = (cand_artist, cand_title)
    return best


def resolve_canonical(artist: str, title: str) -> tuple[str, str]:
    """
    Liefert (artist, title) in kanonischer Schreibweise ueber die Deezer-Suche.

    Fallback auf die unveraenderten Rohwerte, wenn der Schalter aus ist, kein
    belastbarer Treffer existiert oder ein Netz-/HTTP-/JSON-Fehler auftritt
    (lieber GROSS als falsch geraten). Ergebnis wird gecacht (auch Misses).
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
        # Unerwartetes Cache-Format -> ignorieren und neu aufloesen.

    # Plain-Query "<artist> <title>" (die strukturierte Query liefert bei Deezer
    # 0 Treffer, siehe Modul-Docstring). httpx uebernimmt das URL-Encoding.
    query = f"{artist} {title}"
    result = (artist, title)
    try:
        with httpx.Client(timeout=config.HTTP_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(_SEARCH_URL, params={"q": query})
            resp.raise_for_status()
            payload = resp.json()
        # Deezer meldet Fehler als {"error": {...}} mit HTTP 200.
        if isinstance(payload, dict) and payload.get("error"):
            logger.debug(t("flashback.deezer.api_error", artist=artist,
                           title=title, error=payload["error"]))
        else:
            data = payload.get("data", []) if isinstance(payload, dict) else []
            best = _best_canonical(data, artist, title)
            if best:
                result = best
                logger.debug(t("flashback.deezer.casing_resolved", artist=artist,
                               title=title, canonical_artist=best[0],
                               canonical_title=best[1]))
            else:
                logger.debug(t("flashback.deezer.casing_no_match", artist=artist,
                               title=title))
    except (httpx.HTTPError, ValueError) as e:
        logger.debug(t("flashback.deezer.lookup_failed", artist=artist,
                       title=title, error=e))

    # Immer ein Ergebnis cachen (auch den Rohwert-Miss), nie leer lassen.
    cache[k] = [result[0], result[1]]
    _save_cache()
    return result
