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
    is_mp3, origin, and a size-bounded cover data: URL (omitted + oversize flag
    when the embedded image exceeds TAGEDITOR_COVER_MAX_BYTES).
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
        track_name = library._clean_component(cur_title, "track") + ext
        target = os.path.join(new_dir, album_base, track_name)
        return {"action": "rename", "old_dir": src_artist_dir,
                "new_name": new_folder, "target": target}

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
        try:
            library._rename_artist_folder(resolved["old_dir"], resolved["new_name"])
            moved = True
            real_after = resolved["target"]
            moved_to = resolved["new_name"]
        except OSError as e:
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
