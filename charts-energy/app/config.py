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

_VALID_MODES = ("own", "feed")


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
                 theme: str = ""):
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
        playlist = str(entry.get("playlist", "")).strip()
        missing = str(entry.get("missing", "")).strip()

        channels.append(Channel(
            slug=slug, title=title, enabled=enabled, mode=mode,
            playlist=playlist, missing=missing, theme=theme,
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
