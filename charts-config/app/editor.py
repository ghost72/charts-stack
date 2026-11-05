"""
Orchestration for the Manual Tag Editor (search / load_song / save_song).

Keeps main.py thin (mirrors how collector.py holds the finder logic). The routes
(FEAT-003) call these functions and map an EditorError's .code + .status onto the
localized JSON error response; this module itself raises no HTTP objects and
stays import-clean without FastAPI.

Contract the routes rely on:
  * search(q, limit) -> list of result dicts
        {path, artist, title, album, origin} with origin in {"stack","curated"}.
  * load_song(path) -> song dict (see _reload) or raises EditorError.
  * save_song(path, submitted_fields, cover_url, cover_file) -> song dict
        or raises EditorError. Implements the 9-step save algorithm.

EditorError.code is one of (the route maps each to a localized message key):
  path_outside (400), not_found (400/404), mp3_only (400), bad_year (400),
  bad_track (400), cover_not_image (400), cover_fetch_failed (400),
  verify_failed (500), write_failed (500). Auth (step 1) is enforced by the
  route, not here.

Fail-open on reads, fail-closed on writes. mtime preservation is a hard
invariant: every sub-write of a save runs inside ONE outer preserve_mtime block.
"""

import base64
import errno
import logging
import os
from datetime import datetime, timezone

from . import config, cover, library, manifest, tags
from .i18n import t

logger = logging.getLogger(__name__)

# Max length for the free-text fields after trim.
_MAX_TEXT = 512


class EditorError(Exception):
    """A recoverable editor failure carrying a stable code + HTTP status.

    The code is a bare signal string (e.g. "path_outside"); the route maps it to
    the localized message key config.tageditor.<code> and the given status.
    """

    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.code = code
        self.status = status


# --- path guard -------------------------------------------------------------

def _guarded_realpath(path: str) -> str:
    """
    Resolve `path` via os.path.realpath and require it to sit INSIDE
    config.MUSIC_DIR (hard path-traversal guard for the client-supplied path).
    Raises EditorError('path_outside', 400) otherwise. Does NOT check existence.
    """
    base = os.path.realpath(config.MUSIC_DIR)
    real = os.path.realpath(path or "")
    if real != base and not real.startswith(base + os.sep):
        raise EditorError("path_outside", 400)
    return real


def _rel_to_music(path: str) -> str:
    """
    Display-only library-relative path for the read-only file-info field.

    Fail-closed for the DISPLAY string: realpath-resolve the input and run the
    SAME inside-MUSIC_DIR check _guarded_realpath uses. When the real path is
    inside MUSIC_DIR it returns os.path.relpath(real, MUSIC_DIR); when it is NOT
    (which should never happen on the normal post-guard call graph) it returns
    ONLY os.path.basename(path) - never a leading path - so this security-
    sensitive screen can never leak a non-library location. It is NOT a guard
    (it never raises); it is the display formatter for an already-guarded path.
    """
    base = os.path.realpath(config.MUSIC_DIR)
    real = os.path.realpath(path or "")
    if real == base or real.startswith(base + os.sep):
        return os.path.relpath(real, base)
    return os.path.basename(path or "")


# --- normalization (the single comparison/verify basis) ---------------------

def _norm_field(name: str, value: str) -> str:
    """
    Normalize one field to the representation used for BOTH the changed-set
    comparison (step 4) and the write-then-verify (step 8):
      artist/title/album -> trimmed string;
      year -> leading 4 digits (else "");
      track -> leading positive integer as a string (else "").
    """
    v = (value or "").strip()
    if name == "year":
        return tags._leading_year(v)
    if name == "track":
        return tags._leading_track(v)
    return v


# --- search + load ----------------------------------------------------------

