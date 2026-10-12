"""
charts-flashback – main service (download-only).

Reads the "last played" rotation of FLASHBACK FM (GraphQL) per configured
decade channel and lets the SHARED library grow along. It writes NO playlists -
the decade display is handled by the Navidrome Smart Playlists (NSP), which
filter by year.

  1. Build the library index (like charts-sync).
  2. Fetch the last played songs per channel (flashback.fetch_played).
  3. Process NEW songs (not in the state, not already seen in this run) up to
     FLASHBACK_LIMIT: resolve the year (year.resolve_year, hybrid MusicBrainz +
     channel decade) and match against the library.
       - present -> count (nothing to backfill)
       - missing -> into missing-flashback.txt (with a {YYYY} suffix) so
                    charts-download backfills it and charts-import sets the
                    year tag.
  4. Save state, Pushover message.

Runs as a scheduler loop (every INTERVAL_HOURS hours).

Error visibility (watchdog): if the GraphQL fetch fails for ALL channels (dead
hash / API changed), the service reports status=error + Pushover. A successful
fetch without NEW songs is NOT an error.
"""

import json
import logging
import os
import time
from datetime import datetime

from . import config, deezer, flashback, library, notify, status, year as year_mod
from .errors import FetchError
from .i18n import t

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("charts-flashback")


# ---------------------------------------------------------------------------
# State (seen songs, against rotation reprocessing)
# ---------------------------------------------------------------------------

def _load_state() -> dict:
    try:
        with open(config.STATE_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("seen"), dict):
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {"seen": {}}


def _save_state(state: dict) -> None:
    try:
        os.makedirs(os.path.dirname(config.STATE_FILE), exist_ok=True)
        tmp = config.STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=0)
        os.replace(tmp, config.STATE_FILE)
    except OSError as e:
        logger.warning(t("flashback.state.write_failed", error=e))


def _key(artist: str, title: str) -> str:
    return f"{artist.lower()}|{title.lower()}"


# ---------------------------------------------------------------------------
# Missing writer (4-tuple + optional {YYYY} suffix, read by charts-download)
# ---------------------------------------------------------------------------

def write_missing(missing: list[tuple[int, str, str, str]], file_name: str) -> str:
    """
    Write the missing list in the charts-download format ("  <pos>. <artist> -
    <title>") - EXACTLY like charts-sync/charts-hitparade, WITHOUT any year
    character. This way charts-import creates path/name/tags identical to all
    other services (Lidarr style). The year is NOT transported via the file
    name (that formerly poisoned title/album/path) but separately via the year
    map (write_year_map), purely into the metadata.
    missing: (position, artist, title, year) - year is IGNORED here.
    """
    os.makedirs(config.CHARTS_DIR, exist_ok=True)
    missing_path = os.path.join(config.CHARTS_DIR, file_name)

    header = t("flashback.missing_file.header",
               date=datetime.now().strftime("%d.%m.%Y %H:%M")).split("\n")
    body = [f"{pos:>3}. {artist} - {title}" for pos, artist, title, _yr in missing]
    content = "\n".join(header + body) + "\n"

    tmp_path = missing_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        fh.write(content)
    os.replace(tmp_path, missing_path)

    logger.info(t("playlist.missing_written", path=missing_path, count=len(missing)))
    return missing_path


def write_year_map(missing: list[tuple[int, str, str, str]]) -> None:
    """
    Write/update the year map (artist|title -> year) to config.YEAR_MAP_FILE.
    charts-import reads it when tagging and sets the date/originaldate tag -
    WITHOUT the year ever appearing in the file/folder name. The key is
    identical to the matching key (lowercase 'artist|title') so charts-import
    finds the same song again. Existing entries are kept (the map grows along).
    """
    path = config.YEAR_MAP_FILE
    data = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            loaded = json.load(fh)
        if isinstance(loaded, dict):
            data = loaded
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        data = {}

    changed = False
    for _pos, artist, title, yr in missing:
        if not yr:
            continue
        key = _key(artist, title)
        if data.get(key) != yr:
            data[key] = yr
            changed = True
    if not changed:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=0)
        os.replace(tmp, path)
        logger.info(t("flashback.year_map.updated", path=path, count=len(data)))
    except OSError as e:
        logger.warning(t("flashback.year_map.write_failed", error=e))


