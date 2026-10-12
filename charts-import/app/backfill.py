"""
Periodic metadata backfill for charts-import.

Background: the import is "fail-open" - if at import time an external source
(iTunes/Deezer cover) OR ffmpeg (ReplayGain) is temporarily unreachable, the
song is imported ANYWAY, but then WITHOUT a cover or WITHOUT ReplayGain. Because
charts-import is idempotent against already-present songs, that song would never
be touched again - the gap would stay permanent.

This module catches up exactly such gaps periodically. It runs in the same
scheduler loop as the import, but rarely (BACKFILL_INTERVAL_HOURS, default 24h),
and works ONLY over the chart songs from the manifest - the rest of the (Lidarr)
library stays untouched.

The existing building blocks are reused:
  - _iter_manifest_mp3 / _read_artist_title from backfill_covers.py
  - cover.cover_status / cover.embed_cover
  - replaygain.available / _has_replaygain / apply_track_gain

Additionally the backfill reconciles UPPERCASE flashback names against the
official Deezer spelling and corrects them (only on a RELIABLE hit; uncertain
ones stay untouched - never guess). This uses the same logic as the manual
script scripts/recanonicalize_flashback.py (extracted into app/recanon.py) and
first invalidates the candidates' Deezer cache so old "frozen" misses are
re-resolved. Can be disabled via BACKFILL_RECANON=0.
"""

import json
import logging
import os
import time

from . import config, cover, recanon, replaygain
from .backfill_covers import _iter_manifest_mp3, _read_artist_title
from .i18n import t

logger = logging.getLogger("charts-import.backfill")

# Timestamp of the last backfill run (monotonic clock, process-local). None =
# never run -> the first scheduler tick after start triggers it.
_last_run_monotonic: "float | None" = None


def due() -> bool:
    """True if BACKFILL_INTERVAL_HOURS have passed since the last backfill (or
    it has never run). At interval 0 the backfill is off."""
    if config.BACKFILL_INTERVAL_HOURS <= 0:
        return False
    if _last_run_monotonic is None:
        return True
    elapsed = time.monotonic() - _last_run_monotonic
    return elapsed >= config.BACKFILL_INTERVAL_HOURS * 3600


def run_once() -> dict:
    """
    One backfill pass over the manifest songs. Catches up per song:
      - a missing embedded cover (via COVER_SOURCES, only when status is 'none'
        - existing covers are NEVER touched)
      - missing ReplayGain (only if ffmpeg is available and there is no RG tag
        yet)

    Returns: counter dict (for logging/status). Per-file errors are never fatal
    - the run continues with the next file.
    """
    global _last_run_monotonic
    _last_run_monotonic = time.monotonic()

    music_dir = config.MUSIC_DIR
    rg_available = replaygain.available() if config.REPLAYGAIN else False

    scanned = 0
    cover_fixed = 0
    rg_fixed = 0
    errors = 0

    logger.info(t("import.backfill.started", music_dir=music_dir,
                  replaygain=(t("import.backfill.rg_on") if rg_available
                              else t("import.backfill.rg_off"))))

    for path in _iter_manifest_mp3(music_dir):
        scanned += 1
        did_api_call = False
        try:
            # --- Catch up a cover (only if none at all is present) ---
            if cover.cover_status(path) == "none":
                artist, title = _read_artist_title(path)
                if artist and title:
                    did_api_call = True
                    if cover.embed_cover(path, artist, title, overwrite=False):
                        cover_fixed += 1
                        logger.info(t("import.backfill.cover_added",
                                      count=cover_fixed, artist=artist, title=title))

            # --- Catch up ReplayGain (only if none is present yet) ---
            if rg_available and not replaygain._has_replaygain(path):
                if replaygain.apply_track_gain(path, overwrite=False):
                    rg_fixed += 1
                    logger.info(t("import.backfill.rg_added", count=rg_fixed, path=path))
        except Exception as e:  # a broken file must not stop the run
            errors += 1
            logger.debug(t("import.backfill.file_error", path=path, error=e))

        # Only ease the rate limit if we actually queried a cover API.
        if did_api_call and config.BACKFILL_SLEEP_MS > 0:
            time.sleep(config.BACKFILL_SLEEP_MS / 1000.0)

        if config.BACKFILL_LIMIT and (cover_fixed + rg_fixed) >= config.BACKFILL_LIMIT:
            logger.info(t("import.backfill.limit_reached", limit=config.BACKFILL_LIMIT))
            break

    # --- UPPERCASE flashback names against the official Deezer spelling ---
    recanon_fixed, recanon_manual = _recanon_pass(music_dir)

    logger.info(t("import.backfill.done", scanned=scanned, cover_fixed=cover_fixed,
                  rg_fixed=rg_fixed, recanon_fixed=recanon_fixed,
                  recanon_manual=recanon_manual, errors=errors))
    return {
        "scanned": scanned,
        "cover_fixed": cover_fixed,
        "rg_fixed": rg_fixed,
        "recanon_fixed": recanon_fixed,
        "recanon_manual": recanon_manual,
        "errors": errors,
    }