def search(q: str, limit: int = 20) -> list[dict]:
    """
    Autocomplete search delegating to library.search, then classify origin only
    for the returned (<= limit) paths. Fail-open: returns [] on any failure.
    """
    results = library.search(q, limit)
    if not results:
        return []
    entries = manifest.load_entries(config.MANIFEST_FILE)
    out = []
    for rec in results:
        out.append({
            "path": rec["path"],
            "artist": rec["artist"],
            "title": rec["title"],
            "album": rec["album"],
            "origin": library.classify(rec["path"], entries),
        })
    return out


def _reload(path: str) -> dict:
    """
    Read the authoritative current state of one file for the UI: editable tags,
    is_mp3, origin, a size-bounded cover data: URL (omitted + oversize flag when
    the embedded image exceeds TAGEDITOR_COVER_MAX_BYTES), plus the read-only
    file/bitrate info and the audio play/download availability flags.

    Precondition: `path` MUST already be a _guarded_realpath-validated path
    inside MUSIC_DIR. Every caller (load_song, save_song via _finalize_save /
    the in-place paths, move_after_decision) guards before calling _reload; the
    rel_path display field relies on this (and _rel_to_music fails closed to a
    bare basename as a belt-and-braces backstop if a future caller forgets).
    """
    fields = tags.read_editable_tags(path)
    entries = manifest.load_entries(config.MANIFEST_FILE)
    origin = library.classify(path, entries)
    song = {
        "path": path,
        "artist": fields["artist"],
        "title": fields["title"],
        "album": fields["album"],
        "year": fields["year"],
        "track": fields["track"],
        "origin": origin,
        "is_mp3": fields["is_mp3"],
    }
    song["cover"] = _cover_preview(path)
    # Read-only file/bitrate info for the field under Track (always on).
    info = library.read_audio_info(path)
    song["rel_path"] = _rel_to_music(path)
    song["bitrate_kbps"] = info["bitrate_kbps"]
    song["ext"] = info["ext"]
    song["is_lossless"] = info["is_lossless"]
    # Audio play/download availability. 'audio_enabled' is the server opt-in
    # flag (controls whether ANY play/download control renders); 'playable' is
    # whether the browser can play it (MP3 only) AND the flag is on. When the
    # flag is off the UI renders NO play/download controls at all.
    song["audio_enabled"] = config.TAGEDITOR_AUDIO_ENABLED
    song["playable"] = info["playable"] and config.TAGEDITOR_AUDIO_ENABLED
    return song


def _cover_preview(path: str) -> dict:
    """
    Build the cover preview for the song view: a base64 data: URL, or
    {"present": false} when there is none, or {"present": true, "oversize":
    true} when the embedded image exceeds the size cap (editing still works).
    """
    read = cover.read_embedded_cover(path)
    if not read:
        return {"present": False}
    data, mime = read
    if len(data) > config.TAGEDITOR_COVER_MAX_BYTES:
        return {"present": True, "oversize": True}
    b64 = base64.b64encode(data).decode("ascii")
    return {"present": True, "mime": mime, "data_url": f"data:{mime};base64,{b64}"}


def load_song(path: str) -> dict:
    """
    Load one file's editable state. Guards the path inside MUSIC_DIR
    (path_outside) and requires an existing regular file (not_found, 404).
    """
    real = _guarded_realpath(path)
    if not os.path.isfile(real):
        raise EditorError("not_found", 404)
    return _reload(real)


# --- full-resolution cover download -----------------------------------------
# Audio extensions whose embedded art the cover-download endpoint may read. This
# mirrors the song-file reality (the editor only ever loads audio files) and is
# defence in depth on top of _guarded_realpath, so the endpoint never reads the
# raw bytes of an arbitrary in-library non-audio file.
_COVER_AUDIO_EXTS = frozenset({
    ".mp3", ".flac", ".m4a", ".m4b", ".mp4", ".aac", ".ogg", ".opus",
    ".wav", ".wma", ".aiff", ".aif",
})


