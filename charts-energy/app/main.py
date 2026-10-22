"""
charts-energy - main service.

Polls the Swiss NRJ (energy.ch) webradio channels for their now-playing track
via the Icecast ICY in-stream metadata, accumulates the observed spins, matches
them against the library and feeds the missing ones into the existing
charts-download -> charts-import -> Navidrome pipeline.

Two decoupled cadences:
  - POLL (ENERGY_POLL_SECONDS, ~90 s): per enabled channel read one ICY block
    and accumulate into the state buffer. ICY misses are transient (WARN,
    continue) and never set the service to error.
  - MATCH (ENERGY_MATCH_INTERVAL_MINUTES, ~30 min): one library index build,
    then per channel either write an OWN playlist + missing file, or (FEED mode)
    write the per-theme handoff file charts-sync consumes.

Two integration modes per channel:
  - OWN:  energy-<slug>.m3u + missing-energy-<slug>.txt (like charts-hitparade).
  - FEED: a per-theme handoff JSON; charts-sync reads it as an extra feed of an
          existing theme (so there stays exactly one mundart.m3u).

When disabled (ENERGY_ENABLED=0) the container keeps running but does no
polling/matching/output and writes status "idle" (gray tile, not an error).
"""

import logging
import os
import time
from datetime import datetime, timedelta

from . import config, feed, icy, library, notify, playlist, state, status
from .i18n import t

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("charts-energy")


def poll_once() -> int:
    """
    One poll pass over all active channels: read the current ICY track per
    channel and accumulate it into the state buffer.

    Returns the number of NEW tracks added this pass. ICY misses are transient
    (logged once at WARNING) and never raise.
    """
    channels = config.active_channels()
    if not channels:
        return 0

    st = state.load()
    new_count = 0
    for channel in channels:
        try:
            track = icy.read_now_playing(channel.slug)
        except icy.IcyReadError as e:
            # Transient per-channel miss (between songs / briefly unreachable).
            logger.warning(t("energy.icy.read_failed", slug=channel.slug, error=e))
            continue
        if track is None:
            continue
        if state.record_spin(st, channel.slug, track):
            new_count += 1

    state.trim(st)
    try:
        state.save(st)
    except OSError as e:
        # The in-memory accumulation survives; the next cycle retries.
        logger.warning(t("energy.icy.read_failed", slug="state", error=e))
    return new_count


def run_match() -> dict:
    """
    One match + output run over the accumulated state.

    Builds the library index once, then for each active channel:
      - OWN  mode: match the channel's accumulated tracks, write the .m3u and
        the missing file (synthetic positions by first_seen desc).
      - FEED mode: gather the channel's tracks per theme for the handoff writer.
    FEED themes are written once per theme (merged across all channels sharing
    the theme). Returns a counter dict for the dashboard.
    """
    channels = config.active_channels()
    if not channels:
        # Enabled but no channels declared (or all disabled): nothing to do.
        logger.info(t("energy.config.no_channels"))
        return {"channels": 0, "tracks": 0, "matched": 0, "missing": 0}
    logger.info(t("energy.run.start", channels=len(channels)))

    st = state.load()
    lib = library.build_index(config.MUSIC_DIR)

    total_tracks = 0
    total_matched = 0
    total_missing = 0
    own_channels = 0
    # Collect FEED-mode track lists grouped by theme.
    feed_themes: dict[str, list[list[dict]]] = {}
    results: list[dict] = []

    for channel in channels:
        tracks = state.tracks_for(st, channel.slug)
        total_tracks += len(tracks)

        if channel.mode == "feed":
            feed_themes.setdefault(channel.theme, []).append(tracks)
            logger.info(t("energy.channel.done", slug=channel.slug,
                          tracks=len(tracks)))
            continue

        # OWN mode: assign a synthetic chart position by recency (newest first).
        matched = []
        missing = []
        for pos, entry in enumerate(tracks, start=1):
            artist = entry.get("artist", "")
            title = entry.get("title", "")
            track, _score = lib.find_match(artist, title, config.MATCH_THRESHOLD)
            if track:
                matched.append((pos, artist, title, track.path))
            else:
                missing.append((pos, artist, title))

        playlist.write_playlist(matched, channel.playlist, channel.title)
        playlist.write_missing(missing, channel.missing)
        own_channels += 1
        total_matched += len(matched)
        total_missing += len(missing)
        results.append({
            "title": channel.title,
            "matched": len(matched),
            "missing": len(missing),
            "missing_songs": missing,
        })
        logger.info(t("energy.channel.done", slug=channel.slug, tracks=len(tracks)))

    # One handoff file per FEED theme (merged across channels sharing it).
    feed_results: list[dict] = []
    for theme, track_lists in feed_themes.items():
        total = sum(len(tl) for tl in track_lists)
        if total == 0:
            # No accumulated spins for this theme yet - nothing to hand off.
            logger.info(t("energy.feed.skipped", theme=theme))
            continue
        feed.write(theme, track_lists)
        feed_results.append({"theme": theme, "count": total})

    _notify(results, feed_results)

    logger.info(t("energy.run.done"))
    return {
        "channels": len(channels),
        "tracks": total_tracks,
        "matched": total_matched,
        "missing": total_missing,
    }


