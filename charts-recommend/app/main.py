"""
charts-recommend - main service (multi-user, download-only).

Reads the public listening taste of several ListenBrainz users (optionally
additionally Last.fm) and lets the SHARED library grow with it. It no longer
writes any playlists - personalization/display is handled by the client
(e.g. Substreamer) per account.

  1. Build the library index (like charts-sync).
  2. For each configured ListenBrainz user, query the active sources:
       - "stats"      (most-played recordings, available immediately)
       - "createdfor" (Weekly Jams / Weekly Exploration, possibly only after weeks)
     Optionally also Last.fm (global source of the operator).
  3. Merge all results + deduplicate (the same song only once).
  4. Match against the library:
       - present -> count (nothing to fetch)
       - missing -> into ONE combined missing file (charts-download fetches
                    when WRITE_MISSING=1 and the file is in its MISSING_FILES)
  5. Pushover message.

Runs as a scheduler loop (every INTERVAL_HOURS hours).

NOTE: ListenBrainz only generates the createdfor playlists after a few weeks of
listening history. Until then this source is empty - not an error, just missing
data (the "stats" source has data immediately).
"""

import logging
import time

from . import config, lastfm, library, listenbrainz, notify, playlist, status
from .errors import FetchError
from .i18n import t

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("charts-recommend")


def _match_all(lib, tracks: list[tuple[int, str, str]]):
    """
    Match a recommendation list against the library.
    Returns: (matched, missing)
      matched: [(pos, artist, title, path), ...] (found in the library)
      missing: [(pos, artist, title), ...] (not found)
    """
    matched = []
    missing = []
    for pos, artist, title in tracks:
        track, score = lib.find_match(artist, title, config.MATCH_THRESHOLD)
        if track:
            matched.append((pos, artist, title, track.path))
        else:
            missing.append((pos, artist, title))
    return matched, missing


def _merge(tracks, merged: list, seen: set) -> None:
    """
    Append (artist, title) to merged, deduplicated via seen.
    tracks are (pos, artist, title) 3-tuples; pos is discarded after the merge.
    The dedupe key is identical to the Last.fm logic so the same song that
    several users like is fetched only ONCE.
    """
    for item in tracks:
        _pos, artist, title = item
        key = f"{artist.lower()}|{title.lower()}"
        if key in seen:
            continue
        seen.add(key)
        merged.append((artist, title))


