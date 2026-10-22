"""
Configuration for charts-energy.
All values from environment variables (see .env.example).

charts-energy polls the Swiss NRJ (energy.ch) webradio channels for their
now-playing track via the Icecast ICY in-stream metadata, accumulates the
observed spins and feeds them into the existing charts-download pipeline.

The service is config-driven: channels are declared via a CHARTS-style
JSON-in-env (ENERGY_CHANNELS / ENERGY_CHANNELS_FILE), mirroring charts-sync's
CHARTS convention. It ships DISABLED by default (ENERGY_ENABLED=0, empty
channel list) so a fresh clone never polls Icecast unexpectedly.
"""

import json
import logging
import os
import re

from .i18n import t

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
        return float(os.environ.get(name, str(default)))
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
    logger.warning(t("energy.config.enabled_unparsed", name=name, value=raw))
    return False


# --- On/off switch (default OFF; see .env.example / design section 8) ---
# false = pause the service (logs "deaktiviert", does nothing, status idle - NO
# error). Tolerant parser: true/1/yes/on or false/0/no/off. Default OFF because
# reading ICY opens the audio stream; a fresh clone must not poll Icecast
# unexpectedly.
ENERGY_ENABLED = _get_enabled("ENERGY_ENABLED", False)


# --- Paths (mounted in the container, like charts-sync) ---
MUSIC_DIR = os.environ.get("MUSIC_DIR", "/music/Music")
PLAYLIST_DIR = os.environ.get("PLAYLIST_DIR", "") or MUSIC_DIR
CHARTS_DIR = os.environ.get("CHARTS_DIR", "/charts")

# --- ICY read knobs ---
# Poll each channel once every ENERGY_POLL_SECONDS (60-120 s recommended -
# polite to the Infomaniak hosts). The read is a single metadata block, then
# the connection is closed immediately.
ENERGY_POLL_SECONDS = _get_int("ENERGY_POLL_SECONDS", 90)
ENERGY_READ_TIMEOUT = _get_int("ENERGY_READ_TIMEOUT_SECONDS", 15)
ENERGY_USER_AGENT = os.environ.get("ENERGY_USER_AGENT", "Mozilla/5.0")
# Stream host template. {slug} is the Infomaniak host prefix AND the mount name.
# Overridable for tests.
ENERGY_STREAM_HOST_TMPL = os.environ.get(
    "ENERGY_STREAM_HOST_TMPL",
    "https://{slug}.ice.infomaniak.ch/{slug}-high.mp3",
)

# --- Match + output cadence ---
# The ICY poll is fast; matching + output is slower (full library scan). They
# are decoupled: matching runs every ENERGY_MATCH_INTERVAL_MINUTES.
ENERGY_MATCH_INTERVAL_MINUTES = _get_int("ENERGY_MATCH_INTERVAL_MINUTES", 30)
# Run one match+output immediately at container start (1) or wait for the first
# interval (0). Exact hitparade inline form (hitparade has no _get_bool helper).
RUN_ON_START = os.environ.get("RUN_ON_START", "1") == "1"

# --- Resync trigger (forward-compat hook; NO producer in v1) ---
# A dedicated own signal file, like hitparade's, so a just-imported Energy song
# COULD re-render the OWN playlists promptly. In v1 nothing writes this file
# (charts-import is out of scope and untouched), so the hook never fires - the
# reader watches a file that nothing yet creates (benign). OWN playlists refresh
# on the ENERGY_MATCH_INTERVAL_MINUTES cadence only.
ENERGY_RESYNC_SIGNAL = os.environ.get(
    "ENERGY_RESYNC_SIGNAL",
    os.path.join(CHARTS_DIR, ".energy-resync-requested"),
)
ENERGY_RESYNC_CHECK_SECONDS = _get_int("ENERGY_RESYNC_CHECK_SECONDS", 60)

# --- Retention / trim (bound the accumulate buffer) ---
# A radio rotation is "recently played": a 30-day / 500-track window is a
# natural top-up buffer, not an archive.
ENERGY_TRACK_RETENTION_DAYS = _get_int("ENERGY_TRACK_RETENTION_DAYS", 30)
ENERGY_MAX_TRACKS_PER_CHANNEL = _get_int("ENERGY_MAX_TRACKS_PER_CHANNEL", 500)

