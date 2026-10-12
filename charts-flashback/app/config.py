"""
Configuration for the charts-flashback service.
All values from environment variables (see .env.example).

charts-flashback is a download-only service: it reads the "last played"
rotation of FLASHBACK FM (GraphQL, public) per decade channel and lets the
SHARED library grow along. It writes NO playlists - the decade display is
handled by the Navidrome Smart Playlists (NSP), which filter by year.
"""

import logging
import os

from .i18n import t

logger = logging.getLogger(__name__)


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _get_bool(name: str, default: bool) -> bool:
    return os.environ.get(name, "1" if default else "0") == "1"


def _get_enabled(name: str, default: bool = True) -> bool:
    """
    Tolerant on/off switch: accepts true/1/yes/on (case-insensitive) as "on",
    empty -> default. A non-empty, unrecognized value counts as "off" (a config
    error should be visible) and logs a warning once. In addition to _get_bool
    (does not replace it) so that true/false works reliably.
    """
    raw = os.environ.get(name, "").strip().lower()
    if raw == "":
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    logger.warning(t("flashback.config.enabled_unparsed", name=name, value=raw))
    return False


def _get_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


# --- Paths (like charts-sync/charts-recommend) ---
MUSIC_DIR = os.environ.get("MUSIC_DIR", "/music/Music")
CHARTS_DIR = os.environ.get("CHARTS_DIR", "/charts")

# --- On/off switch (default on; the container always keeps running) ---
# false = pause the service (logs "disabled", does nothing, status idle - NO
# error). Tolerant parser: true/1/yes/on or false/0/no/off. With true a
# FLASHBACK_HASH is additionally needed - otherwise the service also stays idle.
FLASHBACK_ENABLED = _get_enabled("FLASHBACK_ENABLED", True)

# --- FLASHBACK FM (GraphQL source) ---
# Persisted-query hash of the LastPlayed query. Public, NOT a secret - but it
# can change when FLASHBACK FM rebuilds its app. Then re-fetch it from the
# browser network tab (request "played", URL ends in .../LastPlayed/<HASH>?).
# Empty = the service runs empty (no error).
FLASHBACK_HASH = os.environ.get("FLASHBACK_HASH", "").strip()

# Which streams (streamName) are queried. Comma-separated.
# IMPORTANT: only the LIVE MAIN STREAM "flashbackfm.ffm" keeps a played history
# (mixed across all decades). The thematic decade/artist channels return
# played=null and do NOT make sense here. Default = main stream.
FLASHBACK_CHANNELS = [
    c.strip() for c in
    os.environ.get("FLASHBACK_CHANNELS", "flashbackfm.ffm").split(",")
    if c.strip()
]

# How many "last played" entries are queried per channel (limitTo).
FLASHBACK_PLAYED_LIMIT = _get_int("FLASHBACK_PLAYED_LIMIT", 50)
# Cap: process at most this many NEW songs per run (against overflow when many
# channels are configured).
FLASHBACK_LIMIT = _get_int("FLASHBACK_LIMIT", 50)

# State file: already seen (artist|title) keys so the radio rotation does not
# reprocess the same songs on every run.
STATE_FILE = os.path.join(CHARTS_DIR, ".charts-flashback-state.json")

# Target missing list (charts-download backfills it if it is in its
# MISSING_FILES). Clean "artist - title" format like charts-sync, WITHOUT a
# year in the name.
MISSING_FILE = os.environ.get("FLASHBACK_MISSING_FILE", "missing-flashback.txt")

# Year map (artist|title -> year) that charts-import reads when tagging to set
# the date/originaldate tag - WITHOUT ever writing the year into file/folder
# names. Lives in the shared Charts folder so charts-import can reach it.
YEAR_MAP_FILE = os.path.join(CHARTS_DIR, ".charts-flashback-years.json")

# --- MusicBrainz (year resolution, hybrid) ---
# Exact release year via MusicBrainz; if not plausible/empty -> the caller
# falls back to the channel decade midpoint (year.py).
MB_ENABLED = _get_bool("MB_ENABLED", True)
# MusicBrainz requires a meaningful User-Agent.
MB_USER_AGENT = os.environ.get("MB_USER_AGENT", "charts-flashback/1.0 (self-hosted)")
# Rate limit: MusicBrainz allows ~1 request/second. 1.1s buffer.
MB_RATE_SLEEP = _get_float("MB_RATE_SLEEP", 1.1)
# Minimum score of a recording hit for it to be considered.
MB_SCORE_MIN = _get_int("MB_SCORE_MIN", 90)
# Cache (Artist|Title -> year or "") so recurring songs do not hit the API
# again. Misses are cached too.
MB_CACHE_FILE = os.path.join(CHARTS_DIR, ".charts-flashback-mbcache.json")

