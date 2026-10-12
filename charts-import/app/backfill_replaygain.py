"""
Einmal-Skript: ReplayGain-Tags fuer die bestehenden Chart-Songs nachruesten.

Geht NUR ueber die Chart-Songs (die in den charts-sync-Playlists .m3u gelisteten
Dateien, PLAYLIST_DIR) - NICHT ueber die ganze Library, damit deine uebrige
(Lidarr-)Sammlung unangetastet bleibt.

Nutzung (im Container):
  python -m app.backfill_replaygain --dry-run   # nur zaehlen, was fehlt
  python -m app.backfill_replaygain             # ReplayGain berechnen + schreiben
  python -m app.backfill_replaygain --overwrite # auch vorhandene neu berechnen

Umgebungsvariablen:
  PLAYLIST_DIR   Ordner mit den charts-sync-Playlists (.m3u). Default MUSIC_DIR.
  MUSIC_DIR      Library-Root (zum Aufloesen relativer Playlist-Pfade).

Braucht ffmpeg im Image. Fehler bei einer Datei stoppen den Lauf nicht.
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
