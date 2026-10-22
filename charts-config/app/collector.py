"""
Read-only backend helpers for charts-config.

Two source groups, migrated verbatim from charts-dashboard (only the i18n key
namespace was re-based onto the config.* root):
  1. Source finder: Deezer playlist/artist search, Deezer genre catalog and the
     Last.fm tag check. All read-only, fail-open (every exception -> {ok:False,
     detail:...}, NEVER raise upward) over the public Deezer/Last.fm HTTP APIs.
  2. Energy channel browser: the confirmed non-regional Energy (NRJ) channels
     with a per-channel default-mode snippet plus a live active/active-mode
     status read from charts-energy's status JSON (mounted read-only).

Unlike the dashboard collector this module has NO Docker SDK and NO status/
connection/storage machinery: charts-config only talks to Deezer/Last.fm (WAN)
and reads the charts-energy status file. All functions are defensive: if a
source fails, they return a fail-open result instead of blowing up the request.
"""

import json
import logging
import os
import re
import time

import httpx

from . import config, i18n

logger = logging.getLogger("charts-config")


def _read_status_files() -> dict:
    """Read all <service>.json from STATUS_DIR. Returns: {service: data}."""
    out: dict = {}
    d = config.STATUS_DIR
    if not os.path.isdir(d):
        return out
    for name in os.listdir(d):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(d, name), "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                out[data.get("service") or name[:-5]] = data
        except (OSError, json.JSONDecodeError):
            continue
    return out


# ---------------------------------------------------------------------------
# Source finder (read-only helpers for the "source finder").
# All three functions follow exactly the same fail-open pattern: httpx with
# timeout=8, every exception -> {"ok": False, "detail": "not reachable
# (<ExcClass>)"}, NEVER raise upward. Deezer/Last.fm report errors as HTTP 200 +
# "error" field, so the field is checked additionally. The "snippet" strings are
# ready, parser-conformant CHARTS source JSONs for copy-pasting into the .env
# (see CHARTS-SOURCES.md).
# ---------------------------------------------------------------------------

# Allowed characters for Last.fm tags: word characters, whitespace and a few
# commonly occurring special characters (multi-word tags like "classic country"
# or "r&b" are ALLOWED).
_TAG_RE = re.compile(r"^[\w\s&/+-]+$", re.UNICODE)


def search_deezer_playlists(q: str) -> dict:
    """
    Deezer playlist search (read-only, fail-open, no cache).
    Returns a ready CHARTS source snippet per hit for copy-pasting.
    """
    q = (q or "").strip()
    if not (1 <= len(q) <= 100):
        return {"ok": False, "detail": i18n.t("config.finder.empty_query")}
    try:
        r = httpx.get("https://api.deezer.com/search/playlist",
                      params={"q": q, "limit": 25}, timeout=8,
                      follow_redirects=True)
        payload = r.json() if r.status_code == 200 else {}
        if r.status_code != 200 or (
                isinstance(payload, dict) and payload.get("error")):
            return {"ok": False, "detail": i18n.t(
                "config.conn.http_error", code=r.status_code)}
        results = []
        for item in (payload.get("data") or []):
            pid = item.get("id")
            if pid is None:
                continue
            pid = str(pid)
            snippet = (
                '{"service":"deezer","type":"playlist","source":"'
                + pid + '","limit":"all","enabled":true}')
            results.append({
                "id": pid,
                "title": item.get("title", ""),
                "nb_tracks": item.get("nb_tracks", 0),
                "user": ((item.get("user") or {}).get("name") or ""),
                "snippet": snippet,
            })
        return {"ok": True, "results": results}
    except Exception as e:
        return {"ok": False, "detail": i18n.t(
            "config.conn.unreachable", err=e.__class__.__name__)}


