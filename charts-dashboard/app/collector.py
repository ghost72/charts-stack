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
    logger.warning(i18n.t("dashboard.log.docker_sdk_unavailable", error=e))
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

    Returns: {service: {"ok": bool, "detail": str}} per service (listenbrainz,
    deezer, lastfm, itunes, pushover, flashback, energy, musicbrainz, hitparade);
    lastfm + pushover additionally with "configured": bool.
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

    # Energy (NRJ): Icecast ICY reachability of the main channel mount, reachable
    # iff HTTP 200 + an icy-name header on the stream. CRITICAL: an Icecast mount
    # with Icy-MetaData:1 returns an ENDLESS audio body - a plain httpx.get()
    # would block reading that body until the timeout on EVERY dashboard refresh
    # and hang the whole /api/status response. So we STREAM: httpx.stream() hands
    # back the response headers WITHOUT reading the body, and the `with` block
    # closes the connection immediately. We never read a single audio byte.
    # configured = ENERGY_ENABLED: when off the tile is "muted" (not in use) and
    # we skip the ping entirely, same as FLASHBACK/Last.fm/Pushover. Fail-open.
    en = {"configured": config.ENERGY_ENABLED, "ok": False, "detail": ""}
    if en["configured"]:
        try:
            url = config.ENERGY_STREAM_HOST_TMPL.format(slug=config.ENERGY_MAIN_SLUG)
            with httpx.stream("GET", url,
                              headers={"Icy-MetaData": "1",
                                       "User-Agent": "Mozilla/5.0"},
                              timeout=httpx.Timeout(8.0, connect=5.0)) as r:
                en["ok"] = r.status_code == 200 and bool(r.headers.get("icy-name"))
                en["detail"] = "" if en["ok"] else i18n.t(
                    "dashboard.conn.http_error", code=r.status_code)
        except Exception as e:
            en["detail"] = i18n.t("dashboard.conn.unreachable",
                                  err=e.__class__.__name__)
    else:
        en["detail"] = i18n.t("dashboard.conn.not_in_use")

    # MusicBrainz: metadata helper (release-year lookup used by charts-energy /
    # charts-flashback), NOT a chart source. Live-ping the recording search with
    # a tiny query; reachable iff HTTP 200. MusicBrainz requires a User-Agent.
    # configured = MB_ENABLED: when off the tile is "muted" (not in use) and we
    # skip the ping. Fail-open like Deezer: any error -> ok=False + detail.
    mb = {"configured": config.MB_ENABLED, "ok": False, "detail": ""}
    if mb["configured"]:
        try:
            r = httpx.get("https://musicbrainz.org/ws/2/recording",
                          params={"query": "recording:test", "fmt": "json", "limit": 1},
                          headers={"User-Agent": "charts-dashboard/1.0 (self-hosted)"},
                          timeout=8, follow_redirects=True)
            mb["ok"] = r.status_code == 200
            mb["detail"] = "" if mb["ok"] else i18n.t(
                "dashboard.conn.http_error", code=r.status_code)
        except Exception as e:
            mb["detail"] = i18n.t("dashboard.conn.unreachable",
                                  err=e.__class__.__name__)
    else:
        mb["detail"] = i18n.t("dashboard.conn.not_in_use")

    # Swiss Hitparade (hitparade.ch): HTTP reachability of the chart page with a
    # real browser User-Agent (the site returns an empty placeholder without
    # one). Chart source like FLASHBACK/Energy. configured = HITPARADE_ENABLED:
    # when off the tile is "muted" (not in use) and we skip the ping. Fail-open.
    hp = {"configured": config.HITPARADE_ENABLED, "ok": False, "detail": ""}
    if hp["configured"]:
        try:
            r = httpx.get(config.HITPARADE_URL,
                          headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel "
                                   "Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, "
                                   "like Gecko) Chrome/120.0 Safari/537.36",
                                   "Accept-Language": "de-CH,de;q=0.9"},
                          timeout=8, follow_redirects=True)
            hp["ok"] = r.status_code == 200
            hp["detail"] = "" if hp["ok"] else i18n.t(
                "dashboard.conn.http_error", code=r.status_code)
        except Exception as e:
            hp["detail"] = i18n.t("dashboard.conn.unreachable",
                                  err=e.__class__.__name__)
    else:
        hp["detail"] = i18n.t("dashboard.conn.not_in_use")

    result = {"listenbrainz": lb, "deezer": dz, "lastfm": lf, "itunes": it,
              "pushover": po, "flashback": fb, "energy": en, "musicbrainz": mb,
              "hitparade": hp}
    _ext_cache = result
    _ext_ts = now
    return result


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
    System-friendly count of the library:
      - artists = top-level folders, albums = their direct subfolders (two
        scandir levels, cheap, system-friendly - NO getsize).
      - songs = EXACT count of audio files anywhere BELOW the music root (full
        recursive walk), so multi-disc / nested structures (e.g.
        Artist/Album/CD1/..) are counted too, not only Artist/Album/ depth.

    Only PLAYABLE library songs are counted: non-library folders are skipped -
    `.charts-trash` (replaced versions in the trash), `@eaDir` (Synology
    thumbnails) and any hidden folder (name starting with '.'). The quarantine
    (`_rejected`) and the slskd downloads live under CHARTS_DIR, not here (this
    walks MUSIC_DIR), so they are out of scope by construction.

    Only the filename extension is tested against the shared _AUDIO_EXT tuple;
    no getsize. This function is expensive (~10s on HDD) and is therefore called
    only very rarely (see _LIBRARY_TTL, default 24h).
    Returns: {"artists", "albums", "songs"}
    """
    if not os.path.isdir(root):
        return {"artists": 0, "albums": 0, "songs": 0}

    def _skip_dir(name: str) -> bool:
        # Skip trash, Synology thumbnails and any hidden folder - these are not
        # playable library content.
        return name == "@eaDir" or name.startswith(".")

    artists = 0
    albums = 0
    songs = 0
    # artists + albums: two cheap scandir levels (unchanged semantics, minus the
    # skip-dir filter so a hidden/trash folder is not counted as an artist/album).
    try:
        with os.scandir(root) as it:
            for entry in it:
                if not entry.is_dir() or _skip_dir(entry.name):
                    continue
                artists += 1
                try:
                    with os.scandir(entry.path) as it2:
                        for sub in it2:
                            if sub.is_dir() and not _skip_dir(sub.name):
                                albums += 1
                except OSError:
                    pass
    except OSError:
        pass
    # songs: full recursive walk so nested (multi-disc) tracks are counted; prune
    # the skip-dirs in-place so os.walk does not descend into them.
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not _skip_dir(d)]
            for f in filenames:
                if f.lower().endswith(_AUDIO_EXT):
                    songs += 1
    except OSError:
        pass
    return {"artists": artists, "albums": albums, "songs": songs}


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
    return {"artists": None, "albums": None, "songs": None, "pending": True}


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


# ===========================================================================
# "In progress" pipeline overview (new section). Four honestly-labelled panels:
#   1. Active downloads (LIVE)   - slskd transfers broken out per song.
#   2. Queue / missing (SNAPSHOT)- missing*.txt backlog (last charts-sync run).
#   3. Quarantine / rejected     - files in /charts/_rejected + run message.
#   4. Upgrade status (last run) - charts-upgrade scan summary.
# Only panel 1 is truly live; panels 2-4 are snapshots / last-run records. The
# payload names each sub-object by its nature and `active` is built on a
# separate uncached path, so the client cannot present a snapshot as live.
# Every sub-collector is fail-open: a source failure degrades ONE panel to an
# honest empty/error state, never the whole dashboard. No write into /charts.
# ===========================================================================

# Missing-file line parser, re-implemented inline from
# charts-download/app/missing.py (the repo's copy-the-small-helper pattern, like
# quality.py/manifest.py - not a runtime import of another service).
import glob as _glob
import re as _re

_MISSING_LINE_RE = _re.compile(r"^\s*(\d{1,3})\.\s+(.*\S)\s*$")


def _missing_source_label(path: str) -> str:
    """Playlist label from a missing-file path (mirrors missing.source_label).

    /charts/missing.txt -> "charts", /charts/missing-<x>.txt -> "<x>".
    """
    base = os.path.basename(path)
    name = base[:-4] if base.lower().endswith(".txt") else base
    if name == "missing":
        return "charts"
    if name.startswith("missing-"):
        return name[len("missing-"):]
    return name or "charts"


def _inflight_join_active(transfers: list, requested: dict, max_items: int) -> dict:
    """PANEL 1 (live) data from the slskd transfer list + the download-state.

    `transfers` is the parsed `GET /api/v0/transfers/downloads` body (a list of
    users, each {username, directories:[{files:[{state, filename, size,
    bytesTransferred}]}]}). `requested` is the `.charts-download-state.json`
    `requested` map {key: {artist, title, username, filename}}.

    A file is ACTIVE iff its lowercased state contains neither "completed" nor
    "cancelled" - the VERBATIM predicate from _slskd_status, so panel 1's count
    agrees with the slskd tile. Each active file is joined on (username,
    filename) to a clean artist/title; unmatched files fall back to the bare
    filename basename. `pct` is best-effort (null when size <= 0 / absent).
    """
    lookup: dict = {}
    for v in (requested or {}).values():
        if isinstance(v, dict):
            lookup[(v.get("username"), v.get("filename"))] = (
                v.get("artist"), v.get("title"))

    items: list = []
    count = 0
    for user in transfers or []:
        uname = user.get("username")
        for directory in user.get("directories", []) or []:
            for f in directory.get("files", []) or []:
                state = (f.get("state") or "").lower()
                if "completed" in state or "cancelled" in state:
                    continue  # terminal -> not in progress
                count += 1
                if len(items) >= max_items:
                    continue  # keep counting the true total, stop collecting
                fn = f.get("filename") or ""
                hit = lookup.get((uname, fn))
                if hit and hit[0] and hit[1]:
                    artist, title = hit[0], hit[1]
                    label = f"{artist} \u2013 {title}"  # "Artist – Title"
                    matched = True
                else:
                    artist = title = None
                    # slskd filenames are Windows-style remote paths; split on
                    # both separators and take the basename.
                    label = fn.replace("\\", "/").rsplit("/", 1)[-1]
                    matched = False
                # pct: best-effort, guard zero/negative/absent size.
                pct = None
                try:
                    size = int(f.get("size") or 0)
                    done = int(f.get("bytesTransferred") or 0)
                    if size > 0:
                        pct = round(100 * done / size)
                except (TypeError, ValueError):
                    pct = None
                items.append({"artist": artist, "title": title,
                              "state": f.get("state") or "", "pct": pct,
                              "label": label, "matched": matched})
    return {"ok": True, "count": count, "items": items,
            "truncated": count > max_items}


def _inflight_parse_missing(paths: list, preview_n: int) -> dict:
    """PANEL 2 (snapshot) data from a list of missing*.txt paths.

    Per-file {source, count}, a deduped `total` (by a lightweight
    lowercase "artist|title" key - this simpler key may OVER-count slightly
    versus charts-download's full normalize, since fewer near-duplicates
    collapse; acceptable for a display headline), and the first `preview_n`
    entries in file order. A single unreadable file is skipped, not fatal.
    Returns the dict without `snapshot_at`/`available` (the caller fills those).
    """
    per_source: list = []
    preview: list = []
    seen: set = set()
    total = 0
    for path in paths:
        source = _missing_source_label(path)
        file_count = 0
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    if line.lstrip().startswith("#") or not line.strip():
                        continue
                    m = _MISSING_LINE_RE.match(line)
                    if not m:
                        continue
                    rest = m.group(2)
                    if " - " not in rest:
                        continue
                    artist, _, title = rest.partition(" - ")
                    artist, title = artist.strip(), title.strip()
                    if not (artist and title):
                        continue
                    file_count += 1
                    key = f"{artist.lower()}|{title.lower()}"
                    if key not in seen:
                        seen.add(key)
                        total += 1
                        if len(preview) < preview_n:
                            preview.append({"artist": artist, "title": title})
        except OSError:
            continue  # skip an unreadable file, keep the rest
        per_source.append({"source": source, "count": file_count})
    return {"total": total, "per_source": per_source,
            "preview": preview, "preview_n": preview_n}


def _inflight_rejected_entries(dir_path: str, max_items: int) -> tuple[int, list]:
    """PANEL 3 data: (count, preview) of audio files in /charts/_rejected.

    Lists the directory, keeps only audio files, sorts by mtime descending and
    keeps the newest `max_items` as {name, when: ISO}. A missing dir -> (0, []).
    A path that vanishes between scandir and stat is skipped.
    """
    entries: list = []
    try:
        with os.scandir(dir_path) as it:
            for entry in it:
                try:
                    if not entry.is_file():
                        continue
                    if not entry.name.lower().endswith(_AUDIO_EXT):
                        continue  # skip .DS_Store, @eaDir junk, etc.
                    entries.append((entry.stat().st_mtime, entry.name))
                except OSError:
                    continue
    except OSError:
        return 0, []
    entries.sort(key=lambda e: e[0], reverse=True)
    preview = [
        {"name": name,
         "when": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(mtime))}
        for mtime, name in entries[:max_items]
    ]
    return len(entries), preview


def _inflight_upgrade_fields(services: dict) -> dict:
    """PANEL 4 (last run) data from the already-parsed charts-upgrade status."""
    up = (services or {}).get("charts-upgrade")
    if not isinstance(up, dict):
        return {"scan_seen": None, "scan_total": None, "upgraded": None,
                "status": None, "last_run_end": None, "next_run": None,
                "available": False}
    counters = up.get("counters") or {}
    totals = up.get("totals") or {}
    return {
        "scan_seen": counters.get("scan_seen"),
        "scan_total": counters.get("scan_total"),
        "upgraded": totals.get("upgraded"),
        "status": up.get("status"),
        "last_run_end": up.get("last_run_end"),
        "next_run": up.get("next_run"),
        "available": True,
    }


def _inflight_import_message(services: dict) -> tuple[str, int | None]:
    """PANEL 3 run-level record from the already-parsed charts-import status.

    Returns (message, rejected_counter). No per-file reason exists anywhere
    (it is log-only), so the panel shows only this run-level note + count.
    """
    imp = (services or {}).get("charts-import")
    if not isinstance(imp, dict):
        return "", None
    counter = (imp.get("counters") or {}).get("rejected")
    return imp.get("message") or "", counter


# Snapshot panels (missing + quarantine) sit behind a short TTL cache, mirroring
# the _ext_cache/_ext_ts and _stats_cache/_stats_ts pattern. The LIVE `active`
# panel is intentionally NOT cached (rebuilt every call).
_inflight_cache: dict | None = None
_inflight_ts: float = 0.0


def _inflight_active() -> dict:
    """Build the LIVE panel 1 (uncached). Fail-open: slskd/state errors degrade
    to an honest empty/unreachable panel, never raise."""
    if not config.SLSKD_API_KEY:
        return {"ok": False, "count": None, "items": [], "truncated": False}
    base = config.SLSKD_HOST.rstrip("/")
    headers = {"X-API-Key": config.SLSKD_API_KEY}
    try:
        r = httpx.get(f"{base}/api/v0/transfers/downloads",
                      headers=headers, timeout=8)
        r.raise_for_status()
        transfers = r.json()
    except Exception as e:
        # Never log the API key - only the exception class name.
        logger.warning(i18n.t("dashboard.inprogress.log.slskd_failed",
                              error=e.__class__.__name__))
        return {"ok": False, "count": None, "items": [], "truncated": False}
    # Read the clean-label state file tolerantly (missing/corrupt -> empty).
    requested: dict = {}
    try:
        state_path = os.path.join(config.CHARTS_DIR, ".charts-download-state.json")
        with open(state_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("requested"), dict):
            requested = data["requested"]
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(i18n.t("dashboard.inprogress.log.state_read_failed",
                              error=e.__class__.__name__))
    return _inflight_join_active(transfers, requested, config.INFLIGHT_ACTIVE_MAX)


def _inflight_snapshots() -> dict:
    """Build the cheap SNAPSHOT panels (missing + quarantine) behind INFLIGHT_TTL."""
    # Panel 2: missing*.txt backlog.
    paths = sorted(_glob.glob(os.path.join(config.CHARTS_DIR, "missing*.txt")))
    missing = _inflight_parse_missing(paths, config.INFLIGHT_MISSING_PREVIEW_N)
    snapshot_at = None
    newest = 0.0
    for path in paths:
        try:
            mt = os.path.getmtime(path)
            if mt > newest:
                newest = mt
        except OSError:
            continue
    if newest > 0:
        snapshot_at = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(newest))
    missing["snapshot_at"] = snapshot_at
    missing["available"] = bool(paths)

    # Panel 3: quarantine files (an empty quarantine is a valid, good state).
    rejected_dir = os.path.join(config.CHARTS_DIR, "_rejected")
    count, preview = _inflight_rejected_entries(
        rejected_dir, config.INFLIGHT_REJECTED_PREVIEW_N)
    rejected = {"count": count, "preview": preview,
                "preview_n": config.INFLIGHT_REJECTED_PREVIEW_N,
                "available": True}
    return {"missing": missing, "rejected": rejected}


def _inflight(services: dict) -> dict:
    """Assemble the in-progress section. `active` is always live/uncached; the
    snapshot panels are cached for INFLIGHT_TTL; the upgrade/import fields come
    free from the already-parsed `services` dict. Fail-open as a whole."""
    global _inflight_cache, _inflight_ts
    try:
        active = _inflight_active()

        now = time.time()
        if _inflight_cache is not None and (now - _inflight_ts) < config.INFLIGHT_TTL:
            snapshots = _inflight_cache
        else:
            snapshots = _inflight_snapshots()
            _inflight_cache = snapshots
            _inflight_ts = now

        rejected = dict(snapshots["rejected"])
        message, counter = _inflight_import_message(services)
        rejected["message"] = message
        rejected["counter"] = counter

        return {
            "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "active": active,
            "missing": snapshots["missing"],
            "rejected": rejected,
            "upgrade": _inflight_upgrade_fields(services),
        }
    except Exception as e:  # pragma: no cover - last-resort safety net
        logger.warning(i18n.t("dashboard.inprogress.log.slskd_failed",
                              error=e.__class__.__name__))
        return {
            "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "active": {"ok": False, "count": None, "items": [], "truncated": False},
            "missing": {"total": 0, "per_source": [], "preview": [],
                        "preview_n": config.INFLIGHT_MISSING_PREVIEW_N,
                        "snapshot_at": None, "available": False},
            "rejected": {"count": 0, "preview": [],
                         "preview_n": config.INFLIGHT_REJECTED_PREVIEW_N,
                         "message": "", "counter": None, "available": True},
            "upgrade": {"scan_seen": None, "scan_total": None, "upgraded": None,
                        "status": None, "last_run_end": None, "next_run": None,
                        "available": False},
        }


def collect() -> dict:
    """Assemble the complete dashboard state."""
    # Capture the status dict once and reuse it for both the "services" key and
    # the in-progress panels (panels 3+4 read charts-import/charts-upgrade from
    # it - no extra file read).
    services = _read_status_files()
    return {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "services": services,
        "containers": _container_states(),
        "slskd": _slskd_status(),
        "lidarr": _lidarr_status(),
        # Pushover now comes from the live check (external.pushover: real
        # validate call) instead of a pure config flag.
        "external": _external_status(),
        "storage": _dir_stats(),
        "inflight": _inflight(services),
    }
