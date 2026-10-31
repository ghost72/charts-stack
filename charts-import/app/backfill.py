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
import re
import time

from mutagen import File as MutagenFile

from . import (backfill_metadata, config, cover, mbyear, recanon, replaygain,
               resolve, tags, yearmap)
from .backfill_covers import (_iter_manifest_mp3, _iter_mp3, _read_artist_title,
                              build_manifest_index)
from .i18n import t

logger = logging.getLogger("charts-import.backfill")

_YEAR_FOLDER_RE = re.compile(r"\((\d{4})\)")

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

    # --- Release-year backfill for all pending songs (MusicBrainz) ---
    year_result = _year_pass(music_dir)

    # --- Library-wide conservative metadata fill (year/album/track) ---
    meta_result = _metadata_pass(music_dir)

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
        "year_corrected": year_result["corrected"],
        "year_adopted": year_result["adopted"],
        "year_kept": year_result["kept"],
        "year_gave_up": year_result["gave_up"],
        "year_errors": year_result["errors"],
        "meta_year_filled": meta_result["year_filled"],
        "meta_year_replaced": meta_result["year_replaced"],
        "meta_album_filled": meta_result["album_filled"],
        "meta_track_filled": meta_result["track_filled"],
        "meta_scanned": meta_result["scanned"],
        "meta_errors": meta_result["errors"],
    }


def _write_year_tag(path: str, year: str) -> None:
    """
    Write ONLY the release year (date + originaldate) into a library file,
    mtime-preserving. Mirrors tags.write_tags's save sequence minus the
    artist/title/album writes, so canonical spelling and the Deezer UPPERCASE
    path are left undisturbed. PRESERVE_MTIME keeps "date added" stable.
    """
    with tags.preserve_mtime(path):
        audio = MutagenFile(path, easy=True)
        if audio is None:
            logger.debug(t("import.tags.not_writable", path=path))
            return
        audio["date"] = str(year)
        audio["originaldate"] = str(year)
        audio.save()
    tags.strip_provenance_tags(path)


def _select_year_target(paths: "list[str]") -> str:
    """
    Deterministically pick the ONE file the year pass inspects/retags when a
    manifest key maps to several paths: (1) prefer the path whose album-folder
    component matches "(YYYY)" (the Lidarr-managed copy with a trustworthy
    year); (2) else the lexicographically first path.
    """
    for p in sorted(paths):
        for comp in p.replace("\\", "/").split("/"):
            if _YEAR_FOLDER_RE.search(comp):
                return p
    return sorted(paths)[0]


def _split_key(key: str) -> "tuple[str, str]":
    """Split a year-map key on the FIRST '|' into (artist_raw, title_raw)."""
    if "|" in key:
        a, t_ = key.split("|", 1)
        return a, t_
    return key, ""


def _norm_lookup_key(artist_raw: str, title_raw: str) -> str:
    """Normalised manifest-index lookup key for a raw year-map artist/title,
    matching build_manifest_index's keying (clean artist folder + _title_key)."""
    from .importer import _title_key
    safe_artist = tags._clean_component(artist_raw, "Unknown Artist")
    return f"{safe_artist.lower()}|{_title_key(title_raw)}"


