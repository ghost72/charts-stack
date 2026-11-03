"""
Trash (Papierkorb) manager for the Manual Tag Editor.

Lists the MUSIC_DIR/.charts-trash/ tree (the stack's single reversible safety
stage: the upgrade/replace path, the manual dedup runs and the FLAC->MP3 convert
move files here instead of hard-deleting them), permanently deletes a single
trashed file, and empties the whole trash.

The delete/empty actions are the MOST destructive operations in the whole stack
(they hard-delete files that are already at the last reversible stage), so the
security scoping is the central concern here:

  * _guarded_trash_realpath delegates to the UNCHANGED editor._guarded_realpath
    (realpath inside MUSIC_DIR) and then narrows to require the resolved path to
    sit strictly INSIDE MUSIC_DIR/.charts-trash. A real library file outside the
    trash can therefore NEVER be deleted through these routes, even if a bug or a
    crafted request passes a non-trash path, and a symlink that resolves out of
    the trash is refused (realpath fully resolves symlinks + '..').
  * empty_trash requires an explicit confirm sentinel server-side and never
    follows a symlink out of the trash.

Mirrors editor.py/convert.py's thin-route contract: every helper raises only
editor.EditorError (bare .code + .status), which main._editor_error maps to the
localized config.tageditor.<code> message. No FastAPI/HTTP objects leak here.

This module is charts-config-only and introduces no cross-service duplication:
its guard delegates to editor._guarded_realpath rather than copying it, so the
repo-root shared-helper parity test needs no change.
"""

import logging
import os
import shutil
import threading
import time

from . import config, editor
from .i18n import t

logger = logging.getLogger(__name__)

# Cap on the number of listed entries. The trash can in principle hold thousands
# of files; this bounds the row list (mirrors the non-MP3 list's truncation
# contract). total_bytes/count still reflect the WHOLE tree (summed during the
# single walk, before truncation) so the headline space figure is honest even
# when the list is truncated.
TRASH_LIST_LIMIT = 2000

# Ignorable sidecar directory names skipped while pruning an emptied subfolder
# (mirrors library._CLEANUP_IGNORE; a folder holding only these counts as empty).
_CLEANUP_IGNORE = {"@eaDir", "#recycle", ".DS_Store"}


# --- trash-scoped realpath guard (the security core) ------------------------

def _trash_root() -> str:
    """
    The real path of the trash root (MUSIC_DIR/.charts-trash), computed fresh so
    a re-pointed MUSIC_DIR (tests) is honored. realpath so a symlinked MUSIC_DIR
    still compares against the resolved delete-path.
    """
    return os.path.realpath(os.path.join(config.MUSIC_DIR, ".charts-trash"))


def _guarded_trash_realpath(path: str) -> str:
    """
    Resolve `path` and require the realpath to sit strictly INSIDE
    MUSIC_DIR/.charts-trash. Defense in depth: first run the EXISTING
    editor._guarded_realpath (realpath inside MUSIC_DIR, unchanged), THEN add the
    trash-root containment check. Raises editor.EditorError:
      - "path_outside" (400) when outside MUSIC_DIR (delegated, unchanged), or
      - "trash_path_outside" (400) when inside MUSIC_DIR but NOT inside the trash
        root (a non-trash library path, or a traversal/symlink that resolves back
        into the library).
    Never raises on existence; callers check os.path.isfile separately.
    """
    real = editor._guarded_realpath(path)          # inside MUSIC_DIR or raises
    root = _trash_root()
    if real != root and not real.startswith(root + os.sep):
        raise editor.EditorError("trash_path_outside", 400)
    return real


# --- size formatting --------------------------------------------------------

def _human_size(n: int) -> str:
    """
    Binary-prefixed human size: '0 B', '512 B', '7.7 MB', '1.3 GB'. Uses 1024
    steps and B/KB/MB/GB/TB labels (the units users expect on a NAS disk-usage
    figure). The numeric+unit string is produced once server-side and shown
    verbatim; the ASCII units are universal so they are not routed through i18n
    (consistent with read_audio_info returning a language-neutral bitrate number).
    Pure; never raises; clamps negatives to 0.
    """
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = 0
    if n < 0:
        n = 0
    units = ("B", "KB", "MB", "GB", "TB")
    size = float(n)
    idx = 0
    while size >= 1024 and idx < len(units) - 1:
        size /= 1024.0
        idx += 1
    if idx == 0:
        # Whole bytes: no decimal place.
        return f"{int(size)} {units[idx]}"
    return f"{size:.1f} {units[idx]}"