def cover_download(path: str) -> tuple[bytes, str]:
    """
    Return (raw_bytes, mime) of the song's FIRST embedded cover in ORIGINAL
    resolution for the download endpoint.

    Deliberately UNLIKE _cover_preview: there is NO size cap, NO re-encoding and
    NO Pillow resize - the whole point of the download is to hand back the full
    original embedded art even when it exceeds TAGEDITOR_COVER_MAX_BYTES (which
    only governs the inline preview). The mime is sniffed from the image bytes
    (JPEG/PNG), defaulting to image/jpeg when present but not sniffable.

    Guards identically to the other /api/tageditor/* reads: realpath inside
    MUSIC_DIR (path_outside), existing regular file (not_found, 404), and an
    audio-extension allowlist (bad_audio_ext). Raises not_found (404) when the
    song carries no embedded cover.
    """
    real = _guarded_realpath(path)
    if not os.path.isfile(real):
        raise EditorError("not_found", 404)
    if os.path.splitext(real)[1].lower() not in _COVER_AUDIO_EXTS:
        raise EditorError("bad_audio_ext", 400)
    read = cover.read_embedded_cover(real)
    if not read:
        raise EditorError("not_found", 404)
    data, mime = read
    return data, mime


# --- delete a loaded song (reversible: move to trash + clear manifest) ------
# Use case: a YouTube/import download landed with a wrong artist name and was
# re-downloaded under the correct name, leaving a duplicate song/folder. The
# Tag-Editor delete button removes that duplicate in ONE click, reversibly.
#
# This is deliberately NOT a hard delete: it mirrors convert.convert_to_mp3's
# established trash convention (move the file into MUSIC_DIR/.charts-trash/
# <label>-<YYYY-MM-DD>/ preserving its library-relative substructure) so the
# original can be restored from the trash. It then clears the file's manifest
# entry (keyed by the lowercased basename, exactly as manifest.record wrote it)
# so charts-import does NOT re-import the just-trashed file on its next run -
# without this step the persistent manifest entry would bring the song back.

def delete_song(path: str) -> dict:
    """
    Move the loaded song at `path` into the trash (reversible) and clear its
    manifest entry. Works for ANY audio file the editor can load (not MP3-only).

    Order of operations, fail-closed before the move:
      1. realpath guard inside MUSIC_DIR (path_outside) - identical to every
         other /api/tageditor/* path (no traversal/absolute escape);
      2. require an existing regular file (not_found, 404);
      3. move it to MUSIC_DIR/.charts-trash/tageditor-delete-<YYYY-MM-DD>/<rel>
         via library._move_file (os.rename, mtime preserved, same filesystem so
         no EXDEV); a unique suffix avoids clobbering a same-day re-delete.
         A move OSError -> EditorError (write_failed, 500) and NOTHING else runs;
      4. ONLY AFTER a successful move: clean up a now-empty stack-single
         <Artist>/<Title>/ folder (never a folder that still holds other files),
         drop the file's manifest entry, and drop its cached index record.

    Returns {"deleted": True, "trashed_to": "<rel path in library>",
             "manifest_cleared": bool}. Raises EditorError on guard/existence/
     move failure (steps 1-3); the post-move cleanup (step 4) is best-effort and
    never turns a successful trash-move into a failure.
    """
    # Steps 1 + 2: guard + existence (fail-closed, before any mutation).
    real = _guarded_realpath(path)
    if not os.path.isfile(real):
        raise EditorError("not_found", 404)

    # Step 3: move the file into the trash (reversible). Mirror the convert
    # path's convention: MUSIC_DIR/.charts-trash/<label>-<date>/<library-rel>.
    base = os.path.realpath(config.MUSIC_DIR)
    rel = os.path.relpath(real, base)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    trash_dir = os.path.join(base, ".charts-trash", f"tageditor-delete-{day}")
    dest = _unique_trash_dest(os.path.join(trash_dir, rel))
    try:
        library._move_file(real, dest)            # os.rename, mtime preserved
    except OSError as e:
        logger.warning(t("import.tags.write_failed", path=real, error=e))
        raise EditorError("write_failed", 500)

    # Step 4 (best-effort, post-move): prune an emptied stack-single folder,
    # clear the manifest entry, drop the cached index record. None of these may
    # turn the already-successful trash-move into an error.
    #
    # Folder cleanup: only for a stack-single <Artist>/<Title>/<Title>.<ext>
    # layout, and only the inner <Title> album folder - never the <Artist> dir
    # (it may legitimately hold other titles) and never a Lidarr/compilation
    # folder (those are left in place). _cleanup_empty_dirs itself refuses to
    # remove a folder that still holds real content, so a shared folder is safe.
    try:
        if library._is_movable_single(base, real):
            library._cleanup_empty_dirs(os.path.dirname(real))
    except Exception:
        pass

    manifest_cleared = False
    try:
        manifest_cleared = manifest.remove_entry(config.MANIFEST_FILE,
                                                 os.path.basename(real))
    except Exception:
        manifest_cleared = False

    try:
        library.remove_index_entry(real)
    except Exception:
        pass

    return {
        "deleted": True,
        "trashed_to": _rel_to_music(dest),
        "manifest_cleared": manifest_cleared,
    }


