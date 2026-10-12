"""
Configuration for the charts-sync service.
All values are read from environment variables (see .env.example).
"""

import json
import logging
import os

from .i18n import t

logger = logging.getLogger(__name__)


def _get_int(name: str, default: int) -> int:
    """Read an integer from the environment, falling back to the default on error."""
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


# --- Paths (mounted inside the container) ---
# MUSIC_DIR:    the folder that Navidrome also sees as the library root.
#               The song paths in the playlists are written relative to this
#               directory so Navidrome can map them.
# PLAYLIST_DIR: target folder for the .m3u playlists. MUST be a location that
#               Navidrome scans (e.g. MUSIC_DIR directly), otherwise the
#               playlists do not show up in Navidrome. Default = MUSIC_DIR.
# CHARTS_DIR:   storage location for the missing files (Navidrome does NOT need
#               to see it).
MUSIC_DIR = os.environ.get("MUSIC_DIR", "/music")
PLAYLIST_DIR = os.environ.get("PLAYLIST_DIR", "") or MUSIC_DIR
CHARTS_DIR = os.environ.get("CHARTS_DIR", "/music/Charts")

# How many chart songs per source are considered (default for sources without
# their own "limit").
#   "all" (default) = fetch all available tracks of the source
#   number          = limit to that many tracks per source
# Internally "all" is implemented as a very high limit (Deezer then returns all).
_ALL_LIMIT = 100000


def _parse_limit(value, default: int = _ALL_LIMIT) -> int:
    """Convert a limit value ('all' or a number) into an integer."""
    if value is None:
        return default
    s = str(value).strip().lower()
    if s in ("all", "alle", ""):
        return _ALL_LIMIT
    try:
        return int(s)
    except ValueError:
        return default


CHART_LIMIT = _parse_limit(os.environ.get("CHART_LIMIT"), _ALL_LIMIT)

# --- Last.fm (optional, second chart source alongside Deezer) ---
# Free API key from https://www.last.fm/api/account/create.
# Only needed when at least one feed of type "lastfm" is active.
LASTFM_API_KEY = os.environ.get("LASTFM_API_KEY", "").strip()

# --- Genre filter (optional) ---
# Excludes chart tracks whose dominant Last.fm tags are in this list (e.g. to
# keep country/schlager out of the pop charts). The filter queries
# track.getTopTags per track and therefore needs LASTFM_API_KEY.
#   GENRE_FILTER=1        -> filter active (opt-in; shipped default in
#                            compose.yml/.env.example is 0 = off)
#   EXCLUDE_TAGS=a,b,c    -> tags to exclude (comma-separated)
# A track is dropped when one of its strongest EXCLUDE_TAG_TOP_N tags is in
# EXCLUDE_TAGS AND its count is >= EXCLUDE_TAG_MIN_COUNT.
GENRE_FILTER = os.environ.get("GENRE_FILTER", "0") == "1"
# Foreign genres that should be kept out of the pop charts. Deliberately WITHOUT
# "hard rock"/"metal": rock classics (AC/DC, Bon Jovi, Guns N' Roses, Europe)
# belong in the 80s/90s playlist. Country/schlager/folk/classical however out.
_DEFAULT_EXCLUDE_TAGS = (
    "country,contemporary country,modern country,schlager,volksmusik,"
    "folk,americana,bluegrass,classical,classic country"
)


def _parse_tags(value: str) -> set[str]:
    return {t.strip().lower() for t in value.split(",") if t.strip()}


# An empty or unset EXCLUDE_TAGS falls back to the default list. (An empty
# EXCLUDE_TAGS= in .env/compose should NOT let everything through, otherwise the
# filter would effectively be off even though GENRE_FILTER=1.)
_exclude_raw = os.environ.get("EXCLUDE_TAGS", "").strip() or _DEFAULT_EXCLUDE_TAGS
EXCLUDE_TAGS = _parse_tags(_exclude_raw)
# How many of a song's strongest tags are checked.
EXCLUDE_TAG_TOP_N = _get_int("EXCLUDE_TAG_TOP_N", 3)
# Minimum count of a tag (Last.fm tags range 0-100) for it to count. 60 keeps
# troll/niche tags (e.g. "brutal death metal 41" on Rick Astley) out; only a
# truly dominant genre (>=60) leads to exclusion.
EXCLUDE_TAG_MIN_COUNT = _get_int("EXCLUDE_TAG_MIN_COUNT", 60)
# Pause between the genre-tag queries (ms), to be gentle on the Last.fm API.
GENRE_FILTER_SLEEP_MS = _get_int("GENRE_FILTER_SLEEP_MS", 200)