# --- lazily-built, TTL-cached trash scan ------------------------------------
# A module-level cache mirroring library.build_search_index's idiom (lock +
# monotonic timestamp + TTL + lazy rebuild) but scoped to the OPPOSITE walk:
# only .charts-trash, EVERY file (audio + non-audio sidecars), never descending
# into the rest of the library. Kept out of library.py so the two walks never
# interfere and library.py's existing pruning invariants stay untouched.

_TRASH_LOCK = threading.Lock()
_cache: dict | None = None       # {"entries","total_bytes","count","truncated"}
_cache_dir: str | None = None    # the trash root the cache was built for
_cache_ts: float = 0.0


def _build_scan() -> dict:
    """
    One bounded os.walk over the trash root collecting a record per file plus the
    running total/count. followlinks=False so the walk never traverses INTO a
    symlinked directory (consistent with the delete guard). Fail-open: an
    absent/unreadable trash root yields an empty result.

    Each entry: {rel_path (POSIX, relative to the trash root, starting with the
    source subfolder), ext (lowercased), format (ext[1:].upper() or '?'), bytes,
    size_human}. total_bytes/count reflect the WHOLE tree even when the entry
    list is truncated to TRASH_LIST_LIMIT.
    """
    root = _trash_root()
    entries: list[dict] = []
    total_bytes = 0
    count = 0
    truncated = False
    if not os.path.isdir(root):
        return {"entries": [], "total_bytes": 0, "count": 0, "truncated": False}
    try:
        for dirpath, _dirs, files in os.walk(root, followlinks=False):
            for name in files:
                full = os.path.join(dirpath, name)
                try:
                    size = os.path.getsize(full)
                except OSError:
                    size = 0  # fail-open: still list it, size 0
                total_bytes += size
                count += 1
                if len(entries) >= TRASH_LIST_LIMIT:
                    truncated = True
                    continue  # keep summing totals, stop appending rows
                rel = os.path.relpath(full, root).replace(os.sep, "/")
                ext = os.path.splitext(name)[1].lower()
                entries.append({
                    "rel_path": rel,
                    "ext": ext,
                    "format": ext[1:].upper() if ext else "?",
                    "bytes": size,
                    "size_human": _human_size(size),
                    # Absolute real path for the delete route; never displayed.
                    "path": full,
                })
    except OSError:
        # Partial walk failure: return what was gathered so far (fail-open).
        pass
    # Stable, scannable order: sort by rel_path case-insensitively.
    entries.sort(key=lambda e: e["rel_path"].lower())
    return {"entries": entries, "total_bytes": total_bytes,
            "count": count, "truncated": truncated}


def scan_trash() -> dict:
    """
    Return the cached {entries,total_bytes,count,truncated} for the current trash
    root, rebuilding when missing, older than config.TAGEDITOR_INDEX_TTL, or built
    for a different root. Thread-safe. Fail-open: an absent/unreadable trash root
    yields an empty scan.
    """
    global _cache, _cache_dir, _cache_ts
    root = _trash_root()
    now = time.monotonic()
    with _TRASH_LOCK:
        fresh = (_cache is not None and _cache_dir == root
                 and (now - _cache_ts) < config.TAGEDITOR_INDEX_TTL)
        if not fresh:
            _cache = _build_scan()
            _cache_dir = root
            _cache_ts = now
        return _cache


def invalidate() -> None:
    """Drop the cache so the next scan re-walks (called after delete/empty)."""
    global _cache, _cache_dir, _cache_ts
    with _TRASH_LOCK:
        _cache = None
        _cache_dir = None
        _cache_ts = 0.0


# --- delete a single trashed file -------------------------------------------

