"""
Tag read/write helpers for the Manual Tag Editor (copied subset of
charts-import/app/tags.py).

This is a COPY-INTO-EACH-SERVICE module (like i18n.py / library.py / manifest.py
elsewhere in the stack): it duplicates the tag helpers rather than importing
across services, so charts-config has no runtime coupling to charts-import. The
copied parts MUST stay behavior-identical to charts-import's canonical write
path - in particular the two TXXX marker names and the provenance-strip
whitelist (_TXXX_PROTECTED_SUBSTR). The editor-specific helpers at the bottom
(read_editable_tags / write_editable_tags) are new to this copy.

Deliberately NOT copied from charts-import: build_target_path / read_metadata /
canonical_artist - the editor edits a file in place and never re-files it.
"""

import contextlib
import logging
import os
import re

from . import config
from .i18n import t

logger = logging.getLogger(__name__)


# --- mtime preservation -----------------------------------------------------
# Navidrome derives a song's "date added" from the file mtime. Any content
# write into an existing library file rewrites the file and bumps the mtime,
# which would wrongly surface the song as "recently added". These helpers
# snapshot the mtime before such a write and restore it afterwards. Gated by
# config.PRESERVE_MTIME (default on). Behaviour-identical to charts-import.


def restore_mtime(path: str, times) -> None:
    """
    Restore (atime, mtime) onto path via os.utime. Never fatal - a failed
    restore only means the file keeps its fresh mtime (fail-open).
    """
    try:
        os.utime(path, times)
    except Exception:
        # Fail-open: a failed restore only means the file keeps its fresh mtime.
        pass


@contextlib.contextmanager
def preserve_mtime(path: str):
    """
    Context manager that keeps a file's (atime, mtime) stable across a content
    write. On enter it snapshots the CURRENT (atime, mtime); on exit it restores
    exactly that value. A no-op when PRESERVE_MTIME is off or when the file does
    not exist on enter. Nesting is safe (an inner block restores the same
    snapshot the outer took).

    Key semantics: it restores whatever mtime the file had WHEN THE CONTEXT WAS
    ENTERED. A brand-new write therefore keeps its fresh download/copy mtime
    (the just-stamped value is snapshotted and restored) - it never invents an
    older timestamp. A deliberate in-place upgrade that must carry an OLD mtime
    is handled explicitly by the caller, not here.
    """
    times = None
    if config.PRESERVE_MTIME:
        try:
            st = os.stat(path)
            times = (st.st_atime, st.st_mtime)
        except OSError:
            times = None  # file not present on enter -> no-op
    try:
        yield
    finally:
        if times is not None:
            restore_mtime(path, times)


# --- Provenance/encoder tags and marker descriptions ---
# Copied verbatim from charts-import so the strip whitelist and the two marker
# names stay in lockstep. See charts-import/app/tags.py for the full rationale.
_PROVENANCE_FULL_PREFIXES = ("COMM", "TENC", "TSSE", "WXXX",
                             "WCOM", "WOAF", "WPUB", "TOWN", "TMED", "WOAR")

# TXXX descriptions that are PROTECTED (case-insensitive substring): everything
# around MusicBrainz / ReplayGain plus the two charts-stack provenance markers,
# which must survive every provenance strip.
_TXXX_PROTECTED_SUBSTR = ("musicbrainz", "replaygain", "charts_source",
                          "charts_album_src")

# Description of the durable source provenance marker frame (TXXX:CHARTS_SOURCE).
CHARTS_SOURCE_DESC = "CHARTS_SOURCE"

# Description of the durable ALBUM provenance marker frame
# (TXXX:CHARTS_ALBUM_SRC), value "resolver" when the stack SET the album.
CHARTS_ALBUM_SRC_DESC = "CHARTS_ALBUM_SRC"


def _is_protected_txxx(frame_key: str) -> bool:
    """True if a TXXX frame is protected (MusicBrainz/ReplayGain/markers)."""
    ks = str(frame_key)
    if not ks.upper().startswith("TXXX"):
        return False
    desc = ks.split(":", 1)[1].lower() if ":" in ks else ""
    return any(sub in desc for sub in _TXXX_PROTECTED_SUBSTR)