# --- Scheduler ---
# Interval between two regular sync runs in hours (for new charts).
SYNC_INTERVAL_HOURS = _get_int("SYNC_INTERVAL_HOURS", 12)
# Synchronize once immediately on start (1) or only after the interval (0).
RUN_ON_START = os.environ.get("RUN_ON_START", "1") == "1"

# --- Resync trigger (from charts-import) ---
# charts-import drops a signal file after an import so the playlist is updated
# promptly instead of only on the next regular run. charts-sync checks every
# RESYNC_CHECK_SECONDS whether the signal exists. The check is just a file
# existence probe (practically free); a real (expensive) sync run only happens
# when the signal is actually there.
RESYNC_SIGNAL = os.environ.get(
    "RESYNC_SIGNAL", os.path.join(CHARTS_DIR, ".resync-requested")
)
RESYNC_CHECK_SECONDS = _get_int("RESYNC_CHECK_SECONDS", 60)

# --- Matching ---
# From which similarity (0-100) a library track counts as a hit.
MATCH_THRESHOLD = _get_int("MATCH_THRESHOLD", 87)

# Minimum similarity (0-100) of the ARTIST for a track to even be considered a
# hit. Prevents a same title by a different artist from matching wrongly (e.g.
# chart "BLACKPINK - Jump" -> "Van Halen - Jump"). Higher = stricter. 60 is a
# good value; raise it with too many false hits, lower it with too many songs
# wrongly reported missing.
ARTIST_MIN_SCORE = _get_int("ARTIST_MIN_SCORE", 60)

# IGNORE_COMPILATIONS=1 (default): library tracks that come from a compilation/
#   a sampler (e.g. "Various Artists", "Bravo Hits", "Top 100") are IGNORED
#   during matching. A chart song that you only have as part of a compilation
#   then counts as "missing" and is fetched by charts-download as a clean
#   single/album version.
# IGNORE_COMPILATIONS=0: match against the complete library (incl. compilations)
#   - then a song that sits in a compilation counts as present and is not
#   re-fetched.
IGNORE_COMPILATIONS = os.environ.get("IGNORE_COMPILATIONS", "1") == "1"
# File extensions considered to be music files.
AUDIO_EXTENSIONS = {
    ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma", ".wav", ".alac",
}

# --- Pushover (optional, identical to the jellyfin-webhook setup) ---
PUSHOVER_TOKEN = os.environ.get("PUSHOVER_TOKEN", "").strip()
PUSHOVER_USER = os.environ.get("PUSHOVER_USER", "").strip()
# Globally mute Pushover for this service (without deleting token/user).
PUSHOVER_ENABLED = os.environ.get("PUSHOVER_ENABLED", "1") == "1"
PUSHOVER_DEVICE = os.environ.get("PUSHOVER_DEVICE", "").strip()
# Cache duration (seconds) for the device list fetched from the Pushover
# account. The PUSHOVER_DEVICE list is filtered against it before sending so an
# invalid/disabled device name does not trigger Pushover's "to all" fallback
# (double push). Default 3600s = 1h.
PUSHOVER_DEVICES_TTL = _get_int("PUSHOVER_DEVICES_TTL", 3600)
# Pushover notification sound. Empty = account default (no sound field in the
# request). Default for charts-sync: "falling".
PUSHOVER_SOUND = os.environ.get("PUSHOVER_SOUND", "falling").strip()
# Notify only when the playlist changed compared to the last run (1) or after
# every run (0).
NOTIFY_ONLY_ON_CHANGE = os.environ.get("NOTIFY_ONLY_ON_CHANGE", "1") == "1"

# How many missing songs are written into the Pushover message at most. This
# gets you the "missing" list straight to your phone without logging into the
# NAS. Pushover limits a message to 1024 characters; with very many missing
# songs the list is truncated and a note appended.
# 0 = do not list any missing songs in the message (only the count).
NOTIFY_MISSING_LIMIT = _get_int("NOTIFY_MISSING_LIMIT", 40)