# --- Matching (identical to charts-sync / charts-hitparade) ---
MATCH_THRESHOLD = _get_int("MATCH_THRESHOLD", 87)
ARTIST_MIN_SCORE = _get_int("ARTIST_MIN_SCORE", 60)
IGNORE_COMPILATIONS = os.environ.get("IGNORE_COMPILATIONS", "1") == "1"
AUDIO_EXTENSIONS = {
    ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma", ".wav", ".alac",
}

# --- library mode: MusicBrainz year resolution (hybrid, for the decade NSP) ---
# Used only by `library` channels (year.py). Exact release year via
# MusicBrainz; if not plausible/empty -> the resolver falls back to the channel
# decade midpoint. The three CONSTANT names MB_USER_AGENT / MB_CACHE_FILE /
# HTTP_TIMEOUT are pinned so the MB machinery copied unchanged from
# charts-flashback/app/year.py dereferences them without edits; only the env
# var the first one reads differs (MB_USER_AGENT_ENERGY).
MB_ENABLED = _get_bool("MB_ENABLED", True)
# Constant name stays MB_USER_AGENT (copied _mb_year uses config.MB_USER_AGENT);
# only the env var it reads is MB_USER_AGENT_ENERGY so the two services can
# carry different User-Agents.
MB_USER_AGENT = os.environ.get("MB_USER_AGENT_ENERGY",
                               "charts-energy/1.0 (self-hosted)")
# Rate limit: MusicBrainz allows ~1 request/second. 1.1s buffer.
MB_RATE_SLEEP = _get_float("MB_RATE_SLEEP", 1.1)
# Minimum score of a recording hit for it to be considered.
MB_SCORE_MIN = _get_int("MB_SCORE_MIN", 90)
# Energy-OWNED year cache (misses too); a plain constant with no env override,
# mirroring flashback, so the copied _load_cache/_save_cache work unedited. NOT
# shared with flashback's cache.
MB_CACHE_FILE = os.path.join(CHARTS_DIR, ".charts-energy-mbcache.json")
# httpx timeout for the copied _mb_year (config.HTTP_TIMEOUT).
HTTP_TIMEOUT = _get_int("HTTP_TIMEOUT", 30)

# Year map (artist|title -> year) that charts-import reads when tagging to set
# the date/originaldate tag - WITHOUT ever writing the year into file/folder
# names. DELIBERATELY the flashback filename: charts-import reads this single
# hardcoded shared file, keyed purely by lowercase 'artist|title', so an
# Energy-written entry is indistinguishable from a flashback-written one (zero
# changes to charts-import/charts-download). The shared name is not a bug.
YEAR_MAP_FILE = os.path.join(CHARTS_DIR, ".charts-flashback-years.json")

# library mode writes ONE shared missing file across all `library` channels
# (mirrors flashback's single missing-flashback.txt). The user appends it to
# charts-download's MISSING_FILES; no charts-download code change.
LIBRARY_MISSING_FILE = os.environ.get("ENERGY_LIBRARY_MISSING_FILE",
                                      "missing-energy-library.txt")

# --- Pushover (optional; identical block to the siblings) ---
PUSHOVER_TOKEN = os.environ.get("PUSHOVER_TOKEN", "").strip()
PUSHOVER_USER = os.environ.get("PUSHOVER_USER", "").strip()
# Mute Pushover globally for this service (without deleting token/user).
PUSHOVER_ENABLED = os.environ.get("PUSHOVER_ENABLED", "1") == "1"
PUSHOVER_DEVICE = os.environ.get("PUSHOVER_DEVICE", "").strip()
# Pushover notification sound. Empty = account default (no sound field in the
# request). Default for charts-energy: "falling".
PUSHOVER_SOUND = os.environ.get("PUSHOVER_SOUND", "falling").strip()
NOTIFY_ONLY_ON_CHANGE = os.environ.get("NOTIFY_ONLY_ON_CHANGE", "1") == "1"
NOTIFY_MISSING_LIMIT = _get_int("NOTIFY_MISSING_LIMIT", 40)

