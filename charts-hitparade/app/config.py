"""
Configuration for charts-hitparade.
All values from environment variables (see .env.example).
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


def _get_enabled(name: str, default: bool = True) -> bool:
    """
    Tolerant on/off switch: accepts true/1/yes/on (case-insensitive) as "on",
    empty -> default. A non-empty, unrecognized value counts as "off" (a config
    error should be visible) and logs a warning once.
    """
    raw = os.environ.get(name, "").strip().lower()
    if raw == "":
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    logger.warning(t("hitparade.config.enabled_unparsed", name=name, value=raw))
    return False


# --- On/off switch (default on; container always keeps running) ---
# false = pause the service (logs "deaktiviert", does nothing, status idle - NO
# error). Tolerant parser: true/1/yes/on or false/0/no/off.
HITPARADE_ENABLED = _get_enabled("HITPARADE_ENABLED", True)


# --- Paths (mounted in the container, like charts-sync) ---
MUSIC_DIR = os.environ.get("MUSIC_DIR", "/music/Music")
PLAYLIST_DIR = os.environ.get("PLAYLIST_DIR", "") or MUSIC_DIR
CHARTS_DIR = os.environ.get("CHARTS_DIR", "/charts")

# --- Which hitparade.ch charts are fetched ---
# Comma-separated. Possible values:
#   singles      -> official singles Top 100 (WORKS, HTML).
#   singles-ch   -> CH artist charts. WARNING: loads via AJAX afterwards -> 0
#                   songs in the HTML -> would be reported as an error forever.
#                   NOT in the default. The CH hits run in the Top 100 anyway.
# Each chart produces its own playlist + missing file:
#   singles    -> hitparade.m3u       / missing-hitparade.txt
#   singles-ch -> hitparade-ch.m3u    / missing-hitparade-ch.txt
HITPARADE_CHARTS = [
    c.strip() for c in os.environ.get("HITPARADE_CHARTS", "singles").split(",")
    if c.strip()
]

# Playlist/file/display names per chart.
CHART_META = {
    "singles": {
        "playlist": "hitparade.m3u",
        "missing": "missing-hitparade.txt",
        "title": "Schweizer Hitparade \U0001F1E8\U0001F1ED",
    },
    "singles-ch": {
        "playlist": "hitparade-ch.m3u",
        "missing": "missing-hitparade-ch.txt",
        "title": "Schweizer Hitparade - CH-Artists \U0001F1E8\U0001F1ED",
    },
}

# --- Scheduler ---
# The Hitparade is updated weekly (on Sundays). Default: check once a week on
# Sunday at 20:00. Weekday 0=Monday ... 6=Sunday.
RUN_WEEKDAY = _get_int("HITPARADE_WEEKDAY", 6)      # 6 = Sunday
RUN_HOUR = _get_int("HITPARADE_HOUR", 20)           # 20:00
RUN_MINUTE = _get_int("HITPARADE_MINUTE", 0)
# How often the scheduler checks whether the run time has been reached (seconds).
CHECK_INTERVAL_SECONDS = _get_int("HITPARADE_CHECK_SECONDS", 300)
# Run once immediately at container start (1) or wait for the window (0).
RUN_ON_START = os.environ.get("RUN_ON_START", "1") == "1"

# --- HTTP ---
FETCH_TIMEOUT_SECONDS = _get_int("HITPARADE_TIMEOUT_SECONDS", 15)
FETCH_RETRIES = _get_int("HITPARADE_RETRIES", 3)

# --- Matching (identical to charts-sync) ---
MATCH_THRESHOLD = _get_int("MATCH_THRESHOLD", 87)
ARTIST_MIN_SCORE = _get_int("ARTIST_MIN_SCORE", 60)
IGNORE_COMPILATIONS = os.environ.get("IGNORE_COMPILATIONS", "1") == "1"
AUDIO_EXTENSIONS = {
    ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma", ".wav", ".alac",
}

# --- Pushover (optional) ---
PUSHOVER_TOKEN = os.environ.get("PUSHOVER_TOKEN", "").strip()
PUSHOVER_USER = os.environ.get("PUSHOVER_USER", "").strip()
# Mute Pushover globally for this service (without deleting token/user).
PUSHOVER_ENABLED = os.environ.get("PUSHOVER_ENABLED", "1") == "1"
PUSHOVER_DEVICE = os.environ.get("PUSHOVER_DEVICE", "").strip()
# Pushover notification sound. Empty = account default (no sound field in the
# request). Default for charts-hitparade: "falling".
PUSHOVER_SOUND = os.environ.get("PUSHOVER_SOUND", "falling").strip()
NOTIFY_ONLY_ON_CHANGE = os.environ.get("NOTIFY_ONLY_ON_CHANGE", "1") == "1"
NOTIFY_MISSING_LIMIT = _get_int("NOTIFY_MISSING_LIMIT", 40)

# State file (for "only on change" notification). Lives in CHARTS_DIR.
STATE_FILE = os.environ.get("STATE_FILE", ".charts-hitparade-state.json")