def _unique_trash_dest(dest: str) -> str:
    """
    Return a trash destination path that does not yet exist: `dest` if free, else
    `name (2).ext`, `name (3).ext`, ... so a same-day re-delete of a re-created
    file never overwrites an earlier trashed copy. Mirrors convert._unique.
    """
    if not os.path.exists(dest):
        return dest
    root, ext = os.path.splitext(dest)
    n = 2
    while True:
        candidate = f"{root} ({n}){ext}"
        if not os.path.exists(candidate):
            return candidate
        n += 1


# --- save (the 9-step algorithm) --------------------------------------------

def save_song(path: str, submitted_fields: dict, cover_url: str = "",
              cover_file: bytes | None = None) -> dict:
    """
    Implement the 9-step save algorithm. Returns the fresh reloaded song state
    on success; raises EditorError on any guard/validation/write failure
    (nothing is modified on a pre-write failure). (Step 1, auth, is the route's.)
    """
    # Step 2: realpath + exists guard, reject non-.mp3.
    real = _guarded_realpath(path)
    if not os.path.isfile(real):
        raise EditorError("not_found", 404)
    if not real.lower().endswith(".mp3"):
        raise EditorError("mp3_only", 400)

    # Step 3: read current state.
    current = tags.read_editable_tags(real)

    # Step 5 (validate first, fail-closed): a NON-EMPTY submitted year/track
    # that cannot be parsed is garbage and is rejected BEFORE any write - even
    # though its normalized form would be "". An empty submitted value is a
    # legal "leave unchanged", so it is not validated here. Text length is
    # checked on the trimmed submitted value.
    for name in ("artist", "title", "album"):
        if name in submitted_fields and len((submitted_fields[name] or "").strip()) > _MAX_TEXT:
            raise EditorError("too_long", 400)
    if (submitted_fields.get("year") or "").strip():
        y = (submitted_fields["year"]).strip()
        if not (len(y) == 4 and y.isdigit() and 1000 <= int(y) <= 2999):
            raise EditorError("bad_year", 400)
    if (submitted_fields.get("track") or "").strip():
        traw = (submitted_fields["track"]).strip()
        head = traw.split("/", 1)[0].strip()
        if not (head.isdigit() and int(head) > 0):
            raise EditorError("bad_track", 400)

    # Step 4: compute the write set by comparing NORMALIZED representations. An
    # empty submitted year/track leaves the field unchanged (never remove a
    # value); cosmetic-only diffs are not changes.
    write_fields: dict = {}
    for name in ("artist", "title", "album", "year", "track"):
        if name not in submitted_fields:
            continue
        submitted_norm = _norm_field(name, submitted_fields.get(name))
        # Empty submitted year/track -> unchanged (SET-only, never removes).
        if name in ("year", "track") and submitted_norm == "":
            continue
        current_norm = _norm_field(name, current.get(name, ""))
        if submitted_norm != current_norm:
            write_fields[name] = submitted_norm

    # Step 6: obtain + validate cover bytes BEFORE opening the write block.
    cover_bytes = None
    cover_mime = None
    has_cover = bool((cover_url or "").strip()) or bool(cover_file)
    if has_cover:
        if cover_file:
            if len(cover_file) > config.TAGEDITOR_COVER_MAX_BYTES:
                raise EditorError("cover_fetch_failed", 400)
            mime = cover._sniff_image_mime(cover_file)
            if not mime:
                raise EditorError("cover_not_image", 400)
            raw, cover_mime = cover_file, mime
        else:
            fetched = cover.download_cover(cover_url)
            if not fetched:
                raise EditorError("cover_fetch_failed", 400)
            raw, cover_mime = fetched
        squared = cover._make_square(raw)
        if not squared:
            raise EditorError("cover_not_image", 400)
        cover_bytes = squared
        # _make_square re-encodes a rectangular image to JPEG; a squared JPEG
        # keeps its mime, a squared PNG that was cropped becomes JPEG. Sniff the
        # final bytes so the APIC mime matches the stored data.
        cover_mime = cover._sniff_image_mime(cover_bytes) or cover_mime

    # Nothing to do? (no field changed and no cover) We still clear markers.
    # Steps 7 + 8: ONE outer preserve_mtime block, then write-then-verify.
    try:
        with tags.preserve_mtime(real):
            if write_fields:
                tags.write_editable_tags(real, write_fields)
            if cover_bytes is not None:
                cover.embed_cover_data(real, cover_bytes, cover_mime)
            # Always clear both markers on a successful save path.
            tags.clear_charts_source(real)
            tags.clear_charts_album_src(real)

            # Step 8: write-then-verify on the NORMALIZED representation, inside
            # the same block so the extra reads do not disturb the restored mtime.
            verify = tags.read_editable_tags(real)
            for name, expected in write_fields.items():
                if _norm_field(name, verify.get(name, "")) != expected:
                    raise EditorError("verify_failed", 500)
            if cover_bytes is not None:
                if not cover._embedded_cover_bytes(real):
                    raise EditorError("verify_failed", 500)
            if tags.read_charts_source(real) or tags.read_charts_album_src(real):
                raise EditorError("verify_failed", 500)
    except EditorError:
        raise
    except Exception as e:
        # Fail-closed on an unexpected write error. Reuses an existing
        # charts-import i18n key (no new key introduced).
        logger.warning(t("import.tags.write_failed", path=real, error=e))
        raise EditorError("write_failed", 500)

    # Step 9 - "folder follows the artist tag": when the save changed the
    # normalized Artist, resolve the destination with the 6-rule precedence and
    # either AUTO move/rename (rules 1,2,3,6) or return a decision_needed dialog
    # (rules 4,5). The resolution is strictly AFTER the committed in-place write,
    # so any move problem (or a cancelled dialog) degrades to "tags saved, not
    # moved": the tag write at the old path is never lost. A save that did not
    # change the artist keeps today's in-place behavior unchanged.
    if "artist" in write_fields:
        resolved = _resolve_move(real, write_fields, current)
        if resolved.get("decision_needed"):
            # Rules 4/5: the in-place tags are already committed at `real`; no
            # move happens until /api/tageditor/move receives the user's choice.
            song = _reload(real)
            library.update_index_entry(real, song["artist"], song["title"])
            song["moved"] = False
            song["move_reason"] = ""
            song["decision"] = resolved["decision"]
            return song
        return _finalize_save(real, resolved)

    # No artist change: plain in-place save, refresh the index record in place.
    song = _reload(real)
    song["moved"] = False
    song["move_reason"] = ""
    library.update_index_entry(real, song["artist"], song["title"])
    return song