def _is_cdrip_toal(frame) -> bool:
    """
    True if a TOAL frame's text carries a '(CD-Rip)'/'cd-rip' marker (provenance
    from a CD rip) that must be stripped. A genuine original-album TOAL returns
    False (kept). Fail-open to False (keep) on an unreadable value.
    """
    try:
        text = " ".join(str(x) for x in (getattr(frame, "text", None) or []))
    except Exception:
        return False
    return "cd-rip" in text.lower()


def _is_musicbrainz_ufid(frame) -> bool:
    """
    True if a UFID frame's owner is MusicBrainz (the one content-valuable UFID
    to keep). Any other / unreadable owner is treated as non-MusicBrainz.
    """
    try:
        owner = str(getattr(frame, "owner", "") or "")
    except Exception:
        return False
    return "musicbrainz.org" in owner.lower()


def strip_provenance_tags(path: str) -> bool:
    """
    Remove provenance/encoder frames (COMM/TENC/TSSE/WXXX/... + non-protected
    TXXX) from an audio file, leaving content tags untouched - in particular
    TXXX:MusicBrainz* / TXXX:*replaygain* and the two charts markers. Lossless
    (metadata only). Returns True if something was removed (then re-saved), else
    False. Errors are not fatal (log + False). Copied from charts-import.
    """
    try:
        from mutagen.id3 import ID3, ID3NoHeaderError
        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            return False  # no ID3 block -> nothing to remove
        to_delete = []
        for k in list(tags.keys()):
            ku = str(k).upper()
            if ku.startswith("TXXX"):
                if not _is_protected_txxx(k):
                    to_delete.append(k)
            elif ku.startswith("TOAL"):
                if _is_cdrip_toal(tags.get(k)):
                    to_delete.append(k)
            elif ku.startswith("UFID"):
                if not _is_musicbrainz_ufid(tags.get(k)):
                    to_delete.append(k)
            elif ku.startswith(_PROVENANCE_FULL_PREFIXES):
                to_delete.append(k)
        if not to_delete:
            return False
        for k in to_delete:
            try:
                del tags[k]
            except Exception:
                pass
        with preserve_mtime(path):
            tags.save(path)
        return True
    except Exception as e:
        logger.warning(t("import.tags.strip_failed", path=path, error=e))
        return False


# --- Provenance marker read/clear (raw ID3) ---------------------------------
# The easy interface cannot read/write an arbitrary TXXX frame, so the markers
# use the raw mutagen.id3.ID3 interface. Copied from charts-import. The editor
# save path uses read_* for the search-result origin classification and clear_*
# unconditionally after a successful save.


def read_charts_source(path: str) -> str:
    """
    Read the durable TXXX:CHARTS_SOURCE provenance marker (e.g. "youtube"), or
    "" when absent/unreadable. Fail-open. Copied from charts-import.
    """
    try:
        from mutagen.id3 import ID3, ID3NoHeaderError
        try:
            id3 = ID3(path)
        except ID3NoHeaderError:
            return ""
        frame = id3.get(f"TXXX:{CHARTS_SOURCE_DESC}")
        if frame is None:
            return ""
        texts = getattr(frame, "text", None) or []
        return str(texts[0]).strip() if texts else ""
    except Exception:
        return ""


def read_charts_album_src(path: str) -> str:
    """
    Read the durable TXXX:CHARTS_ALBUM_SRC provenance marker (e.g. "resolver"),
    or "" when absent/unreadable. Fail-open. Copied from charts-import.
    """
    try:
        from mutagen.id3 import ID3, ID3NoHeaderError
        try:
            id3 = ID3(path)
        except ID3NoHeaderError:
            return ""
        frame = id3.get(f"TXXX:{CHARTS_ALBUM_SRC_DESC}")
        if frame is None:
            return ""
        texts = getattr(frame, "text", None) or []
        return str(texts[0]).strip() if texts else ""
    except Exception:
        return ""


def clear_charts_source(path: str) -> None:
    """
    Remove the TXXX:CHARTS_SOURCE marker, mtime-preserving. Fail-open; no-op when
    absent. Copied from charts-import.
    """
    try:
        from mutagen.id3 import ID3, ID3NoHeaderError
        with preserve_mtime(path):
            try:
                id3 = ID3(path)
            except ID3NoHeaderError:
                return
            key = f"TXXX:{CHARTS_SOURCE_DESC}"
            if id3.get(key) is None:
                return
            id3.delall(key)
            id3.save(path)
    except Exception as e:
        logger.debug(t("import.tags.marker_clear_failed", path=path, error=e))


