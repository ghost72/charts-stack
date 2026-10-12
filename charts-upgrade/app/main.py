"""
charts-upgrade – main service.

Cleans up the quality of the Navidrome library's BACK CATALOG: finds songs with
bad quality (low real bitrate OR upsampled/faked bitrate) and searches slskd
for a genuinely better version. It only replaces IF the new file is really
better according to the real frequency cutoff.

Flow per run:
  1. Scan the library (library.scan) - compilations are skipped, the ffmpeg
     cutoff analysis is cached in the state (once per file).
  2. Search slskd for each bad song (providers that earlier delivered a
     non-better file are filtered out).
  3. DRY_RUN=1 (default): only report what WOULD be found/loaded.
     DRY_RUN=0: load the best candidate, wait for completion, compare the loaded
     file against the existing one by real cutoff.
       - new file better -> manifest entry with replace=true; charts-import
         replaces the existing file (old one to the trash).
       - new file NOT better -> block the provider for this song, do not adopt
         the file (charts-import leaves it alone for lack of a replace upgrade;
         it stays in the downloads folder and is removed later by the
         charts-import cleanup).

Position in the chain:
  charts-upgrade (finds bad back-catalog songs, loads replacement + manifest)
  -> slskd -> /Charts -> charts-import (replaces on a real cutoff upgrade)
"""

import logging
import os
import time

from . import config, library, manifest, notify, quality, slsk, state, status
from .i18n import t

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("charts-upgrade")

# How long (seconds) at most to wait for a download to complete before the
# loaded file can be measured. Conservative; if the download takes longer it is
# evaluated in a later run.
_DOWNLOAD_WAIT_SECONDS = 180
_BLOCK_TTL_DAYS = 30


