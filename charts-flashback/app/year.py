"""
Hybrid year resolution for charts-flashback.

Goal: give each song as correct a release year as possible so it lands in the
right decade smart playlist (NSP) in Navidrome.

Strategy:
  1. MusicBrainz lookup (exact year, best effort): recording search
     artist+title, earliest studio first release (live/remix/karaoke/
     instrumental/demo/cover are excluded, score >= MB_SCORE_MIN). Rate limit
     (MB_RATE_SLEEP) + cache (misses too).
  2. Sanity check against the channel decade:
       - MB year is within the decade -> take the MB year.
       - MB year contradicts/empty/MB off -> decade midpoint (from+5).
       - no decade (theme/artist) + MB year -> MB year.
       - no decade + MB empty -> "" (no year tag).

HONEST: the exact year is best effort - MusicBrainz does not always get it
right. Reliable is the DECADE (via the channel + clamp). For the decade NSP
that is exactly what counts.
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
        logger.warning(t("flashback.mb.cache_write_failed", error=e))


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


def resolve_year(artist: str, title: str, title_year: str) -> str:
    """
    Return a year "YYYY" (or "").

    Strategy:
      1. title_year (from the FLASHBACK title suffix "(JJJJ)") - RELIABLE, is
         preferred. FLASHBACK states the release year itself.
      2. Only if no title year is present: MusicBrainz fallback (best effort,
         with rate limit + cache).
    """
    if title_year:
        return title_year
    if config.MB_ENABLED and artist and title:
        cache = _load_cache()
        key = f"{artist.lower()}|{title.lower()}"
        if key in cache:
            return cache[key] or ""
        mb, ok = _mb_year(artist, title)
        # Only cache solid results (the real miss "" too). On a transient
        # network/HTTP error (ok=False) do NOT cache, otherwise the song would,
        # because of the miss cache + seen state, never be asked for the real
        # year again - the next run should retry.
        if ok:
            cache[key] = mb
            _save_cache()
        return mb
    return ""
