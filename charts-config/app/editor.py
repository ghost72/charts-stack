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

    # Step 9: return the fresh reloaded state.
    song = _reload(real)

    # Refresh the cached search index for this one file so a search right after
    # the save reflects the new tags (otherwise the path-derived index keeps
    # surfacing the OLD name until its TTL expires). In-place, TTL untouched.
    library.update_index_entry(real, song["artist"], song["title"])

    return song
