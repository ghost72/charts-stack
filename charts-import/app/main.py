"""
charts-import – main service.

Connects the chart songs loaded by slskd with the Navidrome library:

  1. Import: file finished downloads from DOWNLOADS_DIR into
     MUSIC_DIR/<Artist>/<Album>/<Track> (copy or move).
  2. Cleanup: tidy up DOWNLOADS_DIR. Count-based by default (KEEP_LATEST: keep
     the N newest downloads), alternatively age-based (RETENTION_DAYS). This
     keeps the slskd share stably filled.
  3. Optional: Pushover notification when new songs were imported.

Runs as an endless loop and repeats the run every INTERVAL_MINUTES minutes.

Interplay with the sister services:
  Deezer -> charts-sync (missing.txt) -> slskd (download) -> charts-import
  (file in) -> Navidrome sees the song -> charts-sync matches it into the
  playlist.
"""

import json
import logging
import os
import time
import unicodedata
import re

from . import backfill, cleanup, config, importer, lidarr, notify, status
from .i18n import t

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("charts-import")


def run_once() -> dict:
    """
    Runs a single import + cleanup pass.
    Returns: counter dict for the status/dashboard module.
    """
    logger.info(t("import.run.start"))

    imported, rejected, flagged_upper = importer.import_downloads()
    removed = cleanup.cleanup_downloads()

    logger.info(t("import.run.done", imported=len(imported),
                  rejected=len(rejected), removed=len(removed)))

    # Release rejected songs in the charts-download state so they are loaded
    # again (ideally better).
    if rejected:
        _release_rejected(rejected)

    if imported:
        _notify_imported(imported)
        _notify_manual_available(imported)
        _unmonitor_in_lidarr(imported)
        _request_resync()
        # Additionally signal charts-hitparade only when a hitparade-relevant
        # song landed, so the expensive hitparade library rescan is not run for
        # imports that cannot change hitparade.m3u.
        if any(_is_hitparade_source(src) for _t, src, *_rest in imported):
            _request_hitparade_resync()

    if rejected:
        _notify_rejected(rejected)

    if flagged_upper:
        _notify_flagged_upper(flagged_upper)

    result = {
        "imported": len(imported),
        "rejected": len(rejected),
        "cleaned": len(removed),
    }
    # Pass the reject reasons for the dashboard (compact, most frequent first).
    # String value -> NOT accumulated into 'totals' by the status module (only
    # numbers), but lands in the 'message' (see _guarded_run).
    if rejected:
        reasons: dict[str, int] = {}
        for r in rejected:
            reason = (r.get("reason") or "unbekannt").strip()
            reasons[reason] = reasons.get(reason, 0) + 1
        top = sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))
        result["_reject_note"] = "; ".join(f"{n}x {reason}" for reason, n in top[:3])
    return result


def _unmonitor_in_lidarr(imported: "list[tuple[str, str, str, str, str, str]]") -> None:
    """
    Keeps the newly imported chart artists unmonitored in Lidarr (so Lidarr
    does not load a catalog). No-op if Lidarr is not configured.
    The artist is read from the target path MUSIC_DIR/<Artist>/<Album>/<Track>.
    """
    artists: list[str] = []
    for target, *_rest in imported:
        try:
            rel = os.path.relpath(target, config.MUSIC_DIR)
            artist = rel.split(os.sep)[0]
            if artist and artist not in (".", ".."):
                artists.append(artist)
        except ValueError:
            continue
    if artists:
        lidarr.unmonitor_artists(artists)


def _request_resync() -> None:
    """
    Creates the signal file so charts-sync updates the playlist promptly
    (instead of only on the next regular run). Only relevant when new songs
    were actually filed in.
    """
    try:
        with open(config.RESYNC_SIGNAL, "w", encoding="utf-8") as fh:
            fh.write("resync")
        logger.info(t("import.resync.set"))
    except OSError as e:
        logger.warning(t("import.resync.failed", error=e))


