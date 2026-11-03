"""
FLAC/non-MP3 -> MP3 320 kbps CBR conversion for the Manual Tag Editor.

Mirrors how editor.py keeps main.py thin: the orchestration lives here and the
route only enforces auth, reads the form, and maps an EditorError onto a
localized JSON response. convert.convert_to_mp3 raises the existing
editor.EditorError (same .code + .status contract, same config.tageditor.<code>
message mapping in main.py); new codes are added to that namespace
(already_mp3 / convert_failed / trash_failed / target_exists).

Hard invariant: if ANYTHING fails, the original non-MP3 file is left exactly
where it was and nothing is moved to trash. The order of operations
(transcode -> temp, tag/marker post-processing on the temp, stamp mtime +
os.replace temp->final, ONLY THEN move the original to trash) guarantees it.

The ffmpeg binary is a system dependency added to the charts-config Dockerfile;
it is invoked via subprocess.run with the argument-list form (never shell=True),
so paths are injection-safe by construction.
"""

import logging
import os
import subprocess
from datetime import datetime, timezone

from . import config, library, tags
from .editor import EditorError, _guarded_realpath, _rel_to_music, _reload
from .i18n import t

logger = logging.getLogger(__name__)

# Suffix for the in-flight temp MP3 (sits next to the final file so os.replace
# is same-directory and cannot raise EXDEV).
_TMP_SUFFIX = ".converting.tmp.mp3"


def _cleanup_tmp(path: str) -> None:
    """Remove a leftover temp file, fail-open (a stray temp is never fatal)."""
    try:
        if path and os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


