"""
Deezer chart client.

Deezer offers an open REST API that needs neither an account nor an API key
for chart lists. We fetch metadata only (artist + title), no audio data.

Endpoints:
  - Global/country charts:  https://api.deezer.com/chart/<id>/tracks
  - Arbitrary playlist:     https://api.deezer.com/playlist/<id>/tracks
"""

import logging
import re
import time

import httpx

from . import config
from .i18n import t

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.deezer.com"


class ChartTrack:
    """A single chart track with the fields relevant for matching."""

    def __init__(self, position: int, artist: str, title: str, deezer_id: int = 0,
                 contributors: list[str] | None = None):
        self.position = position
        self.artist = artist
        self.title = title
        self.deezer_id = deezer_id
        # All artists involved in this track (Deezer 'contributors[]'),
        # including feature/collab partners that are NOT the main artist.
        # Only relevant for the exclude_artists filter; it plays no role in
        # matching. Last.fm tracks have no contributors -> empty list.
        self.contributors: list[str] = contributors or []

    def __repr__(self) -> str:
        return f"ChartTrack(#{self.position} {self.artist} - {self.title})"


def _parse_tracks(items: list[dict]) -> list[ChartTrack]:
    """Convert the Deezer JSON entries into ChartTrack objects."""
    tracks: list[ChartTrack] = []
    for index, item in enumerate(items, start=1):
        artist = (item.get("artist") or {}).get("name", "").strip()
        # title_short has no "(feat. ...)" additions and usually matches better.
        title = (item.get("title_short") or item.get("title") or "").strip()
        if not artist or not title:
            continue
        position = item.get("position", index)
        # Carry contributors along: for collabs Deezer lists the main artist in
        # 'artist' but the feature/supporting acts (only) in 'contributors[]'.
        # The exclude_artists filter needs them to be able to exclude e.g.
        # "Goelae feat. Trauffer" (artist=Goelae, contributor=Trauffer).
        contributors = [
            (c.get("name") or "").strip()
            for c in (item.get("contributors") or [])
            if isinstance(c, dict) and (c.get("name") or "").strip()
        ]
        tracks.append(ChartTrack(position, artist, title, item.get("id", 0),
                                 contributors))
    return tracks


# Deezer returns at most ~100 entries per response, even when a higher 'limit'
# is requested. To fetch more (up to "all") we paginate in fixed steps via the
# 'index' parameter.
_PAGE_SIZE = 100
# Safety cap so a broken/huge source cannot cause an endless loop (comfortably
# covers every real-world chart/playlist).
_MAX_TRACKS = 100000


def _fetch_deezer_feed(feed, client: httpx.Client) -> list[ChartTrack]:
    """
    Fetch ONE Deezer source (chart or playlist), with pagination.
    feed.limit is the desired total count (a very high value for "all"); it is
    loaded in _PAGE_SIZE steps until the limit is reached or no further tracks
    arrive.
    """
    feed_type = feed.deezer_type
    feed_id = feed.source
    if feed_type == "playlist":
        url = f"{_BASE_URL}/playlist/{feed_id}/tracks"
    elif feed_type == "artist":
        # Top tracks of an artist. Same track structure (title + artist) as
        # chart/playlist, so no dedicated parser is needed. Deezer caps
        # /artist/<id>/top internally at ~100 - the pagination below breaks
        # cleanly as soon as no further tracks arrive.
        url = f"{_BASE_URL}/artist/{feed_id}/top"
    else:
        url = f"{_BASE_URL}/chart/{feed_id}/tracks"

    target = min(max(1, feed.limit), _MAX_TRACKS)
    tracks: list[ChartTrack] = []
    index = 0

    while len(tracks) < target:
        page = min(_PAGE_SIZE, target - len(tracks))
        resp = client.get(url, params={"limit": page, "index": index})
        resp.raise_for_status()
        payload = resp.json()

        # Deezer reports errors as {"error": {...}} with HTTP 200.
        if isinstance(payload, dict) and payload.get("error"):
            raise RuntimeError(t("sync.deezer.api_error", feed_type=feed_type,
                                 feed_id=feed_id, error=payload['error']))

        items = payload.get("data", []) if isinstance(payload, dict) else []
        if not items:
            break  # no further tracks (end of the list reached)

        tracks.extend(_parse_tracks(items))

        # If Deezer returns fewer than requested, the list has ended.
        if len(items) < page:
            break
        index += len(items)

    logger.info(t("deezer.feed.result", type=feed_type, id=feed_id, count=len(tracks)))
    return tracks


