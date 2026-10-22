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
    "charts-digest charts-watchdog charts-dashboard",
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

# --- TTL cache for the external live checks (seconds) ---
# The dashboard polls every 15s; the external pings, however, go over WAN to
# third parties. Hence cached (default 2 min), decoupled from the 15s poll.
EXTERNAL_TTL = _get_int("DASHBOARD_EXTERNAL_TTL", 120)

# --- TTL cache for the source finder's Deezer genre list (seconds) ---
# The genre list rarely changes. The source finder loads it on the first page
# view and only refreshes it after this TTL expires (default 24h) on the next
# call - this covers the "refresh daily" wish (no cronjob). Consistent with the
# DASHBOARD_*_TTL style.
DASHBOARD_GENRE_CACHE_TTL = _get_int("DASHBOARD_GENRE_CACHE_TTL", 86400)

# --- Pushover (for the live validate check in _external_status) ---
# Both set -> real POST /1/users/validate.json (no message budget).
# Empty -> tile "muted" (not configured), never red.
PUSHOVER_TOKEN = os.environ.get("PUSHOVER_TOKEN", "").strip()
PUSHOVER_USER = os.environ.get("PUSHOVER_USER", "").strip()

# --- Page auto-refresh (seconds) ---
REFRESH_SECONDS = _get_int("DASHBOARD_REFRESH_SECONDS", 15)

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
