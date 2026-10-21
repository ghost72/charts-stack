"""
Configuration for the charts-import service.
All values are read from environment variables (see .env.example).
"""

import logging
import os

logger = logging.getLogger(__name__)


def _get_int(name: str, default: int) -> int:
    """Reads an integer from the environment, falling back to the default on errors."""
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _get_bool(name: str, default: bool) -> bool:
    """Reads a bool from the environment (1/0)."""
    return os.environ.get(name, "1" if default else "0") == "1"


def _get_float(name: str, default: float) -> float:
    """Reads a float from the environment, falling back to the default on errors."""
    try:
        return float(os.environ.get(name, default))
    except (ValueError, TypeError):
        return default


# --- Paths (mounted in the container) ---
# DOWNLOADS_DIR: where slskd puts the finished chart downloads (= slskd
#                directories.downloads). The freshly loaded songs sit here.
# MUSIC_DIR:     Navidrome library root. Songs are filed in there
#                (Music/<Artist>/<Album>/<Track>).
DOWNLOADS_DIR = os.environ.get("DOWNLOADS_DIR", "/downloads")
MUSIC_DIR = os.environ.get("MUSIC_DIR", "/music/Music")
# Directory where charts-sync puts the playlist .m3u files (charts.m3u,
# mundart.m3u, 80er-90er.m3u). Used by the backfill to restrict --fix-broken
# to ONLY the chart songs (playlist entries) so the rest of your (Lidarr)
# library stays untouched. Default = MUSIC_DIR.
PLAYLIST_DIR = os.environ.get("PLAYLIST_DIR", MUSIC_DIR)

# Manifest from charts-download: maps downloaded files to the CLEAN chart
# metadata (artist/title). Preferred and written into the file tags so songs do
# not land as "Unknown Album" and can be matched correctly by charts-sync.
# IMPORTANT: charts-download writes it to /charts/.charts-manifest.json.
# charts-import sees the same folder (physically /volume2/music/Charts) as
# DOWNLOADS_DIR (/music/Charts) - so the manifest sits here by default under
# DOWNLOADS_DIR/.charts-manifest.json.
MANIFEST_FILE = os.environ.get(
    "MANIFEST_FILE", os.path.join(DOWNLOADS_DIR, ".charts-manifest.json")
)

# Signal file with which charts-import triggers charts-sync after an import so
# the playlist is updated immediately (instead of only on the next 12h run).
# Sits in the shared charts folder (that charts-sync also sees).
RESYNC_SIGNAL = os.environ.get(
    "RESYNC_SIGNAL", os.path.join(DOWNLOADS_DIR, ".resync-requested")
)

# Dedicated signal file dropped when a HITPARADE-relevant song is imported, so
# charts-hitparade re-renders hitparade.m3u promptly instead of only on the next
# Sunday run. Based on DOWNLOADS_DIR (charts-import has no CHARTS_DIR) so it
# resolves to the SAME host file as charts-hitparade's CHARTS_DIR-based
# HITPARADE_RESYNC_SIGNAL. Separate from RESYNC_SIGNAL so each consumer owns and
# clears its own file (no clear-race).
HITPARADE_RESYNC_SIGNAL = os.environ.get(
    "HITPARADE_RESYNC_SIGNAL",
    os.path.join(DOWNLOADS_DIR, ".hitparade-resync-requested"),
)
# Source label whose MEMBERSHIP in a "+"-joined source string (e.g.
# "charts+hitparade") marks an import as hitparade-relevant. Tested per part,
# never as a whole-string/substring compare.
HITPARADE_SOURCE_LABEL = os.environ.get("HITPARADE_SOURCE_LABEL", "hitparade")

# charts-download state: read to release rejected songs (gatekeeper) so
# charts-download loads them again (and ideally better). Sits in the same
# shared charts folder. Set empty to disable the release.
DOWNLOAD_STATE_FILE = os.environ.get(
    "DOWNLOAD_STATE_FILE", os.path.join(DOWNLOADS_DIR, ".charts-download-state.json")
)