def run_once() -> dict:
    """Runs an upgrade pass. Returns: counters for the dashboard."""
    logger.info(t("upgrade.run.start", dry_run="1" if config.DRY_RUN else "0"))

    if not quality.available():
        logger.warning(t("upgrade.run.ffmpeg_unavailable"))

    st = state.load()

    # 1. Scan the library (with cached analysis result).
    bad_songs, scanned, skipped_comp = library.scan(st["seen"], config.MAX_SCAN_PER_RUN)
    state.save(st)  # persist the analysis cache (even if nothing is loaded afterwards)

    # Scan progress for the dashboard: how many MP3s are checkable in total
    # (scan_total) and how many of them have already been analyzed (scan_seen).
    # scan_seen from the 'seen' cache; cap at scan_total in case the cache
    # contains orphan entries (deleted/renamed files) -> never >100%.
    scan_total = library.count_candidates()
    scan_seen = min(len(st.get("seen", {})), scan_total) if scan_total else 0

    logger.info(t("upgrade.scan.summary", scanned=scanned, skipped=skipped_comp,
                  bad=len(bad_songs), scan_seen=scan_seen, scan_total=scan_total))

    if not bad_songs:
        logger.info(t("upgrade.run.no_bad_songs"))
        _notify_summary([], [], scanned, skipped_comp)
        return {"scanned": scanned, "bad": 0, "upgraded": 0,
                "scan_seen": scan_seen, "scan_total": scan_total}

    # 2./3. Search for a replacement (+ optionally load/check/replace).
    try:
        client = slsk.connect()
    except slsk.SlskdError as e:
        logger.error(t("upgrade.run.slskd_unreachable", error=e))
        raise

    planned: list[dict] = []    # DRY_RUN / message
    upgraded: list[dict] = []   # actually triggered as an upgrade
    processed = 0

    for song in bad_songs:
        if processed >= config.MAX_PER_RUN:
            logger.info(t("upgrade.run.max_per_run", max=config.MAX_PER_RUN))
            break

        artist, title = song["artist"], song["title"]
        if state.already_requested(st, artist, title):
            logger.debug(t("upgrade.song.already_requested", artist=artist, title=title))
            continue

        processed += 1
        logger.info(t("upgrade.song.bad", reason=song["reason"], artist=artist, title=title))

        blocked = state.blocked_providers_for(st, artist, title, _BLOCK_TTL_DAYS)
        try:
            candidates = slsk.find_candidates(client, artist, title, blocked_users=blocked)
        except slsk.SlskdError as e:
            logger.warning(t("upgrade.run.slskd_aborted", error=e))
            processed -= 1
            break

        if not candidates:
            logger.info(t("upgrade.song.no_replacement", min=config.MIN_BITRATE))
            continue

        best_user, best_file, best_score = candidates[0]
        new_bitrate = best_file.get("bitRate", 0) or 0

        if config.DRY_RUN:
            logger.info(t("upgrade.song.dry_run_would_load",
                          filename=best_file.get("filename", "?"),
                          bitrate=new_bitrate, user=best_user))
            planned.append({**song, "new_bitrate": new_bitrate})
            continue

        # --- LIVE: load, wait for completion, measure, compare ---
        chosen = _download_first_working(client, candidates)
        if not chosen:
            logger.warning(t("upgrade.song.no_provider_delivered"))
            continue
        username, file_dict = chosen
        fname = file_dict.get("filename", "?")

        local_path = _wait_for_file(client, username, fname)
        if not local_path:
            logger.info(t("upgrade.song.download_not_ready"))
            # Mark as requested so it is not loaded again right away.
            state.mark_requested(st, artist, title, username, fname)
            continue

        # Real cutoff comparison: is the new file REALLY better?
        old_q = quality.quality_key(song["path"], song["bitrate"])
        new_q = quality.quality_key(local_path, new_bitrate)
        if new_q > old_q:
            logger.info(t("upgrade.song.upgrade_confirmed", old_q=old_q, new_q=new_q))
            manifest.record(fname, artist, title, config.SOURCE_LABEL, replace=True)
            state.mark_requested(st, artist, title, username, fname)
            upgraded.append({**song, "new_bitrate": new_bitrate})
        else:
            logger.info(t("upgrade.song.not_better", new_q=new_q, old_q=old_q))
            state.block_provider(st, artist, title, username)

    state.save(st)
    _notify_summary(planned, upgraded, scanned, skipped_comp)
    return {
        "scanned": scanned,
        "bad": len(bad_songs),
        "planned": len(planned),
        "upgraded": len(upgraded),
        "scan_seen": scan_seen,
        "scan_total": scan_total,
    }


def _download_first_working(client, candidates):
    """Triggers the candidates in order until an enqueue succeeds."""
    for cand_user, cand_file, _score in candidates:
        if slsk.enqueue(client, cand_user, cand_file):
            return cand_user, cand_file
        logger.info(t("upgrade.song.provider_no_deliver", user=cand_user))
    return None


def _wait_for_file(client, username: str, remote_filename: str) -> str | None:
    """
    Waits until the download is complete and returns the LOCAL path of the
    finished file in the downloads folder (config.MUSIC_DIR is the library; the
    downloads sit in the shared /charts folder that slskd uses as downloads -
    we locate the file there via its basename). None on timeout/error.

    The downloads folder is known via DOWNLOADS_DIR (identical to the
    charts-import DOWNLOADS_DIR = /charts). We derive it from MANIFEST_FILE,
    which sits in the same folder.
    """
    downloads_dir = os.path.dirname(config.MANIFEST_FILE)
    base = os.path.basename(remote_filename.replace("\\", "/"))

    deadline = time.time() + _DOWNLOAD_WAIT_SECONDS
    while time.time() < deadline:
        found = _find_file(downloads_dir, base)
        if found and _is_stable(found):
            return found
        time.sleep(5)
    return None


def _find_file(root: str, basename: str) -> str | None:
    """Searches recursively for a file with a matching basename (case-insensitive)."""
    target = basename.lower()
    if not os.path.isdir(root):
        return None
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if name.lower() == target:
                return os.path.join(dirpath, name)
    return None