def _is_hitparade_source(source: str) -> bool:
    """True if the "+"-joined source label contains the hitparade label.

    A song can carry a combined label (e.g. "charts+hitparade"), so this tests
    MEMBERSHIP of HITPARADE_SOURCE_LABEL in the "+"-split parts, never a
    whole-string or substring compare.
    """
    return config.HITPARADE_SOURCE_LABEL in [
        p.strip() for p in (source or "").split("+") if p.strip()
    ]


def _request_hitparade_resync() -> None:
    """
    Creates the dedicated hitparade signal file so charts-hitparade re-renders
    hitparade.m3u promptly (instead of only on the next Sunday run). Copy of
    _request_resync for the separate hitparade signal.
    """
    try:
        with open(config.HITPARADE_RESYNC_SIGNAL, "w", encoding="utf-8") as fh:
            fh.write("resync")
        logger.info(t("import.hitparade_resync.set"))
    except OSError as e:
        logger.warning(t("import.hitparade_resync.failed", error=e))


def _normalize(text: str) -> str:
    """
    Normalization identical to charts-download (matching.normalize) so the
    state key matches. Only the steps needed for the key (lowercase, accents,
    & -> and, alphanumeric only).
    """
    if not text:
        return ""
    text = text.lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _release_rejected(rejected: list[dict]) -> None:
    """
    Removes the rejected songs from the charts-download state so they are
    searched/loaded again on the next run (ideally a different, better hit).
    Robust: nothing happens on a missing file/key.
    """
    state_file = config.DOWNLOAD_STATE_FILE
    if not state_file or not os.path.isfile(state_file):
        return
    try:
        with open(state_file, "r", encoding="utf-8") as fh:
            state = json.load(fh)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(t("import.release.state_unreadable", error=e))
        return

    requested = state.get("requested", {})
    failed = state.get("failed", {})
    blocked = state.get("blocked_providers", {})
    if not isinstance(requested, dict):
        requested = {}
    if not isinstance(failed, dict):
        failed = {}
    if not isinstance(blocked, dict):
        blocked = {}
    if not requested and not failed:
        return

    released = 0
    blocked_added = 0
    now = int(time.time())
    for entry in rejected:
        key = f"{_normalize(entry.get('artist', ''))}|{_normalize(entry.get('title', ''))}"
        # Before releasing the song: flag the PROVIDER (Soulseek user) of the
        # rejected file for EXACTLY THIS song so the next search in
        # charts-download skips that bad provider for this song (prevents an
        # endless loop "load -> reject -> load the same junk again"). Other
        # songs from the provider remain allowed (the block is per song, not
        # global). The source user is in the charts-download state under
        # requested[key].username.
        req_entry = requested.get(key)
        if isinstance(req_entry, dict):
            username = req_entry.get("username")
            if username:
                song_block = blocked.setdefault(key, {})
                # Only count if the provider was not (currently) blocked for
                # this song yet.
                if username not in song_block:
                    blocked_added += 1
                song_block[username] = now  # timestamp for the 30-day expiry
        # Remove from 'requested' (already requested) AND 'failed' (cooldown) so
        # the song is searched again immediately on the next run and does not
        # hang in the 24h cooldown.
        if key in requested:
            requested.pop(key, None)
            released += 1
        if key in failed:
            failed.pop(key, None)
            released += 1

    if blocked_added:
        state["blocked_providers"] = blocked
        logger.info(t("import.release.providers_blocked", count=blocked_added))

    if released or blocked_added:
        try:
            tmp = state_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(state, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, state_file)
            logger.info(t("import.release.released", count=released))
        except OSError as e:
            logger.warning(t("import.release.state_write_failed", error=e))


