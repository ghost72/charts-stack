"""
MusicBrainz release-year resolution for the charts-import year backfill.

The low-level MusicBrainz machinery (_lucene_phrase, _load_cache, _save_cache,
_parse_year, _mb_year) is copied UNCHANGED from charts-energy/app/year.py (which
in turn copied it from charts-flashback/app/year.py). The same copy-not-share
convention is used across the independent per-service Docker build contexts: a
shared package would need a packaging change, and the MB code is small and
stable.

On top of the copied machinery this module adds the helpers the backfill needs:
  - resolve_mb_year(artist, title) -> (year, ok, was_cached): cache first, then
    a live _mb_year call (misses cached, transient errors NOT cached).
  - is_cached(artist, title) -> bool: cache-only peek (hit OR cached miss), no
    network, no rate sleep - used to check the per-pass cap BEFORE a live query.
  - is_cached_miss(artist, title) -> bool: True when the key is cached empty.
  - cache_miss(artist, title) -> None: persist "" for the key.
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

# Process-wide cache (persisted in MB_CACHE_FILE).
_cache: dict | None = None


def _key(artist: str, title: str) -> str:
    """Cache/query key: lowercase 'artist|title'."""
    return f"{(artist or '').lower()}|{(title or '').lower()}"


def _lucene_phrase(s: str) -> str:
    """Escape backslash and quotes for a Lucene phrase."""
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
        logger.warning(t("import.yearfill.mb_cache_write_failed", error=e))


def _parse_year(value: str) -> int | None:
    """Extract a plausible 4-digit year from a date string."""
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
    Ask MusicBrainz for the earliest studio release year.
    Returns: (year, ok). year = "YYYY" or "" (no usable hit).
    ok = True when MusicBrainz answered cleanly (result solid, including an
    empty one); ok = False on network/HTTP/parse error (transient -> do NOT
    cache so the next run asks again).
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
        logger.debug(t("import.yearfill.mb_lookup_failed", artist=artist,
                       title=title, error=e))
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
        # (i) Recording top-level first-release-date.
        y = _parse_year(rec.get("first-release-date") or "")
        if y:
            years.append(y)
        # (ii) earliest release.date of this recording.
        for rel in rec.get("releases", []) or []:
            if isinstance(rel, dict):
                ry = _parse_year(rel.get("date") or "")
                if ry:
                    years.append(ry)
                # (iii) release-group first-release-date, if filled.
                rg = rel.get("release-group") if isinstance(rel, dict) else None
                if isinstance(rg, dict):
                    gy = _parse_year(rg.get("first-release-date") or "")
                    if gy:
                        years.append(gy)
    # API answered cleanly -> ok=True, even when no year was found (a real
    # miss, may be cached).
    if not years:
        return "", True
    return str(min(years)), True


def resolve_mb_year(artist: str, title: str) -> "tuple[str, bool, bool]":
    """
    Resolve a release year against the cache, then MusicBrainz.

    Returns: (year, ok, was_cached).
      - Cache hit (year OR cached miss): (cached_value, True, True); no network.
      - Live query: call _mb_year; cache a solid result (a real miss "" too),
        never a transient error. Returns (year, ok, False).
    When MB is disabled or artist/title is empty: ("", True, False) (no network,
    treated as a clean empty answer).
    """
    if not (config.MB_ENABLED and artist and title):
        return "", True, False
    cache = _load_cache()
    key = _key(artist, title)
    if key in cache:
        return (cache[key] or ""), True, True
    resolved, ok = _mb_year(artist, title)
    # Only cache solid results (a real miss "" too). On a transient
    # network/HTTP error (ok=False) do NOT cache so the next run retries.
    if ok:
        cache[key] = resolved
        _save_cache()
    return resolved, ok, False


def is_cached(artist: str, title: str) -> bool:
    """True when the lowercase 'artist|title' key is present in the cache at all
    (a real-year hit OR a cached miss). A cache-only peek: no network, no
    MB_RATE_SLEEP. Used to check the per-pass cap BEFORE issuing a live query."""
    if not (artist and title):
        return False
    return _key(artist, title) in _load_cache()


def is_cached_miss(artist: str, title: str) -> bool:
    """True when the lowercase 'artist|title' is cached with an empty value
    (MusicBrainz already gave up on this song)."""
    if not (artist and title):
        return False
    cache = _load_cache()
    key = _key(artist, title)
    return key in cache and not cache[key]


def cache_miss(artist: str, title: str) -> None:
    """Persist "" for the lowercase 'artist|title' key (same shape _mb_year
    caches a real miss), so a later import does not re-mark the song."""
    if not (artist and title):
        return
    cache = _load_cache()
    cache[_key(artist, title)] = ""
    _save_cache()
