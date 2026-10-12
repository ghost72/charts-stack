"""
Configuration for the charts-upgrade service.
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


def _get_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (ValueError, TypeError):
        return default


# --- slskd API (like charts-download) ---
SLSKD_HOST = os.environ.get("SLSKD_HOST", "http://slskd:5030")
SLSKD_API_KEY = os.environ.get("SLSKD_API_KEY", "").strip()
SLSKD_URL_BASE = os.environ.get("SLSKD_URL_BASE", "/").strip()

# --- Scan target ---
# Navidrome library root that charts-upgrade scans for bad files. A read-only
# mount is enough (charts-upgrade does NOT write directly into the library -
# the replacement is done by charts-import via the manifest/replace path).
MUSIC_DIR = os.environ.get("MUSIC_DIR", "/music/Music")

# --- Quality thresholds ---
# A song counts as "bad" (upgrade candidate) if ONE of these applies:
#   1. Real bitrate < BAD_BITRATE (kbps), OR
#   2. Upsampling detected: claims >= REJECT_UPSAMPLED_MIN_BITRATE but the real
#      frequency cutoff is clearly too low (quality.is_upsampled).
BAD_BITRATE = _get_int("BAD_BITRATE", 256)

# Upsampling detection (identical parameters to charts-import).
CHECK_UPSAMPLED = _get_bool("CHECK_UPSAMPLED", True)
REJECT_UPSAMPLED_MIN_BITRATE = _get_int("REJECT_UPSAMPLED_MIN_BITRATE", 256)
CUTOFF_TOLERANCE_KHZ = _get_float("CUTOFF_TOLERANCE_KHZ", 2.0)

# --- Replacement selection (like charts-download) ---
# Only load MP3 >= MIN_BITRATE as a replacement. Ignore other formats.
MIN_BITRATE = _get_int("MIN_BITRATE", 256)
MATCH_THRESHOLD = _get_int("MATCH_THRESHOLD", 80)
REQUIRE_FREE_SLOT = _get_bool("REQUIRE_FREE_SLOT", True)
# charts-upgrade ALWAYS rejects compilation rips as a replacement (hard-wired
# on): a curated single/album must never be replaced by a sampler track.
REJECT_COMPILATIONS = _get_bool("REJECT_COMPILATIONS", True)

# Exclude keywords (only load original versions). Empty = default list,
# "off"/"none" = off, a custom comma-separated list replaces the default list.
from .matching import DEFAULT_EXCLUDE_KEYWORDS

_exclude_env = os.environ.get("EXCLUDE_KEYWORDS", "").strip()
if _exclude_env.lower() in ("off", "none"):
    EXCLUDE_KEYWORDS = []
elif _exclude_env:
    EXCLUDE_KEYWORDS = [k.strip() for k in _exclude_env.split(",") if k.strip()]
else:
    EXCLUDE_KEYWORDS = DEFAULT_EXCLUDE_KEYWORDS

# --- Compilations ---
# Completely SKIP compilation/sampler folders during the scan (never upgrade).
# Your curated compilations (Bravo Hits, Kuschelrock, ...) are complete albums
# and must not be replaced track by track with Soulseek singles. Detection via
# the path heuristic (matching.is_compilation) PLUS "many different artists in
# the album folder" (see library.py).
SKIP_COMPILATIONS = _get_bool("SKIP_COMPILATIONS", True)

# --- Volume control / safety ---
# How many upgrades are triggered at most per run. Conservative, so half the
# library is not re-loaded at once.
MAX_PER_RUN = _get_int("MAX_PER_RUN", 10)

# How many files are ANALYZED at most per run (the ffmpeg cutoff is CPU-
# expensive). The scan remembers in the state which files have been checked
# (path + mtime + size) and only checks up to this quota of NEW/changed files
# per run. This way the service works through the library gently, instead of
# re-decoding everything on every run.
MAX_SCAN_PER_RUN = _get_int("MAX_SCAN_PER_RUN", 200)

# DRY_RUN=1 (DEFAULT): load/replace nothing. It only analyzes and reports which
# songs were detected as bad/upsampled and what WOULD be loaded. Only set to 0
# once the detection convinces you in a test run.
DRY_RUN = _get_bool("DRY_RUN", True)

# --- Search ---
SEARCH_TIMEOUT_MS = _get_int("SEARCH_TIMEOUT_MS", 15000)
SEARCH_WAIT_SECONDS = _get_int("SEARCH_WAIT_SECONDS", 25)

# --- Scheduler ---
# charts-upgrade runs rarely (cleaning up the back catalog is not an urgent
# task). Default: every 24h.
INTERVAL_MINUTES = _get_int("INTERVAL_MINUTES", 1440)
RUN_ON_START = _get_bool("RUN_ON_START", True)

# --- State / Manifest ---
# Own state (analyzed-files cache + triggered upgrades). Sits in the shared
# /charts folder like the state files of the sister services.
STATE_FILE = os.environ.get("STATE_FILE", "/charts/.charts-upgrade-state.json")
# Shared manifest with charts-import (where the replace entry is read).
MANIFEST_FILE = os.environ.get("MANIFEST_FILE", "/charts/.charts-manifest.json")
# Source label for the manifest (shows up in the charts-import message).
SOURCE_LABEL = os.environ.get("SOURCE_LABEL", "upgrade")

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
# request). Default for charts-upgrade: "cosmic".
PUSHOVER_SOUND = os.environ.get("PUSHOVER_SOUND", "cosmic").strip()