def _notify(results: list[dict], feed_results: list[dict] | None = None) -> None:
    """Pushover message with the OWN-channel match results + FEED summary."""
    feed_results = feed_results or []
    if not results and not feed_results:
        return
    total_matched = sum(r["matched"] for r in results)
    total_missing = sum(r["missing"] for r in results)
    push_title = t("energy.notify.title", service="charts-energy")

    lines = [t("energy.notify.body",
               matched=total_matched, missing=total_missing), ""]
    for r in results:
        lines.append(f"<b>{r['title']}</b>")
        lines.append(t("energy.notify.own_done",
                       matched=r["matched"], missing=r["missing"]))
        missing_songs = r.get("missing_songs") or []
        if missing_songs and config.NOTIFY_MISSING_LIMIT > 0:
            limit = config.NOTIFY_MISSING_LIMIT
            lines.append("")
            for pos, artist, title in missing_songs[:limit]:
                lines.append(t("notify.song_entry", pos=pos, artist=artist, title=title))
            if len(missing_songs) > limit:
                rest = len(missing_songs) - limit
                lines.append(t("notify.missing.more", count=rest))
        lines.append("")
    for fr in feed_results:
        lines.append(t("energy.notify.feed_done",
                       theme=fr["theme"], count=fr["count"]))
    message = _truncate_for_pushover("\n".join(lines).strip())
    notify.send(push_title, message)


def _truncate_for_pushover(message: str, max_len: int = 1024) -> str:
    """Truncate the message to Pushover's 1024-char limit (at a line boundary)."""
    if len(message) <= max_len:
        return message
    suffix = "\n" + t("notify.truncated")
    budget = max_len - len(suffix)
    cut = message[:budget]
    nl = cut.rfind("\n")
    if nl > 0:
        cut = cut[:nl]
    return cut + suffix


def _next_match_dt(now: datetime) -> datetime:
    """Next scheduled match+output time (for the dashboard display)."""
    return now + timedelta(minutes=config.ENERGY_MATCH_INTERVAL_MINUTES)


def _next_match_iso() -> str:
    """Next scheduled match as local ISO time with timezone offset."""
    return _next_match_dt(datetime.now()).astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")


# Counter keys (literal, dashboard payload) -> i18n key for the readable label.
_COUNTER_LABEL_KEYS = {
    "channels": "energy.counter.channels",
    "tracks": "energy.counter.tracks",
    "matched": "energy.counter.matched",
    "missing": "energy.counter.missing",
}


def _guarded_run() -> None:
    """Run run_match() and write the status (idle/ok/error)."""
    next_run = _next_match_iso()
    if not config.ENERGY_ENABLED:
        # Disabled: container keeps running, but no polling/matching/output.
        # Status idle (gray, not an error).
        status.start()
        status.finish("idle", t("energy.status.disabled"), {}, next_run)
        logger.info(t("energy.disabled_skip"))
        return
    status.start()
    try:
        result = run_match() or {}
        counters = {k: v for k, v in result.items() if isinstance(v, (int, float))}
        parts = [f"{v} {t(_COUNTER_LABEL_KEYS[k])}"
                 for k, v in counters.items() if k in _COUNTER_LABEL_KEYS and v]
        msg = ", ".join(parts) if parts else t("energy.status.nothing_to_do")
        status.finish("ok", msg, counters, next_run)
    except Exception as e:
        logger.exception(t("energy.run.error", error=e))
        status.finish("error", str(e), {}, next_run)


def main() -> None:
    status.init("charts-energy")
    logger.info(t(
        "energy.scheduler.started",
        enabled=t("energy.state.yes") if config.ENERGY_ENABLED
        else t("energy.state.no"),
        channels=len(config.CHANNELS),
        poll=config.ENERGY_POLL_SECONDS,
        interval=config.ENERGY_MATCH_INTERVAL_MINUTES,
        library=config.MUSIC_DIR, playlist_dir=config.PLAYLIST_DIR,
    ))

    # Set next_run current immediately at startup (otherwise the dashboard shows
    # no / an old value until the first run).
    status.set_next_run(_next_match_iso())

    if config.ENERGY_ENABLED and config.RUN_ON_START:
        poll_once()
        _guarded_run()
    elif config.RUN_ON_START:
        # Disabled: still write the idle status tile right away.
        _guarded_run()

    # Poll on the fast cadence; run match+output on the slower interval. The
    # loop sleeps the poll interval (with a 30 s floor) and triggers a match
    # whenever the interval has elapsed. Both short-circuit on disabled.
    poll_sleep = max(30, config.ENERGY_POLL_SECONDS)
    match_interval = timedelta(minutes=config.ENERGY_MATCH_INTERVAL_MINUTES)
    last_match = datetime.now()

    while True:
        time.sleep(poll_sleep)
        now = datetime.now()
        # Keep next_run current at every tick so the dashboard never shows
        # "overdue". set_next_run touches ONLY next_run, preserving an
        # idle/disabled status set by _guarded_run.
        status.set_next_run(_next_match_iso())

        if not config.ENERGY_ENABLED:
            # Disabled: refresh the idle tile and do no work.
            _guarded_run()
            continue

        poll_once()

        if now - last_match >= match_interval:
            _guarded_run()
            last_match = now


if __name__ == "__main__":
    main()