def _year_pass(music_dir: str) -> dict:
    """
    Resolve pending year-map objects against MusicBrainz, rate-limited and
    capped per pass (Decision 5). Iterates ONLY the pending objects in the year
    map, builds the manifest index once, locates each file via the normalised
    key, clamps charts-energy decade fallbacks to the channel decade, writes a
    strictly-better year (mtime-preserving), confirms/gives-up, and honours
    YEAR_BACKFILL_ENABLED / YEAR_BACKFILL_LIMIT / YEAR_BACKFILL_MAX_TRIES.
    Returns a counter dict. Per-song errors never abort the pass.
    """
    result = {"corrected": 0, "adopted": 0, "kept": 0, "gave_up": 0, "errors": 0}
    if not config.YEAR_BACKFILL_ENABLED:
        return result

    data = yearmap.load()
    pending = [(k, v) for k, v in data.items() if yearmap.is_pending(v)]
    if not pending:
        return result

    logger.info(t("import.yearfill.started", count=len(pending)))
    index = build_manifest_index(music_dir)
    dirty = False
    queries = 0

    for key, obj in pending:
        artist_raw, title_raw = _split_key(key)
        # Locate the on-disk file via the normalised manifest index.
        paths = index.get(_norm_lookup_key(artist_raw, title_raw))
        if not paths:
            # File not on disk yet / not in the manifest: silent skip, marker
            # stays, retried next pass (no new i18n string for this debug case).
            logger.debug("year pass: file not locatable for %s - %s",
                         artist_raw, title_raw)
            continue
        path = _select_year_target(paths)

        decade = str(obj.get("decade") or "")
        decade_valid = bool(re.fullmatch(r"\d{4}", decade))
        src = str(obj.get("src") or "")
        if src not in ("energy-library", "import-generic"):
            # Scope guard: unknown provenance -> never touched (no MB, no write).
            continue

        try:
            # A Lidarr "(YYYY)" folder retag since import -> adopt that
            # provenance-trustworthy year, no MB call.
            file_year = yearmap._album_folder_year(path)
            if file_year:
                yearmap.confirm(data, key, file_year)
                dirty = True
                result["adopted"] += 1
                logger.info(t("import.yearfill.adopted", artist=artist_raw,
                              title=title_raw, year=file_year))
                continue

            # Per-pass MB budget: peek the cache first (no network); only issue
            # a LIVE query when under the cap. The cap check is BEFORE the live
            # call so query LIMIT+1 (and its MB_RATE_SLEEP) never runs.
            if mbyear.is_cached(artist_raw, title_raw):
                new, ok, _was_cached = mbyear.resolve_mb_year(artist_raw, title_raw)
            else:
                if queries >= config.YEAR_BACKFILL_LIMIT:
                    logger.info(t("import.yearfill.skipped_cap",
                                  limit=config.YEAR_BACKFILL_LIMIT))
                    break
                new, ok, _was_cached = mbyear.resolve_mb_year(artist_raw, title_raw)
                queries += 1

            if not ok:
                # Transient MB/network error: leave marker, do NOT bump tries.
                continue

            if decade_valid:                       # DECADE-AWARE (energy-library)
                frm = int(decade)
                if new and frm <= int(new) <= frm + 9 and new != obj.get("year"):
                    _write_year_tag(path, new)
                    yearmap.confirm(data, key, new)
                    dirty = True
                    result["corrected"] += 1
                    logger.info(t("import.yearfill.corrected", artist=artist_raw,
                                  title=title_raw, year=new))
                elif new and frm <= int(new) <= frm + 9 and new == obj.get("year"):
                    # Promote, NO rewrite: the midpoint was already written to
                    # the file tag at import, so disk == new; only collapse the
                    # map object to the bare string.
                    yearmap.confirm(data, key, new)
                    dirty = True
                    result["corrected"] += 1
                    logger.info(t("import.yearfill.corrected", artist=artist_raw,
                                  title=title_raw, year=new))
                else:                              # MB empty / out of decade
                    if _bump_or_give_up(data, key, obj, "midpoint",
                                        artist_raw, title_raw, result):
                        dirty = True
            else:                                  # GENERIC (no valid decade)
                if new:
                    _write_year_tag(path, new)
                    yearmap.confirm(data, key, new)
                    dirty = True
                    result["corrected"] += 1
                    logger.info(t("import.yearfill.corrected", artist=artist_raw,
                                  title=title_raw, year=new))
                else:
                    if _bump_or_give_up(data, key, obj, "delete+cache",
                                        artist_raw, title_raw, result):
                        dirty = True
        except Exception as e:  # a broken song must not abort the pass
            result["errors"] += 1
            logger.warning(t("import.tags.write_failed", path=path, error=e))

    if dirty:
        try:
            yearmap.rewrite(data)
        except OSError as e:
            logger.warning(t("import.yearfill.mb_cache_write_failed", error=e))

    logger.info(t("import.yearfill.done", corrected=result["corrected"],
                  kept=result["kept"], gave_up=result["gave_up"],
                  errors=result["errors"]))
    return result


