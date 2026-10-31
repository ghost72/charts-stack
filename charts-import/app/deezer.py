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
_ALBUM_URL = "https://api.deezer.com/album"
_TRACK_URL = "https://api.deezer.com/track"

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


def _ranked_items(data: list, raw_artist: str, raw_title: str) -> list:
    """
    All passing Deezer items (per _passes), ranked best-first by combined score.
    Returns a list of (combined_score, item_dict). The EXACT scoring the casing
    resolver uses - so _best_item and resolve_release_meta share one ranking.
    """
    n_raw_artist = _normalize(raw_artist)
    n_raw_title = _normalize(raw_title)
    n_raw_title_stripped = _normalize(_strip_trailing_paren(raw_title))
    ranked: list[tuple[int, dict]] = []
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
            ranked.append((a_score + t_score, item))
    ranked.sort(key=lambda r: r[0], reverse=True)
    return ranked


def _best_item(data: list, raw_artist: str, raw_title: str) -> "dict | None":
    """Best reliable Deezer hit item dict (per _passes) or None."""
    ranked = _ranked_items(data, raw_artist, raw_title)
    return ranked[0][1] if ranked else None


def _best_canonical(data: list, raw_artist: str, raw_title: str):
    """
    Best reliable Deezer hit as (artist, title) or None. Thin wrapper over
    _best_item - public behaviour of resolve_canonical is unchanged.
    """
    item = _best_item(data, raw_artist, raw_title)
    if not item:
        return None
    cand_artist = ((item.get("artist") or {}).get("name") or "").strip()
    cand_title = (item.get("title_short") or item.get("title") or "").strip()
    return cand_artist, cand_title


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


# --- Import-time release-metadata resolver ----------------------------------
# resolve_release_meta resolves release year + a real (non-compilation) album +
# album year + track number for a song so EVERY imported song is tagged
# uniformly. It reuses the casing matcher's ranking (_ranked_items / _best_item)
# and does two bounded detail GETs (/album/<id>, /track/<id>) only after a
# non-compilation album has been selected. Results (including a miss) are cached
# under a "meta|" key namespace in DEEZER_CACHE_FILE, separate from the
# resolve_canonical [artist, title] list entries, so a cache hit needs no
# network at all. Fail-open (no cache) on network/HTTP/parse/API error.

_META_PREFIX = "meta|"

# Upper bound on ranked candidates whose /album/<id> detail the preference scan
# fetches. Bounds the extra GETs on a rejected-first song (zero extra when the
# first candidate is accepted) and is pinned by the call-counter test.
_PREFERENCE_SCAN_MAX = 4


def _meta_key(artist: str, title: str) -> str:
    return _META_PREFIX + f"{artist.lower()}|{title.lower()}"


def _parse_year(value: str) -> str:
    """Leading 4-digit year of a 'YYYY-MM-DD' date string, else ""."""
    if value and len(value) >= 4 and value[:4].isdigit():
        return value[:4]
    return ""


def _get_json(client, url: str) -> "dict | None":
    """One GET returning the parsed JSON dict, or None on error/API-error."""
    resp = client.get(url)
    resp.raise_for_status()
    payload = resp.json()
    if not isinstance(payload, dict) or payload.get("error"):
        return None
    return payload