def _notify_rejected(rejected: list[dict]) -> None:
    """Pushover message about rejected (sorted-out) files."""
    count = len(rejected)
    title = t("import.notify.rejected_title", count=count)
    lines = [t("import.notify.rejected_header", count=count), ""]
    for entry in rejected[:10]:
        base = os.path.basename(entry.get("path", ""))
        reason = entry.get("reason", "?")
        # Separate the reason with a dash (not in parentheses) so reasons that
        # themselves contain parentheses (e.g. "zu kurz (24s < 90s)") do not
        # create double parentheses.
        lines.append(t("import.notify.rejected_entry", base=base, reason=reason))
    if count > 10:
        lines.append("")
        lines.append(t("import.notify.more", count=count - 10))
    notify.send(title, "\n".join(lines))


def _notify_flagged_upper(flagged: "list[tuple[str, str]]") -> None:
    """
    Pushover hint for songs imported in UPPERCASE because Deezer could not
    provide a safe canonical spelling (alias artists like OMD, or titles
    misnamed by FLASHBACK). They are in the library (no gap) but should be
    checked/renamed manually. A batch message at the end of the run (no spam
    per song).
    """
    count = len(flagged)
    title = t("import.notify.flagged_title", count=count)
    lines = [t("import.notify.flagged_header", count=count), ""]
    for artist, song_title in flagged[:10]:
        lines.append(t("import.notify.flagged_entry", artist=artist, title=song_title))
    if count > 10:
        lines.append("")
        lines.append(t("import.notify.more", count=count - 10))
    notify.send(title, "\n".join(lines))


def _notify_imported(imported: "list[tuple[str, str, str, str, str, str]]") -> None:
    """
    Sends a Pushover BATCH message about newly imported songs.
    imported = list of (target_path, source, artist, title, album,
    notify_device). "source" is the playlist label from the manifest (empty if
    unknown).
    """
    count = len(imported)
    title = t("import.notify.imported_title", count=count)
    lines = [t("import.notify.imported_header", count=count)]

    # Group songs by playlist (order: largest group first).
    groups: dict[str, list[str]] = {}
    for path, source, _artist, _title, _album, _dev in imported:
        label = source or t("import.notify.unknown_source")
        try:
            rel = os.path.relpath(path, config.MUSIC_DIR)
        except ValueError:
            rel = os.path.basename(path)
        groups.setdefault(label, []).append(rel)
    ordered = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))

    # Breakdown by playlist (e.g. "3 aus 80er-90er, 3 aus charts"), followed by
    # a blank line.
    breakdown = ", ".join(t("import.breakdown.item", count=len(songs), label=label)
                          for label, songs in ordered)
    lines.append(t("import.notify.imported_breakdown", breakdown=breakdown))
    lines.append("")

    # Songs per playlist as their own group, with a blank line between them.
    # Capped at 10 songs total (keep the Pushover message short).
    shown = 0
    remaining = count
    for i, (label, songs) in enumerate(ordered):
        if shown >= 10:
            break
        if i > 0:
            lines.append("")  # blank line between the groups
        lines.append(t("import.notify.imported_group", label=label))
        for rel in songs:
            if shown >= 10:
                break
            lines.append(rel)
            shown += 1
            remaining -= 1
    if remaining > 0:
        lines.append("")
        lines.append(t("import.notify.more", count=remaining))
    notify.send(title, "\n".join(lines))


def _notify_manual_available(imported: "list[tuple[str, str, str, str, str, str]]") -> None:
    """
    Sends, for each MANUALLY (via charts-songrequest) requested song, its own
    Pushover message to the requesting user's device as soon as the song is in
    the library. Only songs with notify_device set trigger a message (the
    device comes from USER_PUSHOVER_DEVICES in charts-songrequest).

    IMPORTANT: without notify_device NO message is sent. notify.send() with a
    concrete device argument NEVER sends device-less to all (see notify.send:
    empty/invalid target device -> do not send at all). These individual
    messages come IN ADDITION to the unchanged batch message.
    """
    for _target, source, artist, title, album, notify_device in imported:
        if not notify_device:
            continue  # no manual request with a target device -> no message

        push_title = t("import.notify.manual_title")
        msg_lines = [t("import.notify.manual_song", artist=artist, title=title), ""]
        if album and album != title:
            msg_lines.append(t("import.notify.manual_album", album=album))
        if source:
            msg_lines.append(t("import.notify.manual_requested_via", source=source))
        msg_lines.append("")
        msg_lines.append(t("import.notify.manual_ready"))

        # device passed explicitly -> notify.send filters against the valid
        # device list and sends ONLY to this one device (or not at all if it is
        # invalid). Never to all.
        # A separate sound for the manual availability message (default "magic")
        # - overrides the service default "incoming" so the user hears from the
        # sound that THEIR requested song has arrived.
        notify.send(push_title, "\n".join(msg_lines), device=notify_device,
                    sound=config.PUSHOVER_SOUND_MANUAL)
        logger.info(t("import.notify.manual_sent", device=notify_device,
                      artist=artist, title=title))