# State file in which the last state per playlist is stored (for the "only on
# change" notification and the ordering). Lives in CHARTS_DIR.
STATE_FILE = os.environ.get("STATE_FILE", ".charts-sync-state.json")

# --- Playlist definition from a FILE (optional, admin-friendly) ---
# The CHARTS env is necessarily SINGLE-LINE (env limitation) and gets unwieldy
# with many playlists/sources. Alternatively the whole definition can be loaded
# from a multi-line, readable JSON file (CHARTS_FILE).
# Priority: CHARTS_FILE (if set AND file exists AND valid JSON)
# > CHARTS env > built-in default playlists. Empty = file path off.
CHARTS_FILE = os.environ.get("CHARTS_FILE", "").strip()

# --- Playlist order ---
# In which order the tracks are written to the .m3u:
#   "chart"        (default): by chart position (number 1 on top) - the way the
#                  charts really look.
#   "newest-first": songs most recently new to the playlist on top, then the
#                  older ones. Handy when the playlist changes constantly and
#                  you want to see the new additions at the top in the player
#                  app (e.g. Substreamer) right away, instead of always the same
#                  chart-position-1 songs. The "first seen" date per song is
#                  kept in the state (first_seen) so the order stays stable
#                  across runs.
PLAYLIST_ORDER = os.environ.get("PLAYLIST_ORDER", "chart").strip().lower()


class Feed:
    """
    A single chart source within a playlist.

    kind:    "deezer" or "lastfm".
    enabled: False -> feed is skipped (without deleting it from the config). So
             you can e.g. turn off all Deezer feeds and only import Last.fm (or
             vice versa).
    limit:   max number of tracks for EXACTLY this source ("all" -> very high).

    Deezer-specific:
      deezer_type = "playlist" (playlist ID) or "chart" (chart ID)
      source      = the ID
    Last.fm-specific:
      method = e.g. "chart.getTopTracks", "geo.getTopTracks",
               "tag.getTopTracks", "user.getTopTracks"
      params = dict with method-specific extra values
               (country / tag / user / period)
    """

    def __init__(self, kind: str, limit: int, enabled: bool = True,
                 deezer_type: str = "chart", source: str = "0",
                 method: str = "", params: dict | None = None):
        self.kind = kind
        self.limit = limit
        self.enabled = enabled
        self.deezer_type = deezer_type
        self.source = source
        self.method = method
        self.params = params or {}

    def __repr__(self) -> str:
        state = "on" if self.enabled else "OFF"
        if self.kind == "lastfm":
            detail = f"{self.method}{self.params or ''}"
        else:
            detail = f"{self.deezer_type}:{self.source}"
        return f"Feed[{self.kind}/{state}]({detail} <={self.limit})"


class ChartSource:
    """
    A playlist definition. Can bundle ONE or MULTIPLE sources (Deezer and/or
    Last.fm); multiple sources are merged into one playlist and deduplicated by
    artist+title.

    feeds:   list of Feed objects.
    name:    file name of the .m3u (lands in PLAYLIST_DIR).
    title:   display name in Navidrome (emojis + {date} allowed).
    missing: file name of the missing list (lands in CHARTS_DIR).
    limit:   default limit of the entry (fallback for feeds without their own
             "limit"; itself falls back to CHART_LIMIT).
    exclude_artists: artists removed from EXACTLY this playlist.
    genre_filter: per-playlist override for the global GENRE_FILTER
             (True/False). None (missing in the config) -> global default.
    """

    def __init__(self, feeds: list["Feed"], name: str, title: str,
                 missing: str, limit: int,
                 exclude_artists: set[str] | None = None,
                 genre_filter: bool | None = None):
        self.feeds = feeds
        self.name = name
        self.title = title
        self.missing = missing
        self.limit = limit
        # Artists kept out of ONLY THIS playlist (independent of the genre
        # filter). Case-insensitive. Meant for cases that cannot be cleanly
        # separated by genre - e.g. Swiss Mundart schlager (Trauffer) that the
        # APIs list as "pop" but you do not want in this playlist. Other
        # playlists keep the artist.
        self.exclude_artists: set[str] = exclude_artists or set()
        # Per-playlist override for the genre filter. True/False override the
        # global GENRE_FILTER; None (field missing) falls back to the global
        # default (see effective_genre_filter).
        self.genre_filter: bool | None = genre_filter
        # Populated by deezer.fetch_chart after the run (for Pushover):
        # {"deezer_new": int, "lastfm_new": int, "genre_filtered": int}
        self.last_stats: dict[str, int] = {}

    @property
    def active_feeds(self) -> list["Feed"]:
        """Only the enabled feeds (enabled=True)."""
        return [f for f in self.feeds if f.enabled]

    @property
    def effective_genre_filter(self) -> bool:
        """
        The genre-filter switch actually in effect for this playlist: an
        explicit genre_filter (True/False) overrides the global GENRE_FILTER;
        if it is missing (None), the global default applies.
        """
        return self.genre_filter if self.genre_filter is not None else GENRE_FILTER

    def __repr__(self) -> str:
        feeds = ", ".join(repr(f) for f in self.feeds)
        return f"ChartSource({self.title!r} [{feeds}] -> {self.name})"


