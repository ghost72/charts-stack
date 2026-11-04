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

from . import (config, deezer, health, library, notify, notify_budget, playlist,
               presence, status)
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


def _sync_source(chart_source, lib, state: dict) -> str:
    """
    Process a single chart source.

    Returns a per-source outcome for the G3 structural-break detector:
      "error" - the internal fetch except branch fired (transport failure),
      "empty" - fetched successfully but zero chart tracks,
      "ok"    - a successful fetch (tracks returned) + the usual write/notify.
    The match/dedupe/write/notify path below is unchanged; only the return value
    is added. A source that raises PAST this function is caught and mapped to
    "error" by run_sync's per-source try/except.
    """
    logger.info(t("sync.source.header", name=chart_source.title))

    # a. Fetch charts
    try:
        chart_tracks = deezer.fetch_chart(chart_source)
    except Exception as e:
        logger.error(t("sync.source.fetch_failed", name=chart_source.title, error=e))
        return "error"
    if not chart_tracks:
        logger.warning(t("sync.source.no_tracks", name=chart_source.title))
        return "empty"

    # b. Match
    matched: list[tuple[int, str, str, str]] = []
    missing: list[tuple[int, str, str]] = []
    for ct in chart_tracks:
        track, score = lib.find_match(ct.artist, ct.title, config.MATCH_THRESHOLD)
        if track is not None:
            matched.append((ct.position, ct.artist, ct.title, track.path))
            continue
        # Presence guard (mirrors charts-songrequest's find_existing, no
        # compilation filter): if a real library copy exists under <Artist>/ -
        # even inside a compilation-named or Lidarr "(YYYY)" album folder that
        # find_match skipped under IGNORE_COMPILATIONS - do NOT re-queue it.
        # The existing file IS a valid playlist entry, so route it into
        # `matched` (the subsequent path-keyed dedup handles duplicates). This
        # only suppresses a missing write; find_match's return value is never
        # altered, so the in_year_album tie-break is untouched.
        existing = presence.find_existing(config.MUSIC_DIR, ct.artist, ct.title)
        if existing is not None:
            matched.append((ct.position, ct.artist, ct.title, existing))
            continue
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

    return "ok"


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

    # Overview line first: the new/missing counts must survive the Pushover
    # budget cut, so they sit in the head (never cut). Order = detail order.
    new_count = len(new_songs) if (new_songs and changed) else 0
    overview_parts = []
    if new_count:
        overview_parts.append(t("notify.overview.new", count=new_count))
    if missing:
        overview_parts.append(t("notify.overview.missing", count=len(missing)))
    overview = notify_budget.overview_line(overview_parts)
    if overview:
        lines.append(overview)

    blocks = []
    if new_count:
        # Songs 9+ were never listed (the overview carries the full count).
        blocks.append(notify_budget.Block(
            header=["", t("notify.new_in_playlist", count=len(new_songs))],
            entries=[notify_budget.Entry([t("notify.song_entry", pos=pos,
                                            artist=a, title=song_title)])
                     for pos, a, song_title in new_songs[:8]]))

    # List missing songs so you can search for them in slskd straight from the
    # phone (without a NAS login). Amount limited via NOTIFY_MISSING_LIMIT.
    if missing and config.NOTIFY_MISSING_LIMIT > 0:
        limit = config.NOTIFY_MISSING_LIMIT
        blocks.append(notify_budget.Block(
            header=["", t("notify.missing.header", count=len(missing))],
            entries=[notify_budget.Entry([t("notify.song_entry", pos=pos,
                                            artist=a, title=song_title)])
                     for pos, a, song_title in missing[:limit]],
            hidden=max(0, len(missing) - limit),
            cap_note=(t("notify.missing.more", count=len(missing) - limit)
                      if len(missing) > limit else "")))
    elif missing:
        blocks.append(notify_budget.Block(entries=[notify_budget.Entry(
            ["", t("notify.missing.none_listed", count=len(missing))],
            weight=0)]))

    # Pushover limits a message to 1024 characters. Entries are cut as whole
    # songs with one explicit "... and N more" line.
    message = notify_budget.fit_message(
        lines, blocks, [], more=lambda n: t("notify.more", count=n))
    notify.send(title, message)