def run_once() -> tuple[dict, list[str], int, int]:
    """
    Run one recommendation run.

    Returns: (counters, fetch_errors, transport_attempts, transport_failures)
      counters:           counters for the dashboard (sources/candidates/
                          matched/missing, point-in-time values).
      fetch_errors:       list of the source error messages with a user/source
                          prefix (transport errors after all retries).
      transport_attempts: how many (user,source) fetches took place.
      transport_failures: of those, how many with a FetchError.

    Each source is guarded individually: if one user/one source fails, another
    can still return results (partial success). Only a FetchError counts as an
    error - an empty but successful result (no data yet) is not an error.
    """
    logger.info(t("recommend.run.start"))
    lib = library.build_index(config.MUSIC_DIR)
    merged: list[tuple[str, str]] = []   # [(artist, title)] in order
    seen: set[str] = set()               # key = f"{artist.lower()}|{title.lower()}"
    source_results: list[dict] = []      # [{"user":.., "source":.., "n":..}]
    fetch_errors: list[str] = []         # FetchError messages (user/source)
    transport_attempts = 0               # how many fetches took place
    transport_failures = 0               # of those, how many with a FetchError

    if config.LISTENBRAINZ_ENABLED and config.LISTENBRAINZ_USERS:
        for user in config.LISTENBRAINZ_USERS:
            if "stats" in config.RECOMMEND_SOURCES:
                try:
                    recs = listenbrainz.fetch_stats_recordings(
                        user, config.LISTENBRAINZ_TOKEN,
                        config.RECOMMEND_STATS_RANGE, config.RECOMMEND_LIMIT)
                    _merge(recs[:config.RECOMMEND_LIMIT], merged, seen)
                    source_results.append({"user": user, "source": "stats",
                                           "n": len(recs)})
                except FetchError as e:
                    fetch_errors.append(f"{user}/stats: {e}")
                    transport_failures += 1
                finally:
                    transport_attempts += 1
            if "createdfor" in config.RECOMMEND_SOURCES:
                try:
                    by_kind = listenbrainz.fetch_recommendations(
                        user, config.LISTENBRAINZ_TOKEN,
                        config.LISTENBRAINZ_KINDS, config.RECOMMEND_LIMIT)
                    flat = [t for k in config.LISTENBRAINZ_KINDS
                            for t in by_kind.get(k, [])]
                    _merge(flat[:config.RECOMMEND_LIMIT], merged, seen)
                    source_results.append({"user": user, "source": "createdfor",
                                           "n": len(flat)})
                except FetchError as e:
                    fetch_errors.append(f"{user}/createdfor: {e}")
                    transport_failures += 1
                finally:
                    transport_attempts += 1

    if config.LASTFM_ENABLED:
        try:
            lfm = lastfm.fetch_recommendations(
                config.LASTFM_MODE, config.LASTFM_USER, config.LASTFM_API_KEY,
                config.LASTFM_SEEDS, config.RECOMMEND_LIMIT)
            _merge(lfm, merged, seen)   # its own cap in lastfm
            source_results.append({"user": "-", "source": "lastfm",
                                   "n": len(lfm)})
        except FetchError as e:
            fetch_errors.append(f"lastfm: {e}")
            transport_failures += 1
        finally:
            transport_attempts += 1

    # merged (2-tuples) -> flat 3-tuples for the unchanged _match_all().
    tracks = [(pos, artist, title)
              for pos, (artist, title) in enumerate(merged, 1)]
    matched, missing = _match_all(lib, tracks)
    if config.WRITE_MISSING and missing:
        playlist.write_missing(missing, config.MISSING_FILE)

    counters = {
        "sources": len([s for s in source_results if s["n"] > 0]),
        "candidates": len(merged),
        "matched": len(matched),
        "missing": len(missing),
    }
    suffix = (t("recommend.run.missing_written_suffix")
              if (config.WRITE_MISSING and missing) else "")
    logger.info(t("recommend.run.done",
                  sources=counters["sources"], candidates=counters["candidates"],
                  matched=counters["matched"], missing=counters["missing"],
                  suffix=suffix))
    _notify(source_results, counters)
    return counters, fetch_errors, transport_attempts, transport_failures


def _prev_status() -> str:
    """
    Read the status of the PREVIOUS run from the status JSON (before
    status.start() sets it to 'running'). Serves the only-on-change alarm (B):
    this way it can be detected whether the last run was already an error. We
    deliberately read the file via the status module (same file/path) but do not
    create an additional state file. If the file is missing, the run counts as
    'ok' (no previous error).
    """
    data = status._read()
    val = data.get("status")
    return val if isinstance(val, str) else "ok"


# Counter keys (literal, dashboard payload) -> i18n key for the readable label.
_COUNTER_LABEL_KEYS = {
    "sources": "recommend.counter.sources",
    "candidates": "recommend.counter.candidates",
    "matched": "recommend.counter.matched",
    "missing": "recommend.counter.missing",
}


def _base_msg(counters: dict) -> str:
    """Build the readable status/dashboard line from the counters (translated
    label per counter, no raw English counter keys)."""
    parts = [f"{v} {t(_COUNTER_LABEL_KEYS[k])}"
             for k, v in counters.items()
             if k in _COUNTER_LABEL_KEYS and isinstance(v, (int, float)) and v]
    return ", ".join(parts) if parts else t("recommend.status.nothing_found")


def _notify(source_results: list[dict], counters: dict) -> None:
    cand = counters["candidates"]
    miss = counters["missing"]
    if cand == 0:
        logger.info(t("recommend.notify.none_received"))
        return
    # Group per user, keep the order of first mention.
    by_user: dict[str, list[str]] = {}
    for s in source_results:
        if s["n"] <= 0:
            continue
        by_user.setdefault(s["user"], []).append(f"{s['source']} {s['n']}")
    quellen = ", ".join(f"{u} ({', '.join(parts)})"
                        for u, parts in by_user.items())
    n_users = len(by_user)
    if miss > 0:
        head = t("recommend.notify.new_to_load", missing=miss, users=n_users,
                 candidates=cand)
    else:
        head = t("recommend.notify.all_present", candidates=cand, users=n_users)
    body = head + (t("recommend.notify.sources", sources=quellen) if quellen else "")
    notify.send(t("recommend.notify.title", service="charts-recommend"), body)