# Default: TWO playlists.
#   1) charts.m3u      = global chart (capped at Top 100) + Switzerland chart (all).
#      This gets you the worldwide top hits without the global long tail
#      (positions 100-300) plus the complete Swiss chart.
#   2) mundart.m3u     = curated Swiss Mundart collection (all), separate so it
#      does not dilute the hits playlist.
# The decades (80s/90s etc.) no longer run via charts-sync but via the
# backfill service charts-flashback (FLASHBACK FM) - it fills the library, and
# the decade display is handled by the Navidrome Smart Playlists (NSP).
# IDs:
#   chart/0     = global Deezer chart
#   1313617925  = official Deezer playlist "Top Switzerland"
#   658561775   = Deezer playlist "Swiss Mundart Hits"
# Feed notations in the CHARTS config:
#   Deezer:  {"type": "chart"|"playlist", "source": "<id>", "limit": ..,
#            "enabled": true}
#   Last.fm: {"type": "lastfm", "method": "<method>", "limit": ..,
#            "enabled": true, <method parameter: country|tag|user|period>}
#
# The "enabled" switch lets you suspend a source without deleting it -> e.g.
# Deezer off, import only Last.fm (or vice versa).
_DEFAULT_CHARTS = [
    {
        # Current pop hits. The Deezer charts (global Top 100 + Switzerland)
        # deliver the novelties; the optional genre filter (GENRE_FILTER=1)
        # keeps country/schlager out. The Last.fm feeds are predefined but off
        # for now via "enabled": false - just toggle to test.
        "sources": [
            {"type": "chart", "source": "0", "limit": 100, "enabled": True},
            {"type": "playlist", "source": "1313617925", "limit": "all", "enabled": True},
            {"type": "lastfm", "method": "chart.getTopTracks", "limit": 100,
             "enabled": False},
            {"type": "lastfm", "method": "geo.getTopTracks", "country": "Switzerland",
             "limit": 100, "enabled": False},
        ],
        "name": "charts.m3u",
        "title": "Aktuelle Hits & Charts \U0001F525\U0001F3B6",
        "missing": "missing.txt",
    },
    {
        "sources": [
            {"type": "playlist", "source": "658561775", "limit": "all", "enabled": True},
        ],
        "name": "mundart.m3u",
        "title": "Schweizer Mundart \U0001F1E8\U0001F1ED",
        "missing": "missing-mundart.txt",
    },
]

# Deezer feed types (everything other than "lastfm" is treated as Deezer).
#   chart    = genre/global chart (/chart/<id>/tracks, id 0 = global)
#   playlist = curated playlist (/playlist/<id>/tracks)
#   artist   = top tracks of an artist (/artist/<id>/top) - for targeted artist
#              sources (e.g. Mundart: Goelae, Polo Hofer, Trauffer).
_DEEZER_TYPES = {"chart", "playlist", "artist"}
# Non-feed keys of a Last.fm entry (the rest are method parameters).
# "service" and "source" are NOT Last.fm query parameters: they are
# deliberately ignored instead of passed through to the API (a "source"
# accidentally set on a Last.fm source would otherwise be sent as &source=..).
_LASTFM_META_KEYS = {"type", "method", "limit", "enabled", "service", "source"}

# Valid Last.fm methods as a mirror set of lastfm._METHOD_PARAM. Deliberately NO
# import of lastfm here (import cycle config->lastfm->deezer->config, deezer.py
# imports `from . import config` at module level). A drift test in the
# charts-sync tests checks equality with lastfm._METHOD_PARAM.
#   key = lowercase method, value = (mandatory param name|None, required)
_VALID_LASTFM_METHODS = {
    "chart.gettoptracks": (None, False),
    "geo.gettoptracks": ("country", True),
    "tag.gettoptracks": ("tag", True),
    "user.gettoptracks": ("user", True),
}