# --- "folder follows the artist tag" resolution + execution -----------------
# The tag write is ALWAYS committed in save_song before any of this runs. These
# helpers only decide where the already-saved file lives on disk (6-rule
# precedence) and perform the move/rename, so a failure or a cancelled dialog is
# reported as data ("tags saved, not moved"), never a lost edit.

def _resolve_move(real: str, write_fields: dict, current: dict) -> dict:
    """
    Apply the 6-rule precedence (first match wins) for the already-saved file at
    `real`. Returns an action dict for the caller to execute or surface:

      {"action": "none",   "reason": "album_managed" | "unchanged"}
      {"action": "move",   "target": <path>}            # rules 1, 2, 6
      {"action": "rename", "old_dir": <dir>, "new_name": <name>, "target": <p>}
                                                         # rule 3
      {"decision_needed": True, "decision": {...}}       # rules 4, 5

    The decision payload (rules 4/5) carries everything the UI dialog and the
    second-step /api/tageditor/move endpoint need:
      variant      "loose"  (rule 4) | "collision" (rule 5)
      file         the current (old) path of the saved file
      artist/title/album  the saved tag values (for rebuilding the target)
      existing_folder  rule 4: the folder name that still holds other songs
      other_count      rule 4: how many OTHER songs that folder holds
      new_folder       the cleaned new-artist folder name (the "create new" option)
    """
    music_dir = config.MUSIC_DIR
    new_artist = write_fields["artist"]
    cur_album = write_fields.get("album", current.get("album", ""))
    cur_title = write_fields.get("title", current.get("title", ""))

    # Scope guard: only a stack-single <Artist>/<Title>/<Title>.mp3 is movable.
    if not library._is_movable_single(music_dir, real):
        return {"action": "none", "reason": "album_managed"}

    src_artist_dir = os.path.dirname(os.path.dirname(real))
    new_folder = library._clean_component(new_artist, "Unknown Artist")

    # Rules 1 & 2: an exact folder-name match or a STRICT _artist_key match to
    # an existing folder -> AUTO move into it. _find_artist_dir covers both.
    resolved_dir = library._find_artist_dir(music_dir, new_artist)
    if resolved_dir:
        target = library._build_move_target(
            music_dir, real, new_artist, cur_title, cur_album)
        if target == real:
            return {"action": "none", "reason": "unchanged"}
        # Rule 5: never overwrite / never create a '(2)'. A collision when
        # moving into an EXISTING artist folder is a decision, not an auto-skip.
        if library._target_collision(target, cur_title):
            return {
                "decision_needed": True,
                "decision": {
                    "variant": "collision",
                    "file": real,
                    "artist": new_artist,
                    "title": cur_title,
                    "album": cur_album,
                    "existing_folder": os.path.basename(resolved_dir),
                    "new_folder": new_folder,
                },
            }
        return {"action": "move", "target": target}

    # No strict/exact folder match below here.
    #
    # ORDERING NOTE: the loose-match check (rule 4) is evaluated BEFORE the
    # rename-when-emptied check (rule 3). The canonical rule-4 example
    # (typing 'P!nk' while an established 'Pink/' with other songs exists, from
    # a file whose own source folder would otherwise empty out) only reaches the
    # ambiguity dialog if the loose match is considered first; checking rule 3
    # first would blindly rename the source folder and create a near-duplicate
    # 'P!nk/' beside 'Pink/', defeating the one-folder guarantee the dialog
    # protects. Rule 3 therefore means "no strict/exact AND no actionable loose
    # match, and the old folder empties out".

    # Rule 4: a loose-but-not-strict match to a DIFFERENT-spelling folder that
    # still holds OTHER songs (the P!nk-with-existing-Pink case) -> dialog.
    loose_dir = library._find_loose_artist_dir(music_dir, new_artist, src_artist_dir)
    if loose_dir and library._count_audio_files(loose_dir, exclude_path=real) > 0:
        other = library._count_audio_files(loose_dir, exclude_path=real)
        return {
            "decision_needed": True,
            "decision": {
                "variant": "loose",
                "file": real,
                "artist": new_artist,
                "title": cur_title,
                "album": cur_album,
                "existing_folder": os.path.basename(loose_dir),
                "other_count": other,
                "new_folder": new_folder,
            },
        }

    # Rule 3: no strict/exact (and no actionable loose) match AND the old source
    # artist folder empties out once this file leaves -> RENAME the whole folder
    # to the new cleaned artist name (one folder, moves every inner file along).
    # Only when the rename target does not already exist (else it'd be rule 1/2).
    new_dir = os.path.join(music_dir, new_folder)
    if (library._count_audio_files(src_artist_dir, exclude_path=real) == 0
            and not os.path.exists(new_dir)
            and os.path.basename(src_artist_dir.rstrip(os.sep)) != new_folder):
        ext = os.path.splitext(real)[1].lower()
        album_base = os.path.basename(os.path.dirname(real))
        file_base = os.path.basename(real)
        # _rename_artist_folder renames ONLY the artist dir, so the album folder
        # and filename are carried over UNCHANGED. "landed" is the file's real
        # path right after that rename; "target" is where it should ultimately
        # live. They differ only when the same save also changed the title (for
        # a stack single album == title, so the inner album folder and filename
        # both follow the new title). _finalize_save reconciles the two with a
        # follow-up inner move so the returned path always exists on disk.
        landed = os.path.join(new_dir, album_base, file_base)
        safe_album = library._clean_component(cur_album, "Unknown Album")
        track_name = library._clean_component(cur_title, "track") + ext
        target = os.path.join(new_dir, safe_album, track_name)
        return {"action": "rename", "old_dir": src_artist_dir,
                "new_name": new_folder, "landed": landed, "target": target}

    # Rule 6: truly new artist, no match, old folder not emptied -> AUTO create
    # the new folder and move. A collision here is still refused (never (2)).
    target = library._build_move_target(
        music_dir, real, new_artist, cur_title, cur_album)
    if target == real:
        return {"action": "none", "reason": "unchanged"}
    if library._target_collision(target, cur_title):
        return {
            "decision_needed": True,
            "decision": {
                "variant": "collision",
                "file": real,
                "artist": new_artist,
                "title": cur_title,
                "album": cur_album,
                "existing_folder": new_folder,
                "new_folder": new_folder,
            },
        }
    return {"action": "move", "target": target}


