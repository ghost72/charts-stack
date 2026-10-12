"""
Collects the overall state of the charts-stack for the dashboard.

Three sources:
  1. Status JSONs (written by the charts services) -> exact run statistics.
  2. Docker engine (via the socket) -> running/healthy per container.
  3. Live checks: slskd connection (/api/v0/server), Lidarr ping and the
     external/dependent services (ListenBrainz/Deezer/Last.fm + iTunes +
     Pushover validate + FLASHBACK FM GraphQL) via _external_status().
Plus storage/inventory numbers from the mounted folders.

All functions are defensive: if a source fails, it returns "unknown" instead of
blowing up the whole request.
"""

import json
import logging
import os
import re
import threading
import time

import httpx

from . import config, i18n

logger = logging.getLogger("charts-dashboard")

# Lazy-initialize the Docker SDK (degrades gracefully if the socket is missing).
try:
    import docker  # type: ignore
    _docker_client = docker.from_env()
except Exception as e:  # pragma: no cover
    logger.warning("Docker-SDK nicht verfügbar: %s", e)
    _docker_client = None


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


def _container_states() -> dict:
    """
    Docker state per monitored container.
    Returns: {name: {"state": "running"/"exited"/..., "health": "healthy"/
             "none"/..., "since": "<ISO>", "ok": bool}}
    """
    out: dict = {}
    for name in config.WATCH_CONTAINERS:
        info = {"state": "missing", "health": "none", "since": "", "ok": False}
        if _docker_client is not None:
            try:
                c = _docker_client.containers.get(name)
                st = c.attrs.get("State", {})
                info["state"] = st.get("Status", "unknown")
                health = (st.get("Health") or {}).get("Status")
                info["health"] = health or "none"
                info["since"] = st.get("StartedAt", "") or ""
                info["ok"] = (
                    info["state"] == "running"
                    and info["health"] in ("healthy", "none")
                )
            except Exception:
                # Container not found etc. -> missing stays.
                pass
        out[name] = info
    return out


def _slskd_status() -> dict:
    """slskd connection status + rough transfer numbers."""
    out = {"ok": False, "state": i18n.t("dashboard.conn.slskd.state_unknown"),
           "downloads_active": None, "detail": ""}
    if not config.SLSKD_API_KEY:
        out["detail"] = i18n.t("dashboard.conn.slskd.no_api_key")
        return out
    headers = {"X-API-Key": config.SLSKD_API_KEY}
    base = config.SLSKD_HOST.rstrip("/")
    try:
        r = httpx.get(f"{base}/api/v0/server", headers=headers, timeout=8)
        r.raise_for_status()
        srv = r.json()
        out["state"] = srv.get("state", "?")
        out["ok"] = bool(srv.get("isLoggedIn"))
    except Exception as e:
        out["detail"] = i18n.t("dashboard.conn.unreachable",
                               err=e.__class__.__name__)
        return out
    # Count active downloads (optional, best effort).
    try:
        r = httpx.get(f"{base}/api/v0/transfers/downloads", headers=headers, timeout=8)
        if r.status_code == 200:
            data = r.json()
            active = 0
            for user in data:
                for d in user.get("directories", []):
                    for f in d.get("files", []):
                        state = (f.get("state") or "").lower()
                        if "completed" not in state and "cancelled" not in state:
                            active += 1
            out["downloads_active"] = active
    except Exception:
        pass
    return out


def _lidarr_status() -> dict:
    """Lidarr reachability (optional)."""
    out = {"configured": bool(config.LIDARR_URL and config.LIDARR_API_KEY),
           "ok": False, "detail": ""}
    if not out["configured"]:
        out["detail"] = i18n.t("dashboard.conn.lidarr.not_configured")
        return out
    try:
        url = config.LIDARR_URL.rstrip("/") + "/api/v1/system/status"
        r = httpx.get(url, headers={"X-Api-Key": config.LIDARR_API_KEY}, timeout=8)
        r.raise_for_status()
        out["ok"] = True
        # "v{version}" is deliberately kept a literal format string (cosmetic
        # version marker, not prose) - not translated.
        out["detail"] = f"v{r.json().get('version', '?')}"
    except Exception as e:
        out["detail"] = i18n.t("dashboard.conn.unreachable",
                               err=e.__class__.__name__)
    return out