def _build_feed(s: dict, entry_limit: int, context: str,
                index: int, warnings: list[str]) -> Feed:
    """
    Build a Feed object (Deezer or Last.fm) from a source dict.

    The effective service (feed.kind) is determined from the optional field
    "service"; if it is missing, the backward-compatible type heuristic applies.
    All validation findings are appended to `warnings` (which _parse_charts then
    logs in a bundle) - processing is NOT aborted.

    context: playlist name/title for descriptive messages.
    index:   1-based source number within the playlist.
    """
    service = str(s.get("service", "")).strip().lower()
    type_ = str(s.get("type", "")).strip().lower()
    method_raw = str(s.get("method", "")).strip().lower()
    limit = _parse_limit(s.get("limit"), entry_limit)
    enabled = bool(s.get("enabled", True))

    # 1) Determine the effective service.
    if service == "lastfm":
        kind = "lastfm"
        if type_ == "chart" or type_ == "playlist":
            # Harmless but confusing: a Deezer type on a Last.fm source.
            warnings.append(t("sync.config.lastfm_deezer_type",
                               context=context, index=index, type=type_))
    elif service == "deezer":
        kind = "deezer"
        if type_ == "lastfm":
            warnings.append(t("sync.config.deezer_type_lastfm",
                               context=context, index=index))
    elif service == "":
        # Backward-compatible fallback (unchanged legacy behavior).
        kind = "lastfm" if type_ == "lastfm" else "deezer"
    else:
        warnings.append(t("sync.config.unknown_service",
                           context=context, index=index, service=service))
        kind = "deezer"

    # 2) Build + validate the feed depending on the service.
    if kind == "lastfm":
        method = method_raw or "chart.gettoptracks"
        if method not in _VALID_LASTFM_METHODS:
            warnings.append(t("sync.config.unknown_lastfm_method",
                               context=context, index=index, method=method))
            enabled = False
        else:
            param, required = _VALID_LASTFM_METHODS[method]
            if required and not str(s.get(param, "")).strip():
                warnings.append(t("sync.config.lastfm_missing_param",
                                   context=context, index=index,
                                   method=method, param=param))
                enabled = False
        if str(s.get("source", "")).strip():
            warnings.append(t("sync.config.lastfm_has_source",
                               context=context, index=index))
        # All non-meta keys are method parameters (country/tag/user/period).
        params = {k: v for k, v in s.items() if k not in _LASTFM_META_KEYS}
        return Feed(kind="lastfm", limit=limit, enabled=enabled,
                    method=method, params=params)

    # Deezer
    if type_ and type_ not in _DEEZER_TYPES:
        warnings.append(t("sync.config.unknown_deezer_type",
                           context=context, index=index, type=type_))
    deezer_type = type_ if type_ in _DEEZER_TYPES else "chart"
    source = str(s.get("source", "0")).strip() or "0"
    # playlist and artist need a real ID (empty/"0" returns nothing).
    if deezer_type in ("playlist", "artist") and source in ("", "0"):
        warnings.append(t("sync.config.deezer_no_source_id",
                           context=context, index=index, deezer_type=deezer_type))
    return Feed(kind="deezer", limit=limit, enabled=enabled,
                deezer_type=deezer_type, source=source)


def _extract_feeds(entry: dict, entry_limit: int, context: str,
                   warnings: list[str]) -> list["Feed"]:
    """
    Determine the sources of a CHARTS entry as a list of Feed objects.

    Each source can have its own "limit" ('all' or a number) and "enabled"; if
    the limit is missing, the entry limit applies (entry_limit, which in turn
    falls back to CHART_LIMIT).

    Supports both notations:
      - multiple:  "sources": [{...}, {...}]
      - single:    fields directly on the entry (backward-compatible)
    """
    if isinstance(entry.get("sources"), list):
        if not entry["sources"]:
            warnings.append(t("sync.config.empty_sources", context=context))
        return [_build_feed(s, entry_limit, context, i, warnings)
                for i, s in enumerate(entry["sources"], start=1)]
    # Single source
    return [_build_feed(entry, entry_limit, context, 1, warnings)]


