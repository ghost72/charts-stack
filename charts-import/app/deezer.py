"""
Deezer casing resolution for charts-import (last resort).

charts-flashback already canonicalizes artist/title via Deezer before writing
them into the missing list. Still, UPPERCASE raw values can slip through (e.g.
if flashback ran before the matching fix, or a run narrowly discarded the Deezer
hit). So an UPPERCASE name NEVER lands in the library, charts-import resolves the
canonical spelling HERE once more when filing in a chart song - as the final,
central authority right before writing.

Self-contained (own normalization, no library module needed) so it runs in the
charts-import container without an extra dependency. Logic identical to
charts-flashback/app/deezer.py (verified there against the live API):

  GET https://api.deezer.com/search?q=<artist> <title>  (plain query; the
  structured advanced query returns 0 hits on Deezer).
  Hit validation via normalize()/similarity() with an asymmetric threshold
  (_passes): strictly both >= MATCH_MIN, OR one field >= STRONG and the other
  >= RELAX. Plus a trailing-paren strip in the title comparison. No reliable
  hit -> unchanged raw values (rather UPPERCASE than wrong).
  Cache (also misses) in DEEZER_CACHE_FILE.
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

# --- Normalization (identical to charts-flashback/charts-sync library.py) ---
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
    """Cache key lowercase 'artist|title' (like flashback/charts-import)."""
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
    """Cuts off a trailing (…)/[…] addition (once)."""
    return _TRAILING_PAREN.sub("", text or "").strip()


def _passes(a_score: int, t_score: int) -> bool:
    """
    Validation: strict (both >= MATCH_MIN) OR asymmetric (one field >= STRONG,
    the other >= RELAX). Catches alias artists (title certain, artist low) and
    parenthetical titles (artist certain, title depressed) without letting
    doubly weak mismatches through.
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
    """Best reliable Deezer hit (per _passes) or None."""
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
    (artist, title) in canonical spelling via the Deezer search, otherwise the
    unchanged raw values (switch off / no reliable hit / network error). Only
    reliable results are cached (API reachable, with or without a hit);
    network error/timeout/API error are NOT cached, so a transient outage does
    not permanently freeze the raw value.
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
    # Only cache REAL results (API reachable - with or without a hit). On a
    # network error/timeout/API error do NOT cache, otherwise a merely
    # transient outage would permanently freeze the raw value (UPPERCASE name)
    # and the next run would never ask again. This way the case heals itself as
    # soon as Deezer is reachable again.
    cacheable = False
    try:
        with httpx.Client(timeout=config.HTTP_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(_SEARCH_URL, params={"q": query})
            resp.raise_for_status()
            payload = resp.json()
        if isinstance(payload, dict) and payload.get("error"):
            # Deezer reports an API error (e.g. rate limit/quota) - that is NOT
            # a reliable "does not know the song", so do not cache.
            logger.debug(t("import.deezer.api_error", artist=artist,
                           title=title, error=payload["error"]))
        else:
            data = payload.get("data", []) if isinstance(payload, dict) else []
            best = _best_canonical(data, artist, title)
            # The API answered cleanly -> the result is reliable (hit OR a real
            # miss), both may be cached.
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
