"""
charts-download – main service.

Automates downloading the missing chart songs:

  1. Reads the missing file(s) written by charts-sync.
  2. Checks the status of already-requested downloads - failed ones
     (Aborted/Errored/TimedOut) are released from the state and cleaned up in
     slskd so they are searched again in this run.
  3. Skips songs that were already requested successfully (state).
  4. Searches per song via the slskd API and picks the best MP3 >= MIN_BITRATE
     (default 256 kbps).
  5. DRY_RUN=1 (default): only logs/reports what WOULD be loaded.
     DRY_RUN=0: actually triggers the downloads via the slskd API.
  6. At most MAX_PER_RUN songs per run (default 10).

Position in the chain:
  charts-sync (missing.txt) -> charts-download (loads) -> slskd -> /Charts
  -> charts-import (files them in) -> Navidrome
"""

import logging
import time

from . import config, manifest, missing, notify, slsk, state, status
from .i18n import t

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("charts-download")


def _interleave_by_source(entries: list[tuple[int, str, str, list[str]]]
                          ) -> list[tuple[int, str, str, list[str]]]:
    """
    Reorders the merged missing list so the sources take turns (round-robin)
    instead of one long source monopolizing the front of the list.

    Background: load_all() returns every source merged and sorted by chart
    position only, so a long source (e.g. Mundart with 100+ entries) sits in
    front and consumes all MAX_PER_RUN slots, starving the chart songs. By
    interleaving - source A #1, source B #1, source C #1, source A #2, ... -
    every source gets a fair turn. Combined with MAX_PER_SOURCE (applied in the
    run loop) this guarantees each source is attempted every run.

    Within a source the original (position-sorted) order is preserved. If
    MAX_PER_SOURCE is 0 (disabled) the original order is returned unchanged.
    """
    if config.MAX_PER_SOURCE <= 0:
        return entries
    # Preserve first-seen source order (load_all keeps sources in path order
    # after the position sort is stable per source). Bucket by the PRIMARY
    # source (e[3][0]) so fairness is unchanged by the multi-source labels.
    buckets: dict[str, list[tuple[int, str, str, list[str]]]] = {}
    order: list[str] = []
    for e in entries:
        src = e[3][0]
        if src not in buckets:
            buckets[src] = []
            order.append(src)
        buckets[src].append(e)
    if not order:
        return entries
    interleaved: list[tuple[int, str, str, list[str]]] = []
    max_len = max(len(buckets[s]) for s in order)
    for idx in range(max_len):
        for s in order:
            if idx < len(buckets[s]):
                interleaved.append(buckets[s][idx])
    return interleaved