def _parse_exclude_artists(value) -> set[str]:
    """
    Read the 'exclude_artists' of a CHARTS entry. Supports a list
    (["Nemo", "Trauffer"]) OR a comma-separated string ("Nemo,Trauffer").
    The comparison is case-insensitive later, so it is lowercased here already.
    Default empty.
    """
    if not value:
        return set()
    if isinstance(value, str):
        parts = value.split(",")
    elif isinstance(value, (list, tuple)):
        parts = value
    else:
        return set()
    return {str(a).strip().lower() for a in parts if str(a).strip()}


def _load_charts_data():
    """
    Return the raw playlist definition (list of dicts) according to the priority
    CHARTS_FILE > CHARTS env > _DEFAULT_CHARTS. Broken JSON at a set level ->
    clear error message + default (no silent pass-through to the next level).
    """
    # 1. File path (if CHARTS_FILE is set).
    if CHARTS_FILE:
        if os.path.isfile(CHARTS_FILE):
            try:
                with open(CHARTS_FILE, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                logger.info(t("sync.config.loaded_from_file", path=CHARTS_FILE))
                return data
            except json.JSONDecodeError as e:
                logger.error(t("sync.config.file_bad_json",
                               path=CHARTS_FILE, pos=e.pos, msg=e.msg))
                return _DEFAULT_CHARTS
            except OSError as e:
                logger.error(t("sync.config.file_unreadable",
                               path=CHARTS_FILE, error=e))
                return _DEFAULT_CHARTS
        else:
            logger.error(t("sync.config.file_missing", path=CHARTS_FILE))
            # deliberately continue to the env level (missing file != broken file)

    # 2. CHARTS env (single-line JSON).
    raw = os.environ.get("CHARTS", "").strip()
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error(t("sync.config.env_bad_json", pos=e.pos, msg=e.msg))
            return _DEFAULT_CHARTS

    # 3. Default.
    return _DEFAULT_CHARTS


def _parse_charts() -> list[ChartSource]:
    """
    Read the chart sources. Priority:
      1. CHARTS_FILE (multi-line, readable JSON file) - if set, exists and valid
         JSON.
      2. CHARTS env (single-line JSON).
      3. Built-in default playlists.
    On broken JSON it does NOT fall through to the next level but (as with the
    env) falls back to the default playlists and logs clearly - so the admin
    notices that their file/env is faulty instead of silently getting something
    else loaded.
    """
    data = _load_charts_data()

    warnings: list[str] = []
    sources: list[ChartSource] = []
    for i, entry in enumerate(data):
        if not isinstance(entry, dict):
            warnings.append(t("sync.config.entry_not_object", i=i))
            continue
        name = str(entry.get("name", "")).strip()
        if not name:
            warnings.append(t("sync.config.entry_no_name", i=i))
            continue
        context = str(entry.get("title") or name)
        try:
            entry_limit = _parse_limit(entry.get("limit"), CHART_LIMIT)
            feeds = _extract_feeds(entry, entry_limit, context, warnings)
            if not feeds:
                warnings.append(t("sync.config.entry_no_feeds", context=context))
            # Per-playlist genre filter: only a real bool overrides the global
            # GENRE_FILTER; if the field is missing or not a bool, it stays
            # None -> global default.
            gf = entry.get("genre_filter")
            sources.append(ChartSource(
                feeds=feeds,
                name=name,
                title=entry.get("title", name),
                missing=entry.get("missing", f"missing-{i}.txt"),
                limit=entry_limit,
                exclude_artists=_parse_exclude_artists(entry.get("exclude_artists")),
                genre_filter=gf if isinstance(gf, bool) else None,
            ))
        except (KeyError, TypeError, ValueError) as e:
            warnings.append(t("sync.config.entry_skipped", i=i, error=e))

    # Log all collected validation findings in a bundle (one WARN each).
    for w in warnings:
        logger.warning("CHARTS: %s", w)

    # Count the active sources AFTER the enabled=False downgrade.
    active = sum(len(cs.active_feeds) for cs in sources)
    if warnings:
        logger.info(t("sync.config.summary_warn", n_pl=len(sources),
                      n_src=active, n_warn=len(warnings)))
    else:
        logger.info(t("sync.config.summary", n_pl=len(sources), n_src=active))
    return sources


CHARTS = _parse_charts()