def _merge_key(track: ChartTrack) -> str:
    """Key for deduplication across sources (artist+title)."""
    return f"{track.artist.strip().lower()}|{track.title.strip().lower()}"


def _name_hits_exclude(name: str, exclude_set: set[str]) -> bool:
    """
    True if ONE artist name hits the exclude list - either as the whole name
    OR as one of the individual artists separated by "feat."/"&"/","/.. (e.g.
    "Trauffer feat. Beatrice Egli" hits because of "Trauffer"). Case-insensitive.
    """
    n = (name or "").strip().lower()
    if not n:
        return False
    if n in exclude_set:
        return True
    parts = re.split(r"\s*(?:feat\.?|ft\.?|&|,|/|x|vs\.?)\s+", n)
    return any(p.strip() in exclude_set for p in parts if p.strip())


def _is_excluded_artist(artist: str, exclude_set: set[str],
                        contributors: list[str] | None = None) -> bool:
    """
    True if the excluded artist is involved in this track (the exclude list is
    playlist-specific). Checked are:
      1. the main artist (artist) - whole name or one individual artist
         separated by "feat."/"&"/",", and
      2. the Deezer 'contributors[]' (feature/collab partners).
    Reason for (2): for collabs Deezer lists the main act in 'artist' but the
    feature act only in 'contributors[]' - e.g. "Goelae feat. Trauffer" has
    artist="Goelae" and would have slipped through without the contributor
    check (verified against real Mundart data). No API call, purely local.
    """
    if not exclude_set:
        return False
    if _name_hits_exclude(artist, exclude_set):
        return True
    return any(_name_hits_exclude(c, exclude_set) for c in (contributors or []))


def _fetch_one_feed(feed, client: httpx.Client) -> list[ChartTrack]:
    """Dispatcher: fetch a feed from Deezer or Last.fm depending on its type."""
    if feed.kind == "energy":
        # Lazy import to avoid a module import cycle (energy_feed imports from
        # this module for ChartTrack). Reads a local handoff JSON, no socket.
        from . import energy_feed
        return energy_feed.load(feed.theme)
    if feed.kind == "lastfm":
        # Lazy import so Deezer-only setups do not need Last.fm.
        from . import lastfm
        return lastfm.fetch_feed(feed, config.LASTFM_API_KEY, client)
    return _fetch_deezer_feed(feed, client)


def _match_exclude_tag(top_tags: list[tuple[str, int]]):
    """
    Check whether one of the given (tag, count) pairs matches the exclude list.
    A match is a WORD-based substring: the exclude tag 'country' also hits
    'contemporary country', 'modern country', 'country rap' etc. (but not a
    fragment that happens to be contained in another word).

    Returns the first matching pair (name, count) or None.
    """
    for name, cnt in top_tags:
        if cnt < config.EXCLUDE_TAG_MIN_COUNT:
            continue
        words = set(name.replace("-", " ").split())
        for ex in config.EXCLUDE_TAGS:
            ex_words = ex.replace("-", " ").split()
            # Single-word exclude (e.g. "country"): hits when the word occurs in
            # the tag. Multi-word exclude (e.g. "hard rock"): as a phrase.
            if len(ex_words) == 1:
                if ex_words[0] in words:
                    return (name, cnt)
            elif ex in name:
                return (name, cnt)
    return None


def _apply_genre_filter(tracks: list[ChartTrack],
                        client: httpx.Client,
                        active: bool) -> list[ChartTrack]:
    """
    Filter tracks by their Last.fm genre tags. A track is dropped when one of
    its strongest EXCLUDE_TAG_TOP_N tags is in EXCLUDE_TAGS and its count is
    >= EXCLUDE_TAG_MIN_COUNT.

    `active` is the per-playlist effective genre-filter switch
    (ChartSource.effective_genre_filter). If it is False, nothing is filtered.

    Fail-open: if Last.fm returns no tags (or there is no API key), the track
    stays. Needs LASTFM_API_KEY; without a key the filter is skipped.
    """
    if not active:
        return tracks
    if not config.LASTFM_API_KEY:
        logger.warning(t("deezer.genre.no_key"))
        return tracks
    if not config.EXCLUDE_TAGS:
        return tracks

    from . import lastfm

    kept: list[ChartTrack] = []
    dropped = 0
    for track in tracks:
        tags = lastfm.get_top_tags(track.artist, track.title, config.LASTFM_API_KEY, client)
        top = tags[:config.EXCLUDE_TAG_TOP_N]
        hit = _match_exclude_tag(top)
        if hit:
            dropped += 1
            logger.info(t("deezer.genre.dropped",
                          artist=track.artist, title=track.title,
                          tag=hit[0], count=hit[1]))
        else:
            kept.append(track)
        if config.GENRE_FILTER_SLEEP_MS > 0:
            time.sleep(config.GENRE_FILTER_SLEEP_MS / 1000.0)

    logger.info(t("deezer.genre.summary", dropped=dropped, total=len(tracks)))
    return kept


