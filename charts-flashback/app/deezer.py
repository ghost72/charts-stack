"""
Deezer casing resolution for charts-flashback.

FLASHBACK FM delivers artist/title in UPPERCASE (e.g.
``U2 / WHERE THE STREETS HAVE NO NAME``). These raw values would, unfiltered,
move into the missing list and thus into the library's folder/file names. This
module resolves the REAL, official spelling for each song via the open Deezer
search API (no API key) - consistent with charts-sync/charts-hitparade.

Endpoint:
  GET https://api.deezer.com/search?q=<artist> <title>
  Response JSON: {"data": [{"title": ..., "artist": {"name": ...}}, ...]}
  Deezer reports errors as {"error": {...}} WITH HTTP 200 (handled as in
  charts-sync/app/deezer.py) -> then fallback.

IMPORTANT (verified against the live API): the structured advanced-search query
``artist:"<a>" track:"<t>"`` consistently returns 0 hits on Deezer (even for
well-known songs, independent of casing) - the feature would thus be a no-op.
The PLAIN query ``q="<artist> <title>"`` however returns correct hits. The
looser plain query is guarded against false hits by the strict normalize()/
similarity() validation (DEEZER_MATCH_MIN).

Design (style of year.py):
  - resolve_canonical(artist, title) -> (artist, title): canonical values or
    - when disabled/no hit/network error/too large a deviation - the unchanged
    FLASHBACK raw values (rather UPPERCASE than wrongly guessed).
  - Process-wide JSON cache (misses too) so recurring songs do not hit the API
    again.
  - Hit VALIDATION via library.normalize/library.similarity (the same logic as
    the library matching), not reinvented.
  - All network/HTTP/JSON errors are never fatal (log DEBUG + fallback).
"""

import json
import logging
import os

import httpx

from . import config, library
from .i18n import t

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://api.deezer.com/search"

# Process-wide cache (persisted in DEEZER_CACHE_FILE).
_cache: dict | None = None


def _key(artist: str, title: str) -> str:
    """Cache key identical to the matching/year-map key (lowercase 'artist|title')."""
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

# Removes ONE parenthesized addition at the title end (e.g. "There Must Be an
# Angel (Playing With My Heart)" -> "There Must Be an Angel"). library.normalize
# only strips known additions (feat/version/remaster/...); a general
# parenthesized subtitle otherwise stays and depresses the title similarity.
_TRAILING_PAREN = _re.compile(r"\s*[\(\[][^\(\)\[\]]*[\)\]]\s*$")


def _strip_trailing_paren(text: str) -> str:
    """Cut off a trailing (…)/[…] addition (once)."""
    return _TRAILING_PAREN.sub("", text or "").strip()


def _passes(a_score: int, t_score: int) -> bool:
    """
    Validation rule for a hit. Primarily strict (both >= MATCH_MIN).
    Additionally ASYMMETRIC: if ONE field is very confident (>= STRONG), the
    relaxed threshold (>= RELAX) is enough for the other. Catches alias artists
    (title 100, artist low) and parenthesized titles (artist 100, title
    depressed) without allowing false hits when both are weak.
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
    Pick the BEST solid candidate from the Deezer hits (data[]). Solid =
    validation per _passes() (strict OR asymmetric, see above). For the title
    score the paren-stripped variant is also checked and the better of the two
    values taken (Deezer subtitles like "(Playing With My Heart)" should not
    count against the hit). Among the solid ones the one with the highest sum
    (artist+title) wins. Returns: (artist, title) or None (no valid hit -> raw
    values).
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
        # title_short omits "(feat. ...)" additions and usually matches better.
        cand_title = (item.get("title_short") or item.get("title") or "").strip()
        if not cand_artist or not cand_title:
            continue
        a_score = library.similarity(library.normalize(cand_artist), n_raw_artist)
        # Title score: better value from the full and the paren-stripped
        # comparison, on both sides (candidate AND raw value can carry the add).
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
    Return (artist, title) in canonical spelling via the Deezer search.

    Fall back to the unchanged raw values when the switch is off, no solid hit
    exists or a network/HTTP/JSON error occurs (rather UPPERCASE than wrongly
    guessed). The result is cached (misses too).
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
        # Unexpected cache format -> ignore and resolve anew.

    # Plain query "<artist> <title>" (the structured query returns 0 hits on
    # Deezer, see module docstring). httpx handles the URL encoding.
    query = f"{artist} {title}"
    result = (artist, title)
    try:
        with httpx.Client(timeout=config.HTTP_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(_SEARCH_URL, params={"q": query})
            resp.raise_for_status()
            payload = resp.json()
        # Deezer reports errors as {"error": {...}} with HTTP 200.
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

    # Always cache a result (the raw-value miss too), never leave it empty.
    cache[k] = [result[0], result[1]]
    _save_cache()
    return result