def _finalize_save(real: str, resolved: dict) -> dict:
    """
    Execute an AUTO action (rules 1,2,3,6) and return the reloaded song state
    with moved/move_reason/moved_to_artist set. A move/rename OSError degrades to
    "tags saved, not moved" (reason cross_device / move_failed); the in-place
    tags at `real` are already committed, so nothing is lost. Refreshes the
    search index to follow the new path.
    """
    action = resolved.get("action")
    real_after = real
    moved = False
    reason = resolved.get("reason", "")
    moved_to = ""

    if action == "move":
        target = resolved["target"]
        try:
            library._move_file(real, target)
            moved = True
            real_after = target
            moved_to = os.path.basename(os.path.dirname(os.path.dirname(target)))
            library._cleanup_empty_dirs(
                os.path.dirname(real), os.path.dirname(os.path.dirname(real)))
        except OSError as e:
            reason = "cross_device" if e.errno == errno.EXDEV else "move_failed"
    elif action == "rename":
        landed = resolved["landed"]
        target = resolved["target"]
        try:
            library._rename_artist_folder(resolved["old_dir"], resolved["new_name"])
            moved = True
            moved_to = resolved["new_name"]
            # The artist-folder rename alone leaves the file at `landed` under
            # its ORIGINAL album folder/filename. If the save also changed the
            # title/album the on-disk name must still follow: move the file from
            # `landed` to `target` (os.rename, mtime preserved) and clean up the
            # now-empty old inner album folder. If nothing else changed the two
            # paths are equal and this is a no-op, so the returned path is always
            # the real on-disk path.
            if os.path.normpath(landed) != os.path.normpath(target):
                library._move_file(landed, target)
                library._cleanup_empty_dirs(os.path.dirname(landed))
                real_after = target
            else:
                real_after = landed
        except OSError as e:
            # Fail-open: if the follow-up inner move raised AFTER the folder
            # rename succeeded, the file is at `landed` (a real path) - report
            # that so the index/UI point at an existing file, not `target`.
            if moved:
                real_after = landed
            reason = "cross_device" if e.errno == errno.EXDEV else "move_failed"

    song = _reload(real_after)
    song["moved"] = moved
    song["move_reason"] = reason
    song["moved_to_artist"] = moved_to
    if moved:
        library.move_index_entry(real, real_after, song["artist"], song["title"])
    else:
        library.update_index_entry(real_after, song["artist"], song["title"])
    return song


