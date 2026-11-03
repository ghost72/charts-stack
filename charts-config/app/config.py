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


# --- Manual Tag Editor (library paths + cover/index limits) ---
# MUSIC_DIR: the Navidrome library root the editor searches and writes into
# (Music/<Artist>/<Album>/<Track>). Mounted read-write for this feature; without
# the mount the editor is inert (the real off-switch, so there is NO
# TAGEDITOR_ENABLED flag).
MUSIC_DIR = os.environ.get("MUSIC_DIR", "/music/Music")

# charts-config-side path for the SAME physical manifest file charts-import keys
# under DOWNLOADS_DIR/.charts-manifest.json: charts-config mounts the Charts dir
# at /charts, so this literal resolves to the same host file as charts-import's
# DOWNLOADS_DIR default (/volume2/music/Charts/.charts-manifest.json). It is a
# different literal from charts-import's config only because the mount point
# differs, not a mismatch.
MANIFEST_FILE = os.environ.get("MANIFEST_FILE", "/charts/.charts-manifest.json")

# Gates tags.preserve_mtime; default on (the hard mtime invariant). Parsed with
# the repo's boolean-env idiom (same as CONFIG_ENABLED / CONFIG_DONATE above) so
# only "0"/"false"/"no" turn it off and nothing else does - NOT a bare bool(...)
# which would treat "0" as truthy. charts-config has no _get_bool helper, so the
# idiom is inlined. The copied tags.py reads config.PRESERVE_MTIME from here.
PRESERVE_MTIME = os.environ.get("PRESERVE_MTIME", "1").strip().lower() not in (
    "0", "false", "no")

# Rebuild interval (seconds) for the lazily-built in-memory search index. A
# keystroke must not re-walk thousands of folders; the index is rebuilt on the
# next search only after this TTL expires.
TAGEDITOR_INDEX_TTL = _get_int("TAGEDITOR_INDEX_TTL", 300)

# Hard byte cap for a cover image (upload stream or URL fetch). Also the
# threshold above which an embedded cover preview is omitted from the song view.
TAGEDITOR_COVER_MAX_BYTES = _get_int("TAGEDITOR_COVER_MAX_BYTES", 8 * 1024 * 1024)

# Timeout (seconds) for the cover-from-URL httpx fetch.
TAGEDITOR_COVER_FETCH_TIMEOUT = _get_int("TAGEDITOR_COVER_FETCH_TIMEOUT", 15)

# --- Manual Tag Editor: in-browser audio play + download (opt-in, default OFF) ---
# Gates BOTH the GET /api/tageditor/audio endpoint and its UI controls (the play
# overlay + download button). Default OFF (fail-closed): a fresh clone has NO
# audio egress at all. charts-config is password-protected, but users may expose
# it to the internet, so this feature stays off unless deliberately enabled. When
# ON the audio endpoint is STILL behind the SAME _current_user auth gate AND the
# SAME editor._guarded_realpath path guard - this flag is an ADDITIONAL gate,
# never a replacement. Note the asymmetry with CONFIG_ENABLED/PRESERVE_MTIME
# above: those default ON and treat only "0"/"false"/"no" as off; this one must
# default OFF, so it instead turns on ONLY for an explicit truthy token.
TAGEDITOR_AUDIO_ENABLED = os.environ.get(
    "TAGEDITOR_AUDIO_ENABLED", "0").strip().lower() in ("1", "true", "yes")

# Timeout (seconds) for a single tag-editor FLAC->MP3 ffmpeg transcode. Bounds a
# hung ffmpeg; generous for one track. Parsed with _get_int like the other ints.
TAGEDITOR_CONVERT_TIMEOUT = _get_int("TAGEDITOR_CONVERT_TIMEOUT", 600)