def fetch_chart(chart_source) -> list[ChartTrack]:
    """
    Fetch the chart list(s) for a ChartSource and merge multiple feeds (Deezer
    and/or Last.fm) into one deduplicated list. Disabled feeds (enabled=False)
    are skipped. Afterwards an optional genre filter is applied.

    Order: feeds are queried in configuration order. If a song appears in
    multiple feeds, the first occurrence wins (i.e. the earlier-listed source).
    Positions are renumbered after the merge so the playlist order is stable.
    """
    active = chart_source.active_feeds
    # Per-playlist effective genre filter (explicit genre_filter > global).
    genre_active = chart_source.effective_genre_filter
    skipped = len(chart_source.feeds) - len(active)
    skipped_suffix = t("deezer.chart.fetch_skipped", skipped=skipped) if skipped else ""
    logger.info(t(
        "deezer.chart.fetch",
        name=chart_source.title, active=len(active), skipped_suffix=skipped_suffix,
    ))

    merged: list[ChartTrack] = []
    seen: set[str] = set()
    # Counter for tracks removed via exclude_artists (playlist-specific).
    excluded_artists = 0
    # Contribution stats per source: raw delivered and of those NEW (not yet
    # covered by an earlier feed).
    stats: dict[str, dict[str, int]] = {}
    # Remember which source a song originally came from (for the filter balance).
    origin: dict[str, str] = {}

    with httpx.Client(timeout=30.0, headers={"User-Agent": "charts-sync/1.0"},
                      follow_redirects=True) as client:
        for feed in active:
            try:
                tracks = _fetch_one_feed(feed, client)
            except Exception as e:
                logger.error(t("deezer.feed.failed", feed=repr(feed), error=e))
                continue
            s = stats.setdefault(feed.kind, {"raw": 0, "new": 0})
            s["raw"] += len(tracks)
            for track in tracks:
                # Check the artist exclusion (playlist-specific) BEFORE the
                # dedup. Order matters: if we did 'seen' first and exclusion
                # second, an excluded artist could slip through when the same
                # song (same _merge_key) came first from a feed under a NOT
                # excluded name - the second, correctly excluded hit would then
                # hit 'continue' at 'key in seen' before the filter kicks in.
                # Exclude first, then dedup, closes this bypass path. No API call.
                if _is_excluded_artist(track.artist, chart_source.exclude_artists,
                                       track.contributors):
                    excluded_artists += 1
                    logger.info(t("deezer.artist.excluded",
                                  artist=track.artist, title=track.title))
                    continue
                key = _merge_key(track)
                if key in seen:
                    continue
                seen.add(key)
                origin[key] = feed.kind
                s["new"] += 1
                merged.append(track)

        if excluded_artists:
            logger.info(t("deezer.exclude.summary",
                          name=chart_source.title, count=excluded_artists))

        before_filter = len(merged)
        # Apply the (optional) genre filter to the merged list.
        merged = _apply_genre_filter(merged, client, genre_active)

    # Reassign positions after the merge (1..N in merge order).
    for i, track in enumerate(merged, start=1):
        track.position = i

    # Log the contribution stats (what each source adds NET).
    if stats:
        parts = []
        for kind in ("deezer", "lastfm", "energy"):
            if kind in stats:
                s = stats[kind]
                dup = s["raw"] - s["new"]
                parts.append(t("deezer.source.contribution_item",
                               kind=kind, new=s["new"], raw=s["raw"], dup=dup))
        filtered = before_filter - len(merged)
        logger.info(t("deezer.source.contribution",
                      name=chart_source.title, parts=" | ".join(parts)))
        if genre_active and filtered:
            logger.info(t("deezer.genre.filter_removed",
                          name=chart_source.title, count=filtered))
    else:
        filtered = 0

    # Store stats on the chart_source for the later notification (Pushover).
    chart_source.last_stats = {
        "deezer_new": stats.get("deezer", {}).get("new", 0),
        "lastfm_new": stats.get("lastfm", {}).get("new", 0),
        "energy_new": stats.get("energy", {}).get("new", 0),
        "genre_filtered": filtered if genre_active else 0,
    }

    logger.info(t("deezer.merge.result", name=chart_source.title, count=len(merged)))
    return merged