def run_sync() -> tuple[dict, int, int, dict]:
    """
    Run a single sync run over all chart sources.

    Returns a 4-tuple (counters, produced, attempted, health_map):
      counters:   the UNCHANGED dashboard counter dict
                  {"sources": ok_sources, "library_tracks": ...}. "sources"
                  keeps its exact current meaning: the number of _sync_source
                  calls that did NOT raise at the run-loop level (an "empty" and
                  a caught run-loop exception both leave it unchanged, as today).
      produced:   detector-internal count of sources that returned "ok".
      attempted:  detector-internal count of configured sources (len(CHARTS)).
      health_map: the per-source health map (already persisted this run).

    produced/attempted/health_map are detector-internal (G3) and are NEVER
    written into counters. EVERY return site yields the same 4-tuple shape so
    _guarded_sync's four-value unpack can never raise ValueError.
    """
    logger.info(t("sync.run.start", sources=len(config.CHARTS)))

    if not config.CHARTS:
        logger.warning(t("sync.run.no_sources"))
        # MEDIUM-1: match the 4-tuple arity. attempted == 0 means the
        # whole_run_empty guard (attempted > 0 and produced == 0) is False, so
        # an empty-CHARTS deployment is correctly NON-alarming; _load_health()
        # yields a fail-open health dict whose "sources" map is {} -> no dead
        # sources.
        return {"sources": 0, "library_tracks": 0}, 0, 0, health.load()

    # 1. Index the library ONCE (the expensive part, reused for all sources)
    lib = library.build_index(config.MUSIC_DIR)
    if not lib.tracks:
        logger.warning(t("sync.run.library_empty", dir=config.MUSIC_DIR))

    # 2. Load state + health, process all sources, save both
    state = _load_state()
    health_map = health.load()
    ok_sources = 0
    for chart_source in config.CHARTS:
        try:
            outcome = _sync_source(chart_source, lib, state)  # "ok"|"empty"|"error"
            if outcome == "ok":
                ok_sources += 1  # UNCHANGED existing "sources" counter
        except Exception as e:
            logger.exception(t("sync.source.exception", name=chart_source.title, error=e))
            outcome = "error"  # a run-loop crash is an "error" outcome
        # One place records the per-source outcome into the health counter.
        health._record_source_outcome(health_map, chart_source.name, outcome)
    _save_state(state)
    try:
        health.save(health_map)
    except OSError as e:
        # Keep the in-memory verdict for this run; retry next run.
        logger.warning(t("sync.health.write_failed", error=e))

    logger.info(t("sync.run.done"))
    counters = {
        "sources": ok_sources,
        "library_tracks": len(lib.tracks),
    }
    attempted = len(config.CHARTS)
    return counters, ok_sources, attempted, health_map


def _next_run_iso() -> str:
    """Time of the next regular run as ISO-8601 (for the dashboard)."""
    return time.strftime("%Y-%m-%dT%H:%M:%S%z",
                         time.localtime(time.time() + max(1, config.SYNC_INTERVAL_HOURS) * 3600))


def _prev_status() -> str:
    """
    Read the status of the PREVIOUS run from the status JSON (before
    status.start() sets it to 'running'). Serves the state-change-gated alarm:
    this way it can be detected whether the last run was already an error. We
    read the file via the status module (same file/path) but do not create an
    additional state file. If the file is missing, the run counts as 'ok' (no
    previous error). Copied verbatim from charts-recommend.
    """
    data = status._read()
    val = data.get("status")
    return val if isinstance(val, str) else "ok"


def _alert_structural(message: str, was_error_before: bool) -> None:
    """
    Pushover alarm on a structural break - only on state change, when
    NOTIFY_ONLY_ON_CHANGE is set, so the persisting outage does not re-alarm on
    every run. Without PUSHOVER_* notify.send() is a no-op anyway. Mirrors
    charts-recommend's _alert_fetch_error.
    """
    if config.NOTIFY_ONLY_ON_CHANGE and was_error_before:
        logger.info(t("sync.alert.still_failing"))
        return
    notify.send(t("sync.alert.structural_title"), message)


def _structural_reason_msg(whole_run_empty: bool, attempted: int,
                           dead_sources: list[str]) -> str:
    """Build the readable status/alarm reason line from the verdict parts."""
    parts: list[str] = []
    if whole_run_empty:
        parts.append(t("sync.alert.whole_run_empty", attempted=attempted))
    if dead_sources:
        parts.append(t("sync.alert.sources_dead",
                       count=len(dead_sources),
                       runs=config.SYNC_DEAD_RUNS_THRESHOLD,
                       names=", ".join(sorted(dead_sources))))
    return " ".join(parts)


def _guarded_sync() -> None:
    """Run run_sync(), evaluate the structural-break verdict, write status."""
    # Remember the previous status BEFORE start() (start() sets 'running').
    prev_status = _prev_status()
    status.start()
    try:
        counters, produced, attempted, health_map = run_sync()
        # Whole-run empty fires immediately (no threshold): every configured
        # source produced nothing this run.
        whole_run_empty = attempted > 0 and produced == 0
        # A previously-ok source dead across N consecutive runs.
        dead_sources = [
            name for name, h in health_map.get("sources", {}).items()
            if isinstance(h, dict)
            and h.get("dead_runs", 0) >= config.SYNC_DEAD_RUNS_THRESHOLD]

        if whole_run_empty or dead_sources:
            reason = _structural_reason_msg(whole_run_empty, attempted, dead_sources)
            # counters unchanged; produced/attempted/health never enter it.
            status.finish("error",
                          t("sync.status.structural_error", reason=reason),
                          counters, _next_run_iso())
            _alert_structural(reason, prev_status == "error")
            return

        parts = [f"{v} {k}" for k, v in counters.items() if isinstance(v, (int, float)) and v]
        msg = ", ".join(parts) if parts else "nichts zu tun"
        status.finish("ok", msg, counters, _next_run_iso())
        if prev_status == "error":
            notify.send(t("notify.title", service="charts-sync"),
                        t("sync.notify.structural_recovered"))
    except Exception as e:
        logger.exception(t("sync.unexpected_error", error=e))
        status.finish("error", str(e), {}, _next_run_iso())
        _alert_structural(str(e), prev_status == "error")


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