# --- Import behavior ---
# COPY_MODE=1  -> COPY the file (the original stays in the downloads folder so
#                 slskd can keep sharing ("seeding") it for a while).
# COPY_MODE=0  -> MOVE the file (disappears from the share immediately).
COPY_MODE = _get_bool("COPY_MODE", True)

# REQUIRE_MANIFEST=1 (default): import ONLY files that have an entry in the
# charts-download manifest (= chart songs specifically loaded by
# charts-download). The downloads folder often contains whole sampler/album
# folders that came along with the song download from Soulseek; without this
# lock charts-import would file in arbitrary tracks (e.g. compilation versions)
# from them - and on EVERY run again, as long as the source files sit in the
# shared downloads folder. With the lock the library stays clean.
# REQUIRE_MANIFEST=0 -> old behavior: import everything that can be mapped
# (e.g. when you place files manually in the downloads folder).
REQUIRE_MANIFEST = _get_bool("REQUIRE_MANIFEST", True)

# --- Cleanup strategy ---
# There are two strategies for cleaning up DOWNLOADS_DIR. KEEP_LATEST takes
# precedence if both are set.
#
# KEEP_LATEST (count-based, RECOMMENDED):
#   Always keep the N NEWEST downloads, only delete the excess.
#   Advantage: the share stays stable at ~N files, no matter how often/rarely
#   you manually search for new songs. Ideal to permanently share enough files
#   and not be considered a "leecher".
#   Values:
#     "all" = keep ALL downloads (never delete, maximum seeding)
#     number = keep only the N newest, delete the rest
#     0     = disabled (then RETENTION_DAYS applies)
# Internally: "all" -> -1 (special value for "keep unlimited").
def _parse_keep(value, default: int) -> int:
    if value is None:
        return default
    s = str(value).strip().lower()
    if s in ("all", "alle"):
        return -1
    try:
        return int(s)
    except ValueError:
        return default


KEEP_LATEST = _parse_keep(os.environ.get("KEEP_LATEST"), 500)

# RETENTION_DAYS (age-based, fallback):
#   Deletes downloads older than this many days. Only active when
#   KEEP_LATEST = 0. Fits poorly with irregular manual searching, because the
#   share can run empty during longer pauses. 0 = never delete.
RETENTION_DAYS = _get_int("RETENTION_DAYS", 0)

# Trash retention: the upgrade flow moves replaced (worse) versions to
# MUSIC_DIR/.charts-trash/<date>/ (reversible). These are deleted automatically
# when they are older than this many days. 0 = never delete (then empty the
# trash manually).
TRASH_RETENTION_DAYS = _get_int("TRASH_RETENTION_DAYS", 30)

# Only perform the import if the file has not changed for at least this many
# seconds (prevents filing in half-finished downloads).
MIN_AGE_SECONDS = _get_int("MIN_AGE_SECONDS", 120)

# --- Scheduler ---
# Interval between two runs in minutes.
INTERVAL_MINUTES = _get_int("INTERVAL_MINUTES", 30)
# Run once immediately at start (1) or only after the interval (0).
RUN_ON_START = _get_bool("RUN_ON_START", True)

# --- Metadata backfill (periodic catch-up) ---
# Catches up missing covers/ReplayGain when an external source was temporarily
# unreachable at import time (fail-open then leaves the song without cover/RG
# in the library). Runs in the same scheduler loop, but rarely (own interval in
# hours). Scope: ONLY the chart songs from the manifest - the rest of the
# (Lidarr) library stays untouched.
# 0 = backfill off.
BACKFILL_INTERVAL_HOURS = _get_int("BACKFILL_INTERVAL_HOURS", 24)
# Pause between the API calls (ms), eases iTunes/Deezer rate limits.
BACKFILL_SLEEP_MS = _get_int("BACKFILL_SLEEP_MS", 400)
# Max files per backfill run (0 = all). Cap against very long runs.
BACKFILL_LIMIT = _get_int("BACKFILL_LIMIT", 0)
# Reconcile UPPERCASE flashback names in the backfill against the official
# Deezer spelling and correct them (only safe hits; renames the library
# folder/file + updates tags/manifest). 1 = on, 0 = off.
BACKFILL_RECANON = _get_bool("BACKFILL_RECANON", True)