def delete_file(path: str) -> dict:
    """
    Permanently delete ONE file inside the trash. Guards the path strictly inside
    the trash root (path_outside / trash_path_outside), requires an existing
    regular file (not_found, 404), removes it (trash_delete_failed, 500 on OSError),
    invalidates the cache and best-effort prunes the now-empty source subfolder
    chain (confined to the trash root; the trash root itself is always kept).
    Returns {"deleted": "<rel_path>"} (rel-to-trash display path). Raises
    editor.EditorError on guard/existence/remove failure.
    """
    real = _guarded_trash_realpath(path)
    if not os.path.isfile(real):
        raise editor.EditorError("not_found", 404)
    root = _trash_root()
    rel = os.path.relpath(real, root).replace(os.sep, "/")
    try:
        os.remove(real)
    except OSError as e:
        logger.warning(t("config.tageditor.log.trash_delete_failed",
                                path=real, error=str(e)))
        raise editor.EditorError("trash_delete_failed", 500)
    invalidate()
    _prune_up_to_root(os.path.dirname(real), root)
    return {"deleted": rel}


def _prune_up_to_root(start_dir: str, root: str) -> None:
    """
    Remove now-empty subfolders from `start_dir` up toward (but NEVER including)
    the trash `root`, deepest-first. A folder holding only ignorable sidecars
    (@eaDir/#recycle/.DS_Store) counts as empty: the sidecars are removed, then
    the folder. Fail-open: never raises; a folder that still holds real content
    stops the climb. Confined to the trash root (stops once the parent IS root or
    escapes it).
    """
    d = start_dir
    while True:
        # Stop at/above the trash root: the root dir is always kept, and we never
        # climb into the library above it.
        if not d or d == root or not d.startswith(root + os.sep):
            return
        try:
            scan = list(os.scandir(d))
        except OSError:
            return
        if any(e.name not in _CLEANUP_IGNORE for e in scan):
            return  # holds real content -> keep, stop climbing
        for e in scan:
            try:
                if e.is_dir(follow_symlinks=False):
                    shutil.rmtree(e.path)
                else:
                    os.remove(e.path)
            except OSError:
                pass
        try:
            os.rmdir(d)
        except OSError:
            return
        d = os.path.dirname(d)


# --- empty the whole trash --------------------------------------------------

def empty_trash(confirm: str) -> dict:
    """
    Permanently remove the CONTENTS of the trash root (keeping the root dir
    itself). Requires confirm == "empty" (else EditorError trash_confirm_required,
    400, and NOTHING is removed). Never follows a symlink out of the trash: each
    top-level entry is re-checked to be inside the trash root, a symlink is
    unlinked (never followed into its target), a directory is rmtree'd (bounded to
    its subtree; rmtree unlinks symlinks rather than following them). Fail-open per
    entry so one locked file does not abort the whole empty. Returns
    {"removed": n, "failed": m}.
    """
    if confirm != "empty":
        raise editor.EditorError("trash_confirm_required", 400)
    root = _trash_root()
    removed = 0
    failed = 0
    if not os.path.isdir(root):
        # Nothing to empty; still a successful no-op.
        invalidate()
        return {"removed": 0, "failed": 0}
    try:
        top = list(os.scandir(root))
    except OSError as e:
        logger.warning(t("config.tageditor.log.trash_delete_failed",
                                path=root, error=str(e)))
        invalidate()
        return {"removed": 0, "failed": 1}
    for entry in top:
        try:
            if entry.is_symlink():
                # A symlink (file OR dir): unlink the LINK itself only, never
                # follow it into its target. Do not rmtree through it.
                os.remove(entry.path)
                removed += 1
                continue
            # Re-resolve and verify the entry still sits inside the trash root
            # before any destructive action (belt over the scandir result).
            real = os.path.realpath(entry.path)
            if real != root and not real.startswith(root + os.sep):
                failed += 1
                continue
            if entry.is_dir(follow_symlinks=False):
                shutil.rmtree(entry.path)
            else:
                os.remove(entry.path)
            removed += 1
        except OSError as e:
            failed += 1
            logger.warning(t("config.tageditor.log.trash_delete_failed",
                                    path=entry.path, error=str(e)))
    invalidate()
    return {"removed": removed, "failed": failed}