def run_once() -> dict:
    """
    Runs a single search/download pass.
    Returns: counter dict for the status/dashboard module.
    """
    logger.info(t("download.run.start", dry_run="1" if config.DRY_RUN else "0"))

    entries = missing.load_all(config.MISSING_FILES)
    if not entries:
        logger.info(t("download.run.no_missing"))
        return {"enqueued": 0, "retried": 0, "missing": 0}

    # Fairness: interleave the sources so a long/hard source (Mundart) cannot
    # monopolize the MAX_PER_RUN slots and starve the findable chart songs.
    entries = _interleave_by_source(entries)

    try:
        client = slsk.connect()
    except slsk.SlskdError as e:
        logger.error(t("download.run.slskd_unreachable", error=e))
        raise

    st = state.load()

    # First: release failed downloads from the state so they are searched/loaded
    # again in this run. Failed transfers in slskd are cleaned up in the process.
    retried = _requeue_failed(client, st)

    # Housekeeping: also remove dead/stuck transfers from slskd that no longer
    # map to a song in the state (failed ones, and 0-byte stuck transfers older
    # than STALE_DOWNLOAD_HOURS) so they do not pile up in slskd's list.
    _cleanup_stale(client)

    # Each entry is (label, "#pos Artist - Title (bitrate)") so the message can
    # group by the originating playlist.
    planned: list[tuple[str, str]] = []   # for dry-run / message
    enqueued: list[tuple[str, str]] = []  # actually triggered
    processed = 0

    # Heuristic against the "slskd logged in but not yet search-ready" case
    # (right after a rebuild/restart EVERY search returns 0 hits even though the
    # songs really are findable). searched = number of songs actually searched,
    # empty_results = of those, the ones with 0 hits. If a whole run returns
    # 0 hits for ALL searched songs and NOTHING was triggered, we treat that as
    # "slskd maybe not search-ready yet" and revert the cooldowns we just set
    # (otherwise the whole backlog sits idle for up to 6h).
    searched = 0
    empty_results = 0
    failed_this_run: list[tuple[str, str]] = []

    skipped_cooldown = 0
    per_source_done: dict[str, int] = {}  # how many searched per source this run
    for pos, artist, title, sources in entries:
        # Primary source drives fairness/logging; the full list is used only for
        # the manifest + message label (so every contributing playlist shows up).
        source = sources[0]
        # Combined label for the Pushover messages, e.g. "charts+hitparade".
        label = "+".join(sources)
        if processed >= config.MAX_PER_RUN:
            logger.info(t("download.run.max_per_run", max=config.MAX_PER_RUN))
            break

        if state.already_requested(st, artist, title):
            logger.debug(t("download.song.already_requested", artist=artist, title=title))
            continue

        # Per-source fairness cap: once a source hit its MAX_PER_SOURCE budget
        # for this run, skip the rest of that source (they are retried next
        # run) so other sources are not starved. Does not consume a global slot.
        if config.MAX_PER_SOURCE > 0 \
                and per_source_done.get(source, 0) >= config.MAX_PER_SOURCE:
            continue

        # Recently searched without success? Then skip WITHOUT consuming one of
        # the MAX_PER_RUN slots. This keeps permanently unfindable songs (e.g.
        # Mundart) from blocking every run so the findable ones get through.
        # After the cooldown expires the search is retried.
        if state.in_cooldown(st, artist, title, config.SEARCH_COOLDOWN_HOURS):
            skipped_cooldown += 1
            continue

        processed += 1
        per_source_done[source] = per_source_done.get(source, 0) + 1
        logger.info(t("download.song.searching", pos=pos, source=source,
                      artist=artist, title=title))

        # Filter out providers that already delivered a bad/upsampled file for
        # this song (flagged by charts-import). Expired entries are cleaned up
        # at the same time.
        blocked_users = state.blocked_providers_for(
            st, artist, title, config.BLOCK_TTL_DAYS)
        if blocked_users:
            logger.info(t("download.song.providers_blocked", count=len(blocked_users)))

        try:
            candidates = slsk.find_candidates(client, artist, title,
                                              blocked_users=blocked_users)
        except slsk.SlskdError as e:
            # Connection/API error (e.g. slskd just restarted). This is NOT a
            # "song not findable" -> do not set a cooldown, do not consume a
            # slot. Since the connection is broken we abort the run (do not keep
            # hammering slskd); the next run tries again.
            logger.warning(t("download.run.slskd_aborted", error=e))
            processed -= 1
            break
        # A real search (slskd responded) has run.
        searched += 1
        if not candidates:
            empty_results += 1
            logger.info(t("download.song.no_match", min=config.MIN_BITRATE,
                          cooldown=config.SEARCH_COOLDOWN_HOURS))
            if not config.DRY_RUN:
                state.mark_failed(st, artist, title)
                failed_this_run.append((artist, title))
            continue

        # Best hit for the dry-run display; the rest are retry fallbacks.
        best_user, best_file, best_score = candidates[0]
        bitrate = best_file.get("bitRate", 0)

        if config.DRY_RUN:
            logger.info(t("download.song.dry_run_would_load",
                          filename=best_file.get("filename", "?"), bitrate=bitrate,
                          score=best_score, user=best_user))
            planned.append((label, f"#{pos} {artist} - {title} ({bitrate}kbps)"))
            # In dry-run do NOT mark it as requested, so it is considered again
            # on the real run.
            continue

        # Retry across multiple providers: Soulseek is P2P - the best hit can
        # come from a peer that is currently offline / rejects the upload. Work
        # through the candidates in order until an enqueue succeeds.
        chosen = None  # (username, file_dict, bitrate)
        for cand_user, cand_file, _score in candidates:
            if slsk.enqueue(client, cand_user, cand_file):
                chosen = (cand_user, cand_file, cand_file.get("bitRate", 0))
                break
            logger.info(t("download.song.provider_no_deliver", user=cand_user))

        if chosen:
            username, file_dict, bitrate = chosen
            fname = file_dict.get("filename", "?")
            logger.info(t("download.song.enqueued", filename=fname, user=username))
            state.mark_requested(st, artist, title, username, fname)
            state.clear_failed(st, artist, title)  # successful hit -> drop cooldown
            # Store clean chart metadata for charts-import so the file is tagged
            # and filed correctly (not "Unknown Album").
            manifest.record(fname, artist, title, sources)
            enqueued.append((label, f"#{pos} {artist} - {title} ({bitrate}kbps)"))
        else:
            logger.warning(t("download.song.no_provider_delivered",
                             artist=artist, title=title))

    if skipped_cooldown:
        logger.info(t("download.run.cooldown_skipped", count=skipped_cooldown))

    # Suspicion "slskd logged in but not yet search-ready": a search ran, ALL
    # searched songs returned 0 hits AND nothing was triggered. That is almost
    # certainly a timing/peer problem (shortly after start), not a real "all
    # unfindable" - revert the cooldowns we just set so the backlog does not sit
    # idle for up to SEARCH_COOLDOWN_HOURS. The next run then retries normally.
    if (not config.DRY_RUN and searched > 0 and empty_results == searched
            and not enqueued):
        for a, song_title in failed_this_run:
            state.clear_failed(st, a, song_title)
        logger.warning(t("download.run.all_empty_requeued", count=searched))

    if not config.DRY_RUN:
        state.save(st)

    _summary_and_notify(planned, enqueued, retried)
    return {
        "enqueued": len(enqueued),
        "planned": len(planned),
        "retried": retried,
        "missing": len(entries),
    }


