"""
Configuration for charts-dashboard (status UI).
All values from environment variables (see .env.example).
"""

import os


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _get_bool(name: str, default: bool) -> bool:
    """Tolerant bool parser (true/1/yes/on vs false/0/no/off), matching the
    *_ENABLED flags charts-energy / charts-flashback use so the dashboard reads
    the same on/off semantics."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("true", "1", "yes", "on")


# --- Status files (written by the charts services) ---
# Directory with the <service>.json status files. Points to the same physical
# folder as STATUS_DIR of the other services (/volume2/music/Charts/
# .charts-status), mounted read-only here.
STATUS_DIR = os.environ.get("STATUS_DIR", "/charts/.charts-status")

# --- Folders for the storage/inventory display (read-only) ---
CHARTS_DIR = os.environ.get("CHARTS_DIR", "/charts")
MUSIC_DIR = os.environ.get("MUSIC_DIR", "/music/Music")

# --- Which containers are monitored (Docker status) ---
WATCH_CONTAINERS = os.environ.get(
    "WATCH_CONTAINERS",
    "slskd charts-sync charts-hitparade charts-flashback charts-energy charts-download "
    "charts-import charts-recommend charts-upgrade charts-songrequest "
    "charts-digest charts-watchdog charts-dashboard charts-config",
).split()

# --- slskd (connection status + transfer numbers) ---
SLSKD_HOST = os.environ.get("SLSKD_HOST", "http://slskd:5030")
SLSKD_API_KEY = os.environ.get("SLSKD_API_KEY", "").strip()

# --- Lidarr (reachability check) ---
LIDARR_URL = os.environ.get("LIDARR_URL", "").strip()
LIDARR_API_KEY = os.environ.get("LIDARR_API_KEY", "").strip()

# --- Last.fm (optional live reachability check of the external source) ---
# Used only for the Last.fm live ping. Empty = Last.fm tile stays "muted"
# (not configured), never red. Last.fm has no key-free ping.
LASTFM_API_KEY = os.environ.get("LASTFM_API_KEY", "").strip()

# --- FLASHBACK FM (GraphQL live check of the external source) ---
# Persisted query hash; empty = FLASHBACK tile stays "muted" (not configured).
# A dead hash -> tile red (signal: fetch a new hash).
FLASHBACK_HASH = os.environ.get("FLASHBACK_HASH", "").strip()

# --- Energy (NRJ) Icecast stream (reachability live check) ---
# charts-energy reads the Swiss NRJ (energy.ch) channels via the Infomaniak
# Icecast ICY metadata. The dashboard pings the main channel's stream mount the
# same way charts-energy probes liveness (headers-only GET with Icy-MetaData:1,
# reachable iff HTTP 200 + an icy-name header). Both knobs mirror charts-energy's
# config.ENERGY_STREAM_HOST_TMPL / main slug so the dashboard points at the same
# host without importing charts-energy.
ENERGY_STREAM_HOST_TMPL = os.environ.get(
    "ENERGY_STREAM_HOST_TMPL",
    "https://{slug}.ice.infomaniak.ch/{slug}-high.mp3",
)
ENERGY_MAIN_SLUG = os.environ.get("ENERGY_MAIN_SLUG", "energymadeinswitzerland").strip()
# Whether charts-energy is in use. Mirrors charts-energy's ENERGY_ENABLED
# (default OFF there: .env.example ENERGY_ENABLED=0, compose ${ENERGY_ENABLED:-false}).
# When off, the Energy tile shows "muted" (not in use) instead of pinging the
# stream - same treatment as FLASHBACK/Last.fm/Pushover.
ENERGY_ENABLED = _get_bool("ENERGY_ENABLED", False)

# --- Swiss Hitparade (hitparade.ch) reachability live check ---
# charts-hitparade scrapes the official Swiss chart from hitparade.ch. The
# dashboard pings the same page with a real browser User-Agent (the site serves
# an empty placeholder without one). Mirrors charts-hitparade's HITPARADE_ENABLED
# (default on): when off the tile shows "not in use" instead of pinging.
HITPARADE_ENABLED = _get_bool("HITPARADE_ENABLED", True)
HITPARADE_URL = os.environ.get("HITPARADE_URL", "https://hitparade.ch/charts").strip()

# --- MusicBrainz (reachability live check of the metadata helper) ---
# Metadata helper (release-year lookup) used by charts-energy / charts-flashback,
# not a chart source. Mirrors their MB_ENABLED (default on). When off, the
# MusicBrainz tile shows "muted" (not in use) instead of pinging.
MB_ENABLED = _get_bool("MB_ENABLED", True)

# --- TTL cache for the external live checks (seconds) ---
# The dashboard polls every 15s; the external pings, however, go over WAN to
# third parties. Hence cached (default 2 min), decoupled from the 15s poll.
EXTERNAL_TTL = _get_int("DASHBOARD_EXTERNAL_TTL", 120)

# --- Pushover (for the live validate check in _external_status) ---
# Both set -> real POST /1/users/validate.json (no message budget).
# Empty -> tile "muted" (not configured), never red.
PUSHOVER_TOKEN = os.environ.get("PUSHOVER_TOKEN", "").strip()
PUSHOVER_USER = os.environ.get("PUSHOVER_USER", "").strip()

# --- Page auto-refresh (seconds) ---
REFRESH_SECONDS = _get_int("DASHBOARD_REFRESH_SECONDS", 15)

# --- "In progress" pipeline overview (new section) ---
# How many preview rows per in-progress panel + the live-list cap. All have
# working defaults, so no .env change is required to run. Reuses CHARTS_DIR,
# STATUS_DIR, SLSKD_HOST, SLSKD_API_KEY - no new mount/credential.
# The missing/rejected snapshot panels now ship the FULL list (the client puts
# it in a fixed-height, internally-scrolling box), so these caps are high
# enough to cover the real backlogs (~286 missing / ~61 quarantine) with
# headroom while still bounding the payload. The live `active` panel keeps its
# small cap of 25 (genuinely few concurrent downloads; the "+N more" line is a
# safety net).
INFLIGHT_MISSING_PREVIEW_N = _get_int("DASHBOARD_INFLIGHT_MISSING_N", 500)
INFLIGHT_REJECTED_PREVIEW_N = _get_int("DASHBOARD_INFLIGHT_REJECTED_N", 500)
INFLIGHT_ACTIVE_MAX = _get_int("DASHBOARD_INFLIGHT_ACTIVE_MAX", 25)
# TTL (seconds) for the CHEAP snapshot reads only (missing*.txt + _rejected
# listing). The live slskd "active downloads" panel is NEVER cached - it is
# rebuilt on every poll so "downloading now" stays honest. The snapshot sources
# change slowly (charts-sync ~12h, charts-import ~30min), so a short TTL
# decouples them from every 15s poll without going stale.
INFLIGHT_TTL = _get_int("DASHBOARD_INFLIGHT_TTL", 30)

# --- Discreet donate footer (PayPal/Revolut) ---
# ON by default. Can be turned off with DASHBOARD_DONATE=0/false/no; then the
# footer is not rendered into the page server-side at all.
DASHBOARD_DONATE = os.environ.get(
    "DASHBOARD_DONATE", "1").strip() not in ("0", "false", "no")

# --- Web UI / auth ---
# The dashboard has its OWN login (separate from charts-songrequest) because it
# shows internal state. Fixed user "admin" + DASHBOARD_PASSWORD. Username
# changeable via DASHBOARD_USERNAME (default "admin").
# Fallback: if DASHBOARD_PASSWORD is empty, the shared APP_USERS/APP_PASSWORD
# are used (so nothing breaks when nothing is set).
DASHBOARD_USERNAME = os.environ.get("DASHBOARD_USERNAME", "admin").strip()
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "").strip()

APP_PASSWORD = os.environ.get("APP_PASSWORD", "").strip()
APP_PASSWORD_USERNAME = os.environ.get("APP_PASSWORD_USERNAME", "youruser").strip()


def _parse_users() -> dict:
    """
    User->password map. Precedence:
      1. DASHBOARD_PASSWORD set -> ONLY {DASHBOARD_USERNAME: DASHBOARD_PASSWORD}
         (own, separate dashboard login).
      2. otherwise fall back to the shared APP_USERS + APP_PASSWORD.
    """
    if DASHBOARD_PASSWORD:
        return {DASHBOARD_USERNAME: DASHBOARD_PASSWORD}

    users: dict = {}
    raw = os.environ.get("APP_USERS", "").strip()
    if raw:
        for pair in raw.split(","):
            pair = pair.strip()
            if not pair or ":" not in pair:
                continue
            name, pw = pair.split(":", 1)
            name = name.strip()
            if name and pw:
                users[name] = pw
    if APP_PASSWORD and APP_PASSWORD_USERNAME not in users:
        users[APP_PASSWORD_USERNAME] = APP_PASSWORD
    return users


APP_USERS = _parse_users()
SESSION_SECRET = os.environ.get("SESSION_SECRET", "").strip()
