"""
Configuration for charts-config (snippet/config UI).
All values from environment variables (see .env.example).

charts-config is the dashboard's sibling: it was carved out of charts-dashboard
and shows the same internal snippets (source finder + Energy channel browser).
It therefore adopts the dashboard's auth convention verbatim (own CONFIG_*
login + shared APP_USERS/APP_PASSWORD fallback). Unlike the dashboard it needs
NO Docker SDK: its only data sources are the public Deezer/Last.fm HTTP APIs
and the charts-energy status JSON (a mounted read-only file).
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
# .charts-status), mounted read-only here. Used for the Energy live status.
STATUS_DIR = os.environ.get("STATUS_DIR", "/charts/.charts-status")

# --- Last.fm (optional; tag check + graying the Last.fm box) ---
# Used only for the Last.fm tag validation. Empty = the Last.fm box is grayed
# out in the UI (not configured); Last.fm has no key-free ping.
LASTFM_API_KEY = os.environ.get("LASTFM_API_KEY", "").strip()

# --- Web UI port ---
# The container listens on this port internally (uvicorn --port in the
# Dockerfile uses 5058); published via compose as ${CONFIG_PORT:-5058}:5058.
CONFIG_PORT = _get_int("CONFIG_PORT", 5058)

# --- TTL cache for the source finder's Deezer genre list (seconds) ---
# The genre list rarely changes. The finder loads it on the first page view and
# only refreshes it after this TTL expires (default 24h) on the next call. The
# env name is shared with the rest of the stack (kept per design to avoid a
# second TTL var); charts-config reads it under the same name.
DASHBOARD_GENRE_CACHE_TTL = _get_int("DASHBOARD_GENRE_CACHE_TTL", 86400)

# --- Advisory enable flag ---
# A web UI has no run-loop; the authoritative off-switch is simply not starting
# the container. This flag exists for parity with ENERGY_ENABLED et al. and is
# surfaced in .env.example / the compose environment for documentation.
CONFIG_ENABLED = os.environ.get("CONFIG_ENABLED", "true").strip().lower() not in (
    "0", "false", "no")

# --- Discreet donate footer (PayPal/Revolut) ---
# ON by default. Can be turned off with CONFIG_DONATE=0/false/no; then the
# footer is not rendered into the page server-side at all. Mirrors the
# dashboard's DASHBOARD_DONATE flag.
CONFIG_DONATE = os.environ.get(
    "CONFIG_DONATE", "1").strip() not in ("0", "false", "no")

# --- Web UI / auth ---
# charts-config has its OWN login (separate from the dashboard/songrequest)
# because it shows internal snippets. Fixed user "admin" + CONFIG_PASSWORD.
# Username changeable via CONFIG_USERNAME (default "admin").
# Fallback: if CONFIG_PASSWORD is empty, the shared APP_USERS/APP_PASSWORD are
# used (so a stack that already sets APP_USERS protects charts-config too).
CONFIG_USERNAME = os.environ.get("CONFIG_USERNAME", "admin").strip()
CONFIG_PASSWORD = os.environ.get("CONFIG_PASSWORD", "").strip()

APP_PASSWORD = os.environ.get("APP_PASSWORD", "").strip()
APP_PASSWORD_USERNAME = os.environ.get("APP_PASSWORD_USERNAME", "youruser").strip()


def _parse_users() -> dict:
    """
    User->password map. Precedence:
      1. CONFIG_PASSWORD set -> ONLY {CONFIG_USERNAME: CONFIG_PASSWORD}
         (own, separate config login).
      2. otherwise fall back to the shared APP_USERS + APP_PASSWORD.
    """
    if CONFIG_PASSWORD:
        return {CONFIG_USERNAME: CONFIG_PASSWORD}

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
