#!/usr/bin/env python3
"""
One-shot: fix the release YEAR of HISTORICAL YouTube songs in the library.

Why this exists: songs imported from YouTube BEFORE the durable
TXXX:CHARTS_SOURCE=youtube marker existed carry a wrong year (the yt-dlp
upload/publish year) that is PRESENT, so the conservative metadata backfill will
not touch it, and they have no file marker, so the periodic pass cannot
recognize them. The ONLY reliable, durable signal for them is the manifest
``source == "youtube"`` field (the manifest is never pruned; KEEP_LATEST recycles
download files only).

This script reads the manifest, selects the youtube-sourced entries, maps each
to its on-disk file (via the same normalized artist|title index the cover/year
passes use), re-resolves ONLY the year via resolve.resolve_metadata and rewrites
ONLY the year (mtime-preserving, via backfill._write_year_tag). It touches
NOTHING else - no album/track/cover. This is the one place in the whole feature
that overwrites a present year without a per-file marker, justified because the
manifest source is a known-untrusted-year signal.

Fail-open per file: one bad song never aborts the run. Built as a general tool -
it scales to many entries with progress logging, not just the local count.

Usage (inside the charts-import container; /music is rw there):
  python -m scripts.fix_youtube_years --dry-run   # preview (default)
  python -m scripts.fix_youtube_years --apply      # actually rewrite the year
  python -m scripts.fix_youtube_years --apply --limit 50

Flags:
  --dry-run   change nothing, only log old year -> new year (the default).
  --apply     actually write (required to change files).
  --limit N   stop after N files CHANGED (0 = no cap). Invalid -> 0.
"""

import argparse
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import backfill, config, resolve, tags  # noqa: E402
from app.backfill_covers import build_manifest_index  # noqa: E402
from app.i18n import t  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("fix-youtube-years")


def _parse_limit(raw) -> int:
    try:
        n = int(raw)
        return n if n >= 0 else 0
    except (TypeError, ValueError):
        return 0


def _is_youtube_entry(v: dict) -> bool:
    """
    True when a manifest entry is YouTube-sourced, handling both shapes (mirror
    tags.manifest_sources): the new "sources" list OR the old single "source".
    Matched exactly against "youtube" (never as a substring).
    """
    sources = v.get("sources")
    if isinstance(sources, list) and "youtube" in sources:
        return True
    return v.get("source") == "youtube"


def _load_youtube_entries() -> "list[dict]":
    """
    Walk the manifest's nested "entries" dict and return the youtube-sourced
    per-song records. Fail-open to [] on a missing/corrupt manifest. Never
    assumes the records are top-level keys.
    """
    try:
        with open(config.MANIFEST_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError) as e:
        logger.warning(t("import.ytfix.manifest_unreadable",
                         path=config.MANIFEST_FILE, error=e))
        return []
    entries = data.get("entries", {}) if isinstance(data, dict) else {}
    out = []
    for v in entries.values():
        if isinstance(v, dict) and _is_youtube_entry(v):
            out.append(v)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="only show, write nothing (default)")
    ap.add_argument("--apply", action="store_true",
                    help="actually write (required to change files)")
    ap.add_argument("--limit", default="0",
                    help="stop after N files changed (0 = no cap)")
    args = ap.parse_args()

    dry_run = not args.apply or args.dry_run
    limit = _parse_limit(args.limit)

    music_dir = config.MUSIC_DIR
    if not os.path.isdir(music_dir):
        logger.error(t("import.metafill.cli_no_music_dir", path=music_dir))
        return 1

    mode = t("import.metafill.cli_mode_dry") if dry_run else t("import.metafill.cli_mode_apply")
    resolve.reset_run_budget()
    tags.reload_manifest()

    entries = _load_youtube_entries()
    logger.info(t("import.ytfix.started", count=len(entries), mode=mode, limit=limit))
    if not entries:
        logger.info(t("import.ytfix.summary", total=0, changed=0, skipped=0,
                      errors=0))
        return 0

    # Build the artist|title -> on-disk paths index once (finds the file even
    # after Lidarr renamed it into an (YYYY) album folder).
    index = build_manifest_index(music_dir)

    total = changed = skipped = errors = 0
    for v in entries:
        total += 1
        if limit and changed >= limit:
            logger.info(t("import.metafill.limit_reached", limit=limit))
            break
        artist = (v.get("artist") or "").strip()
        title = (v.get("title") or "").strip()
        if not artist or not title:
            skipped += 1
            continue
        try:
            key = backfill._norm_lookup_key(artist, title)
            paths = index.get(key)
            if not paths:
                skipped += 1
                logger.debug(t("import.ytfix.not_on_disk", artist=artist, title=title))
                continue
            path = backfill._select_year_target(paths)
            cur_year, _album = tags.read_existing_year_album(path)

            meta = resolve.resolve_metadata(artist, title)
            new_year = (meta.year or meta.album_year or "").strip()
            if not new_year:
                skipped += 1
                logger.debug(t("import.ytfix.no_resolve", artist=artist, title=title))
                continue
            if new_year == cur_year:
                skipped += 1
                continue

            logger.info(t("import.ytfix.entry", artist=artist, title=title,
                          old=cur_year or t("import.metafill.cli_none"),
                          new=new_year, rel=os.path.relpath(path, music_dir)))
            if not dry_run:
                backfill._write_year_tag(path, new_year)
                # Harmless if a marker happens to be present (idempotent).
                tags.clear_charts_source(path)
                backfill._maybe_sleep()
            changed += 1
        except Exception as e:  # a broken song must not abort the run
            errors += 1
            logger.debug(t("import.ytfix.file_error", artist=artist, title=title,
                           error=e))

    logger.info(t("import.ytfix.summary", total=total, changed=changed,
                  skipped=skipped, errors=errors))
    if dry_run:
        logger.info(t("import.metafill.cli_dry_notice"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