# ---------------------------------------------------------------------------
# Main run
# ---------------------------------------------------------------------------

def run_once() -> tuple[dict, list[str], int, int]:
    """
    Run one FLASHBACK run.

    Returns: (counters, fetch_errors, transport_attempts, transport_failures)
      counters:           {channels, new, matched, missing} (snapshot values).
      fetch_errors:       channel error messages (FetchError after retries).
      transport_attempts: how many channel fetches happened.
      transport_failures: of those, how many with a FetchError.

    Each channel is guarded individually: if one fails, others can still
    deliver. Only a FetchError counts as an error; a successful fetch without
    new songs is not an error.
    """
    logger.info(t("flashback.run.start"))
    lib = library.build_index(config.MUSIC_DIR)
    state = _load_state()
    seen_state: dict = state["seen"]
    seen_run: set[str] = set()           # within this run (first channel wins)
    missing: list[tuple[int, str, str, str]] = []
    channels_with_data = 0
    new_count = 0
    matched_count = 0
    fetch_errors: list[str] = []
    transport_attempts = 0
    transport_failures = 0
    pos = 0

    if config.FLASHBACK_HASH and config.FLASHBACK_CHANNELS:
        for channel in config.FLASHBACK_CHANNELS:
            if new_count >= config.FLASHBACK_LIMIT:
                break
            try:
                played = flashback.fetch_played(
                    channel, config.FLASHBACK_HASH, config.FLASHBACK_PLAYED_LIMIT)
            except FetchError as e:
                fetch_errors.append(f"{channel}: {e}")
                transport_failures += 1
                continue
            finally:
                transport_attempts += 1

            if played:
                channels_with_data += 1
            for artist, title, title_year in played:
                if new_count >= config.FLASHBACK_LIMIT:
                    break
                # Canonicalize casing BEFORE _key/seen/find_match/missing run:
                # FLASHBACK delivers UPPERCASE, Deezer gives the real spelling.
                # _key/seen stay casing-robust (lowercase), but the name written
                # into missing.txt determines the later folder/file name and
                # must be canonical. Fallback = raw value.
                artist, title = deezer.resolve_canonical(artist, title)
                k = _key(artist, title)
                if k in seen_state or k in seen_run:
                    continue
                seen_run.add(k)
                seen_state[k] = datetime.now().strftime("%Y-%m-%d")
                new_count += 1
                yr = year_mod.resolve_year(artist, title, title_year)
                track, _score = lib.find_match(artist, title, config.MATCH_THRESHOLD)
                if track:
                    matched_count += 1
                else:
                    pos += 1
                    missing.append((pos, artist, title, yr))

    _save_state(state)
    if config.WRITE_MISSING and missing:
        write_missing(missing, config.MISSING_FILE)
        write_year_map(missing)   # year separately -> charts-import tags from it

    counters = {
        "channels": channels_with_data,
        "new": new_count,
        "matched": matched_count,
        "missing": len(missing),
    }
    suffix = (t("flashback.run.missing_written_suffix")
              if (config.WRITE_MISSING and missing) else "")
    logger.info(t("flashback.run.done",
                  channels=counters["channels"], new=counters["new"],
                  matched=counters["matched"], missing=counters["missing"],
                  suffix=suffix))
    _notify(counters)
    return counters, fetch_errors, transport_attempts, transport_failures


def _prev_status() -> str:
    data = status._read()
    val = data.get("status")
    return val if isinstance(val, str) else "ok"


# Counter keys (literal, dashboard payload) -> i18n key for the readable label.
_COUNTER_LABEL_KEYS = {
    "channels": "flashback.counter.channels",
    "new": "flashback.counter.new",
    "matched": "flashback.counter.matched",
    "missing": "flashback.counter.missing",
}


