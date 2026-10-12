"""
One-off script: retrofit ReplayGain tags for the existing chart songs.

Goes ONLY over the chart songs (the files listed in the charts-sync playlists
.m3u, PLAYLIST_DIR) - NOT over the whole library, so your rest of the (Lidarr)
collection stays untouched.

Usage (in the container):
  python -m app.backfill_replaygain --dry-run   # only count what is missing
  python -m app.backfill_replaygain             # compute + write ReplayGain
  python -m app.backfill_replaygain --overwrite # recompute existing ones too

Environment variables:
  PLAYLIST_DIR   folder with the charts-sync playlists (.m3u). Default MUSIC_DIR.
  MUSIC_DIR      library root (to resolve relative playlist paths).

Needs ffmpeg in the image. Errors on one file do not stop the run.
"""

import logging
import os
import sys

from . import config, replaygain
from .backfill_covers import _iter_playlist_mp3
from .i18n import t

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backfill-replaygain")


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    overwrite = "--overwrite" in sys.argv

    if not replaygain.available():
        logger.error(t("import.bf_rg.no_ffmpeg"))
        return

    mode = t("import.bf_rg.mode_dry") if dry_run else \
           (t("import.bf_rg.mode_overwrite") if overwrite else t("import.bf_rg.mode_missing"))
    logger.info(t("import.bf_rg.started", path=config.PLAYLIST_DIR, mode=mode))

    total = 0
    done = 0
    skipped = 0
    failed = 0
    for path in _iter_playlist_mp3(config.PLAYLIST_DIR, config.MUSIC_DIR):
        if not path.lower().endswith(".mp3"):
            continue
        total += 1
        rel = os.path.relpath(path, config.MUSIC_DIR)

        has_rg = replaygain._has_replaygain(path)
        if has_rg and not overwrite:
            skipped += 1
            continue

        if dry_run:
            logger.info(t("import.bf_rg.dry_entry", rel=rel))
            continue

        if replaygain.apply_track_gain(path, overwrite=overwrite):
            done += 1
            logger.info(t("import.bf_rg.set", count=done, rel=rel))
        else:
            failed += 1
            logger.info(t("import.bf_rg.no_measure", rel=rel))

    logger.info(t("import.bf_rg.summary", total=total, done=done,
                  skipped=skipped, failed=failed))
    if dry_run:
        logger.info(t("import.bf_rg.dry_notice"))


if __name__ == "__main__":
    main()
