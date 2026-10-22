"""
Hybrid year resolution for charts-energy library mode.

Goal: give each library-mode Energy song as correct a release year as possible
so it lands in the right decade smart playlist (NSP) in Navidrome.

Strategy:
  1. MusicBrainz lookup (exact year, best effort): recording search
     artist+title, earliest studio first release (live/remix/karaoke/
     instrumental/demo/cover are excluded, score >= MB_SCORE_MIN). Rate limit
     (MB_RATE_SLEEP) + cache (misses too).
  2. Sanity check against the channel decade (resolve_year):
       - MB year is within the decade -> take the MB year.
       - MB year contradicts/empty/MB off -> decade midpoint (from+5).
       - no decade + MB year -> MB year.
       - no decade + MB empty -> "" (no year tag).

HONEST: the exact year is best effort - MusicBrainz does not always get it
right. Reliable is the DECADE (via the channel slug + clamp). For the decade
NSP that is exactly what counts.

The MusicBrainz machinery (_lucene_phrase, _load_cache, _save_cache,
_parse_year, _mb_year) is copied unchanged from charts-flashback/app/year.py;
only resolve_year carries NEW decade-clamp logic (flashback's resolver has no
decade handling).
"""

import json
import logging
import os
import re
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
        logger.warning(t("energy.mb.cache_write_failed", error=e))


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
        logger.debug(t("energy.mb.lookup_failed", artist=artist, title=title, error=e))
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


def resolve_year(artist: str, title: str, decade: str) -> str:
    """
    Resolve a release year "YYYY" (or "") for a library-mode Energy song.

    `decade` is a 4-digit "from" year string (e.g. "1980") or "" (no decade).
    NEW logic for charts-energy (flashback's resolve_year has no decade clamp);
    only _mb_year/_parse_year/_lucene_phrase/_load_cache/_save_cache are copied
    from flashback. The branch table below is normative and is pinned by
    test_year_resolve.py:

        from_year = int(decade) if re.fullmatch(r"\\d{4}", decade) else None
        mb = _mb_year(artist, title)[0]   # "" if MB off / no hit / transient

        if from_year is not None:                      # decade channel
            if mb and from_year <= int(mb) <= from_year + 9:
                return mb                              # (A) MB inside decade
            return str(from_year + 5)                  # (B) MB outside/empty
        else:                                          # non-decade channel
            return mb                                  # (C) MB year or ""

    The MB result itself is resolved via the copied _mb_year + a lowercase
    'artist|title' cache lookup (misses cached, transient errors not cached),
    exactly as flashback does; this function only adds the clamp table on top.
    """
    from_year = int(decade) if re.fullmatch(r"\d{4}", decade or "") else None

    mb = ""
    if config.MB_ENABLED and artist and title:
        cache = _load_cache()
        key = f"{artist.lower()}|{title.lower()}"
        if key in cache:
            mb = cache[key] or ""
        else:
            resolved, ok = _mb_year(artist, title)
            # Only cache solid results (a real miss "" too). On a transient
            # network/HTTP error (ok=False) do NOT cache so the next run
            # retries.
            if ok:
                cache[key] = resolved
                _save_cache()
            mb = resolved

    if from_year is not None:                      # decade channel
        if mb and from_year <= int(mb) <= from_year + 9:
            return mb                              # (A) MB inside the decade
        return str(from_year + 5)                  # (B) MB outside/empty -> mid
    return mb                                      # (C) MB year, or ""