# --- Deezer casing (canonical spelling of artist/title) ---
# FLASHBACK FM delivers artist/title in UPPERCASE. Before writing into the
# missing list (-> folder/file name) the canonical spelling is resolved via the
# open Deezer search API (no API key), consistent with charts-sync.
DEEZER_CANONICAL_ENABLED = _get_bool("DEEZER_CANONICAL_ENABLED", True)
# Process-wide cache (Artist|Title -> [artist, title]), misses (raw value) are
# cached too. Lives in the shared Charts folder, analogous to MB_CACHE_FILE.
DEEZER_CACHE_FILE = os.path.join(CHARTS_DIR, ".charts-flashback-deezer-cache.json")
# Minimum similarity (artist AND title, 0-100) of a Deezer hit for it to count
# as the canonical source. A bit below MATCH_THRESHOLD, since normalize()
# lowercases anyway (casing irrelevant) - the threshold only catches real false
# hits.
DEEZER_MATCH_MIN = _get_int("DEEZER_MATCH_MIN", 80)
# Asymmetric validation for real cases where the plain AND threshold (both >=
# MATCH_MIN) fails although it is the same song:
#   - alias artists (FLASHBACK "OMD" vs. Deezer "Orchestral Manoeuvres In The
#     Dark"): title 100, artist low.
#   - title with a parenthesized addition (Deezer "There Must Be an Angel
#     (Playing With My Heart)"): artist 100, title depressed by the addition.
# Rule: if ONE field is very confident (>= STRONG), RELAX is enough for the
# other. Otherwise the strict AND threshold MATCH_MIN still applies. STRONG high
# (near exact), RELAX moderate so the relaxation lets no false hits through.
DEEZER_MATCH_STRONG = _get_int("DEEZER_MATCH_STRONG", 97)
DEEZER_MATCH_RELAX = _get_int("DEEZER_MATCH_RELAX", 75)

# --- Matching (identical to charts-sync/charts-recommend) ---
MATCH_THRESHOLD = _get_int("MATCH_THRESHOLD", 87)
ARTIST_MIN_SCORE = _get_int("ARTIST_MIN_SCORE", 60)
IGNORE_COMPILATIONS = _get_bool("IGNORE_COMPILATIONS", True)
AUDIO_EXTENSIONS = {
    ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma", ".wav", ".alac",
}

# --- Backfill (interlock with charts-download) ---
WRITE_MISSING = _get_bool("WRITE_MISSING", True)

# --- HTTP robustness (FLASHBACK FM GraphQL) ---
HTTP_TIMEOUT = _get_int("HTTP_TIMEOUT", 30)
HTTP_RETRIES = _get_int("HTTP_RETRIES", 3)

# --- Scheduler ---
INTERVAL_HOURS = _get_int("FLASHBACK_INTERVAL_HOURS", 2)
RUN_ON_START = _get_bool("RUN_ON_START", True)

# --- Pushover (optional) ---
PUSHOVER_TOKEN = os.environ.get("PUSHOVER_TOKEN", "").strip()
PUSHOVER_USER = os.environ.get("PUSHOVER_USER", "").strip()
# Globally mute Pushover for this service (without deleting token/user).
PUSHOVER_ENABLED = os.environ.get("PUSHOVER_ENABLED", "1") == "1"
PUSHOVER_DEVICE = os.environ.get("PUSHOVER_DEVICE", "").strip()
PUSHOVER_DEVICES_TTL = _get_int("PUSHOVER_DEVICES_TTL", 3600)
# Pushover notification sound. Empty = account default (no sound field in the
# request). Default for charts-flashback: "falling".
PUSHOVER_SOUND = os.environ.get("PUSHOVER_SOUND", "falling").strip()
NOTIFY_ONLY_ON_CHANGE = _get_bool("NOTIFY_ONLY_ON_CHANGE", True)
