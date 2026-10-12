"""
Cleanup logic for the downloads folder.

Soulseek/slskd has no seed counter like BitTorrent ("delete after N uploads").
More important than the upload amount is the NUMBER of shared files anyway:
sharing too few files makes you a "leecher" and gets you throttled.

Hence there are two strategies (KEEP_LATEST takes precedence):

  1. KEEP_LATEST (count-based, recommended):
     Always keep the N newest downloads, delete only the excess. The share
     stays stable at ~N files, regardless of how often you manually search for
     new songs.

  2. RETENTION_DAYS (age-based, fallback):
     Delete downloads older than N days. Only active when KEEP_LATEST = 0.

After deletion, empty subfolders in DOWNLOADS_DIR are removed.
"""

import logging
import os
import time

from . import config
from .i18n import t

logger = logging.getLogger(__name__)


def _iter_files_with_mtime(root: str) -> list[tuple[str, float]]:
    """Returns all files under root as (path, mtime), incl. subfolders."""
    result: list[tuple[str, float]] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            path = os.path.join(dirpath, name)
            try:
                result.append((path, os.path.getmtime(path)))
            except OSError:
                continue
    return result


def _remove_empty_dirs(root: str) -> None:
    """Removes empty subfolders below root (root itself stays)."""
    for dirpath, _dirnames, _filenames in os.walk(root, topdown=False):
        if os.path.abspath(dirpath) == os.path.abspath(root):
            continue
        try:
            if not os.listdir(dirpath):
                os.rmdir(dirpath)
                logger.info(t("import.cleanup.empty_dir_removed", path=dirpath))
        except OSError as e:
            logger.debug(t("import.cleanup.dir_not_removed", path=dirpath, error=e))


def _cleanup_keep_latest(keep: int) -> list[str]:
    """Keeps the 'keep' newest files, deletes all older ones."""
    files = _iter_files_with_mtime(config.DOWNLOADS_DIR)
    if len(files) <= keep:
        logger.debug(t("import.cleanup.below_keep", count=len(files), keep=keep))
        return []

    # Newest first; everything from index 'keep' onward is deleted.
    files.sort(key=lambda item: item[1], reverse=True)
    to_delete = files[keep:]

    removed: list[str] = []
    for path, _mtime in to_delete:
        try:
            os.remove(path)
            removed.append(path)
            logger.info(t("import.cleanup.excess_removed", keep=keep, path=path))
        except OSError as e:
            logger.error(t("import.cleanup.delete_failed", path=path, error=e))
    return removed


def _cleanup_by_age(days: int) -> list[str]:
    """Deletes files older than 'days' days."""
    cutoff = time.time() - days * 86400
    removed: list[str] = []
    for path, mtime in _iter_files_with_mtime(config.DOWNLOADS_DIR):
        if mtime < cutoff:
            try:
                os.remove(path)
                removed.append(path)
                logger.info(t("import.cleanup.old_removed", days=days, path=path))
            except OSError as e:
                logger.error(t("import.cleanup.delete_failed", path=path, error=e))
    return removed


def cleanup_downloads() -> list[str]:
    """
    Tidies up DOWNLOADS_DIR according to the active strategy.
    Returns: list of the deleted file paths.
    """
    # Clean the trash (replaced upgrade versions) independently of the downloads
    # folder - even when KEEP_LATEST=all.
    cleanup_trash(config.TRASH_RETENTION_DAYS)

    # Clean the quarantine (_rejected) independently. Rejected files are NOT
    # covered by the KEEP_LATEST cleanup (the _rejected folder is exempt), so
    # they would otherwise accumulate unbounded. The song is reloaded
    # better/anew anyway - the quarantine is only a short-lived buffer.
    cleanup_rejected(config.REJECTED_RETENTION_DAYS)

    if not os.path.isdir(config.DOWNLOADS_DIR):
        return []

    if config.KEEP_LATEST == -1:
        logger.debug(t("import.cleanup.keep_all"))
        return []
    elif config.KEEP_LATEST > 0:
        removed = _cleanup_keep_latest(config.KEEP_LATEST)
    elif config.RETENTION_DAYS > 0:
        removed = _cleanup_by_age(config.RETENTION_DAYS)
    else:
        logger.debug(t("import.cleanup.none_active"))
        return []

    _remove_empty_dirs(config.DOWNLOADS_DIR)
    return removed


def cleanup_trash(days: int) -> int:
    """
    Deletes files in the upgrade trash (MUSIC_DIR/.charts-trash/) older than
    'days' days and cleans up date subfolders that became empty.
    days <= 0 -> disabled (trash stays, empty manually).
    Returns: number of deleted files.
    """
    if days <= 0:
        return 0
    trash_root = os.path.join(config.MUSIC_DIR, ".charts-trash")
    if not os.path.isdir(trash_root):
        return 0

    cutoff = time.time() - days * 86400
    removed = 0
    for path, mtime in _iter_files_with_mtime(trash_root):
        if mtime < cutoff:
            try:
                os.remove(path)
                removed += 1
            except OSError as e:
                logger.error(t("import.cleanup.trash_delete_failed", path=path, error=e))
    if removed:
        logger.info(t("import.cleanup.trash_cleaned", count=removed, days=days))
    _remove_empty_dirs(trash_root)
    return removed


def cleanup_rejected(days: int) -> int:
    """
    Deletes files in the quarantine (REJECTED_DIR) older than 'days' days and
    cleans up the folder when it becomes empty.

    Rejected files (too short, remix, fake bitrate ...) are moved here by the
    gatekeeper and otherwise NEVER deleted - the _rejected folder is exempt from
    the KEEP_LATEST cleanup. Without this cleanup the quarantine grows unbounded
    (a disk-space eater). The affected song is reloaded better/anew anyway; the
    sorted-out file is not needed. days <= 0 -> disabled. Returns: number of
    deleted files.
    """
    if days <= 0:
        return 0
    rejected_root = config.REJECTED_DIR
    if not os.path.isdir(rejected_root):
        return 0

    cutoff = time.time() - days * 86400
    removed = 0
    for path, mtime in _iter_files_with_mtime(rejected_root):
        if mtime < cutoff:
            try:
                os.remove(path)
                removed += 1
            except OSError as e:
                logger.error(t("import.cleanup.quarantine_delete_failed", path=path, error=e))
    if removed:
        logger.info(t("import.cleanup.quarantine_cleaned", count=removed, days=days))
    _remove_empty_dirs(rejected_root)
    return removed
