"""
Configuration for charts-digest (from environment variables).

charts-digest sends a Pushover summary once a day of the songs newly filed into
the library the PREVIOUS day, grouped by source
(charts/mundart/flashback/manuell/upgrade ...). The data basis is the import
log kept by charts-import (JSON Lines) - NO mtime/title heuristic.
"""

import os


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _get_bool(name: str, default: bool) -> bool:
    return os.environ.get(name, "1" if default else "0") == "1"


# --- Schedule -------------------------------------------------------------
# Time (local) of the daily summary. Default 09:00. The service runs as a
# scheduler loop and waits until the next target time.
DIGEST_HOUR = _get_int("DIGEST_HOUR", 9)
DIGEST_MINUTE = _get_int("DIGEST_MINUTE", 0)

# Send a summary immediately on start (for testing). Default off - otherwise a
# message would come on every container restart.
RUN_ON_START = _get_bool("RUN_ON_START", False)

# --- Time window ----------------------------------------------------------
# Look-back window in hours. Default 24 = "since yesterday at the same time".
# With DIGEST_HOUR=9, 24h cleanly covers the previous day 09:00 -> today 09:00.
DIGEST_WINDOW_HOURS = _get_int("DIGEST_WINDOW_HOURS", 24)

# --- Paths (mounted in the container) ------------------------------------
# Shared charts folder: this holds the import log written by charts-import.
# Must be the same folder as DOWNLOADS_DIR of charts-import.
CHARTS_DIR = os.environ.get("CHARTS_DIR", "/charts")
IMPORT_LOG_FILE = os.environ.get(
    "IMPORT_LOG_FILE", os.path.join(CHARTS_DIR, ".charts-imported-log.jsonl"))

# Log retention: on read, entries older than this many days are discarded and
# the log is rewritten compactly (otherwise it grows unbounded).
LOG_RETENTION_DAYS = _get_int("LOG_RETENTION_DAYS", 7)

# Max. number of songs listed per source in the message. The goal is to list
# ALL songs if possible - the actual cap is the character budget
# (PUSHOVER_MESSAGE_LIMIT), not this number. Set high so it normally does not
# kick in; only a safety net against extreme cases. The rest per source is
# summarized as "... and N more".
MAX_PER_SOURCE = _get_int("MAX_PER_SOURCE", 100)

# Long song titles are shortened at the END ("start…") so more songs fit the
# character budget. The artist always stays full; artist + title start are
# enough to recognize it. Only kicks in from this title length (characters); 0
# turns the shortening off.
TITLE_MAX_LEN = _get_int("TITLE_MAX_LEN", 34)

# Hard character budget of the Pushover message. Pushover truncates a 'message'
# at 1024 characters SILENTLY (then e.g. the footer is missing / a song breaks
# off mid-title). So we build the message budget-aware and replace the overflow
# with a clean "... and N more". A little reserve under 1024 for the guaranteed
# footer.
PUSHOVER_MESSAGE_LIMIT = _get_int("PUSHOVER_MESSAGE_LIMIT", 1024)

# Also send a message when there was NOTHING new (default off - no "0 new
# songs" spam; the service only reports when something was actually added).
NOTIFY_WHEN_EMPTY = _get_bool("NOTIFY_WHEN_EMPTY", False)

# --- Pushover -------------------------------------------------------------
PUSHOVER_TOKEN = os.environ.get("PUSHOVER_TOKEN", "").strip()
PUSHOVER_USER = os.environ.get("PUSHOVER_USER", "").strip()
# Mute Pushover for this service globally (without deleting token/user).
PUSHOVER_ENABLED = os.environ.get("PUSHOVER_ENABLED", "1") == "1"
PUSHOVER_DEVICE = os.environ.get("PUSHOVER_DEVICE", "").strip()
PUSHOVER_DEVICES_TTL = _get_int("PUSHOVER_DEVICES_TTL", 3600)
# Pushover notification sound. Empty = account default (no sound field in the
# request). Default for charts-digest: "magic".
PUSHOVER_SOUND = os.environ.get("PUSHOVER_SOUND", "magic").strip()

# --- Status (dashboard integration) --------------------------------------
TZ = os.environ.get("TZ", "Europe/Zurich")
