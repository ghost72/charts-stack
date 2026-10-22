"""
Charts-Sync – main service.

Flow per sync run:
  1. Index the music library (MUSIC_DIR) ONCE.
  2. For EVERY configured chart source (CHARTS):
     a. Fetch the current charts from Deezer (metadata only, no account).
     b. Match each chart track against the library.
     c. Write the hits as a dedicated M3U playlist in PLAYLIST_DIR.
     d. Write missing songs into a dedicated missing file in CHARTS_DIR.
  3. Optional: Pushover notification (only on change, if desired).

The service runs as an endless loop and repeats the run every
SYNC_INTERVAL_HOURS hours.

Note: this service deliberately downloads NO audio files. It works exclusively
with metadata and the already present library.
"""

import json
import logging
import os
import time
from datetime import datetime

from . import config, deezer, library, notify, playlist, status
from .i18n import t

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("charts-sync")


# --- State (for the "only on change" notification) ---

def _state_path() -> str:
    return os.path.join(config.CHARTS_DIR, config.STATE_FILE)


def _load_state() -> dict:
    """Load the last saved state per playlist."""
    try:
        with open(_state_path(), encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    os.makedirs(config.CHARTS_DIR, exist_ok=True)
    tmp = _state_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, _state_path())


def _sync_source(chart_source, lib, state: dict) -> None:
    """Process a single chart source."""
    logger.info(t("sync.source.header", name=chart_source.title))

    # a. Fetch charts
    try:
        chart_tracks = deezer.fetch_chart(chart_source)
    except Exception as e:
        logger.error(t("sync.source.fetch_failed", name=chart_source.title, error=e))
        return
    if not chart_tracks:
        logger.warning(t("sync.source.no_tracks", name=chart_source.title))
        return

    # b. Match
    matched: list[tuple[int, str, str, str]] = []
    missing: list[tuple[int, str, str]] = []
    for ct in chart_tracks:
        track, score = lib.find_match(ct.artist, ct.title, config.MATCH_THRESHOLD)
        if track is not None:
            matched.append((ct.position, ct.artist, ct.title, track.path))
        else:
            missing.append((ct.position, ct.artist, ct.title))

    matched.sort(key=lambda x: x[0])
    missing.sort(key=lambda x: x[0])

    # Dedup: the same song can be hit by multiple chart positions. We keep the
    # best (lowest) chart position each time.
    deduped: list[tuple[int, str, str, str]] = []
    seen_paths: set[str] = set()
    for entry in matched:
        if entry[3] in seen_paths:
            continue
        seen_paths.add(entry[3])
        deduped.append(entry)
    removed = len(matched) - len(deduped)
    if removed:
        logger.info(t("sync.source.dedup_removed", name=chart_source.title, count=removed))
    matched = deduped

    # Determine the order (chart position OR newest first).
    ordered = _order_matched(chart_source, matched, state)

    # c. + d. Write
    playlist.write_playlist(ordered, chart_source.name, chart_source.title)
    playlist.write_missing(missing, chart_source.missing)

    logger.info(t(
        "sync.source.done",
        name=chart_source.title, matched=len(matched), total=len(chart_tracks),
    ))

    # Notification (change detection via the matched paths)
    _handle_notify(chart_source, matched, missing, state)


def _order_matched(chart_source, matched, state: dict):
    """
    Put the matched tracks into the configured order while maintaining the
    "first_seen" date per song in the state.

    matched: list (position, artist, title, absolute_path), already
             chart-sorted + deduplicated.
    Returns: list in the same tuple form, in the desired order.

    PLAYLIST_ORDER="chart" (default): unchanged (chart position, number 1 on top).

    PLAYLIST_ORDER="newest-first": songs most recently added on top. For this
      the date of first appearance is kept per song in the state
      (first_seen[relpath] = ISO timestamp). Known songs keep their date, new
      ones get "now" -> move to the top. On an equal date (e.g. on the very
      first run all are new) the chart position decides, so the order stays
      deterministic.
    """
    if config.PLAYLIST_ORDER != "newest-first":
        return matched

    prev = state.get(chart_source.name, {})
    first_seen: dict[str, str] = dict(prev.get("first_seen", {}))
    now_iso = datetime.now().isoformat(timespec="seconds")

    # Maintain first_seen for current songs (relative path = stable key,
    # identical to what is written in the m3u).
    current_relpaths: list[str] = []
    for _pos, _a, _t, path in matched:
        rel = os.path.relpath(path, config.MUSIC_DIR)
        current_relpaths.append(rel)
        if rel not in first_seen:
            first_seen[rel] = now_iso

    # Keep only entries for currently present songs (do not let the state grow;
    # a song returning later rightly counts as new again).
    first_seen = {rel: first_seen[rel] for rel in current_relpaths}

    # Remember it so _handle_notify does not discard it on the state update.
    chart_source._first_seen = first_seen  # noqa: SLF001 (internal transport)

    # Sort stably in two stages (Python's sort is stable):
    #   1. by chart position ascending (tiebreaker on equal date),
    #   2. by first_seen descending (main criterion: newest on top).
    ordered = sorted(matched, key=lambda e: e[0])  # chart position ascending
    ordered.sort(
        key=lambda e: first_seen.get(os.path.relpath(e[3], config.MUSIC_DIR), now_iso),
        reverse=True,
    )
    return ordered


