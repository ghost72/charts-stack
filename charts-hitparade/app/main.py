"""
charts-hitparade - main service.

Fetches the Swiss Hitparade from hitparade.ch weekly (on Sundays), matches it
against the library and writes a playlist (.m3u) + a missing file for
charts-download per chart. Plain HTTP fetch, no headless browser.

Flow per run (per configured chart):
  1. Fetch chart list from hitparade.ch (hitparade.fetch_chart).
  2. Match each song against the library (library.find_match).
  3. Hits -> playlist (.m3u), missing ones -> missing file (charts-download).

Monitoring: If hitparade.ch returns 0 songs (page rebuilt/blocked) or is
unreachable, the run is reported as status=error -> dashboard tile orange/red +
Pushover. This way a broken scraper stands out immediately.

Position in the chain (like charts-sync):
  charts-hitparade (hitparade.m3u + missing-hitparade.txt)
  -> charts-download (fetches the missing ones) -> charts-import -> Navidrome
"""

import logging
import time
from datetime import date, datetime

from . import config, hitparade, library, notify, playlist, status
from .i18n import t

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("charts-hitparade")


def _match_all(lib, songs):
    """Match a chart list against the library. -> (matched, missing)."""
    matched = []
    missing = []
    for pos, artist, title in songs:
        track, _score = lib.find_match(artist, title, config.MATCH_THRESHOLD)
        if track:
            matched.append((pos, artist, title, track.path))
        else:
            missing.append((pos, artist, title))
    return matched, missing


def run_once() -> dict:
    """
    Run one Hitparade run (all configured charts).
    Returns: counter dict for the dashboard. Re-raises an exception on a source
    error (-> _guarded_run reports status=error).
    """
    logger.info(t("hitparade.run.start", charts=", ".join(config.HITPARADE_CHARTS)))
    lib = library.build_index(config.MUSIC_DIR)
    day = hitparade.latest_sunday()

    total_songs = 0
    total_matched = 0
    total_missing = 0
    results = []
    errors = []        # real scraper errors (0 songs) -> red
    unavailable = []   # hitparade.ch temporarily down (5xx) -> yellow/idle

    for chart in config.HITPARADE_CHARTS:
        meta = config.CHART_META.get(chart)
        if not meta:
            logger.warning(t("hitparade.run.unknown_chart", chart=chart))
            continue
        try:
            songs = hitparade.fetch_chart(
                chart, day,
                timeout_s=config.FETCH_TIMEOUT_SECONDS,
                retries=config.FETCH_RETRIES,
            )
        except hitparade.HitparadeUnavailable as e:
            # hitparade.ch temporarily unreachable (5xx/timeout) - this is
            # THEIR problem, not a scraper defect. Record separately so it is
            # handled more leniently (idle/yellow, no alarm).
            logger.warning(t("hitparade.run.chart_unavailable", chart=chart, error=e))
            unavailable.append(f"{chart}: {e}")
            continue
        except hitparade.HitparadeError as e:
            # Page answers but 0 songs -> scraper broken -> hard error.
            logger.error(t("hitparade.run.chart_failed", chart=chart, error=e))
            errors.append(f"{chart}: {e}")
            continue

        matched, missing = _match_all(lib, songs)
        playlist.write_playlist(matched, meta["playlist"], meta["title"])
        playlist.write_missing(missing, meta["missing"])

        logger.info(t("hitparade.run.chart_done", chart=chart, songs=len(songs),
                      matched=len(matched), missing=len(missing)))
        total_songs += len(songs)
        total_matched += len(matched)
        total_missing += len(missing)
        results.append({"chart": chart, "title": meta["title"],
                        "total": len(songs), "matched": len(matched),
                        "missing": len(missing),
                        # Pass through the full missing list (pos, artist,
                        # title) for the Pushover listing (like charts-sync).
                        "missing_songs": missing})

    _notify(results)

    # Real scraper error (0 songs despite a response) -> hard error (red).
    if errors and not results:
        raise hitparade.HitparadeError("; ".join(errors))
    # Partial error (one chart ok, one broken) also visible as an error.
    if errors:
        logger.warning(t("hitparade.run.partial_failed", errors="; ".join(errors)))

    # NO real error, but all sources only temporarily unreachable (5xx/timeout)
    # and nothing loaded -> signal as UNAVAILABLE so _guarded_run sets the
    # status to idle (yellow, no alarm) instead of red.
    if unavailable and not results and not errors:
        raise hitparade.HitparadeUnavailable("; ".join(unavailable))
    if unavailable:
        logger.warning(t("hitparade.run.partial_unavailable", errors="; ".join(unavailable)))

    logger.info(t("hitparade.run.done"))
    return {
        "charts": len(results),
        "songs": total_songs,
        "matched": total_matched,
        "missing": total_missing,
        "_error_note": "; ".join(errors) if errors else "",
    }