def search_deezer_artists(q: str) -> dict:
    """
    Deezer artist search (read-only, fail-open, no cache).
    Returns a ready CHARTS source snippet (type:artist) per hit for
    copy-pasting. nb_fan helps distinguish artists with the same name (e.g.
    there are two "Hecht").
    """
    q = (q or "").strip()
    if not (1 <= len(q) <= 100):
        return {"ok": False, "detail": i18n.t("config.finder.empty_query")}
    try:
        r = httpx.get("https://api.deezer.com/search/artist",
                      params={"q": q, "limit": 25}, timeout=8,
                      follow_redirects=True)
        payload = r.json() if r.status_code == 200 else {}
        if r.status_code != 200 or (
                isinstance(payload, dict) and payload.get("error")):
            return {"ok": False, "detail": i18n.t(
                "config.conn.http_error", code=r.status_code)}
        results = []
        for item in (payload.get("data") or []):
            aid = item.get("id")
            if aid is None:
                continue
            aid = str(aid)
            # limit:50 as a number (not "all"), because Deezer's /artist/top is
            # capped at ~100 anyway - 50 is a good default.
            snippet = (
                '{"service":"deezer","type":"artist","source":"'
                + aid + '","limit":50,"enabled":true}')
            results.append({
                "id": aid,
                "name": item.get("name", ""),
                "nb_fan": item.get("nb_fan", 0),
                "snippet": snippet,
            })
        return {"ok": True, "results": results}
    except Exception as e:
        return {"ok": False, "detail": i18n.t(
            "config.conn.unreachable", err=e.__class__.__name__)}


# Deezer genre list: cached once a day (own TTL, rarely changed). Module-global
# cache following the finder cache pattern.
_genre_cache: dict | None = None
_genre_ts: float = 0.0


