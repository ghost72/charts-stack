"""
Aufraeum-Logik fuer den Downloads-Ordner.

Soulseek/slskd kennt keinen Seed-Zaehler wie BitTorrent ("nach N Uploads
loeschen"). Wichtiger als die Upload-Menge ist ohnehin die ANZAHL der geteilten
Dateien: Wer zu wenige Dateien teilt, gilt als "Leecher" und wird ausgebremst.

Deshalb gibt es zwei Strategien (KEEP_LATEST hat Vorrang):

  1. KEEP_LATEST (Anzahl-basiert, empfohlen):
     Behalte immer die N neuesten Downloads, loesche nur den Ueberschuss. Der
     Share bleibt stabil bei ~N Dateien, unabhaengig davon, wie oft du manuell
     neue Songs suchst.

  2. RETENTION_DAYS (Alter-basiert, Fallback):
     Loesche Downloads, die aelter als N Tage sind. Nur aktiv, wenn
     KEEP_LATEST = 0.

Nach dem Loeschen werden leere Unterordner im DOWNLOADS_DIR entfernt.
"""

import logging
import os
import time

from . import config
from .i18n import t

logger = logging.getLogger(__name__)


def _iter_files_with_mtime(root: str) -> list[tuple[str, float]]:
    """Liefert alle Dateien unter root als (Pfad, mtime), inkl. Unterordner."""
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
    """Entfernt leere Unterordner unterhalb von root (root selbst bleibt)."""
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
    """Behaelt die 'keep' neuesten Dateien, loescht alle aelteren."""
    files = _iter_files_with_mtime(config.DOWNLOADS_DIR)
    if len(files) <= keep:
        logger.debug(t("import.cleanup.below_keep", count=len(files), keep=keep))
        return []

    # Neueste zuerst; alles ab Index 'keep' wird geloescht.
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
    """Loescht Dateien, die aelter als 'days' Tage sind."""
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
    Raeumt den DOWNLOADS_DIR nach der aktiven Strategie auf.
    Rueckgabe: Liste der geloeschten Dateipfade.
    """
    # Papierkorb (ersetzte Upgrade-Versionen) unabhaengig vom Downloads-Ordner
    # bereinigen - auch wenn KEEP_LATEST=all ist.
    cleanup_trash(config.TRASH_RETENTION_DAYS)

    # Quarantaene (_rejected) unabhaengig bereinigen. Abgelehnte Dateien werden
    # NICHT vom KEEP_LATEST-Aufraeumen erfasst (der _rejected-Ordner ist davon
    # ausgenommen), sammeln sich also sonst unbegrenzt an. Der Song wird ohnehin
    # neu/besser nachgeladen - die Quarantaene ist nur ein kurzlebiger Puffer.
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
    Loescht Dateien im Upgrade-Papierkorb (MUSIC_DIR/.charts-trash/), die aelter
    als 'days' Tage sind, und raeumt leer gewordene Datums-Unterordner auf.
    days <= 0 -> deaktiviert (Papierkorb bleibt, manuell leeren).
    Rueckgabe: Anzahl geloeschter Dateien.
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
    Loescht Dateien in der Quarantaene (REJECTED_DIR), die aelter als 'days'
    Tage sind, und raeumt den Ordner auf, wenn er leer wird.

    Abgelehnte Dateien (zu kurz, Remix, Fake-Bitrate ...) werden vom
    Tuersteher hierhin verschoben und sonst NIE geloescht - der _rejected-Ordner
    ist vom KEEP_LATEST-Aufraeumen ausgenommen. Ohne diese Bereinigung waechst
    die Quarantaene unbegrenzt (Speicherplatz-Fresser). Der betroffene Song wird
    ohnehin neu/besser nachgeladen; die aussortierte Datei wird nicht gebraucht.
    days <= 0 -> deaktiviert. Rueckgabe: Anzahl geloeschter Dateien.
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