def move_after_decision(path: str, choice: str, artist: str, title: str,
                        album: str) -> dict:
    """
    Second step of the move dialog (rules 4/5): the user has chosen what to do
    with the already-saved file at `path`. The in-place tag write is long since
    committed, so a cancel simply keeps the file where it is.

    choice:
      "into_existing" - file INTO the matched existing folder (rule 4 only; a
                        strict-key merge, so _find_artist_dir resolves it).
      "create_new"    - create a new folder named after the cleaned artist and
                        move there (rule 4).
      "cancel"        - do nothing (rule 4 and rule 5 both offer this; rule 5's
                        only non-cancel option is also a cancel-equivalent, since
                        we never overwrite the existing song).

    Returns the reloaded song state (moved/move_reason/moved_to_artist + path).
    Raises EditorError on a guard failure (path_outside / not_found / mp3_only).
    Never overwrites: a collision on the chosen target degrades to not-moved.
    """
    real = _guarded_realpath(path)
    if not os.path.isfile(real):
        raise EditorError("not_found", 404)
    if not real.lower().endswith(".mp3"):
        raise EditorError("mp3_only", 400)

    music_dir = config.MUSIC_DIR
    real_after = real
    moved = False
    reason = ""
    moved_to = ""

    if choice in ("into_existing", "create_new"):
        if choice == "into_existing":
            # Resolve the existing (loose-matched) folder by its strict key; the
            # user confirmed it IS the same artist, so merge into it.
            resolved_dir = library._find_artist_dir(music_dir, artist)
            if resolved_dir:
                safe_artist = os.path.basename(resolved_dir)
            else:
                # The matched folder was loose-only; find it again by loose key.
                loose = library._find_loose_artist_dir(
                    music_dir, artist, os.path.dirname(os.path.dirname(real)))
                safe_artist = (os.path.basename(loose) if loose
                               else library._clean_component(artist, "Unknown Artist"))
        else:  # create_new
            safe_artist = library._clean_component(artist, "Unknown Artist")

        safe_album = library._clean_component(album, "Unknown Album")
        ext = os.path.splitext(real)[1].lower()
        track_name = library._clean_component(title, "track") + ext
        target = os.path.join(music_dir, safe_artist, safe_album, track_name)

        if target == real:
            reason = "unchanged"
        elif library._target_collision(target, title):
            reason = "target_exists"  # never overwrite
        else:
            try:
                library._move_file(real, target)
                moved = True
                real_after = target
                moved_to = os.path.basename(
                    os.path.dirname(os.path.dirname(target)))
                library._cleanup_empty_dirs(
                    os.path.dirname(real), os.path.dirname(os.path.dirname(real)))
            except OSError as e:
                reason = "cross_device" if e.errno == errno.EXDEV else "move_failed"
    else:
        # choice == "cancel" (or anything unrecognized) -> leave in place. The
        # in-place tags written by save_song stay at the old path.
        reason = "kept_in_place"

    song = _reload(real_after)
    song["moved"] = moved
    song["move_reason"] = reason
    song["moved_to_artist"] = moved_to
    if moved:
        library.move_index_entry(real, real_after, song["artist"], song["title"])
    else:
        library.update_index_entry(real_after, song["artist"], song["title"])
    return song