def _recanon_pass(music_dir: str) -> "tuple[int, int]":
    """
    Reconciles UPPERCASE flashback names from the manifest against the official
    Deezer spelling and corrects them (only reliable hits).

    Approach: load the manifest -> find UPPERCASE candidates -> invalidate their
    Deezer cache (otherwise old frozen misses keep returning UPPERCASE) -> per
    candidate recanon.recanonicalize_entry() -> on changes write the manifest +
    trigger a Navidrome resync.

    Returns: (corrected, open/manual). (0, 0) if disabled or nothing to do.
    Errors are not fatal.
    """
    if not config.BACKFILL_RECANON:
        return 0, 0
    try:
        with open(config.MANIFEST_FILE, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError) as e:
        logger.debug(t("import.recanon.manifest_unreadable",
                       path=config.MANIFEST_FILE, error=e))
        return 0, 0
    entries = manifest.get("entries", {}) if isinstance(manifest, dict) else {}
    todo = [(k, v) for k, v in entries.items()
            if isinstance(v, dict) and recanon.needs_fix(v)]
    if not todo:
        return 0, 0

    logger.info(t("import.recanon.candidates", count=len(todo)))
    # Invalidate the candidates' old, possibly "frozen" cache misses so they are
    # re-resolved with the current matching (and without cache poisoning).
    cleared = recanon.clear_cache_for([v for _k, v in todo])
    if cleared:
        logger.info(t("import.recanon.cache_invalidated", count=cleared))

    fixed = 0
    manual = 0
    changed = False
    for key, meta in todo:
        try:
            res = recanon.recanonicalize_entry(entries, key, meta, music_dir,
                                               dry_run=False, log=_recanon_log)
        except Exception as e:
            logger.debug(t("import.recanon.entry_error", key=key, error=e))
            continue
        if res == "fixed":
            fixed += 1
            changed = True
        elif res == "manual":
            manual += 1
        # Ease the Deezer rate limit (resolve_canonical makes an API call).
        if config.BACKFILL_SLEEP_MS > 0:
            time.sleep(config.BACKFILL_SLEEP_MS / 1000.0)

    if changed:
        try:
            tmp = config.MANIFEST_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(manifest, fh, ensure_ascii=False, indent=0)
            os.replace(tmp, config.MANIFEST_FILE)
            logger.info(t("import.recanon.manifest_updated", count=fixed))
        except OSError as e:
            logger.error(t("import.recanon.manifest_write_failed", error=e))
        # Navidrome should re-read the renamed paths.
        _request_resync()

    return fixed, manual


def _recanon_log(fmt, *args) -> None:
    """Log adapter for recanon.recanonicalize_entry (uses the same logger)."""
    logger.info(fmt, *args)


def _request_resync() -> None:
    """Creates the resync signal file so charts-sync/Navidrome re-read the
    renamed paths promptly. Errors are not fatal."""
    try:
        with open(config.RESYNC_SIGNAL, "w", encoding="utf-8") as fh:
            fh.write("resync")
        logger.info(t("import.recanon.resync_set"))
    except OSError as e:
        logger.debug(t("import.recanon.resync_failed", error=e))