# Live reachability of the EXTERNAL chart sources (WAN to third parties).
# Cached following the _dir_stats pattern (module-global cache + timestamp)
# because the dashboard polls every 15s - without a cache every refresh would
# trigger three WAN requests. TTL: config.EXTERNAL_TTL (default 120s).
_ext_cache: dict | None = None
_ext_ts: float = 0.0


def _external_status() -> dict:
    """
    Live reachability of the external/dependent services (ListenBrainz, Deezer,
    Last.fm, iTunes info only, Pushover). Following the _slskd_status/
    _lidarr_status pattern: fail-open (every exception -> ok=False + detail,
    NEVER raise upward), timeout=8. Cached (EXTERNAL_TTL) because the dashboard
    polls every 15s - without a cache a real Pushover auth call (among others)
    would go out per refresh.

    Returns: {service: {"ok": bool, "detail": str}} for five services; lastfm +
    pushover additionally with "configured": bool.
    """
    global _ext_cache, _ext_ts
    now = time.time()
    if _ext_cache is not None and (now - _ext_ts) < config.EXTERNAL_TTL:
        return _ext_cache

    # ListenBrainz: /1/validate-token WITHOUT a token -> HTTP 400 = reachable.
    # Hence NO raise_for_status(): ok=True as soon as any response came back.
    # follow_redirects=True: ListenBrainz redirects its endpoints via 308 to the
    # URL with a trailing slash; without following, the check lands on the
    # redirect instead of the real validate-token response (green, but only
    # because "some response came" - with following, the check is real).
    lb = {"ok": False, "detail": ""}
    try:
        r = httpx.get("https://api.listenbrainz.org/1/validate-token", timeout=8,
                      follow_redirects=True)
        lb["ok"] = True
        # No detail on success: the UI already shows "reachable" as the status
        # line. Only fill detail if it says something additional.
        lb["detail"] = ""
    except Exception as e:
        lb["detail"] = i18n.t("dashboard.conn.unreachable",
                              err=e.__class__.__name__)

    # Deezer: /chart/0/tracks?limit=1 -> 200 WITHOUT an 'error' field. Deezer
    # reports errors as HTTP 200 with an error object, so check both.
    dz = {"ok": False, "detail": ""}
    try:
        r = httpx.get("https://api.deezer.com/chart/0/tracks",
                      params={"limit": 1}, timeout=8, follow_redirects=True)
        payload = r.json() if r.status_code == 200 else {}
        dz["ok"] = r.status_code == 200 and not (
            isinstance(payload, dict) and payload.get("error"))
        dz["detail"] = "" if dz["ok"] else i18n.t(
            "dashboard.conn.http_error", code=r.status_code)
    except Exception as e:
        dz["detail"] = i18n.t("dashboard.conn.unreachable",
                              err=e.__class__.__name__)

    # Last.fm: optional, only with an API key (no anonymous ping). Without a
    # key: configured=False -> UI shows the tile "muted", NOT red.
    lf = {"configured": bool(config.LASTFM_API_KEY), "ok": False, "detail": ""}
    if lf["configured"]:
        try:
            r = httpx.get("https://ws.audioscrobbler.com/2.0/",
                          params={"method": "chart.gettoptracks", "limit": 1,
                                  "format": "json",
                                  "api_key": config.LASTFM_API_KEY},
                          timeout=8, follow_redirects=True)
            payload = r.json() if r.status_code == 200 else {}
            lf["ok"] = r.status_code == 200 and "error" not in payload
            lf["detail"] = "" if lf["ok"] else i18n.t(
                "dashboard.conn.http_error", code=r.status_code)
        except Exception as e:
            lf["detail"] = i18n.t("dashboard.conn.unreachable",
                                  err=e.__class__.__name__)
    else:
        lf["detail"] = i18n.t("dashboard.conn.lastfm.no_api_key")

    # iTunes/Apple: INFO only, NEVER coupled to the red logic. 429/403 =
    # throttled, not dead -> still ok=True (reachable), detail distinguishes.
    it = {"ok": False, "detail": ""}
    try:
        r = httpx.get("https://itunes.apple.com/search",
                      params={"term": "test", "limit": 1}, timeout=8,
                      follow_redirects=True)
        if r.status_code in (429, 403):
            it["ok"] = True
            # Language-independent machine signal (like ok/configured): the
            # frontend keys the "warn" colour off this flag, NOT off a German
            # regex over the (now translated) detail text.
            it["throttled"] = True
            it["detail"] = i18n.t("dashboard.conn.itunes.throttled")
        else:
            it["ok"] = r.status_code == 200
            it["detail"] = "" if it["ok"] else i18n.t(
                "dashboard.conn.http_error", code=r.status_code)
    except Exception as e:
        it["detail"] = i18n.t("dashboard.conn.unreachable",
                              err=e.__class__.__name__)

    # Pushover: real validate call (POST /1/users/validate.json). Uses NO
    # message budget but really checks token+user. Only with both credentials;
    # otherwise configured=False -> UI shows the tile "muted", not red. Valid:
    # HTTP 200 + {"status":1}; invalid: HTTP 400 + {"status":0}. That is why the
    # TTL cache MUST take effect (otherwise an auth call on every 15s refresh).
    po = {"configured": bool(config.PUSHOVER_TOKEN and config.PUSHOVER_USER),
          "ok": False, "detail": ""}
    if po["configured"]:
        try:
            r = httpx.post("https://api.pushover.net/1/users/validate.json",
                           data={"token": config.PUSHOVER_TOKEN,
                                 "user": config.PUSHOVER_USER}, timeout=8)
            payload = r.json() if r.headers.get(
                "content-type", "").startswith("application/json") else {}
            po["ok"] = r.status_code == 200 and (
                isinstance(payload, dict) and payload.get("status") == 1)
            # detail = plain-text extra info, consistent with the other checks.
            # The plain-text verdict ("credentials valid/invalid") is set by the
            # UI itself from pu.ok - do NOT repeat it here, otherwise the same
            # sentence appears twice in the tile.
            po["detail"] = (i18n.t("dashboard.conn.pushover.ok") if po["ok"]
                            else i18n.t("dashboard.conn.pushover.rejected"))
        except Exception as e:
            po["detail"] = i18n.t("dashboard.conn.unreachable",
                                  err=e.__class__.__name__)
    else:
        po["detail"] = i18n.t("dashboard.conn.pushover.not_configured")

    # FLASHBACK FM: GraphQL live check (LastPlayed persisted query). Only with a
    # set hash; otherwise configured=False -> UI "muted". ok only on HTTP 200
    # WITHOUT a GraphQL errors block AND with a non-empty streams list. A dead
    # persisted query hash (API rebuilt) -> ok=False -> red tile -> signal
    # "fetch a new FLASHBACK_HASH".
    fb = {"configured": bool(config.FLASHBACK_HASH), "ok": False, "detail": ""}
    if fb["configured"]:
        try:
            import json as _json
            _vars = _json.dumps({"streamName": "flashbackfm.ffm", "limitTo": 1},
                                separators=(",", ":"))
            r = httpx.get(
                "https://www.flashbackfm.ch/api/pub/gql/flashbackfm/LastPlayed/"
                + config.FLASHBACK_HASH,
                params={"variables": _vars},
                headers={"accept": "application/json"}, timeout=8,
                follow_redirects=True)
            payload = r.json() if r.status_code == 200 else {}
            has_err = isinstance(payload, dict) and payload.get("errors")
            streams = (((payload.get("data") or {}).get("audioPlayer") or {})
                       .get("streams") or []) if isinstance(payload, dict) else []
            fb["ok"] = r.status_code == 200 and not has_err and bool(streams)
            if fb["ok"]:
                # Leave detail EMPTY: the tile already shows the status
                # "reachable" in line 1 (fb.ok). An additional "reachable" in
                # detail would double the line (like Deezer: single line only).
                fb["detail"] = ""
            elif has_err:
                fb["detail"] = i18n.t("dashboard.conn.flashback.bad_hash")
            else:
                fb["detail"] = i18n.t("dashboard.conn.http_error",
                                      code=r.status_code)
        except Exception as e:
            fb["detail"] = i18n.t("dashboard.conn.unreachable",
                                  err=e.__class__.__name__)
    else:
        fb["detail"] = i18n.t("dashboard.conn.flashback.no_hash")

    result = {"listenbrainz": lb, "deezer": dz, "lastfm": lf, "itunes": it,
              "pushover": po, "flashback": fb}
    _ext_cache = result
    _ext_ts = now
    return result