def _base_msg(counters: dict) -> str:
    """Readable status/dashboard line from the counters (translated label per
    counter, no raw English counter keys)."""
    parts = [f"{v} {t(_COUNTER_LABEL_KEYS[k])}"
             for k, v in counters.items()
             if k in _COUNTER_LABEL_KEYS and isinstance(v, (int, float)) and v]
    return ", ".join(parts) if parts else t("flashback.status.nothing_new")


def _notify(counters: dict) -> None:
    new = counters["new"]
    miss = counters["missing"]
    if new == 0:
        logger.info(t("flashback.notify.no_new_songs"))
        return
    if miss > 0:
        head = t("flashback.notify.new_to_load", missing=miss, new=new)
    else:
        head = t("flashback.notify.all_present", new=new)
    notify.send(t("flashback.notify.title", service="charts-flashback"), head)


def _next_run_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z",
                         time.localtime(time.time() + max(1, config.INTERVAL_HOURS) * 3600))


def _alert_fetch_error(message: str, was_error_before: bool) -> None:
    if config.NOTIFY_ONLY_ON_CHANGE and was_error_before:
        logger.info(t("flashback.alert.still_failing"))
        return
    notify.send(t("flashback.notify.title_warning", service="charts-flashback"), message)


def _guarded_run() -> None:
    # Disabled: the container keeps running but no fetch. Status idle (gray, not
    # an error) instead of ok/error. BEFORE prev_status/status.start()/run_once().
    if not config.FLASHBACK_ENABLED:
        status.start()
        status.finish("idle", t("flashback.status.disabled"), {}, _next_run_iso())
        logger.info(t("flashback.disabled_skip"))
        return
    # Active but no hash set -> there is nothing to fetch. More honest than a
    # green "ok" (no real fetch ran): status idle.
    if not config.FLASHBACK_HASH:
        status.start()
        status.finish("idle", t("flashback.status.no_hash"),
                      {}, _next_run_iso())
        return
    prev_status = _prev_status()
    status.start()
    try:
        counters, fetch_errors, attempts, failures = run_once()
        base = _base_msg(counters)
        err_msg = "; ".join(fetch_errors)

        if attempts == 0:
            status.finish("ok", t("flashback.status.nothing_to_do"),
                          counters, _next_run_iso())
        elif failures == attempts:
            status.finish("error", err_msg, counters, _next_run_iso())
            _alert_fetch_error(err_msg, prev_status == "error")
        elif failures > 0:
            status.finish("ok",
                          t("flashback.status.channel_disrupted", base=base, error=err_msg),
                          counters, _next_run_iso())
            _alert_fetch_error(t("flashback.alert.one_channel_disrupted", error=err_msg),
                               prev_status == "error")
        else:
            status.finish("ok", base, counters, _next_run_iso())
            if prev_status == "error":
                notify.send(t("flashback.notify.title_recovered", service="charts-flashback"),
                            t("flashback.notify.fetch_recovered"))
    except Exception as e:
        logger.exception(t("flashback.run.error", error=e))
        status.finish("error", str(e), {}, _next_run_iso())
        _alert_fetch_error(str(e), prev_status == "error")


def main() -> None:
    status.init("charts-flashback")
    logger.info(t(
        "flashback.scheduler.started",
        enabled=t("flashback.state.yes") if config.FLASHBACK_ENABLED
        else t("flashback.state.no"),
        library=config.MUSIC_DIR,
        hash=t("flashback.state.hash_set") if config.FLASHBACK_HASH
        else t("flashback.state.hash_missing"),
        channels=", ".join(config.FLASHBACK_CHANNELS) or "-",
        musicbrainz=t("flashback.state.on") if config.MB_ENABLED
        else t("flashback.state.off"),
        deezer=t("flashback.state.on") if config.DEEZER_CANONICAL_ENABLED
        else t("flashback.state.off"),
        interval=config.INTERVAL_HOURS,
    ))

    if config.RUN_ON_START:
        _guarded_run()

    interval_seconds = max(1, config.INTERVAL_HOURS) * 3600
    while True:
        logger.info(t("flashback.scheduler.next_run", interval=config.INTERVAL_HOURS))
        time.sleep(interval_seconds)
        _guarded_run()


if __name__ == "__main__":
    main()
