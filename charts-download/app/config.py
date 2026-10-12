"""
Configuration for the charts-download service.
All values are read from environment variables (see .env.example).
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


# --- slskd API ---
# Base URL of the slskd instance (usually the container name in the Docker net).
SLSKD_HOST = os.environ.get("SLSKD_HOST", "http://slskd:5030")
# API key from slskd.yml (web.authentication.api_keys). Role: readwrite.
SLSKD_API_KEY = os.environ.get("SLSKD_API_KEY", "").strip()
# url_base, in case slskd runs under a sub-path (default "/").
SLSKD_URL_BASE = os.environ.get("SLSKD_URL_BASE", "/").strip()

# --- Source: missing.txt ---
# The missing list(s) written by charts-sync. Multiple files (semicolon-
# separated) can be given, e.g. global and Switzerland charts.
MISSING_FILES = [
    p.strip() for p in os.environ.get(
        "MISSING_FILES", "/charts/missing.txt"
    ).split(";") if p.strip()
]

# --- Selection criteria ---
# Minimum MP3 bitrate in kbps. Results below are discarded.
# (By choice: only MP3 >= 256; FLAC/other formats are ignored.)
MIN_BITRATE = _get_int("MIN_BITRATE", 256)

# Similarity threshold 0-100 for matching search results against
# "Artist - Title". Higher = stricter. 80 is a good starting point.
MATCH_THRESHOLD = _get_int("MATCH_THRESHOLD", 80)

# Exclude keywords: files whose name contains one of these words are discarded
# entirely (only load original versions - no lyrics videos, remixes, live,
# acapella, sped-up versions etc.).
#
# Values in .env / the environment:
#   - unset OR empty            -> built-in default list (recommended)
#   - "off" / "none"            -> filter completely OFF
#   - "word1,word2,..."         -> custom list (replaces the default list)
from .matching import DEFAULT_EXCLUDE_KEYWORDS

_exclude_env = os.environ.get("EXCLUDE_KEYWORDS", "").strip()
if _exclude_env.lower() in ("off", "none"):
    EXCLUDE_KEYWORDS = []
elif _exclude_env:
    EXCLUDE_KEYWORDS = [k.strip() for k in _exclude_env.split(",") if k.strip()]
else:
    EXCLUDE_KEYWORDS = DEFAULT_EXCLUDE_KEYWORDS

# Globally excluded artists: songs by these artists are NEVER loaded (no matter
# which chart source/missing file). Complements the playlist-specific
# "exclude_artists" in charts-sync: while that one only prevents the playlist
# reference, THIS list makes sure the song is never downloaded and placed in
# the library at all. For artists you generally do not want in the collection
# (e.g. Mundart Schlager like Trauffer, which the APIs wrongly classify as
# "Pop").
# Values: comma-separated ("Trauffer,Nemo") OR empty (no exclusion).
# Comparison is case-insensitive and also matches feat. combinations.
EXCLUDE_ARTISTS = [
    a.strip() for a in os.environ.get("EXCLUDE_ARTISTS", "").split(",")
    if a.strip()
]

# Reject compilation/sampler hits entirely (1) instead of only treating them as
# lower priority (0). With 1 a song is NOT loaded if only compilation rips are
# available (e.g. "Bravo Hits", "Top 100", "Various Artists ...") - it then
# stays "missing" until a real single/album version shows up. This keeps the
# library clean (only real singles/albums, no sampler tracks). With 0 (old
# behavior) the compilation is taken if there is nothing better. Compilation
# detection is a path heuristic (is_compilation), not a reliable signal.
REJECT_COMPILATIONS = _get_bool("REJECT_COMPILATIONS", False)

# --- Volume control / safety ---
# How many songs are downloaded (or, in dry-run, planned) at most per run.
# Deliberately conservative so things do not all kick off at once.
MAX_PER_RUN = _get_int("MAX_PER_RUN", 10)

# DRY_RUN=1: NOTHING is downloaded. It only logs/notifies what WOULD be loaded
#   (to check the matching quality). DEFAULT.
# DRY_RUN=0: downloads are actually triggered via the slskd API.
DRY_RUN = _get_bool("DRY_RUN", True)

# --- Search ---
# Timeout of a single slskd search in ms.
SEARCH_TIMEOUT_MS = _get_int("SEARCH_TIMEOUT_MS", 15000)
# How long (seconds) at most to wait for a search to complete.
SEARCH_WAIT_SECONDS = _get_int("SEARCH_WAIT_SECONDS", 25)
# Only consider users with a free upload slot (faster downloads).
REQUIRE_FREE_SLOT = _get_bool("REQUIRE_FREE_SLOT", True)

# Cooldown (hours) for unsuccessful searches. A song that was searched for but
# not found in sufficient quality is skipped for this many hours. Prevents
# permanently unfindable songs (e.g. many Mundart titles) from blocking the
# MAX_PER_RUN quota on EVERY run and never letting the findable songs through.
# After it expires the search is retried.
# 0 = no cooldown (search again every run, old behavior).
SEARCH_COOLDOWN_HOURS = _get_int("SEARCH_COOLDOWN_HOURS", 24)

# Expiry (days) of the per-song provider block list. charts-import remembers
# which Soulseek provider delivered a bad/upsampled file for a song (state:
# blocked_providers). That provider is skipped for EXACTLY THIS song for that
# long. After expiry it gets another chance (it may have replaced its file with
# a better one in the meantime).
# 0 = never expire (blocked permanently until removed manually).
BLOCK_TTL_DAYS = _get_int("BLOCK_TTL_DAYS", 30)

# --- Scheduler ---
INTERVAL_MINUTES = _get_int("INTERVAL_MINUTES", 60)
RUN_ON_START = _get_bool("RUN_ON_START", True)

# --- State ---
# File that records already-requested songs (no duplicate downloads).
STATE_FILE = os.environ.get("STATE_FILE", "/charts/.charts-download-state.json")

# --- Manifest ---
# File that stores the clean chart metadata (artist/title) per download.
# charts-import reads it and tags the file correctly instead of adopting the
# bad slskd tags. MUST sit in the same (shared) folder that charts-import sees
# as /charts.
MANIFEST_FILE = os.environ.get("MANIFEST_FILE", "/charts/.charts-manifest.json")

# --- Pushover (optional) ---
PUSHOVER_TOKEN = os.environ.get("PUSHOVER_TOKEN", "").strip()
PUSHOVER_USER = os.environ.get("PUSHOVER_USER", "").strip()
# Mute Pushover globally for this service (without deleting token/user).
PUSHOVER_ENABLED = os.environ.get("PUSHOVER_ENABLED", "1") == "1"
PUSHOVER_DEVICE = os.environ.get("PUSHOVER_DEVICE", "").strip()
# Cache duration (seconds) for the device list fetched from the Pushover
# account. The PUSHOVER_DEVICE list is filtered against it before sending, so
# an invalid/disabled device name does not trigger Pushover's "send to all"
# fallback (double push). Default 3600s = 1h.
PUSHOVER_DEVICES_TTL = _get_int("PUSHOVER_DEVICES_TTL", 3600)
# Pushover notification sound. Empty = account default (no sound field in the
# request). Default for charts-download: "falling".
PUSHOVER_SOUND = os.environ.get("PUSHOVER_SOUND", "falling").strip()