# State file (accumulate + dedupe buffer). Lives in CHARTS_DIR.
STATE_FILE = os.environ.get("STATE_FILE", ".charts-energy-state.json")


# --- Channel list (CHARTS-style JSON-in-env) ---

_VALID_MODES = ("own", "feed", "library")

# OWN-mode playlists are restricted to this reserved namespace so Energy can
# never collide with a charts-sync curated playlist (charts.m3u/mundart.m3u/
# ...), which by convention is never energy-prefixed. Names are lowercased
# before matching AND before writing.
_OWN_PLAYLIST_RE = re.compile(r"^energy-[a-z0-9-]+\.m3u$")

# Decade slugs: energy80s/90s/00s/10s/20s. Used to derive the decade hint for
# library mode (the explicit `decade` field overrides it).
_ENERGY_DECADE_RE = re.compile(r"^energy(\d{2})s$")


def decade_from_slug(slug: str) -> str:
    """'energy80s'->'1980', 'energy00s'->'2000', else ''. Pure for tests.

    The slug is lowercased before the match so a capitalized slug cannot escape
    the parse. The century split (nn >= 30 -> 1900s) resolves the 2-digit
    ambiguity for the confirmed set: 80s/90s -> 1980/1990, 00s/10s/20s ->
    2000/2010/2020.
    """
    m = _ENERGY_DECADE_RE.match(slug.strip().lower())
    if not m:
        return ""
    nn = int(m.group(1))
    century = 1900 if nn >= 30 else 2000
    return str(century + nn)


def _default_names(slug: str) -> tuple[str, str]:
    """
    Derive the default OWN-mode playlist and missing-file names from a slug.

    A leading "energy" is stripped for readability, so energypop -> energy-pop.
    Pure function so a test can pin the derivation.
    Returns (playlist_name, missing_name).
    """
    rest = slug[len("energy"):] if slug.startswith("energy") else slug
    rest = rest.strip("-") or slug
    return f"energy-{rest}.m3u", f"missing-energy-{rest}.txt"


class Channel:
    """
    One Energy webradio channel declaration (parsed from ENERGY_CHANNELS).

    Plain class matching the Feed/ChartSource style in charts-sync (no
    dataclasses dependency added).
    """

    def __init__(self, slug: str, title: str = "", enabled: bool = True,
                 mode: str = "own", playlist: str = "", missing: str = "",
                 theme: str = "", decade: str = ""):
        self.slug = slug
        self.title = title or slug
        self.enabled = enabled
        self.mode = mode
        # OWN-mode fields (ignored in feed mode). Fall back to the derived
        # default names so a minimal entry (just slug + mode) works.
        default_playlist, default_missing = _default_names(slug)
        self.playlist = playlist or default_playlist
        self.missing = missing or default_missing
        # FEED-mode field (ignored in own mode).
        self.theme = theme
        # LIBRARY-mode decade hint (ignored by own/feed): explicit `decade`
        # override, else parsed from the slug (energy80s -> 1980), else "".
        self.decade = decade or decade_from_slug(slug)

    def __repr__(self) -> str:  # pragma: no cover - debug aid only
        return (f"Channel(slug={self.slug!r}, mode={self.mode!r}, "
                f"enabled={self.enabled!r}, theme={self.theme!r})")


