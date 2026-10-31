#!/usr/bin/env python3
"""
One-shot, library-wide conservative metadata backfill.

Walks the ENTIRE Navidrome library (MUSIC_DIR) and fills ONLY the MISSING
metadata fields (release year / real album / track number) via the import-time
resolver (Deezer->MusicBrainz, fail-open). It NEVER overwrites an existing value
(curated/Lidarr album, resolver-set year, existing track number) - with one
exception: a year that came from a YouTube import (marked by the durable
TXXX:CHARTS_SOURCE=youtube frame) is replaced by the resolver's real release
year and the marker is then cleared.

This script shares the SAME decision + write core as the periodic pass in
app/backfill.py (app/backfill_metadata.py) and updates the shared state file so
a subsequent periodic pass does not re-query what this run already settled -
one implementation, one progress record.

mtime is preserved on every write (Navidrome keys "date added" off mtime).

Usage (inside the charts-import container; /music is rw there):
  # preview, change nothing (default):
  python -m scripts.backfill_metadata --dry-run
  # actually fill:
  python -m scripts.backfill_metadata --apply
  # cap the number of files changed:
  python -m scripts.backfill_metadata --apply --limit 200

Flags:
  --dry-run   change nothing, only log what WOULD change (the default).
  --apply     actually write (required to change files).
  --limit N   stop after N files CHANGED (0 = no cap). Invalid -> 0.
"""

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import backfill, backfill_metadata, config, resolve, tags  # noqa: E402
from app.backfill_covers import _iter_mp3  # noqa: E402
from app.i18n import t  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backfill-metadata")


def _parse_limit(raw) -> int:
    """Non-negative int limit; invalid -> 0 (no cap), matching backfill_covers."""
    try:
        n = int(raw)
        return n if n >= 0 else 0
    except (TypeError, ValueError):
        return 0


def _none(value: str) -> str:
    """Render an empty field as a readable placeholder for the per-file log."""
    return value if value else t("import.metafill.cli_none")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="only show, write nothing (default)")
    ap.add_argument("--apply", action="store_true",
                    help="actually write (required to change files)")
    ap.add_argument("--limit", default="0",
                    help="stop after N files changed (0 = no cap)")
    args = ap.parse_args()

    # Default-safe: write only when --apply is given AND --dry-run is not.
    dry_run = not args.apply or args.dry_run
    limit = _parse_limit(args.limit)

    music_dir = config.MUSIC_DIR
    if not os.path.isdir(music_dir):
        logger.error(t("import.metafill.cli_no_music_dir", path=music_dir))
        return 1

    mode = t("import.metafill.cli_mode_dry") if dry_run else t("import.metafill.cli_mode_apply")
    logger.info(t("import.metafill.cli_started", music_dir=music_dir, mode=mode,
                  limit=limit))

    # Reset the per-pass live-lookup budget; share the periodic state record.
    resolve.reset_run_budget()
    tags.reload_manifest()
    state = backfill._load_meta_state(music_dir)

    scanned = changed = year_filled = year_replaced = album_filled = 0
    track_filled = errors = 0

    for path in _iter_mp3(music_dir):
        if limit and changed >= limit:
            logger.info(t("import.metafill.limit_reached", limit=limit))
            break
        relpath = os.path.relpath(path, music_dir)
        mtime = backfill._file_mtime(path)
        try:
            fstate = backfill_metadata.read_state(path)
            if fstate is None:
                continue
            if not fstate.artist or not fstate.title:
                if not dry_run:
                    backfill._mark_meta_done(state, relpath, mtime)
                continue
            plan = backfill_metadata.missing_fields(fstate)
            if not plan.needs_lookup():
                if not dry_run:
                    backfill._mark_meta_done(state, relpath, mtime)
                continue
            scanned += 1

            meta = resolve.resolve_metadata(fstate.artist, fstate.title)
            empty = not (meta.year or meta.album_year or meta.album or meta.track)
            if empty:
                if not dry_run:
                    backfill._bump_meta_miss(state, relpath, mtime, plan)
                continue

            res = backfill_metadata.apply_backfill(path, fstate, plan, meta,
                                                   dry_run=dry_run)
            if res.error:
                errors += 1
                continue

            if res.changed:
                changed += 1
                parts = []
                if res.year_filled or res.year_replaced:
                    verb = (t("import.metafill.cli_f_year_replace")
                            if res.year_replaced else t("import.metafill.cli_f_year"))
                    parts.append(t("import.metafill.cli_field", field=verb,
                                   old=_none(res.old_year), new=res.new_year))
                    if res.year_filled:
                        year_filled += 1
                    if res.year_replaced:
                        year_replaced += 1
                if res.album_filled:
                    parts.append(t("import.metafill.cli_field",
                                   field=t("import.metafill.cli_f_album"),
                                   old=_none(res.old_album), new=res.new_album))
                    album_filled += 1
                if res.track_filled:
                    parts.append(t("import.metafill.cli_field",
                                   field=t("import.metafill.cli_f_track"),
                                   old=t("import.metafill.cli_none"),
                                   new=res.new_track))
                    track_filled += 1
                logger.info(t("import.metafill.cli_entry", rel=relpath,
                              changes="; ".join(parts)))
                if not dry_run:
                    backfill._mark_meta_done(state, relpath, backfill._file_mtime(path))
                    backfill._maybe_sleep()
            else:
                if not dry_run:
                    backfill._mark_meta_done(state, relpath, backfill._file_mtime(path))
                    backfill._maybe_sleep()
        except Exception as e:  # a broken file must not stop the run
            errors += 1
            logger.debug(t("import.metafill.file_error", path=path, error=e))

    if not dry_run:
        backfill._save_meta_state(music_dir, state)

    logger.info(t("import.metafill.cli_summary", scanned=scanned, changed=changed,
                  year_filled=year_filled, year_replaced=year_replaced,
                  album_filled=album_filled, track_filled=track_filled,
                  errors=errors))
    if dry_run:
        logger.info(t("import.metafill.cli_dry_notice"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