def _next_run_iso() -> str:
    """Timestamp of the next run as ISO-8601 (for the dashboard)."""
    return time.strftime("%Y-%m-%dT%H:%M:%S%z",
                         time.localtime(time.time() + max(1, config.INTERVAL_MINUTES) * 60))


def _guarded_run() -> None:
    """Runs run_once() and writes the status (success/error)."""
    status.start()
    try:
        result = run_once() or {}
        # Pull out the reject note (string) - belongs in the message, not in the
        # numeric summary/counters.
        reject_note = result.pop("_reject_note", "")
        counters = {k: v for k, v in result.items() if isinstance(v, (int, float))}
        parts = [f"{v} {k}" for k, v in counters.items() if v]
        msg = ", ".join(parts) if parts else t("import.summary.nothing")
        if reject_note:
            msg += t("import.summary.reject_suffix", note=reject_note)
        status.finish("ok", msg, counters, _next_run_iso())
    except Exception as e:
        logger.exception(t("import.run.error", error=e))
        status.finish("error", str(e), {}, _next_run_iso())


def _maybe_backfill() -> None:
    """Triggers the periodic metadata backfill when it is due.

    Runs deliberately WITHOUT writing its own status (the backfill is a quiet
    background helper; the service status keeps reflecting the import run).
    Errors are never fatal for the scheduler loop.
    """
    if not backfill.due():
        return
    try:
        backfill.run_once()
    except Exception as e:
        logger.exception(t("import.backfill.failed", error=e))


def main() -> None:
    """Starts the scheduler loop."""
    status.init("charts-import")
    if config.KEEP_LATEST == -1:
        cleanup_desc = t("import.cleanup_desc.keep_all")
    elif config.KEEP_LATEST > 0:
        cleanup_desc = t("import.cleanup_desc.keep_latest", count=config.KEEP_LATEST)
    elif config.RETENTION_DAYS > 0:
        cleanup_desc = t("import.cleanup_desc.by_age", days=config.RETENTION_DAYS)
    else:
        cleanup_desc = t("import.cleanup_desc.none")

    logger.info(t("import.scheduler.started",
                  downloads=config.DOWNLOADS_DIR,
                  library=config.MUSIC_DIR,
                  mode=(t("import.mode.copy") if config.COPY_MODE
                        else t("import.mode.move")),
                  cleanup=cleanup_desc,
                  interval=config.INTERVAL_MINUTES))

    if config.BACKFILL_INTERVAL_HOURS > 0:
        logger.info(t("import.backfill.active", hours=config.BACKFILL_INTERVAL_HOURS))

    if config.RUN_ON_START:
        _guarded_run()
        _maybe_backfill()
    else:
        logger.info(t("import.scheduler.wait_interval"))

    interval_seconds = max(1, config.INTERVAL_MINUTES) * 60
    while True:
        logger.info(t("import.scheduler.next_run", interval=config.INTERVAL_MINUTES))
        time.sleep(interval_seconds)
        _guarded_run()
        # After the import, check whether a metadata backfill is due (own, rarer
        # interval - due() throttles itself).
        _maybe_backfill()


if __name__ == "__main__":
    main()