def deezer_genres() -> dict:
    """
    Deezer genre catalog (read-only, fail-open, 24h cache).
    Returns on success: {"ok": True, "genres": [{"id","name"}], "fetched":
    "<ISO>"}; on error: {"ok": False, "detail": ..., "genres": []} (the frontend
    then falls back to the static list).
    """
    global _genre_cache, _genre_ts
    now = time.time()
    if _genre_cache is not None and (
            now - _genre_ts) < config.DASHBOARD_GENRE_CACHE_TTL:
        return _genre_cache
    try:
        r = httpx.get("https://api.deezer.com/genre", timeout=8,
                      follow_redirects=True)
        payload = r.json() if r.status_code == 200 else {}
        if r.status_code != 200 or (
                isinstance(payload, dict) and payload.get("error")):
            return {"ok": False, "detail": i18n.t(
                "config.conn.http_error", code=r.status_code),
                "genres": []}
        genres = []
        for item in (payload.get("data") or []):
            gid = item.get("id")
            if gid is None:
                continue
            genres.append({"id": str(gid), "name": item.get("name", "")})
        result = {"ok": True, "genres": genres,
                  "fetched": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        _genre_cache = result
        _genre_ts = now
        return result
    except Exception as e:
        return {"ok": False,
                "detail": i18n.t("config.conn.unreachable",
                                 err=e.__class__.__name__),
                "genres": []}


def lastfm_tag_check(q: str) -> dict:
    """
    Last.fm tag validation via tag.getTopTracks (read-only, fail-open).
    Without an API key -> configured:false. A tag counts as usable if the call
    returns HTTP 200 without an error field and a non-empty track list.
    On success a ready CHARTS source snippet is returned as well.
    """
    if not config.LASTFM_API_KEY:
        return {"ok": False, "configured": False,
                "detail": i18n.t("config.finder.lastfm.no_api_key")}
    q = (q or "").strip()
    if not (1 <= len(q) <= 50) or not _TAG_RE.match(q):
        return {"ok": False, "configured": True,
                "detail": i18n.t("config.finder.lastfm.invalid_tag")}
    try:
        r = httpx.get("https://ws.audioscrobbler.com/2.0/", follow_redirects=True,
                      params={"method": "tag.gettoptracks", "tag": q,
                              "api_key": config.LASTFM_API_KEY,
                              "format": "json", "limit": 20}, timeout=8)
        payload = r.json() if r.status_code == 200 else {}
        if r.status_code != 200 or not isinstance(payload, dict) or \
                "error" in payload:
            # payload.message = third-party Last.fm API text -> passed through
            # verbatim (not our prose, no key); only the HTTP fallback is keyed.
            detail = (payload.get("message")
                      if isinstance(payload, dict) else None) \
                or i18n.t("config.conn.http_error", code=r.status_code)
            return {"ok": False, "configured": True, "detail": detail}
        tracks = ((payload.get("tracks") or {}).get("track") or [])
        if not tracks:
            return {"ok": False, "configured": True,
                    "detail": i18n.t("config.finder.lastfm.no_tracks")}
        # The REAL playlist pull uses limit:100; expose the tag's actual total
        # track count (not the small probe size) so the UI is not misleading.
        total = None
        raw_total = ((payload.get("tracks") or {}).get("@attr") or {}).get(
            "total")
        try:
            total = int(raw_total)
        except (TypeError, ValueError):
            total = None
        sample = []
        for t in tracks[:10]:
            artist = ((t.get("artist") or {}).get("name") or "").strip()
            title = (t.get("name") or "").strip()
            if artist or title:
                sample.append(f"{artist} - {title}".strip(" -"))
        snippet = (
            '{"service":"lastfm","method":"tag.getTopTracks","tag":"'
            + q + '","limit":100,"enabled":true}')
        return {"ok": True, "configured": True, "tag": q,
                "total": total, "sample": sample,
                "snippet": snippet}
    except Exception as e:
        return {"ok": False, "configured": True,
                "detail": i18n.t("config.conn.unreachable",
                                 err=e.__class__.__name__)}


# ---------------------------------------------------------------------------
# Energy channel browser (read-only helper for the "Energy channels" panel).
# Returns the confirmed non-regional Energy (NRJ) webradio channels with a
# per-channel default-mode snippet, a derived missing-file name, and a live
# active/active-mode status read from charts-energy's status JSON. Fully
# fail-open (never raises). See the finder functions above for the style.
# ---------------------------------------------------------------------------

# The confirmed NON-regional Energy channels as (display, slug, genre) tuples.
# KEEP IN SYNC with charts-energy/app/discover.py :: CONFIRMED_CHANNELS; a drift
# test (tests/test_energy_channels.py) enforces this. The 4 genre=="Regional"
# channels (energyzuerich/bern/basel/luzern) are intentionally excluded: they
# add noise and all map to the same generic default. This is the same
# "copy-into-every-service" discipline the repo uses for i18n.py / status.py;
# charts-config runs in a separate container and cannot import charts-energy.
_ENERGY_CHANNELS: list[tuple[str, str, str]] = [
    ("Energy Made In Switzerland", "energymadeinswitzerland", "Swiss/Mundart"),
    ("Energy Pop", "energypop", "Pop"),
    ("Energy Hits", "energyhits", "Hits"),
    ("Energy Dance", "energydance", "Dance"),
    ("Energy Rock", "energyrock", "Rock"),
    ("Energy Urban", "energyurban", "Rap/Hip-Hop"),
    ("Energy 80s", "energy80s", "80s"),
    ("Energy 90s", "energy90s", "90s"),
    ("Energy 00s", "energy00s", "00s"),
    ("Energy 10s", "energy10s", "10s"),
    ("Energy 20s", "energy20s", "20s"),
    ("Energy Workout", "energyworkout", "Workout"),
    ("Energy Lounge", "energylounge", "Lounge"),
    ("Energy Love", "energylove", "Love/Ballads"),
    ("Energy Latin", "energylatin", "Latin"),
    ("Energy At Work", "energyatwork", "At Work"),
    ("Energy Me Time", "energymetime", "Me Time"),
    ("Energy Xmas", "energyxmas", "Christmas"),
]

# Decade slugs default to `library` mode (hybrid year resolution per channel).
_ENERGY_DECADE_SLUGS = frozenset(
    ("energy80s", "energy90s", "energy00s", "energy10s", "energy20s"))


def _energy_default_mode(slug: str) -> str:
    """Return the suggested default mode for a channel slug (pure).

    Decade channels -> 'library'; 'energymadeinswitzerland' -> 'feed'; every
    other channel -> 'own'. This only selects the DEFAULT shown in the snippet;
    the user may run a channel in any mode.
    """
    if slug in _ENERGY_DECADE_SLUGS:
        return "library"
    if slug == "energymadeinswitzerland":
        return "feed"
    return "own"


def _energy_default_names(slug: str) -> tuple[str, str]:
    """Derive the OWN-mode (playlist, missing_file) names from a slug (pure).

    Re-implements charts-energy/app/config.py :: _default_names exactly (strip a
    leading 'energy', re-prefix 'energy-'): energypop -> ('energy-pop.m3u',
    'missing-energy-pop.txt'). charts-config cannot import the charts-energy
    package (separate container), so this one small derivation is duplicated; a
    unit test pins it against the documented examples so it cannot drift.
    """
    rest = slug[len("energy"):] if slug.startswith("energy") else slug
    rest = rest.strip("-") or slug
    return f"energy-{rest}.m3u", f"missing-energy-{rest}.txt"


def _energy_snippet(display: str, slug: str) -> str:
    """Build one ready-to-paste ENERGY_CHANNELS array element (compact JSON).

    Built by explicit string concatenation (not json.dumps) like the finder
    snippets, so the compact, parser-conformant form is guaranteed and the key
    order is fixed: slug, (title for own), mode, (playlist for own / theme for
    feed), enabled. feed is only ever emitted for energymadeinswitzerland with
    theme 'mundart' byte-for-byte.
    """
    mode = _energy_default_mode(slug)
    if mode == "library":
        return ('{"slug":"' + slug + '","mode":"library","enabled":true}')
    if mode == "feed":
        return ('{"slug":"' + slug
                + '","mode":"feed","theme":"mundart","enabled":true}')
    # own: carries the user-visible playlist name + a title.
    playlist, _missing = _energy_default_names(slug)
    return ('{"slug":"' + slug + '","title":"' + display
            + '","mode":"own","playlist":"' + playlist + '","enabled":true}')


def energy_channels() -> dict:
    """Return the confirmed Energy channels with snippet + live status.

    Reads the 'charts-energy' status entry (via _read_status_files) for its
    'status' and 'channels'. A channel is ACTIVE iff status is 'ok'/'running'
    AND its slug is present in 'channels' with enabled True. Fully fail-open: a
    missing/old status file, a missing 'channels' key, or status 'idle'/'error'
    yields every channel inactive. Returns {"ok": True, "channels": [...]}.
    """
    try:
        status_data = _read_status_files().get("charts-energy") or {}
        svc_status = status_data.get("status")
        raw_channels = status_data.get("channels")
        # Build {slug: active_mode} from well-formed, enabled entries only.
        # Defensive: skip anything that is not a dict with a string slug. A mode
        # outside own/feed/library is still recorded verbatim (badge prints it).
        running: dict[str, str] = {}
        if isinstance(raw_channels, list):
            for entry in raw_channels:
                if not isinstance(entry, dict):
                    continue
                slug = entry.get("slug")
                if not isinstance(slug, str) or not slug:
                    continue
                if entry.get("enabled") is not True:
                    continue
                mode = entry.get("mode")
                running[slug] = mode if isinstance(mode, str) else ""
        # Positive-whitelist predicate: only ok/running count as operating.
        service_live = svc_status in ("ok", "running")

        channels = []
        for display, slug, genre in _ENERGY_CHANNELS:
            mode = _energy_default_mode(slug)
            if mode == "own":
                _playlist, missing_file = _energy_default_names(slug)
            elif mode == "library":
                # All library channels share this one file (charts-energy
                # default ENERGY_LIBRARY_MISSING_FILE).
                missing_file = "missing-energy-library.txt"
            else:  # feed
                missing_file = ""
            active = service_live and slug in running
            channels.append({
                "display": display,
                "slug": slug,
                "genre": genre,
                "mode": mode,
                "snippet": _energy_snippet(display, slug),
                "missing_file": missing_file,
                "active": active,
                "active_mode": running.get(slug, "") if active else "",
            })
        return {"ok": True, "channels": channels}
    except Exception as e:
        # Unexpected failure -> fail-open with the finder's unreachable prose.
        logger.warning(i18n.t("config.conn.unreachable",
                              err=e.__class__.__name__))
        return {"ok": False,
                "detail": i18n.t("config.conn.unreachable",
                                 err=e.__class__.__name__),
                "channels": []}