# --- File types ---
# File extensions considered music files (identical to charts-sync).
AUDIO_EXTENSIONS = {
    ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma", ".wav", ".alac",
}

# --- Gatekeeper / quality control ---
# Files that fail this check are NOT filed into the library but moved to
# REJECTED_DIR (quarantine). This way no junk lands in the library, no matter
# where the file comes from (download, back catalog, manual). The song stays in
# missing.txt -> charts-download loads a better version.

# 1 = gatekeeper active, 0 = off (everything is filed in as before).
REJECT_ENABLED = _get_bool("REJECT_ENABLED", True)

# --- Upsampling/fake-bitrate detection (frequency cutoff) ---
# Detects files that CLAIM a high bitrate (e.g. 320 kbps) but really only have
# the frequency range of a low bitrate (upsampled/faked). The real frequency
# cutoff is measured via ffmpeg (quality.py). Deliberately CONSERVATIVE: only
# kicks in at a claimed bitrate >= REJECT_UPSAMPLED_MIN_BITRATE and only when
# the measured cutoff is CLEARLY (tolerance) below expectation. If the cutoff is
# not measurable (ffmpeg/filter missing), it is NOT rejected. 1 = active,
# 0 = off.
#
# CPU WARNING: every check decodes the file once with ffmpeg. Noticeable on weak
# hardware (NAS without HW transcoding). Only manifest files (real chart
# downloads) are checked, not the whole downloads folder.
REJECT_UPSAMPLED = _get_bool("REJECT_UPSAMPLED", True)
# Only check for upsampling from this CLAIMED bitrate (kbps) upward. Below it a
# low cutoff is normal (no fake) and the file is covered by the bitrate
# criterion anyway.
REJECT_UPSAMPLED_MIN_BITRATE = _get_int("REJECT_UPSAMPLED_MIN_BITRATE", 256)
# Tolerance (kHz): how far the measured cutoff may be below expectation BEFORE
# the file counts as upsampled. Larger = more lenient (fewer false alarms, but
# finds fewer fakes). 2.0 is a safe starting value.
CUTOFF_TOLERANCE_KHZ = _get_float("CUTOFF_TOLERANCE_KHZ", 2.0)

# --- Cover / artwork ---
# EMBED_COVERS=1: for songs with clean chart metadata (manifest) the matching
# cover is searched via the configured sources (COVER_SOURCES, iTunes->Deezer)
# and embedded into the MP3 (only if no cover is present yet). This way the
# songs have artwork in Navidrome without external agents. 0 = off.
# Minimum length (seconds). Shorter files count as aborted/incomplete downloads
# (e.g. a 24s fragment) and are rejected + released in the charts-download state
# (searched again). Chart singles are practically always >90s. 0 = check off.
# Affects only the import (chart songs), not the rest of the library.
MIN_DURATION_SECONDS = _get_int("MIN_DURATION_SECONDS", 90)

# --- Deezer casing canonicalization (last resort at import) ---
# When filing in a chart song, charts-import resolves the canonical spelling
# (mixed case) via the open Deezer search API if the manifest name is still
# UPPERCASE/suspicious (flashback raw values that slipped through). Central last
# resort so an UPPERCASE name NEVER reaches the library.
DEEZER_CANONICAL_ENABLED = _get_bool("DEEZER_CANONICAL_ENABLED", True)
# Cache (artist|title -> [artist, title], also misses). Sits in the shared
# charts folder (= DOWNLOADS_DIR), shareable with charts-flashback.
DEEZER_CACHE_FILE = os.environ.get(
    "DEEZER_CACHE_FILE",
    os.path.join(DOWNLOADS_DIR, ".charts-flashback-deezer-cache.json"),
)
# Minimum similarity (0-100) of a Deezer hit; asymmetric loosening via
# STRONG/RELAX (see deezer._passes). Conservative -> no mismatches.
DEEZER_MATCH_MIN = _get_int("DEEZER_MATCH_MIN", 80)
DEEZER_MATCH_STRONG = _get_int("DEEZER_MATCH_STRONG", 97)
DEEZER_MATCH_RELAX = _get_int("DEEZER_MATCH_RELAX", 75)
# HTTP timeout for the Deezer lookup (seconds).
HTTP_TIMEOUT = _get_int("HTTP_TIMEOUT", 15)