def _requeue_failed(client, st: dict) -> int:
    """
    Checks the status of the already-requested downloads. Failed ones are
    removed from the state (so they are loaded again) and cleaned up in slskd.
    Returns the number of released songs.

    In dry-run only detection/logging happens, no changes.
    """
    requested = st.get("requested", {})
    if not requested:
        return 0

    failed_keys, to_cleanup = slsk.check_download_states(client, requested)
    if not failed_keys:
        return 0

    if config.DRY_RUN:
        logger.info(t("download.requeue.dry_run", count=len(failed_keys)))
        return 0

    for key in failed_keys:
        requested.pop(key, None)
    slsk.cleanup_failed(client, to_cleanup)
    state.save(st)
    logger.info(t("download.requeue.freed", count=len(failed_keys)))
    return len(failed_keys)


def _cleanup_stale(client) -> None:
    """
    Housekeeping: remove dead/stuck transfers from slskd's download list
    (failed ones, and 0-byte 'Queued, Remotely' transfers older than
    STALE_DOWNLOAD_HOURS). Independent of the per-song state. No-op in dry-run
    or when disabled. Never fatal.
    """
    if not config.STALE_CLEANUP or config.DRY_RUN:
        return
    try:
        slsk.cleanup_stale_downloads(client, config.STALE_DOWNLOAD_HOURS)
    except Exception as e:
        logger.debug(t("download.slsk.cleanup_failed", error=e))