def _handle_notify(chart_source, matched, missing, state: dict) -> None:
    """
    Send Pushover, optionally only when something changed.

    "Change" covers both the playlist hits (matched paths) and the list of
    missing songs. So a message also goes out when only the songs to search for
    change (e.g. new chart entries), even if the hit list stays identical.
    """
    current_paths = sorted(p for _pos, _a, _t, p in matched)
    # Missing songs as a comparable signature (artist|title), position-independent.
    current_missing = sorted(f"{a}|{t}" for _pos, a, t in missing)

    prev = state.get(chart_source.name, {})
    prev_paths = prev.get("paths", [])
    prev_missing = prev.get("missing", [])

    paths_changed = current_paths != prev_paths
    missing_changed = current_missing != prev_missing
    changed = paths_changed or missing_changed

    # Newly added songs (for a descriptive message)
    new_songs = [
        (pos, a, t) for pos, a, t, p in matched if p not in set(prev_paths)
    ]

    # Always update the state (hits AND missing songs). Carry first_seen along
    # (for PLAYLIST_ORDER=newest-first) if _order_matched set it - otherwise the
    # order history would be lost on the next run. With PLAYLIST_ORDER=chart it
    # does not exist -> omit it.
    entry = {"paths": current_paths, "missing": current_missing}
    first_seen = getattr(chart_source, "_first_seen", None)
    if first_seen is not None:
        entry["first_seen"] = first_seen
    state[chart_source.name] = entry

    if config.NOTIFY_ONLY_ON_CHANGE and not changed:
        logger.info(t("sync.notify.no_change", name=chart_source.title))
        return

    total = len(matched) + len(missing)
    # Pushover title = service emoji + service name (like all other charts
    # services: "⬇️ charts-download", "🎧 charts-recommend" ...). The playlist
    # name with its own emojis sits as a bold header in the body - so it is
    # identical to the Navidrome display name. {date} is replaced as in the
    # playlist writer.
    title = t("notify.title", service="charts-sync")
    playlist_name = chart_source.title.replace(
        "{date}", datetime.now().strftime("%d.%m.%Y"))
    lines = [f"<b>{playlist_name}</b>",
             t("notify.summary", matched=len(matched), total=total)]

    # Source/filter stats (if available) – shows what Last.fm adds net and how
    # many foreign-genre songs the filter removed.
    stats = getattr(chart_source, "last_stats", None) or {}
    stat_bits = []
    if stats.get("deezer_new"):
        stat_bits.append(f"Deezer {stats['deezer_new']}")
    if stats.get("lastfm_new"):
        stat_bits.append(f"Last.fm {stats['lastfm_new']}")
    if stats.get("energy_new"):
        stat_bits.append(f"Energy {stats['energy_new']}")
    if stat_bits:
        lines.append(t("notify.sources", sources=", ".join(stat_bits)))
    if stats.get("genre_filtered"):
        lines.append(t("notify.genre_filtered", count=stats["genre_filtered"]))

    if new_songs and changed:
        lines.append("")
        lines.append(t("notify.new_in_playlist", count=len(new_songs)))
        for pos, a, song_title in new_songs[:8]:
            lines.append(t("notify.song_entry", pos=pos, artist=a, title=song_title))

    # List missing songs so you can search for them in slskd straight from the
    # phone (without a NAS login). Amount limited via NOTIFY_MISSING_LIMIT.
    if missing and config.NOTIFY_MISSING_LIMIT > 0:
        limit = config.NOTIFY_MISSING_LIMIT
        shown = missing[:limit]
        lines.append("")
        lines.append(t("notify.missing.header", count=len(missing)))
        for pos, a, song_title in shown:
            lines.append(t("notify.song_entry", pos=pos, artist=a, title=song_title))
        if len(missing) > limit:
            lines.append(t("notify.missing.more", count=len(missing) - limit))
    elif missing:
        lines.append("")
        lines.append(t("notify.missing.none_listed", count=len(missing)))

    message = "\n".join(lines)
    # Pushover limits a message to 1024 characters. On overflow we cut at the
    # last complete song and append a note.
    message = _truncate_for_pushover(message)
    notify.send(title, message)