def _next_run_iso() -> str:
    """Time of the next run as ISO-8601 (for the dashboard)."""
    return time.strftime("%Y-%m-%dT%H:%M:%S%z",
                         time.localtime(time.time() + max(1, config.INTERVAL_HOURS) * 3600))


def _alert_fetch_error(message: str, was_error_before: bool) -> None:
    """
    Pushover alarm on a fetch error - only on state change (B), when
    NOTIFY_ONLY_ON_CHANGE is set. This way there is no new alarm on every 24h run
    while the outage persists. Without PUSHOVER_* notify.send() is a no-op
    anyway.
    """
    if config.NOTIFY_ONLY_ON_CHANGE and was_error_before:
        logger.info(t("recommend.alert.still_failing"))
        return
    notify.send(t("recommend.notify.title_warning", service="charts-recommend"), message)


def _guarded_run() -> None:
    """Run run_once() and write the status (success/error)."""
    # Remember the previous status BEFORE start() (start() sets 'running').
    prev_status = _prev_status()
    status.start()
    try:
        counters, fetch_errors, attempts, failures = run_once()
        base = _base_msg(counters)
        err_msg = "; ".join(fetch_errors)

        if attempts == 0:
            # No users configured / feature off -> idle, not an error.
            status.finish("ok", t("recommend.status.nothing_to_do"),
                          counters, _next_run_iso())
        elif failures == attempts:
            # All fetches transport-failed -> real error.
            status.finish("error", err_msg, counters, _next_run_iso())
            _alert_fetch_error(err_msg, prev_status == "error")
        elif failures > 0:
            # Partial success: one source delivered, another failed.
            status.finish("ok",
                          t("recommend.status.source_disrupted", base=base, error=err_msg),
                          counters, _next_run_iso())
            _alert_fetch_error(t("recommend.alert.one_source_disrupted", error=err_msg),
                               prev_status == "error")
        else:
            # All ok (an empty result is also a success here).
            # Special case: fetches ran without errors but NOTHING came back (0
            # recommendations) - typical when ListenBrainz has not yet generated
            # stats/playlists for the configured users (too little listening
            # history; the stats/createdfor endpoints then return 404).
            # Instead of the uninformative "nothing found", a descriptive line
            # so the dashboard makes clear WHY nothing comes (stays green/ok,
            # not an error).
            if counters["candidates"] == 0:
                msg = t("recommend.status.no_recommendations")
            else:
                msg = base
            status.finish("ok", msg, counters, _next_run_iso())
            if prev_status == "error":
                notify.send(t("recommend.notify.title_recovered", service="charts-recommend"),
                            t("recommend.notify.fetch_recovered"))
    except Exception as e:
        logger.exception(t("recommend.run.error", error=e))
        status.finish("error", str(e), {}, _next_run_iso())
        _alert_fetch_error(str(e), prev_status == "error")


def main() -> None:
    status.init("charts-recommend")
    logger.info(t(
        "recommend.scheduler.started",
        library=config.MUSIC_DIR,
        listenbrainz=t("recommend.state.on") if config.LISTENBRAINZ_ENABLED
        else t("recommend.state.off"),
        users=", ".join(config.LISTENBRAINZ_USERS) or "-",
        sources=", ".join(config.RECOMMEND_SOURCES) or "-",
        lastfm=t("recommend.state.on") if config.LASTFM_ENABLED
        else t("recommend.state.off"),
        interval=config.INTERVAL_HOURS,
    ))

    if config.RUN_ON_START:
        _guarded_run()

    interval_seconds = max(1, config.INTERVAL_HOURS) * 3600
    while True:
        logger.info(t("recommend.scheduler.next_run", interval=config.INTERVAL_HOURS))
        time.sleep(interval_seconds)
        _guarded_run()


if __name__ == "__main__":
    main()