# ---------------------------------------------------------------------------
# Source finder (read-only helpers for the dashboard panel "source finder").
# All three functions follow exactly the _external_status pattern: httpx with
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
        return {"ok": False, "detail": i18n.t("dashboard.finder.empty_query")}
    try:
        r = httpx.get("https://api.deezer.com/search/playlist",
                      params={"q": q, "limit": 25}, timeout=8,
                      follow_redirects=True)
        payload = r.json() if r.status_code == 200 else {}
        if r.status_code != 200 or (
                isinstance(payload, dict) and payload.get("error")):
            return {"ok": False, "detail": i18n.t(
                "dashboard.conn.http_error", code=r.status_code)}
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
            "dashboard.conn.unreachable", err=e.__class__.__name__)}


def search_deezer_artists(q: str) -> dict:
    """
    Deezer artist search (read-only, fail-open, no cache).
    Returns a ready CHARTS source snippet (type:artist) per hit for
    copy-pasting. nb_fan helps distinguish artists with the same name (e.g.
    there are two "Hecht").
    """
    q = (q or "").strip()
    if not (1 <= len(q) <= 100):
        return {"ok": False, "detail": i18n.t("dashboard.finder.empty_query")}
    try:
        r = httpx.get("https://api.deezer.com/search/artist",
                      params={"q": q, "limit": 25}, timeout=8,
                      follow_redirects=True)
        payload = r.json() if r.status_code == 200 else {}
        if r.status_code != 200 or (
                isinstance(payload, dict) and payload.get("error")):
            return {"ok": False, "detail": i18n.t(
                "dashboard.conn.http_error", code=r.status_code)}
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
            "dashboard.conn.unreachable", err=e.__class__.__name__)}