def _is_stable(path: str, min_age: int = 10) -> bool:
    """True if the file has not changed for min_age seconds."""
    try:
        return (time.time() - os.path.getmtime(path)) >= min_age
    except OSError:
        return False


def _notify_summary(planned: list[dict], upgraded: list[dict],
                    scanned: int, skipped_comp: int) -> None:
    """Logs and reports (Pushover) a summary."""
    if config.DRY_RUN:
        if not planned:
            logger.info(t("upgrade.notify.dry_run_done_nothing"))
            return
        logger.info(t("upgrade.notify.dry_run_done", count=len(planned)))
        lines = [t("upgrade.notify.dry_run_header", count=len(planned)), ""]
        for s in planned[:15]:
            lines.append(t("upgrade.notify.song_entry", artist=s['artist'], title=s['title']))
            lines.append(t("upgrade.notify.reason", reason=s['reason']))
        if len(planned) > 15:
            lines.append(t("upgrade.notify.more", count=len(planned) - 15))
        notify.send(t("upgrade.notify.title_dry"), "\n".join(lines))
    else:
        if not upgraded:
            logger.info(t("upgrade.notify.live_done_nothing"))
            return
        logger.info(t("upgrade.notify.live_done", count=len(upgraded)))
        lines = [t("upgrade.notify.upgraded_header", count=len(upgraded)), ""]
        for s in upgraded[:15]:
            lines.append(t("upgrade.notify.song_entry", artist=s['artist'], title=s['title']))
            lines.append(t("upgrade.notify.reason_bitrate", reason=s['reason'],
                           bitrate=s.get('new_bitrate', '?')))
        if len(upgraded) > 15:
            lines.append(t("upgrade.notify.more", count=len(upgraded) - 15))
        notify.send(t("upgrade.notify.title_live"), "\n".join(lines))


def _next_run_iso() -> str:
    """Timestamp of the next run as ISO-8601 (for the dashboard)."""
    return time.strftime("%Y-%m-%dT%H:%M:%S%z",
                         time.localtime(time.time() + max(1, config.INTERVAL_MINUTES) * 60))


def _guarded_run() -> None:
    """Runs run_once() and writes the status (success/error)."""
    status.start()
    try:
        counters = run_once() or {}
        # scan_seen/scan_total are progress metadata (own dashboard line), not
        # part of the run summary -> keep them out of the 'message'. They stay
        # in 'counters' for the dashboard.
        _msg_skip = {"scan_seen", "scan_total"}
        parts = [f"{v} {k}" for k, v in counters.items()
                 if k not in _msg_skip and isinstance(v, (int, float)) and v]
        msg = ", ".join(parts) if parts else "nichts zu tun"
        status.finish("ok", msg, counters, _next_run_iso())
    except Exception as e:
        logger.exception(t("upgrade.run.error", error=e))
        status.finish("error", str(e), {}, _next_run_iso())


def main() -> None:
    status.init("charts-upgrade")
    mode = t("upgrade.mode.dry_run") if config.DRY_RUN else t("upgrade.mode.live")
    logger.info(t(
        "upgrade.scheduler.started",
        library=config.MUSIC_DIR, slskd=config.SLSKD_HOST, mode=mode,
        bad_bitrate=config.BAD_BITRATE,
        upsampling=t("upgrade.upsampling.on") if config.CHECK_UPSAMPLED
        else t("upgrade.upsampling.off"),
        max_per_run=config.MAX_PER_RUN, max_scan=config.MAX_SCAN_PER_RUN,
        interval=config.INTERVAL_MINUTES,
    ))

    if config.RUN_ON_START:
        _guarded_run()

    interval_seconds = max(1, config.INTERVAL_MINUTES) * 60
    while True:
        logger.info(t("upgrade.scheduler.next_run", interval=config.INTERVAL_MINUTES))
        time.sleep(interval_seconds)
        _guarded_run()


if __name__ == "__main__":
    main()