def _breakdown(items: list[tuple[str, str]]) -> str:
    """
    Summarizes how many songs come from which playlist, e.g.
    "3 aus charts, 2 aus 80er-90er". Ordered by count (most frequent first).
    """
    counts: dict[str, int] = {}
    for label, _ in items:
        counts[label] = counts.get(label, 0) + 1
    parts = [t("download.breakdown.item", count=n, label=label)
             for label, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    return ", ".join(parts)


def _summary_and_notify(planned: list[tuple[str, str]],
                        enqueued: list[tuple[str, str]],
                        retried: int) -> None:
    """Logs a summary and optionally sends Pushover."""
    if config.DRY_RUN:
        logger.info(t("download.summary.done_dry_run", count=len(planned)))
        if planned:
            header = [t("download.notify.dry_run_header", count=len(planned)),
                      f"<i>{_breakdown(planned)}</i>"]
            _notify(t("download.notify.title_dry"), header, planned)
    else:
        parts = []
        if enqueued:
            parts.append(t("download.summary.enqueued", count=len(enqueued)))
        if retried:
            parts.append(t("download.summary.retried", count=retried))
        summary = ", ".join(parts) if parts else t("download.summary.nothing")
        logger.info(t("download.summary.done", summary=summary))
        if enqueued or retried:
            header = []
            if enqueued:
                header.append(t("download.notify.enqueued_header", count=len(enqueued)))
                header.append(f"<i>{_breakdown(enqueued)}</i>")
            if retried:
                header.append(t("download.notify.retried_header", count=retried))
            _notify(t("download.notify.title_live"), header, enqueued)


def _notify(title: str, header: list[str], items: list[tuple[str, str]]) -> None:
    """
    Sends a Pushover message with songs grouped by playlist.
    header = header lines (count + breakdown). items = (label, text).
    Format: header, blank line, then one bold heading per playlist with the
    songs below it, blank lines between the groups. Capped at 15 songs total
    (keep the Pushover message short).
    """
    lines = list(header)
    # Group by label (order: largest group first).
    groups: dict[str, list[str]] = {}
    for label, text in items:
        groups.setdefault(label, []).append(text)
    ordered = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))

    shown = 0
    remaining = len(items)
    for i, (label, texts) in enumerate(ordered):
        if shown >= 15:
            break
        lines.append("")  # blank line before each group (also after the header)
        lines.append(f"<b>{label}</b>")
        for text in texts:
            if shown >= 15:
                break
            lines.append(text)
            shown += 1
            remaining -= 1
    if remaining > 0:
        lines.append("")
        lines.append(t("download.notify.more", count=remaining))
    notify.send(title, "\n".join(lines))


def _next_run_iso() -> str:
    """Timestamp of the next run as ISO-8601 (for the dashboard)."""
    return time.strftime("%Y-%m-%dT%H:%M:%S%z",
                         time.localtime(time.time() + max(1, config.INTERVAL_MINUTES) * 60))


def _guarded_run() -> None:
    """Runs run_once() and writes the status (success/error)."""
    status.start()
    try:
        counters = run_once() or {}
        parts = [f"{v} {k}" for k, v in counters.items() if isinstance(v, (int, float)) and v]
        msg = ", ".join(parts) if parts else "nichts zu tun"
        status.finish("ok", msg, counters, _next_run_iso())
    except Exception as e:
        logger.exception(t("download.run.error", error=e))
        status.finish("error", str(e), {}, _next_run_iso())


def main() -> None:
    status.init("charts-download")
    mode = t("download.mode.dry_run") if config.DRY_RUN else t("download.mode.live")
    logger.info(t(
        "download.scheduler.started",
        slskd=config.SLSKD_HOST,
        missing=", ".join(config.MISSING_FILES),
        mode=mode,
        max=config.MAX_PER_RUN,
        min=config.MIN_BITRATE,
        interval=config.INTERVAL_MINUTES,
    ))

    if config.RUN_ON_START:
        _guarded_run()

    interval_seconds = max(1, config.INTERVAL_MINUTES) * 60
    while True:
        logger.info(t("download.scheduler.next_run", interval=config.INTERVAL_MINUTES))
        time.sleep(interval_seconds)
        _guarded_run()


if __name__ == "__main__":
    main()