def resolve_release_meta(artist: str, title: str):
    """
    Resolve (year, album, album_year, track) for a song via Deezer. Returns a
    resolve.ResolvedMeta; an all-empty one on no reliable hit / network error
    (fail-open). Respects the per-pass Deezer live-lookup budget. The album is
    dropped (="") when the only candidates are compilations so the importer
    keeps album=title.
    """
    from . import resolve  # lazy to avoid an import cycle at module load

    if not config.DEEZER_CANONICAL_ENABLED:
        return resolve.ResolvedMeta()
    a = (artist or "").strip()
    ti = (title or "").strip()
    if not a or not ti or resolve.is_no_resolve_artist(a) or ti.lower() == "unknown album":
        # VA/unknown file artist: refuse before any network call (and MB is
        # skipped upstream in resolve.resolve_metadata for the same input).
        return resolve.ResolvedMeta()

    cache = _load_cache()
    mk = _meta_key(a, ti)
    if mk in cache:
        cached = cache[mk]
        if isinstance(cached, list) and len(cached) == 4:
            return resolve.ResolvedMeta(year=cached[0], album=cached[1],
                                        album_year=cached[2], track=cached[3])
        return resolve.ResolvedMeta()  # cached miss ([])

    # A live lookup is needed - respect the per-pass cap (cache hits above never
    # reach here).
    if not resolve.deezer_budget_available():
        return resolve.ResolvedMeta()
    resolve.note_deezer_live()

    year = album = album_year = track = ""
    cacheable = False
    try:
        with httpx.Client(timeout=config.HTTP_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(_SEARCH_URL, params={"q": f"{a} {ti}"})
            resp.raise_for_status()
            payload = resp.json()
            if isinstance(payload, dict) and payload.get("error"):
                # API error (rate limit/quota) -> NOT a reliable miss, don't cache.
                logger.debug(t("import.deezer.api_error", artist=a, title=ti,
                               error=payload["error"]))
                return resolve.ResolvedMeta()
            data = payload.get("data", []) if isinstance(payload, dict) else []
            # The API answered cleanly -> the result (hit OR miss) is cacheable.
            cacheable = True
            ranked = _ranked_items(data, a, ti)
            if ranked:
                best_item = ranked[0][1]
                chosen = best_item  # safe source of year/track even if no album
                accepted = False
                # Bounded preference scan: prefer a studio-album release over a
                # single/compilation/junk one. Reads record_type + album artist
                # from the SAME /album/<id> detail the resolver already fetched;
                # no new HTTP endpoint. The ranking already enforces artist+title
                # similarity, so iterating never drifts to the wrong song.
                for _score, item in ranked[:_PREFERENCE_SCAN_MAX]:
                    alb = (item.get("album") or {})
                    alb_title = (alb.get("title") or "").strip()
                    # Cheap pre-filter (no network): skip obvious comp/junk.
                    if alb_title and (resolve.is_compilation_title(alb_title)
                                      or resolve.is_junk_album_title(alb_title)):
                        continue
                    album_id = alb.get("id")
                    if not album_id:
                        # Cannot verify the structured signals -> fail safe.
                        continue
                    adata = _get_json(client, f"{_ALBUM_URL}/{album_id}")
                    if not adata:
                        continue
                    aart = adata.get("artist") or {}
                    if resolve.album_detail_acceptable(
                            alb_title, adata.get("record_type"),
                            aart.get("id"), aart.get("name")):
                        # Store the thin search-hit alb_title (NOT adata["title"])
                        # to keep the pre-change observable output + cache shape.
                        album = alb_title
                        chosen = item
                        accepted = True
                        album_year = _parse_year(adata.get("release_date") or "")
                        break
                if not accepted:
                    # No candidate passed: leave album empty (importer keeps
                    # album=title) but still source album_year from the best
                    # hit's detail so the `year = album_year` fallback below
                    # does not regress (Finding 2).
                    balb = (best_item.get("album") or {})
                    balb_id = balb.get("id")
                    if balb_id:
                        adata = _get_json(client, f"{_ALBUM_URL}/{balb_id}")
                        if adata:
                            album_year = _parse_year(adata.get("release_date") or "")
                # Track detail GET -> track year (preferred) + track position.
                track_id = chosen.get("id")
                if track_id:
                    tdata = _get_json(client, f"{_TRACK_URL}/{track_id}")
                    if tdata:
                        year = _parse_year(tdata.get("release_date") or "")
                        pos = tdata.get("track_position")
                        if isinstance(pos, int) and pos > 0:
                            track = str(pos)
                # Fall back to the album year for the written date.
                if not year:
                    year = album_year
    except (httpx.HTTPError, ValueError) as e:
        logger.debug(t("import.deezer.lookup_failed", artist=a, title=ti, error=e))
        return resolve.ResolvedMeta()

    if cacheable:
        cache[mk] = [year, album, album_year, track]
        _save_cache()
    return resolve.ResolvedMeta(year=year, album=album, album_year=album_year,
                                track=track)