def _parse_channels(data) -> tuple[list[Channel], list[str]]:
    """
    Build Channel objects from a parsed JSON list.

    Collects validation findings in one bundle (mirroring charts-sync's
    _parse_charts warnings list); an invalid entry is skipped, never fatal.
    Returns (channels, warnings).
    """
    channels: list[Channel] = []
    warnings: list[str] = []
    # Collision guard (OWN only): never let two writers target the same .m3u or
    # the same missing file (both are full overwrites). Resolved, normalized
    # names are tracked here; feed/library are exempt (no .m3u, feed writes no
    # per-channel missing file, library channels share one missing file).
    seen_playlists: set[str] = set()
    seen_missing: set[str] = set()

    if not isinstance(data, list):
        return channels, warnings

    for entry in data:
        if not isinstance(entry, dict):
            continue
        slug = str(entry.get("slug", "")).strip()
        if not slug:
            warnings.append(t("energy.config.channel_no_slug"))
            continue

        mode = str(entry.get("mode", "own")).strip().lower()
        if mode not in _VALID_MODES:
            warnings.append(t("energy.config.channel_bad_mode", slug=slug, mode=mode))
            mode = "own"

        theme = str(entry.get("theme", "")).strip()
        if mode == "feed" and not theme:
            warnings.append(t("energy.config.feed_no_theme", slug=slug))
            continue

        enabled = bool(entry.get("enabled", True))
        title = str(entry.get("title", "")).strip()
        # Normalize the slug to lowercase so _default_names always produces a
        # pattern-valid default (a capitalized slug would otherwise yield
        # energy-Energy80s.m3u and fail the reserved-namespace check).
        norm_slug = slug.lower()
        playlist = str(entry.get("playlist", "")).strip()
        missing = str(entry.get("missing", "")).strip()
        decade = str(entry.get("decade", "")).strip()

        if mode == "own":
            default_playlist, default_missing = _default_names(norm_slug)
            # Resolve + normalize (lowercase) the playlist name.
            resolved_playlist = (playlist.lower() if playlist
                                 else default_playlist)
            if not _OWN_PLAYLIST_RE.match(resolved_playlist):
                # Reserved namespace: an explicit non-energy-*.m3u name is a
                # hard refuse (use feed to write into an existing playlist).
                warnings.append(t("energy.config.own_playlist_reserved",
                                   slug=slug, name=resolved_playlist))
                continue
            resolved_missing = (missing.lower() if missing
                                else default_missing.lower())
            if (resolved_playlist in seen_playlists
                    or resolved_missing in seen_missing):
                # Another OWN channel already owns this .m3u or missing file.
                name = (resolved_playlist if resolved_playlist in seen_playlists
                        else resolved_missing)
                warnings.append(t("energy.config.own_playlist_duplicate",
                                   slug=slug, name=name))
                continue
            seen_playlists.add(resolved_playlist)
            seen_missing.add(resolved_missing)
            # Store the normalized names so the eventual write uses them.
            playlist = resolved_playlist
            missing = resolved_missing

        channels.append(Channel(
            slug=norm_slug if mode == "own" else slug,
            title=title, enabled=enabled, mode=mode,
            playlist=playlist, missing=missing, theme=theme, decade=decade,
        ))

    return channels, warnings


def _load_channels_data():
    """
    Load the raw channel JSON with priority
    ENERGY_CHANNELS_FILE > ENERGY_CHANNELS > empty default.

    Broken JSON at a level logs a clear error and falls back to the next level
    (same discipline as charts-sync's _load_charts_data). The built-in default
    is an empty list, so even ENERGY_ENABLED=1 with no channels polls nothing.
    """
    file_path = os.environ.get("ENERGY_CHANNELS_FILE", "").strip()
    if file_path:
        try:
            with open(file_path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError) as e:
            # Rare developer-facing fallback: keep the frozen energy.config.*
            # key set (design section 10) untouched; log in place and fall
            # through to the next priority level.
            logger.error("ENERGY_CHANNELS_FILE %s is unreadable/invalid: %s",
                         file_path, e)

    raw = os.environ.get("ENERGY_CHANNELS", "").strip()
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error("ENERGY_CHANNELS is not valid JSON: %s", e)

    return []


def _build_channels() -> list[Channel]:
    """Parse the configured channels and log the validation-warnings bundle."""
    channels, warnings = _parse_channels(_load_channels_data())
    for warning in warnings:
        logger.warning(warning)
    return channels


# All declared channels (including per-entry disabled ones).
CHANNELS = _build_channels()


def active_channels() -> list[Channel]:
    """
    Channels that are polled this run: per-entry enabled only.

    Mirrors ChartSource.active_feeds - a disabled entry stays in CHANNELS but is
    excluded from polling.
    """
    return [c for c in CHANNELS if c.enabled]
