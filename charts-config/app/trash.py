"""
Trash (Papierkorb) manager for the Manual Tag Editor.

Lists the MUSIC_DIR/.charts-trash/ tree (the stack's single reversible safety
stage: the upgrade/replace path, the manual dedup runs and the FLAC->MP3 convert
move files here instead of hard-deleting them), permanently deletes a single
trashed file, empties the whole trash, and RESTORES a single Tag-Editor-deleted
file back to its original library location.

Restore is deliberately SCOPED: only rows under tageditor-delete-<date>/ that
still carry their per-file restore sidecar (written by editor.delete_song) are
restorable, because that is the one flow where charts-config both preserves the
full library-relative path and owns the manifest relationship. The collision
policy is REFUSE (never overwrite a file that already sits at the target).

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

import json
import logging
import os
import shutil
import threading
import time

from . import config, editor, library, manifest, tags
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

# The only trash subfolder prefix whose rows get the green restore button.
# Scope (a): restore is limited to the Tag-Editor delete flow, the ONE flow that
# both preserves the full library-relative path AND writes a restore sidecar.
# Convert (flac2mp3-), dedup (dedup-) and charts-import upgrade/replace
# (<YYYYMMDD>/<basename>) rows are deliberately NOT restorable (ambiguous target
# and/or no sidecar) and keep only their red delete button.
_RESTORE_LABEL_PREFIX = "tageditor-delete-"


def _is_restorable(rel: str, full: str) -> bool:
    """
    True only for a Tag-Editor-delete row (top folder starts with
    tageditor-delete-) that still carries its restore sidecar
    ("<full>" + editor.RESTORE_SIDECAR_SUFFIX). Pre-feature deletions have no
    sidecar and are therefore not restorable. Fail-closed: any check that is not
    unambiguously yes returns False.
    """
    top = rel.split("/", 1)[0] if rel else ""
    if not top.startswith(_RESTORE_LABEL_PREFIX):
        return False
    return os.path.isfile(full + editor.RESTORE_SIDECAR_SUFFIX)


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
    size_human, restorable (bool)}. total_bytes/count reflect the WHOLE tree even
    when the entry list is truncated to TRASH_LIST_LIMIT.

    The per-file restore sidecars (editor.RESTORE_SIDECAR_SUFFIX) are internal
    bookkeeping: they are SKIPPED here (neither listed nor counted nor summed) so
    they never show up as their own trashed row. `restorable` is True only for a
    row that both lives under a tageditor-delete-<date>/ folder AND has its
    restore sidecar present (so the green button only appears where restore can
    actually put the file back + re-create the manifest entry).
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
                # Internal restore sidecars are never a trash row of their own.
                if name.endswith(editor.RESTORE_SIDECAR_SUFFIX):
                    continue
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
                    # Restorable only for Tag-Editor-delete rows that still carry
                    # their restore sidecar (scope (a); see _is_restorable).
                    "restorable": _is_restorable(rel, full),
                    # Absolute real path for the delete/restore routes; never displayed.
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
    # Clean up the file's restore sidecar alongside it (best-effort; a leftover
    # sidecar would otherwise be orphaned and still skipped by the listing).
    try:
        sidecar = real + editor.RESTORE_SIDECAR_SUFFIX
        if os.path.isfile(sidecar):
            os.remove(sidecar)
    except OSError:
        pass
    invalidate()
    _prune_up_to_root(os.path.dirname(real), root)
    return {"deleted": rel}


# --- restore a single Tag-Editor-trashed file -------------------------------

def restore_file(path: str) -> dict:
    """
    Restore ONE Tag-Editor-trashed file back to its original library location
    (the GREEN "Wiederherstellen" action). Scoped to tageditor-delete-<date>/
    rows with a restore sidecar only (see _is_restorable); every other trash
    origin is non-restorable and never reaches here with a usable sidecar.

    Order of operations, fail-closed:
      1. Source guard: _guarded_trash_realpath (realpath inside MUSIC_DIR AND
         strictly inside .charts-trash) + require an existing regular file
         (not_found, 404). No traversal, no restoring from outside the trash.
      2. Read the restore sidecar ("<src>" + RESTORE_SIDECAR_SUFFIX). No sidecar
         (pre-feature deletion / other origin) -> restore_no_sidecar (400): refuse
         rather than guess a target.
      3. Build the target as MUSIC_DIR/<original_rel_path> and guard it with the
         UNCHANGED editor._guarded_realpath (inside MUSIC_DIR), then assert it is
         NOT inside the trash (never "restore" into the trash).
      4. Collision policy = REFUSE: if the target already exists ->
         restore_target_exists (400), nothing moved (the re-download case: the
         correct song may already sit at that path; never clobber it).
      5. Move the file back via library._move_file (recreates a pruned stack-single
         folder, mtime preserved, os.rename). A move OSError -> trash_restore_failed
         (500).
      6. Best-effort post-move: re-insert the manifest entry from the sidecar
         (manifest.add_entry) so charts-import recognizes the song again, remove
         the consumed sidecar, invalidate the cache, prune the emptied trash
         subfolder, and refresh the search index for the restored path.

    Returns {"restored": "<target rel-to-MUSIC_DIR>"}. Raises editor.EditorError
    on guard/existence/sidecar/collision/move failure.
    """
    real = _guarded_trash_realpath(path)
    if not os.path.isfile(real):
        raise editor.EditorError("not_found", 404)

    sidecar = real + editor.RESTORE_SIDECAR_SUFFIX
    info = _read_restore_sidecar(sidecar)
    if info is None:
        raise editor.EditorError("restore_no_sidecar", 400)
    original_rel = (info.get("original_rel_path") or "").strip()
    if not original_rel:
        raise editor.EditorError("restore_no_sidecar", 400)

    base = os.path.realpath(config.MUSIC_DIR)
    target = os.path.normpath(os.path.join(base, original_rel))
    # Target guard: must resolve inside MUSIC_DIR (reuse the unchanged guard) and
    # must NOT be inside the trash root. editor._guarded_realpath realpaths a
    # possibly-missing target by resolving its existing ancestors.
    guarded_target = editor._guarded_realpath(target)
    root = _trash_root()
    if guarded_target == root or guarded_target.startswith(root + os.sep):
        raise editor.EditorError("trash_path_outside", 400)

    # Collision = refuse (never overwrite, never keep-both).
    if os.path.exists(target):
        raise editor.EditorError("restore_target_exists", 400)

    try:
        library._move_file(real, target)          # recreates folders, mtime kept
    except OSError as e:
        logger.warning(t("config.tageditor.log.trash_restore_failed",
                                path=real, error=str(e)))
        raise editor.EditorError("trash_restore_failed", 500)

    # Best-effort post-move: re-insert the manifest entry, drop the sidecar,
    # invalidate the cache, prune the emptied trash folder, refresh the index.
    manifest_key = (info.get("manifest_key") or "").lower()
    manifest_entry = info.get("manifest_entry")
    if manifest_key and manifest_entry is not None:
        try:
            manifest.add_entry(config.MANIFEST_FILE, manifest_key, manifest_entry)
        except Exception:
            pass
    try:
        if os.path.isfile(sidecar):
            os.remove(sidecar)
    except OSError:
        pass
    invalidate()
    _prune_up_to_root(os.path.dirname(real), root)
    try:
        fields = tags.read_editable_tags(target)
        # add_index_entry (not update_index_entry): the delete removed this path
        # from the cached search index, so update would be a no-op (it only
        # overlays an existing record). add upserts it back so a Tag-Editor
        # search right after restore finds the song, not only after the TTL.
        library.add_index_entry(target, fields.get("artist", ""),
                                fields.get("title", ""))
    except Exception:
        pass
    return {"restored": os.path.relpath(target, base).replace(os.sep, "/")}


def _read_restore_sidecar(sidecar: str) -> dict | None:
    """
    Load and return the restore sidecar dict at `sidecar`, or None when it is
    absent / unreadable / malformed (fail-closed: a bad sidecar is treated as
    "no sidecar", so restore refuses rather than guessing a target).
    """
    try:
        with open(sidecar, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


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