EMBED_COVERS = _get_bool("EMBED_COVERS", True)
# OVERWRITE_COVER=1: for chart songs (manifest), REPLACE an already-embedded
# cover with the newly searched single cover (source per COVER_SOURCES,
# iTunes->Deezer). Important when the song comes from a compilation/sampler -
# there the sampler cover is often embedded, not the single's. The cover is
# searched via artist+title and returns the original single/album cover.
# Replaces only if a source actually returns a cover (otherwise the existing
# one is kept). 0 = never overwrite an existing cover.
OVERWRITE_COVER = _get_bool("OVERWRITE_COVER", True)
# Cover sources and order (comma-separated). iTunes/Apple has the largest
# catalog with dedicated single artworks in high resolution and is always
# square; Deezer serves as a fallback if iTunes does not know a song.
#   COVER_SOURCES=itunes,deezer  (default) = iTunes first, then Deezer
#   COVER_SOURCES=deezer         = Deezer only (old behavior)
COVER_SOURCES = [
    s.strip().lower()
    for s in os.environ.get("COVER_SOURCES", "itunes,deezer").split(",")
    if s.strip()
]
# Desired cover edge length at iTunes (Apple delivers arbitrary sizes via the
# file name; 1000 is a good compromise of quality/size).
ITUNES_COVER_SIZE = _get_int("ITUNES_COVER_SIZE", 1000)
# iTunes/Apple throttles the free search API (HTTP 429/403) after many requests
# in a short time. On a throttle the client waits briefly and retries
# (exponentially increasing) before falling back to Deezer. This way more real
# iTunes single covers get through.
ITUNES_MAX_RETRIES = _get_int("ITUNES_MAX_RETRIES", 3)
# Base wait time in ms for the first retry (doubled afterwards: 800, 1600 ...).
ITUNES_RETRY_BASE_MS = _get_int("ITUNES_RETRY_BASE_MS", 800)

# ReplayGain normalization: at import, measure the loudness (EBU R128) and
# write the track gain as a tag so all songs play back equally loud. Lossless
# (tags only). Needs ffmpeg in the image. IMPORTANT: the player (Navidrome/app)
# must have the ReplayGain mode enabled, otherwise it has no effect. 0 = off.
REPLAYGAIN = _get_bool("REPLAYGAIN", True)
# Target loudness in LUFS. ReplayGain 2.0 reference = -18 (quieter, lots of
# headroom). Streaming services are louder: Spotify/YouTube ~-14, Apple Music
# ~-16. A smaller (more negative) value = quieter, a larger one = louder.
# Default -14 (streaming level, ~+4 dB louder than the old -18 reference).
REPLAYGAIN_TARGET_LUFS = _get_float("REPLAYGAIN_TARGET_LUFS", -14.0)

# PRESERVE_MTIME=1 (default): keep a library file's mtime stable when
# charts-import only RETOUCHES an existing file (retag, cover embed, ReplayGain
# write) and when a song is UPGRADED in place. Navidrome derives "date added"
# from the mtime, so without this an old song would wrongly jump to the top of
# a "recently added" playlist after a backfill/retag. First-time imports keep
# their fresh download mtime (correct date added). 0 = let every write bump the
# mtime (old behavior).
PRESERVE_MTIME = _get_bool("PRESERVE_MTIME", True)

# Subfolder in DOWNLOADS_DIR into which rejected files are moved.
REJECTED_DIR = os.environ.get(
    "REJECTED_DIR", os.path.join(DOWNLOADS_DIR, "_rejected")
)