def _unique(dest: str) -> str:
    """
    Return a destination path that does not yet exist: `dest` if free, else
    `name (2).ext`, `name (3).ext`, ... so a trashed original is never
    overwritten (a second same-day convert of a re-created file).
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


def _build_ffmpeg_cmd(src: str, tmp_mp3: str) -> list[str]:
    """
    MP3 320 kbps CBR, copy over all metadata + the embedded cover.
      -map 0:a           audio stream
      -map 0:v?          OPTIONAL attached picture (cover-less source still works)
      -c:a libmp3lame -b:a 320k (no VBR flag) -> constant 320 kbps CBR
      -c:v copy          keep the embedded cover byte-for-byte (no re-encode)
      -map_metadata 0    carry FLAC/Vorbis/MP4 tags across to ID3
      -id3v2_version 3   write ID3v2.3 so mutagen's easy interface reads them
      -nostdin           never block on stdin; -y overwrite a stale temp
    """
    return [
        "ffmpeg", "-nostdin", "-y",
        "-i", src,
        "-map", "0:a", "-map", "0:v?",
        "-c:a", "libmp3lame", "-b:a", "320k",
        "-c:v", "copy",
        "-map_metadata", "0",
        "-id3v2_version", "3",
        "-write_id3v1", "1",
        tmp_mp3,
    ]


def convert_to_mp3(path: str) -> dict:
    """
    Convert a non-MP3 audio file at `path` to a sibling <name>.mp3 (320 kbps CBR),
    carrying tags + cover over, then move the original into
    MUSIC_DIR/.charts-trash/flac2mp3-<YYYY-MM-DD>/ (reversible, NOT deleted),
    preserve the original mtime on the new MP3, clear the stack provenance
    markers (so the song is hand-managed), and return the reloaded song dict
    augmented with {"converted": True, "trashed_to": "<rel path>"}.

    Raises EditorError on any guard/validation/transcode/move failure. All guard
    logic lives here (not the route) so it is unit-testable without HTTP.
    """
    # Guards, in strict fail-closed order (each before any filesystem mutation).
    real = _guarded_realpath(path)                 # realpath inside MUSIC_DIR
    if not os.path.isfile(real):
        raise EditorError("not_found", 404)
    ext = os.path.splitext(real)[1].lower()
    if ext == ".mp3":
        raise EditorError("already_mp3", 400)      # precise message before generic
    if ext not in library.NON_MP3_AUDIO_EXTENSIONS:
        raise EditorError("bad_audio_ext", 400)

    final_mp3 = os.path.splitext(real)[0] + ".mp3"
    # Never clobber a pre-existing MP3 of the same name (the move path's stance).
    if os.path.exists(final_mp3):
        raise EditorError("target_exists", 400)

    tmp_mp3 = os.path.splitext(real)[0] + _TMP_SUFFIX

    # Step 1: transcode -> temp (the ORIGINAL is untouched by this step).
    cmd = _build_ffmpeg_cmd(real, tmp_mp3)
    try:
        proc = subprocess.run(cmd, capture_output=True,
                              timeout=config.TAGEDITOR_CONVERT_TIMEOUT)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        # ffmpeg missing / hung / unlaunchable: clean the temp, nothing moved.
        _cleanup_tmp(tmp_mp3)
        logger.warning(t("config.tageditor.log.convert_failed",
                         path=real, error=str(e)))
        raise EditorError("convert_failed", 500)
    if proc.returncode != 0 or not (os.path.isfile(tmp_mp3)
                                    and os.path.getsize(tmp_mp3) > 0):
        _cleanup_tmp(tmp_mp3)
        stderr = (proc.stderr or b"")[-500:].decode("utf-8", "replace")
        logger.warning(t("config.tageditor.log.convert_failed",
                         path=real, error=stderr))
        raise EditorError("convert_failed", 500)

    # Step 2: tag/marker post-processing on the TEMP (original still untouched).
    try:
        # (a) Re-assert the five editable tags from the source (belt over
        #     ffmpeg's -map_metadata). SET-only, non-empty, the five editable
        #     keys only - never is_mp3, never blank a mapped field.
        src_tags = tags.read_editable_tags(real)
        reassert = {k: src_tags[k]
                    for k in ("artist", "title", "album", "year", "track")
                    if src_tags.get(k)}
        if reassert:
            tags.write_editable_tags(tmp_mp3, reassert)
        # (b) Strip provenance/shop tags (same hygiene as the import path; the
        #     cover APIC is untouched by this function).
        tags.strip_provenance_tags(tmp_mp3)
        # (c) Clear the stack provenance markers -> the result is hand-managed
        #     (idempotent; a freshly transcoded file has none).
        tags.clear_charts_source(tmp_mp3)
        tags.clear_charts_album_src(tmp_mp3)
    except Exception as e:
        _cleanup_tmp(tmp_mp3)
        logger.warning(t("config.tageditor.log.convert_failed",
                         path=real, error=str(e)))
        raise EditorError("convert_failed", 500)

    # Step 3: snapshot the ORIGINAL mtime, then rename the temp into the final
    # sibling MP3 and stamp it (preserve "date added"). This creates the sibling
    # MP3 but does NOT yet touch the original.
    try:
        st = os.stat(real)
        orig_times = (st.st_atime, st.st_mtime)
        os.replace(tmp_mp3, final_mp3)             # same dir -> atomic, no EXDEV
        tags.restore_mtime(final_mp3, orig_times)
    except OSError as e:
        _cleanup_tmp(tmp_mp3)
        logger.warning(t("config.tageditor.log.convert_failed",
                         path=real, error=str(e)))
        raise EditorError("convert_failed", 500)

    # Step 4: move the ORIGINAL into the trash bin (reversible - NOT deleted).
    # Mirror the dedup trash convention: MUSIC_DIR/.charts-trash/<label>-<date>/
    # preserving the file's library-relative substructure. If this fails, roll
    # the just-created final_mp3 back so the folder is not left with both.
    rel = os.path.relpath(real, config.MUSIC_DIR)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    trash_dir = os.path.join(config.MUSIC_DIR, ".charts-trash", f"flac2mp3-{day}")
    dest = _unique(os.path.join(trash_dir, rel))
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        os.replace(real, dest)                     # same filesystem -> no EXDEV
    except OSError as e:
        # Trash move failed AFTER final_mp3 was created: delete final_mp3 so no
        # duplicate is left behind, then report trash_failed.
        _cleanup_tmp(final_mp3)
        logger.warning(t("config.tageditor.log.convert_failed",
                         path=real, error=str(e)))
        raise EditorError("trash_failed", 500)

    # Keep the search index consistent: drop the old non-MP3 record, add the new
    # MP3 path (reusing the exact helper the move path uses).
    reloaded = _reload(final_mp3)
    library.move_index_entry(real, final_mp3,
                            reloaded["artist"], reloaded["title"])

    reloaded["converted"] = True
    reloaded["trashed_to"] = _rel_to_music(dest)
    return reloaded