def _bump_or_give_up(data: dict, key: str, obj: dict, give_up_as: str,
                     artist: str, title: str, result: dict) -> bool:
    """
    Increment the try counter FIRST; give up on the pass where tries reaches
    YEAR_BACKFILL_MAX_TRIES. On give-up a decade fallback is confirmed to the
    bare-string midpoint; a generic song is deleted from the map AND its MB miss
    is cached (so it is not re-marked). Otherwise the object keeps the bumped
    tries for the next pass. Returns True when the map changed.
    """
    tries = int(obj.get("tries", 0)) + 1
    if tries >= config.YEAR_BACKFILL_MAX_TRIES:
        if give_up_as == "midpoint":
            yearmap.confirm(data, key, obj.get("year", ""))
        else:
            yearmap.give_up(data, key)
            mbyear.cache_miss(artist, title)
        result["gave_up"] += 1
        logger.info(t("import.yearfill.gave_up", tries=tries, artist=artist,
                      title=title))
        return True
    obj["tries"] = tries
    result["kept"] += 1
    logger.info(t("import.yearfill.kept", artist=artist, title=title, tries=tries))
    return True


# --- Library-wide conservative metadata backfill -----------------------------
# State file: remembers, per library-relative path, whether a file is settled so
# it is not re-queried every pass. Mirrors the year map's atomic tmp+replace
# write and fail-open load. Keyed by on-disk relpath (not artist|title) because
# the pass spans the WHOLE library, not just pending year objects.
_META_STATE_NAME = ".charts-metadata-backfill-state.json"


def _meta_state_path(music_dir: str) -> str:
    return os.path.join(music_dir, _META_STATE_NAME)


def _load_meta_state(music_dir: str) -> dict:
    """Load the metadata-backfill state, fail-open to an empty dict."""
    try:
        with open(_meta_state_path(music_dir), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_meta_state(music_dir: str, state: dict) -> None:
    """Write the state atomically (tmp + os.replace). Errors are not fatal."""
    path = _meta_state_path(music_dir)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=0)
        os.replace(tmp, path)
    except OSError as e:
        logger.warning(t("import.metafill.state_write_failed", error=e))


def _file_mtime(path: str) -> str:
    """Current mtime as a string (free read; does not bump it). "" on error."""
    try:
        return str(int(os.stat(path).st_mtime))
    except OSError:
        return ""


def _meta_is_eligible(state: dict, relpath: str, mtime: str) -> bool:
    """
    A file is eligible this pass when it is not yet recorded, is not marked
    done, OR its mtime changed since the last check (an escape hatch so a
    re-imported/edited file is re-examined).
    """
    rec = state.get(relpath)
    if not isinstance(rec, dict):
        return True
    if rec.get("mtime") != mtime:
        return True
    return not rec.get("done", False)