# Deezer genre list: cached once a day (own TTL, rarely changed). Module-global
# cache following the _ext_cache pattern.
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
                "dashboard.conn.http_error", code=r.status_code),
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
                "detail": i18n.t("dashboard.conn.unreachable",
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
                "detail": i18n.t("dashboard.finder.lastfm.no_api_key")}
    q = (q or "").strip()
    if not (1 <= len(q) <= 50) or not _TAG_RE.match(q):
        return {"ok": False, "configured": True,
                "detail": i18n.t("dashboard.finder.lastfm.invalid_tag")}
    try:
        r = httpx.get("https://ws.audioscrobbler.com/2.0/", follow_redirects=True,
                      params={"method": "tag.gettoptracks", "tag": q,
                              "api_key": config.LASTFM_API_KEY,
                              "format": "json", "limit": 5}, timeout=8)
        payload = r.json() if r.status_code == 200 else {}
        if r.status_code != 200 or not isinstance(payload, dict) or \
                "error" in payload:
            # payload.message = third-party Last.fm API text -> passed through
            # verbatim (not our prose, no key); only the HTTP fallback is keyed.
            detail = (payload.get("message")
                      if isinstance(payload, dict) else None) \
                or i18n.t("dashboard.conn.http_error", code=r.status_code)
            return {"ok": False, "configured": True, "detail": detail}
        tracks = ((payload.get("tracks") or {}).get("track") or [])
        if not tracks:
            return {"ok": False, "configured": True,
                    "detail": i18n.t("dashboard.finder.lastfm.no_tracks")}
        sample = []
        for t in tracks[:3]:
            artist = ((t.get("artist") or {}).get("name") or "").strip()
            title = (t.get("name") or "").strip()
            if artist or title:
                sample.append(f"{artist} - {title}".strip(" -"))
        snippet = (
            '{"service":"lastfm","method":"tag.getTopTracks","tag":"'
            + q + '","limit":100,"enabled":true}')
        return {"ok": True, "configured": True, "tag": q,
                "sample_count": len(tracks), "sample": sample,
                "snippet": snippet}
    except Exception as e:
        return {"ok": False, "configured": True,
                "detail": i18n.t("dashboard.conn.unreachable",
                                 err=e.__class__.__name__)}


_AUDIO_EXT = (".mp3", ".flac", ".m4a", ".ogg", ".opus", ".aac", ".wav", ".wma")

# Directory statistics are expensive (os.walk over possibly tens of thousands
# of files, especially the music library on HDD). Hence cache and only recompute
# every STATS_TTL seconds - not on every 15s fetch.
def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


# Charts share + quarantine are small -> recounting more often is cheap.
_STATS_TTL = _env_int("DASHBOARD_STATS_TTL", 300)
# The library is LARGE (tens of thousands of files on HDD). Its folder scan
# takes ~10s -> recount only VERY rarely (default 24h). The collection grows
# only slowly anyway. Separate cache so the frequent charts stats do not trigger
# the expensive library scan every time.
_LIBRARY_TTL = _env_int("DASHBOARD_LIBRARY_TTL", 86400)
_stats_cache: dict | None = None
_stats_ts: float = 0.0
_library_cache: dict | None = None
_library_ts: float = 0.0