def clear_charts_album_src(path: str) -> None:
    """
    Remove the TXXX:CHARTS_ALBUM_SRC marker, mtime-preserving. Mirrors
    clear_charts_source exactly. Used by the Manual Tag Editor's save path to
    drop the stack-written album provenance marker when a user takes over by
    hand. Fail-open; no-op when absent. Identical to charts-import's copy.
    """
    try:
        from mutagen.id3 import ID3, ID3NoHeaderError
        with preserve_mtime(path):
            try:
                id3 = ID3(path)
            except ID3NoHeaderError:
                return
            key = f"TXXX:{CHARTS_ALBUM_SRC_DESC}"
            if id3.get(key) is None:
                return
            id3.delall(key)
            id3.save(path)
    except Exception as e:
        logger.debug(t("import.tags.marker_clear_failed", path=path, error=e))


# --- Editor-specific helpers (new in this copy) -----------------------------
# The editor reads the easy-interface fields for display/diff and writes back
# only the fields the user changed. SET-only: there is NO pop/clear path, so the
# editor never removes a value (a manifest-scoped backfill would otherwise refill
# a cleared field next pass).

from mutagen import File as MutagenFile  # noqa: E402  (kept near its only users)


def _first_tag(audio, *keys) -> str:
    """Return the first present easy-tag value from a list of candidate keys."""
    for key in keys:
        try:
            value = audio.get(key)
        except Exception:
            value = None
        if not value:
            continue
        if isinstance(value, list):
            value = value[0] if value else ""
        text = str(value).strip()
        if text:
            return text
    return ""


def _leading_year(raw: str) -> str:
    """Leading 4-digit year of a date string, else "" (read_existing_year_album
    semantics: y[:4] when the first four chars are digits)."""
    y = (raw or "")[:4]
    return y if y.isdigit() else ""


def _leading_track(raw: str) -> str:
    """Leading positive integer of a 'N' or 'N/M' tracknumber as a string, else
    "" (read_track semantics). Returns the number WITHOUT the '/M' total."""
    if not raw:
        return ""
    head = raw.split("/", 1)[0].strip()
    m = re.match(r"\d+", head)
    if not m:
        return ""
    num = int(m.group(0))
    return str(num) if num > 0 else ""


def read_editable_tags(path: str) -> dict:
    """
    Read the editor's editable fields from a file (easy interface), fail-open.

    Returns a dict with: artist (albumartist preferred, then artist), title,
    album, year (leading 4 digits), track (leading int of N or N/M as a string),
    is_mp3 (extension check). Unreadable tags degrade to empty strings.
    """
    is_mp3 = path.lower().endswith(".mp3")
    artist = title = album = year = track = ""
    try:
        audio = MutagenFile(path, easy=True)
        if audio is not None:
            artist = _first_tag(audio, "albumartist", "artist")
            title = _first_tag(audio, "title")
            album = _first_tag(audio, "album")
            year = _leading_year(_first_tag(audio, "date", "originaldate"))
            track = _leading_track(_first_tag(audio, "tracknumber"))
    except Exception as e:
        logger.debug(t("import.tags.read_failed", path=path, error=e))
    return {"artist": artist, "title": title, "album": album,
            "year": year, "track": track, "is_mp3": is_mp3}


# The editor's write set uses these field names; each maps to the easy key(s) to
# set. "artist" writes BOTH artist and albumartist (the stack's convention).
_WRITE_FIELDS = ("artist", "title", "album", "year", "track")


def write_editable_tags(path: str, fields: dict) -> None:
    """
    SET-only write of the editor's easy fields for the keys present in `fields`
    (the server-computed write set). Maps:
        artist -> artist + albumartist
        title  -> title
        album  -> album
        year   -> date
        track  -> tracknumber
    Writes the user's submitted raw string for each field. There is NO pop/clear
    path - the editor never removes a value. The CALLER owns the outer
    preserve_mtime block (so this does not open its own) and clears the markers.
    """
    audio = MutagenFile(path, easy=True)
    if audio is None:
        logger.debug(t("import.tags.not_writable", path=path))
        return
    if "artist" in fields:
        audio["artist"] = fields["artist"]
        audio["albumartist"] = fields["artist"]
    if "title" in fields:
        audio["title"] = fields["title"]
    if "album" in fields:
        audio["album"] = fields["album"]
    if "year" in fields:
        audio["date"] = fields["year"]
    if "track" in fields:
        audio["tracknumber"] = fields["track"]
    audio.save()