def _metadata_pass(music_dir: str) -> dict:
    """
    Library-wide conservative metadata fill. Walks EVERY mp3 under MUSIC_DIR,
    fills ONLY missing year/album/track via resolve.resolve_metadata and never
    overwrites a present value - except a YouTube-marked year, which is replaced
    by the resolver's real year (marker then cleared). Gated by
    METADATA_BACKFILL_ENABLED, capped per pass (METADATA_BACKFILL_LIMIT), with
    progress persisted so settled files are skipped on later passes. Shares the
    Deezer/MB live-lookup budget and BACKFILL_SLEEP_MS. Fail-open per file.

    Returns a counter dict. Disabled -> all zeros (no-op).
    """
    result = {"scanned": 0, "year_filled": 0, "year_replaced": 0,
              "album_filled": 0, "track_filled": 0, "errors": 0}
    if not config.METADATA_BACKFILL_ENABLED:
        return result

    # Reset the per-pass Deezer/MB live-lookup budget (cache hits are free). The
    # metadata pass runs in the scheduler loop independently of an import pass.
    resolve.reset_run_budget()
    tags.reload_manifest()
    state = _load_meta_state(music_dir)
    logger.info(t("import.metafill.started", music_dir=music_dir))
    corrected = 0
    dirty = False

    for path in _iter_mp3(music_dir):
        if config.METADATA_BACKFILL_LIMIT and corrected >= config.METADATA_BACKFILL_LIMIT:
            logger.info(t("import.metafill.limit_reached",
                          limit=config.METADATA_BACKFILL_LIMIT))
            break
        relpath = os.path.relpath(path, music_dir)
        mtime = _file_mtime(path)
        if not _meta_is_eligible(state, relpath, mtime):
            continue
        result["scanned"] += 1

        try:
            fstate = backfill_metadata.read_state(path)
            if fstate is None:
                # Unreadable tags: skip this pass, do NOT mark done (retry later).
                continue
            if not fstate.artist or not fstate.title:
                # Cannot resolve without artist/title: settle so we stop looking.
                _mark_meta_done(state, relpath, mtime)
                dirty = True
                continue

            plan = backfill_metadata.missing_fields(fstate)
            if not plan.needs_lookup():
                # Nothing to fill and not a youtube file -> settled, zero cost.
                _mark_meta_done(state, relpath, mtime)
                dirty = True
                continue

            # A live resolver call may happen now -> honour the per-pass budget.
            meta = resolve.resolve_metadata(fstate.artist, fstate.title)
            empty = not (meta.year or meta.album_year or meta.album or meta.track)
            if empty:
                # Resolver miss: retry a few passes, then give up (non-youtube).
                dirty = _bump_meta_miss(state, relpath, mtime, plan) or dirty
                _maybe_sleep()
                continue

            res = backfill_metadata.apply_backfill(path, fstate, plan, meta,
                                                   dry_run=False)
            _maybe_sleep()
            if res.error:
                result["errors"] += 1
                # Leave it eligible (do not mark done) so a later pass retries.
                continue

            if res.changed:
                corrected += 1
                if res.year_filled:
                    result["year_filled"] += 1
                if res.year_replaced:
                    result["year_replaced"] += 1
                if res.album_filled:
                    result["album_filled"] += 1
                if res.track_filled:
                    result["track_filled"] += 1
                logger.info(t("import.metafill.corrected", artist=fstate.artist,
                              title=fstate.title, rel=relpath))
            # Settled: a confident resolve ran; whatever was fillable is filled.
            # Re-read the mtime (a write preserves it, but be robust).
            _mark_meta_done(state, relpath, _file_mtime(path))
            dirty = True
        except Exception as e:  # a broken file must not stop the pass
            result["errors"] += 1
            logger.debug(t("import.metafill.file_error", path=path, error=e))

    if dirty:
        _save_meta_state(music_dir, state)

    logger.info(t("import.metafill.done", scanned=result["scanned"],
                  year_filled=result["year_filled"],
                  year_replaced=result["year_replaced"],
                  album_filled=result["album_filled"],
                  track_filled=result["track_filled"], errors=result["errors"]))
    return result


def _mark_meta_done(state: dict, relpath: str, mtime: str) -> None:
    """Record a file as settled (skipped on future passes until its mtime changes)."""
    state[relpath] = {"done": True, "mtime": mtime}


def _bump_meta_miss(state: dict, relpath: str, mtime: str, plan) -> bool:
    """
    Record a resolver miss: increment tries and, for a NON-youtube file, give up
    (mark done) once tries reaches METADATA_BACKFILL_MAX_TRIES so the sources
    are not hammered forever. A youtube-marked file is never capped out this way
    (its marker is the durable retry signal). Returns True (state changed).
    """
    rec = state.get(relpath)
    tries = int(rec.get("tries", 0)) + 1 if isinstance(rec, dict) else 1
    if not plan.is_youtube and tries >= config.METADATA_BACKFILL_MAX_TRIES:
        state[relpath] = {"done": True, "mtime": mtime, "tries": tries}
    else:
        state[relpath] = {"done": False, "mtime": mtime, "tries": tries}
    return True


def _maybe_sleep() -> None:
    """Ease the API rate limit after a resolver call (shared sleep budget)."""
    if config.BACKFILL_SLEEP_MS > 0:
        time.sleep(config.BACKFILL_SLEEP_MS / 1000.0)


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