def _walk_count_bytes(root: str) -> tuple[int, int]:
    """
    Count audio files + total size in ONE walk. For the SMALL folders (charts
    share, quarantine) - there the full walk is cheap.
    """
    files_n = 0
    total = 0
    if not os.path.isdir(root):
        return 0, 0
    for dp, _dn, files in os.walk(root):
        for f in files:
            path = os.path.join(dp, f)
            try:
                total += os.path.getsize(path)
            except OSError:
                pass
            if f.lower().endswith(_AUDIO_EXT):
                files_n += 1
    return files_n, total


def _library_counts(root: str) -> dict:
    """
    System-friendly count of the library: ONLY directory entries over two
    levels (artist folders + their album subfolders). NO file walk, NO getsize
    over tens of thousands of files.

    DELIBERATELY no song count is estimated - the library contains many
    single/chart folders with only 1 track, a blanket "per album" factor would
    be strongly misleading. Only the EXACTLY counted artists + albums are shown.

    This function is expensive (~10s on HDD) and is therefore called only very
    rarely (see _LIBRARY_TTL, default 24h).
    Returns: {"artists", "albums"}
    """
    if not os.path.isdir(root):
        return {"artists": 0, "albums": 0}
    artists = 0
    albums = 0
    try:
        with os.scandir(root) as it:
            for entry in it:
                if not entry.is_dir():
                    continue
                artists += 1
                try:
                    with os.scandir(entry.path) as it2:
                        for sub in it2:
                            if sub.is_dir():
                                albums += 1
                except OSError:
                    pass
    except OSError:
        pass
    return {"artists": artists, "albums": albums}


_library_lock = threading.Lock()
_library_running = False


def _library_refresh_bg() -> None:
    """Compute the library numbers in the background and store them in the cache."""
    global _library_cache, _library_ts, _library_running
    try:
        counts = _library_counts(config.MUSIC_DIR)
        counts["computed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        _library_cache = counts
        _library_ts = time.time()
    finally:
        with _library_lock:
            _library_running = False


def _library_stats() -> dict:
    """
    Library numbers with their own LONG cache (_LIBRARY_TTL, default 24h).
    NON-BLOCKING: if the cache is empty/stale, the (expensive, ~10s) count is
    triggered in a background thread and the OLD value (or "being computed") is
    returned immediately. This way no /api/status fetch blocks 10 seconds on the
    library scan.
    """
    global _library_running
    now = time.time()
    stale = _library_cache is None or (now - _library_ts) >= _LIBRARY_TTL
    if stale:
        with _library_lock:
            if not _library_running:
                _library_running = True
                threading.Thread(target=_library_refresh_bg, daemon=True).start()
    if _library_cache is not None:
        return _library_cache
    return {"artists": None, "albums": None, "pending": True}


def _dir_stats() -> dict:
    """
    File/size numbers (read-only). Cached (STATS_TTL).
    - Charts share + quarantine: exact (small folders, cheap).
    - Library: only a ROUGH estimate over folder count, NO expensive file walk.
    """
    global _stats_cache, _stats_ts
    now = time.time()
    if _stats_cache is not None and (now - _stats_ts) < _STATS_TTL:
        return _stats_cache

    charts = config.CHARTS_DIR
    rejected = os.path.join(charts, "_rejected")
    charts_files, charts_bytes = _walk_count_bytes(charts)
    rejected_files, rejected_bytes = _walk_count_bytes(rejected)

    _stats_cache = {
        "charts_files": charts_files,
        "charts_bytes": charts_bytes,
        "rejected_files": rejected_files,
        "rejected_bytes": rejected_bytes,
        # The library has its OWN long cache (expensive scan) - fetch separately.
        "library": _library_stats(),
        "computed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now)),
    }
    _stats_ts = now
    return _stats_cache


def collect() -> dict:
    """Assemble the complete dashboard state."""
    return {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "services": _read_status_files(),
        "containers": _container_states(),
        "slskd": _slskd_status(),
        "lidarr": _lidarr_status(),
        # Pushover now comes from the live check (external.pushover: real
        # validate call) instead of a pure config flag.
        "external": _external_status(),
        "storage": _dir_stats(),
    }