def _notify(results: list[dict]) -> None:
    """Pushover message with the Hitparade results (only on change)."""
    if not results:
        return
    total_matched = sum(r["matched"] for r in results)
    total_missing = sum(r["missing"] for r in results)

    # Pushover title = service emoji + service name (like all other charts
    # services). The playlist name with its emojis appears as a bold header per
    # playlist in the body - identical to the Navidrome display name.
    push_title = t("hitparade.notify.title", service="charts-hitparade")

    lines = [t("hitparade.notify.summary_header",
               matched=total_matched, missing=total_missing), ""]
    for r in results:
        lines.append(f"<b>{r['title']}</b>")
        lines.append(t("hitparade.notify.chart_line",
                       matched=r["matched"], missing=r["missing"], total=r["total"]))
        # List missing songs (like charts-sync) so you can see directly from
        # your phone what will be fetched. Quantity limited via
        # NOTIFY_MISSING_LIMIT (0 = only the count, no song list).
        missing_songs = r.get("missing_songs") or []
        if missing_songs and config.NOTIFY_MISSING_LIMIT > 0:
            limit = config.NOTIFY_MISSING_LIMIT
            lines.append("")  # blank line before the song list (readability)
            for pos, artist, title in missing_songs[:limit]:
                lines.append(t("notify.song_entry", pos=pos, artist=artist, title=title))
            if len(missing_songs) > limit:
                rest = len(missing_songs) - limit
                lines.append(t("notify.missing.more", count=rest))
        lines.append("")
    message = _truncate_for_pushover("\n".join(lines).strip())
    # NOTIFY_ONLY_ON_CHANGE is not checked in notify.send itself - the
    # scheduler only runs 1x/week anyway, so one message per run is fine.
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


def _next_run_dt(now: datetime) -> datetime:
    """Next scheduled run time (for the dashboard display)."""
    import datetime as _dt
    # Days until the configured weekday
    days_ahead = (config.RUN_WEEKDAY - now.weekday()) % 7
    candidate = now.replace(hour=config.RUN_HOUR, minute=config.RUN_MINUTE,
                            second=0, microsecond=0) + _dt.timedelta(days=days_ahead)
    if candidate <= now:
        candidate += _dt.timedelta(days=7)
    return candidate


def _next_run_iso() -> str:
    """Next scheduled run as local ISO time with timezone offset."""
    return _next_run_dt(datetime.now()).astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")


# Counter keys (literal, dashboard payload) -> i18n key for the readable label.
_COUNTER_LABEL_KEYS = {
    "charts": "hitparade.counter.charts",
    "songs": "hitparade.counter.songs",
    "matched": "hitparade.counter.matched",
    "missing": "hitparade.counter.missing",
}


def _guarded_run() -> None:
    """Run run_once() and write the status (success/error)."""
    next_run = _next_run_iso()
    if not config.HITPARADE_ENABLED:
        # Disabled: container keeps running, but no fetch/playlist/Pushover.
        # Status idle (gray, not an error) instead of ok/error.
        status.start()
        status.finish("idle", t("hitparade.status.disabled"), {}, next_run)
        logger.info(t("hitparade.disabled_skip"))
        return
    status.start()
    try:
        result = run_once() or {}
        error_note = result.pop("_error_note", "")
        counters = {k: v for k, v in result.items() if isinstance(v, (int, float))}
        # Readable status/dashboard line from the counters (translated label per
        # counter, no raw English counter keys).
        parts = [f"{v} {t(_COUNTER_LABEL_KEYS[k])}"
                 for k, v in counters.items() if k in _COUNTER_LABEL_KEYS and v]
        msg = ", ".join(parts) if parts else t("hitparade.status.nothing_to_do")
        if error_note:
            # Partial error: the run ran, but one source was broken -> mark as
            # an error so it stands out in the dashboard.
            status.finish("error",
                          t("hitparade.status.error_suffix", msg=msg, error=error_note),
                          counters, next_run)
        else:
            status.finish("ok", msg, counters, next_run)
    except hitparade.HitparadeUnavailable as e:
        # hitparade.ch temporarily unreachable (5xx/timeout) - NOT a scraper
        # defect. Idle (yellow/gray in the dashboard, no Pushover alarm);
        # recovers on its own at the next run.
        logger.warning(t("hitparade.unavailable.warn", error=e))
        status.finish("idle", t("hitparade.status.source_unavailable", error=e),
                      {}, next_run)
    except Exception as e:
        logger.exception(t("hitparade.run.error", error=e))
        status.finish("error", str(e), {}, next_run)


def main() -> None:
    status.init("charts-hitparade")
    logger.info(t(
        "hitparade.scheduler.started",
        enabled=t("hitparade.state.yes") if config.HITPARADE_ENABLED
        else t("hitparade.state.no"),
        charts=", ".join(config.HITPARADE_CHARTS),
        weekday=config.RUN_WEEKDAY,
        hour=config.RUN_HOUR, minute=config.RUN_MINUTE,
        library=config.MUSIC_DIR, playlist_dir=config.PLAYLIST_DIR,
    ))

    # Set next_run current immediately at startup (otherwise the dashboard
    # shows no / an old value until the first run).
    status.set_next_run(_next_run_iso())

    if config.RUN_ON_START:
        _guarded_run()

    check = max(30, config.CHECK_INTERVAL_SECONDS)
    last_run_day: date | None = date.today() if config.RUN_ON_START else None

    while True:
        time.sleep(check)
        now = datetime.now()
        # Keep next_run current at EVERY check so the dashboard display never
        # shows "overdue" just because the last run was longer ago.
        # set_next_run touches ONLY next_run, not the status - so an
        # idle/disabled set by _guarded_run is preserved.
        status.set_next_run(_next_run_iso())
        # Run window: correct weekday, hour reached, not yet run today.
        due = (now.weekday() == config.RUN_WEEKDAY
               and now.hour >= config.RUN_HOUR
               and (now.hour > config.RUN_HOUR or now.minute >= config.RUN_MINUTE)
               and last_run_day != now.date())
        if due:
            logger.info(t("hitparade.scheduler.due",
                          hour=config.RUN_HOUR, minute=config.RUN_MINUTE))
            _guarded_run()
            last_run_day = now.date()


if __name__ == "__main__":
    main()
