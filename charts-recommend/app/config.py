"""
Configuration for the charts-recommend service.
All values from environment variables (see .env.example).
"""

import logging
import os

logger = logging.getLogger(__name__)


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _get_bool(name: str, default: bool) -> bool:
    return os.environ.get(name, "1" if default else "0") == "1"


# --- Paths (like charts-sync) ---
MUSIC_DIR = os.environ.get("MUSIC_DIR", "/music/Music")
CHARTS_DIR = os.environ.get("CHARTS_DIR", "/charts")
# PLAYLIST_DIR is dropped - the service no longer writes .m3u (download-only).

# --- ListenBrainz (multi-user, download-only) ---
# The service reads the PUBLIC listening taste of a list of ListenBrainz users
# and lets the shared library grow with it. It writes NO playlists.
LISTENBRAINZ_ENABLED = _get_bool("LISTENBRAINZ_ENABLED", True)

# List of public ListenBrainz USERNAMES whose taste lets the shared library
# grow. ONLY usernames (public, NO secrets). Empty = source off (service runs
# empty, not an error).
_users_raw = os.environ.get("RECOMMEND_LISTENBRAINZ_USERS", "").strip()
# Backward compat: old single value LISTENBRAINZ_USER if the list is empty.
LISTENBRAINZ_USER = os.environ.get("LISTENBRAINZ_USER", "").strip()
if _users_raw:
    LISTENBRAINZ_USERS = [u.strip() for u in _users_raw.split(",") if u.strip()]
elif LISTENBRAINZ_USER:
    LISTENBRAINZ_USERS = [LISTENBRAINZ_USER]
else:
    LISTENBRAINZ_USERS = []

# Token ONLY to raise the rate limit when reading the public profiles.
# NOT needed to read the profiles at all. NOT a password - neither
# ListenBrainz nor Navidrome.
LISTENBRAINZ_TOKEN = os.environ.get("LISTENBRAINZ_TOKEN", "").strip()

# Which data sources: "stats" (most-played recordings, available immediately)
# and/or "createdfor" (Weekly Jams/Exploration, possibly only after 2-4 weeks).
# Default both. Comma-separated.
RECOMMEND_SOURCES = [
    s.strip().lower() for s in
    os.environ.get("RECOMMEND_SOURCES", "stats,createdfor").split(",")
    if s.strip()
]

# For the "createdfor" source: which "Created for you" playlists are fetched:
# "jams" (known/popular) and/or "exploration" (new discoveries).
LISTENBRAINZ_KINDS = [
    k.strip().lower() for k in
    os.environ.get("LISTENBRAINZ_KINDS", "jams,exploration").split(",")
    if k.strip()
]

# Time range for the "stats" source: week | month | quarter | half_yearly |
# year | all_time (ListenBrainz range values). Default "month".
RECOMMEND_STATS_RANGE = os.environ.get("RECOMMEND_STATS_RANGE", "month").strip()

# --- Last.fm (optional, sofort nutzbar mit vorhandenem API-Key) ---
LASTFM_ENABLED = _get_bool("LASTFM_ENABLED", False)
LASTFM_API_KEY = os.environ.get("LASTFM_API_KEY", "").strip()
# Mode: "top" (user.getTopTracks) or "similar" (track.getSimilar).
LASTFM_MODE = os.environ.get("LASTFM_MODE", "similar").strip().lower()
# Last.fm username (for "top" or as a seed source for "similar"). Optional.
LASTFM_USER = os.environ.get("LASTFM_USER", "").strip()
# Optional seed songs for "similar" if you have no Last.fm user.
# Format: "Artist - Title; Artist2 - Title2".
_seeds_raw = os.environ.get("LASTFM_SEEDS", "").strip()
LASTFM_SEEDS: list[tuple[str, str]] = []
for part in _seeds_raw.split(";"):
    part = part.strip()
    if " - " in part:
        a, _, t = part.partition(" - ")
        if a.strip() and t.strip():
            LASTFM_SEEDS.append((a.strip(), t.strip()))

# --- Quantities ---
# Per-user cap on recommendations (applies per ListenBrainz user and source as
# well as the overall cap of the Last.fm source, as before).
RECOMMEND_LIMIT = _get_int("RECOMMEND_LIMIT", 100)

# --- HTTP robustness (ListenBrainz/Last.fm) ---
# Timeout per HTTP request (seconds) and number of attempts per request. A
# transport error (timeout/HTTP/connection) is retried up to HTTP_RETRIES times
# with a short backoff, and only then reported as an error to the caller.
HTTP_TIMEOUT = _get_int("HTTP_TIMEOUT", 30)
HTTP_RETRIES = _get_int("HTTP_RETRIES", 3)

# --- Matching (identical to charts-sync) ---
MATCH_THRESHOLD = _get_int("MATCH_THRESHOLD", 87)
ARTIST_MIN_SCORE = _get_int("ARTIST_MIN_SCORE", 60)
IGNORE_COMPILATIONS = _get_bool("IGNORE_COMPILATIONS", True)
AUDIO_EXTENSIONS = {
    ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma", ".wav", ".alac",
}

# --- Fetching (interlock with charts-download) ---
# Missing recommendations are written into ONE combined missing file that
# charts-download fetches (its MISSING_FILES must contain this file).
# 1 = write the missing file (fetch), 0 = only show what is already there.
WRITE_MISSING = _get_bool("WRITE_MISSING", True)
# Fixed name of the ONE combined missing list (no playlists anymore, hence no
# split by source). Internal override hook, not wired into compose/.env - the
# default is enough.
MISSING_FILE = os.environ.get("RECOMMEND_MISSING_FILE", "missing-recommend.txt")

# --- Scheduler ---
# ListenBrainz updates weekly; checking daily does no harm.
INTERVAL_HOURS = _get_int("INTERVAL_HOURS", 24)
RUN_ON_START = _get_bool("RUN_ON_START", True)

# --- Pushover (optional) ---
PUSHOVER_TOKEN = os.environ.get("PUSHOVER_TOKEN", "").strip()
PUSHOVER_USER = os.environ.get("PUSHOVER_USER", "").strip()
# Mute Pushover globally for this service (without deleting token/user).
PUSHOVER_ENABLED = os.environ.get("PUSHOVER_ENABLED", "1") == "1"
PUSHOVER_DEVICE = os.environ.get("PUSHOVER_DEVICE", "").strip()
# Cache duration (seconds) for the device list fetched from the Pushover
# account. The PUSHOVER_DEVICE list is filtered against it before sending so an
# invalid/disabled device name does not trigger Pushover's "to all" fallback
# (double push). Default 3600s = 1h.
PUSHOVER_DEVICES_TTL = _get_int("PUSHOVER_DEVICES_TTL", 3600)
# Pushover notification sound. Empty = account default (no sound field in the
# request). Default for charts-recommend: "falling".
PUSHOVER_SOUND = os.environ.get("PUSHOVER_SOUND", "falling").strip()
# Send error alarm (fetch failed) only on state change (1) instead of on every
# run (0) - prevents daily spam during a longer outage. Analogous to charts-sync
# NOTIFY_ONLY_ON_CHANGE.
NOTIFY_ONLY_ON_CHANGE = _get_bool("NOTIFY_ONLY_ON_CHANGE", True)