# Quarantine retention: rejected files in REJECTED_DIR are deleted
# automatically when they are older than this many days. Without this rule the
# quarantine grows unbounded (it is exempt from the KEEP_LATEST cleanup). The
# affected song is reloaded better/anew anyway, the discarded file is not
# needed. 0 = never delete (empty manually).
REJECTED_RETENTION_DAYS = _get_int("REJECTED_RETENTION_DAYS", 14)

# Reject if no usable artist can be determined (empty / "Unknown Artist").
# Album is deliberately NOT checked, since chart singles often have no album.
# 1 = active.
REJECT_UNKNOWN_ARTIST = _get_bool("REJECT_UNKNOWN_ARTIST", True)

# Exclude keywords: reject a file if the name contains one of these words
# (lyrics videos, remixes, live, acapella versions etc.). This way old/foreign
# files that did not come via charts-download are also sorted out. Leave empty
# for the default list; "off"/"none" turns off the keyword check; a custom
# comma-separated list replaces the default list.
_DEFAULT_REJECT_KEYWORDS = [
    "lyrics", "lyric video", "official video", "official audio", "music video",
    "visualizer", "video edit",
    "acapella", "a capella", "instrumental", "karaoke",
    "remix", "mashup", "bootleg", "flip", "rework", "vip mix",
    "extended mix", "club mix", "radio mix", "dance mix", "club intro",
    "live", "cover", "tribute", "unplugged",
    "sped up", "spedup", "slowed", "reverb", "nightcore", "8d", "8d audio",
    "snippet", "teaser", "preview", "loop",
    "break in", "clean short edit", "clean radio edit",
]
_reject_kw_env = os.environ.get("REJECT_KEYWORDS", "").strip()
if _reject_kw_env.lower() in ("off", "none"):
    REJECT_KEYWORDS = []
elif _reject_kw_env:
    REJECT_KEYWORDS = [k.strip() for k in _reject_kw_env.split(",") if k.strip()]
else:
    REJECT_KEYWORDS = _DEFAULT_REJECT_KEYWORDS

# --- Pushover (optional, identical to the charts-sync / jellyfin-webhook setup) ---
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
# request). Default for charts-import: "incoming".
PUSHOVER_SOUND = os.environ.get("PUSHOVER_SOUND", "incoming").strip()
# A separate sound ONLY for the availability message of songs manually
# requested via charts-songrequest ("Your song is here!"). Default "magic" so
# the user hears from the sound that THEIR requested song has arrived (instead
# of a routine import). Empty = account default. The normal import batch
# message still uses PUSHOVER_SOUND ("incoming").
PUSHOVER_SOUND_MANUAL = os.environ.get("PUSHOVER_SOUND_MANUAL", "magic").strip()

# --- Lidarr integration (optional) ---
# Keeps chart single artists in Lidarr "unmonitored" so Lidarr tags/knows them
# but does NOT load the whole catalog. This way the default monitoring in
# Lidarr can stay active for real albums/compilations.
# Only active if LIDARR_URL + LIDARR_API_KEY are set.
LIDARR_URL = os.environ.get("LIDARR_URL", "").strip()
LIDARR_API_KEY = os.environ.get("LIDARR_API_KEY", "").strip()
# Master switch (1 = on). Inactive anyway without URL/key.
LIDARR_UNMONITOR = _get_bool("LIDARR_UNMONITOR", True)
# Create missing artists in Lidarr anew (directly unmonitored)? If 0, only
# already-present artists are unmonitored, not newly created.
LIDARR_ADD_MISSING = _get_bool("LIDARR_ADD_MISSING", True)
# Profile/root folder for the new creation (IDs from your Lidarr instance).
LIDARR_QUALITY_PROFILE_ID = _get_int("LIDARR_QUALITY_PROFILE_ID", 4)
LIDARR_METADATA_PROFILE_ID = _get_int("LIDARR_METADATA_PROFILE_ID", 1)
LIDARR_ROOT_FOLDER = os.environ.get("LIDARR_ROOT_FOLDER", "/music/Music").strip()
