"""
Configuration for charts-songrequest (web UI + API).
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


# --- slskd API (identical to charts-download) ---
SLSKD_HOST = os.environ.get("SLSKD_HOST", "http://slskd:5030")
SLSKD_API_KEY = os.environ.get("SLSKD_API_KEY", "").strip()
SLSKD_URL_BASE = os.environ.get("SLSKD_URL_BASE", "/").strip()

# --- Selection criteria (identical to charts-download) ---
MIN_BITRATE = _get_int("MIN_BITRATE", 256)
MATCH_THRESHOLD = _get_int("MATCH_THRESHOLD", 80)
REQUIRE_FREE_SLOT = _get_bool("REQUIRE_FREE_SLOT", True)

# Exclusion keywords (only load original versions). Like charts-download:
# empty/unset -> built-in default list; "off"/"none" -> filter off;
# own comma-separated list -> replaces the default list.
from .matching import DEFAULT_EXCLUDE_KEYWORDS

_exclude_env = os.environ.get("EXCLUDE_KEYWORDS", "").strip()
if _exclude_env.lower() in ("off", "none"):
    EXCLUDE_KEYWORDS = []
elif _exclude_env:
    EXCLUDE_KEYWORDS = [k.strip() for k in _exclude_env.split(",") if k.strip()]
else:
    EXCLUDE_KEYWORDS = DEFAULT_EXCLUDE_KEYWORDS

# Reject compilation/sampler hits completely (like charts-download).
# Manual requests should get a real single/album version, not a sampler
# track. Default on.
REJECT_COMPILATIONS = _get_bool("REJECT_COMPILATIONS", True)

# --- Search ---
SEARCH_TIMEOUT_MS = _get_int("SEARCH_TIMEOUT_MS", 15000)
SEARCH_WAIT_SECONDS = _get_int("SEARCH_WAIT_SECONDS", 25)

# --- Library (for the "already present?" pre-check) ---
# Navidrome library root. Must be mounted into the container (read-only is
# enough) so it can be checked before the search whether the song already
# exists. Empty/not mounted -> check is skipped (then it downloads as before).
MUSIC_DIR = os.environ.get("MUSIC_DIR", "/music/Music")

# --- Manifest (the same file as charts-download/charts-import!) ---
# IMPORTANT: must point at the same file that charts-import reads so the
# manually requested song gets filed cleanly on the next import run.
MANIFEST_FILE = os.environ.get("MANIFEST_FILE", "/charts/.charts-manifest.json")

# Playlist label for the manifest (shows up in the charts-import message).
MANUAL_SOURCE_LABEL = os.environ.get("MANUAL_SOURCE_LABEL", "manuell")

# --- Web UI / Auth ---
# Pure multi-user login via SONGREQUEST_USERS = "user1:pass1,user2:pass2".
# At least one user MUST be defined, otherwise the app does not start (no
# unprotected access from outside). The previously supported simple login
# (APP_PASSWORD/APP_USERS) was removed.


def _parse_users() -> dict:
    """
    Builds the user->password map from SONGREQUEST_USERS.
    Format "user1:pass1,user2:pass2": the user name is split at the FIRST ':',
    the rest is the password (so a password must not contain a colon; comma
    separates pairs). charts-songrequest is pure multi-user login - there is NO
    simple-password fallback anymore.
    """
    users: dict = {}
    raw = os.environ.get("SONGREQUEST_USERS", "").strip()
    if raw:
        for pair in raw.split(","):
            pair = pair.strip()
            if not pair or ":" not in pair:
                continue
            name, pw = pair.split(":", 1)
            name = name.strip()
            if name and pw:
                users[name] = pw
    return users


# User->password map. Empty = app refuses to start.
APP_USERS = _parse_users()


def _parse_user_devices() -> dict:
    """
    Builds the user->Pushover-device map from USER_PUSHOVER_DEVICES.
    Format "user1:device1,user2:device2" (comma separates pairs, first ':'
    separates user name from device name). The MATCHING happens later on the
    logged-in USER NAME.

    Purpose: when a manually requested song is later filed into the library by
    charts-import, the requesting user gets a Pushover message to EXACTLY THIS
    device. If a user is NOT in the map, no device is written into the
    manifest -> there is NO message (deliberate: never send to everyone).
    Empty/unset -> empty dict -> no availability message for anyone.
    """
    out: dict = {}
    raw = os.environ.get("USER_PUSHOVER_DEVICES", "").strip()
    if raw:
        for pair in raw.split(","):
            pair = pair.strip()
            if not pair or ":" not in pair:
                continue
            name, device = pair.split(":", 1)
            name = name.strip()
            device = device.strip()
            if name and device:
                out[name] = device
    return out


# User->Pushover-device. Empty = no availability message for anyone.
USER_PUSHOVER_DEVICES = _parse_user_devices()

# Secret for the session cookies (random string). If missing, a random one is
# generated (then existing sessions are invalid after a restart).
SESSION_SECRET = os.environ.get("SESSION_SECRET", "").strip()

# --- Pushover (optional, confirmation on request) ---
PUSHOVER_TOKEN = os.environ.get("PUSHOVER_TOKEN", "").strip()
PUSHOVER_USER = os.environ.get("PUSHOVER_USER", "").strip()
PUSHOVER_DEVICE = os.environ.get("PUSHOVER_DEVICE", "").strip()
# Cache duration (seconds) for the device list fetched from the Pushover
# account. The PUSHOVER_DEVICE list is filtered against it before sending so an
# invalid/disabled device name does not trigger Pushover's "to everyone"
# fallback (double push). Default 3600s = 1h.
PUSHOVER_DEVICES_TTL = _get_int("PUSHOVER_DEVICES_TTL", 3600)
# Pushover notification sound. Empty = account default (no sound field in the
# request). Default for charts-songrequest: "magic". NOTE: currently inert,
# since charts-songrequest itself sends no Pushover message - the availability
# message ("Your song is here!") comes from charts-import. The variable stays
# for consistency and only takes effect if songrequest later sends itself.
PUSHOVER_SOUND = os.environ.get("PUSHOVER_SOUND", "magic").strip()