def _truncate_for_pushover(message: str, max_len: int = 1024) -> str:
    """Truncate the message to Pushover's 1024-character limit (at a line boundary)."""
    if len(message) <= max_len:
        return message
    suffix = "\n" + t("notify.truncated")
    budget = max_len - len(suffix)
    cut = message[:budget]
    # Cut at the last complete line so no song is left half-shown.
    nl = cut.rfind("\n")
    if nl > 0:
        cut = cut[:nl]
    return cut + suffix


def run_sync() -> None:
    """
    Run a single sync run over all chart sources.
    Returns: counter dict for the status/dashboard module.
    """
    logger.info(t("sync.run.start", sources=len(config.CHARTS)))

    if not config.CHARTS:
        logger.warning(t("sync.run.no_sources"))
        return {"sources": 0, "library_tracks": 0}

    # 1. Index the library ONCE (the expensive part, reused for all sources)
    lib = library.build_index(config.MUSIC_DIR)
    if not lib.tracks:
        logger.warning(t("sync.run.library_empty", dir=config.MUSIC_DIR))

    # 2. Load state, process all sources, save state
    state = _load_state()
    ok_sources = 0
    for chart_source in config.CHARTS:
        try:
            _sync_source(chart_source, lib, state)
            ok_sources += 1
        except Exception as e:
            logger.exception(t("sync.source.exception", name=chart_source.title, error=e))
    _save_state(state)

    logger.info(t("sync.run.done"))
    return {
        "sources": ok_sources,
        "library_tracks": len(lib.tracks),
    }


def _next_run_iso() -> str:
    """Time of the next regular run as ISO-8601 (for the dashboard)."""
    return time.strftime("%Y-%m-%dT%H:%M:%S%z",
                         time.localtime(time.time() + max(1, config.SYNC_INTERVAL_HOURS) * 3600))


def _guarded_sync() -> None:
    """Run run_sync() and write the status (success/error)."""
    status.start()
    try:
        counters = run_sync() or {}
        parts = [f"{v} {k}" for k, v in counters.items() if isinstance(v, (int, float)) and v]
        msg = ", ".join(parts) if parts else "nichts zu tun"
        status.finish("ok", msg, counters, _next_run_iso())
    except Exception as e:
        logger.exception(t("sync.unexpected_error", error=e))
        status.finish("error", str(e), {}, _next_run_iso())


def main() -> None:
    """Start the scheduler loop."""
    status.init("charts-sync")
    logger.info(t(
        "sync.scheduler.started",
        sources=", ".join(f"{c.title} ({c.name})" for c in config.CHARTS) or "keine",
        interval=config.SYNC_INTERVAL_HOURS,
        resync=config.RESYNC_CHECK_SECONDS,
        playlist_dir=config.PLAYLIST_DIR,
        charts_dir=config.CHARTS_DIR,
    ))

    if config.RUN_ON_START:
        _guarded_sync()
        _clear_resync_signal()  # clear a signal possibly set during the run
    else:
        logger.info(t("sync.scheduler.run_on_start_off"))

    interval_seconds = max(1, config.SYNC_INTERVAL_HOURS) * 3600
    check_seconds = max(5, config.RESYNC_CHECK_SECONDS)

    logger.info(t("sync.scheduler.waiting", interval=config.SYNC_INTERVAL_HOURS))
    next_regular = time.time() + interval_seconds
    while True:
        time.sleep(check_seconds)

        trigger = None
        if _resync_requested():
            trigger = t("sync.scheduler.trigger.resync")
        elif time.time() >= next_regular:
            trigger = t("sync.scheduler.trigger.interval")

        if not trigger:
            continue

        logger.info(t("sync.scheduler.triggered", trigger=trigger))
        _guarded_sync()
        _clear_resync_signal()
        next_regular = time.time() + interval_seconds


def _resync_requested() -> bool:
    """True when charts-import has left a resync signal."""
    return os.path.exists(config.RESYNC_SIGNAL)


def _clear_resync_signal() -> None:
    """Remove the resync signal after a run (idempotent)."""
    try:
        os.remove(config.RESYNC_SIGNAL)
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.debug(t("sync.resync.clear_failed", error=e))


if __name__ == "__main__":
    main()
