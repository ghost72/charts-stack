"""
Tag reading and target-path building.

Metadata sources in this order:
  1. Manifest from charts-download (CLEAN chart artist/title) - preferred.
     Also written into the file tags so Navidrome/charts-sync see correct
     values (instead of "Unknown Album").
  2. File tags (mutagen).
  3. Fallback to the file name.

Target path scheme: Music/<Artist>/<Album>/<Track>.<ext>.
"""

import json
import logging
import os
import re

from mutagen import File as MutagenFile

from . import config
from .i18n import t

logger = logging.getLogger(__name__)

# --- Provenance/encoder tags removed from chart downloads ---
# These frames reveal the SOURCE (Soulseek uploader/release-group signatures
# like "eXenz", "PMEDIA", "www.t.me/...", the fre:ac ad comment) or the
# rip/encoder tool. They are removed on every manifest import so no provenance
# traces land in the library. Done deliberately via the full ID3 interface
# (NOT easy=True, which cannot delete COMM/TENC/TXXX reliably).
#
# IMPORTANT - WHAT IS DELETED:
#   COMM = comment (almost always an uploader/release-group signature) -> clear
#   TENC = encoded by, TSSE = encoder settings (rip tool) -> clear
#   WXXX/WCOM/WOAF/WPUB = URL frames (source links) -> clear
#   TOWN = file owner -> clear
#   TXXX = user-defined free-text frames: only deleted if the description name
#          is NOT on the protection list (see below).
#
# WHAT STAYS PROTECTED (NEVER touched) - content-valuable tags that in this
# library sit in TXXX: MusicBrainz IDs and ReplayGain values. These must on NO
# account be lost (metadata/loudness normalization).
_PROVENANCE_FULL_PREFIXES = ("COMM", "TENC", "TSSE", "WXXX",
                             "WCOM", "WOAF", "WPUB", "TOWN")
# TXXX descriptions (the part after "TXXX:") that are PROTECTED (case-insensitive,
# checked as a substring): everything around MusicBrainz and ReplayGain.
_TXXX_PROTECTED_SUBSTR = ("musicbrainz", "replaygain")


def _is_protected_txxx(frame_key: str) -> bool:
    """True if a TXXX frame is protected (MusicBrainz/ReplayGain)."""
    ks = str(frame_key)
    if not ks.upper().startswith("TXXX"):
        return False
    desc = ks.split(":", 1)[1].lower() if ":" in ks else ""
    return any(sub in desc for sub in _TXXX_PROTECTED_SUBSTR)


def strip_provenance_tags(path: str) -> bool:
    """
    Removes provenance/encoder frames (COMM/TENC/TSSE/WXXX/... + non-protected
    TXXX) from an audio file, leaving all content tags untouched - in
    particular TXXX:MusicBrainz* and TXXX:*replaygain*. Lossless (metadata only,
    no re-encoding). Returns True if something was actually removed (then the
    file was re-saved), otherwise False. Errors are not fatal (log + False).
    """
    # Use the ID3 tag view directly (only the tag block, NOT the audio) - more
    # robust than MutagenFile(), which parses the MPEG frames and would fail on
    # slightly broken files with "can't sync to MPEG frame".
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
                # TXXX only if NOT protected (MusicBrainz/ReplayGain stay).
                if not _is_protected_txxx(k):
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
        tags.save(path)
        return True
    except Exception as e:
        logger.warning(t("import.tags.strip_failed", path=path, error=e))
        return False

# Manifest cache (loading once per process per run is enough, but we read it
# lazily here and cache it simply).
_manifest_cache: dict | None = None


def _load_manifest() -> dict:
    """Loads the charts-download manifest (basename -> metadata)."""
    global _manifest_cache
    if _manifest_cache is not None:
        return _manifest_cache
    entries = {}
    try:
        with open(config.MANIFEST_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            entries = data.get("entries", {})
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        entries = {}
    _manifest_cache = entries
    return entries


def reload_manifest() -> None:
    """Forces a reload of the manifest (at the start of every run)."""
    global _manifest_cache
    _manifest_cache = None


def _manifest_lookup(path: str) -> dict | None:
    """
    Finds the clean metadata for a file by its basename.
    Tolerates slskd-typical numeric suffixes (e.g.
    'Stargazing_639241038209671901.mp3') so the same file from different
    providers finds the same manifest entry.
    """
    entries = _load_manifest()
    base = os.path.basename(path.replace("\\", "/")).lower()
    # 1. exact hit
    if base in entries:
        return entries[base]
    # 2. without the slskd suffix before the extension: 'name_639....mp3' -> 'name.mp3'
    stripped = re.sub(r"_\d{6,}(\.[a-z0-9]+)$", r"\1", base)
    if stripped != base and stripped in entries:
        return entries[stripped]
    return None


def manifest_lookup(path: str) -> dict | None:
    """Public access to a file's manifest entry (or None)."""
    return _manifest_lookup(path)

# Characters that cause problems in file/folder names on most file systems.
# They are replaced with underscores.
_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _clean_component(value: str, fallback: str) -> str:
    """Cleans a single path component (folder or file name)."""
    value = (value or "").strip()
    if not value:
        value = fallback
    # Replace invalid characters, collapse multiple spaces, remove trailing
    # dots/spaces (Windows/SMB compatibility).
    value = _INVALID_CHARS.sub("_", value)
    value = re.sub(r"\s+", " ", value).strip()
    value = value.rstrip(". ")
    return value or fallback


def _first_tag(audio, *keys) -> str:
    """Returns the first present tag value from a list of possible keys."""
    for key in keys:
        try:
            value = audio.get(key)
        except Exception:
            value = None
        if not value:
            continue
        # mutagen returns lists or strings depending on the format.
        if isinstance(value, list):
            value = value[0] if value else ""
        text = str(value).strip()
        if text:
            return text
    return ""


def read_metadata(path: str) -> tuple[str, str, str, bool]:
    """
    Returns (artist, album, title, from_manifest).

    Order:
      1. Manifest (clean chart metadata from charts-download) -> preferred.
      2. File tags (mutagen).
      3. Fallback to the file name / "Unknown ...".

    from_manifest=True signals that the values come from the manifest and
    should therefore also be written into the file tags.
    """
    filename = os.path.splitext(os.path.basename(path))[0]

    # 1. Prefer the manifest. IMPORTANT: return the ORIGINAL values (for the
    # tags), not the path-cleaned ones - otherwise e.g. "HUNTR/X" would be
    # stored as the tag "HUNTR_X" and charts-sync could not match it. The path
    # cleaning happens only in build_target_path.
    m = _manifest_lookup(path)
    if m and m.get("artist") and m.get("title"):
        artist = (m.get("artist") or "").strip() or "Unknown Artist"
        title = (m.get("title") or "").strip() or filename
        album = (m.get("album") or m.get("title") or "").strip() or title
        return artist, album, title, True

    # 2. File tags
    artist = album = title = ""
    try:
        audio = MutagenFile(path, easy=True)
        if audio is not None:
            artist = _first_tag(audio, "albumartist", "artist")
            album = _first_tag(audio, "album")
            title = _first_tag(audio, "title")
    except Exception as e:
        logger.warning(t("import.tags.read_failed", path=path, error=e))

    # 3. Fallback
    artist = _clean_component(artist, "Unknown Artist")
    album = _clean_component(album, "Unknown Album")
    title = _clean_component(title, filename)
    return artist, album, title, False


def write_tags(path: str, artist: str, title: str, album: str,
               year=None) -> None:
    """
    Writes artist/title/album into the file tags (only for manifest hits).
    Errors are logged but not propagated - the import should succeed even if
    the tag writing fails.

    year = optional release year from the manifest (e.g. "1987"). If set, it is
    written as date/originaldate tags so the decade smart playlists in Navidrome
    assign the song to the right decade. If year is missing (None/""), the date
    tags stay untouched -> behavior as before (no overwriting of existing years
    with an empty value).
    """
    try:
        audio = MutagenFile(path, easy=True)
        if audio is None:
            logger.debug(t("import.tags.not_writable", path=path))
            return
        audio["artist"] = artist
        audio["albumartist"] = artist
        audio["title"] = title
        audio["album"] = album
        if year:
            audio["date"] = str(year)
            audio["originaldate"] = str(year)
        audio.save()
    except Exception as e:
        logger.warning(t("import.tags.write_failed", path=path, error=e))
    # Remove provenance/encoder frames (fre:ac comment, TENC/TSSE/TXXX ...) so
    # no source traces land in the library. Separate, after the easy-save,
    # because the easy mode cannot delete these frames.
    strip_provenance_tags(path)


def canonical_artist(music_dir: str, artist: str) -> str | None:
    """
    Returns the CANONICAL artist spelling = the real basename of an
    already-present artist folder under music_dir (searched CASE-INSENSITIVELY),
    or None if the artist does not exist yet.

    Reason: manifest/tag values can carry a different casing (e.g. an old 'rod
    stewart' manifest entry) while the library already keeps the artist as 'Rod
    Stewart'. Without this resolution a second folder 'rod stewart' would arise
    on case-sensitive file systems (ext4). This applies to ALL services that use
    charts-import (songrequest, sync, download) - not only for manual requests.
    """
    safe_artist = _clean_component(artist, "")
    if not safe_artist or not music_dir or not os.path.isdir(music_dir):
        return None
    # 1. Exact folder present -> get the real basename from the FS (not the
    #    constructed path, which on case-insensitive FS mirrors the input).
    target = safe_artist.lower()
    try:
        for name in os.listdir(music_dir):
            if name.lower() == target and os.path.isdir(os.path.join(music_dir, name)):
                return name
    except OSError:
        pass
    return None


def _looks_uppercase(artist: str, title: str) -> bool:
    """
    True if the artist OR title is 'suspiciously UPPERCASE' (at least one
    letter, fully uppercase, longer than 3 characters). Short real acronyms like
    'U2', 'ABBA', 'INXS' should NOT trigger if the rest is clean - hence the
    length threshold and the OR logic only as a suspicion gate (the actual
    decision is made by the Deezer validation).
    """
    for s in (artist, title):
        s = (s or "").strip()
        if s and len(s) > 3 and any(c.isalpha() for c in s) and s == s.upper():
            return True
    return False


def _canonicalize(artist: str, title: str) -> tuple[str, str]:
    """
    Resolves the canonical (mixed-case) spelling via Deezer. Lazy import of the
    deezer module so tags.py stays importable without httpx/network and an error
    never blocks the import (fallback: unchanged raw values).
    """
    try:
        from . import deezer
        return deezer.resolve_canonical(artist, title)
    except Exception as e:  # import/runtime error never fatal
        logger.debug(t("import.tags.canonicalize_skipped", artist=artist,
                       title=title, error=e))
        return artist, title


def build_target_path(music_dir: str, path: str) -> tuple[str, tuple]:
    """
    Builds the target path Music/<Artist>/<Album>/<Track>.<ext>.

    Returns: (target_path, (artist, title, album, from_manifest)) - the
    metadata is returned too so the importer can set the tags in the (copied)
    file on manifest hits.
    """
    artist, album, title, from_manifest = read_metadata(path)
    ext = os.path.splitext(path)[1].lower()
    # DEEZER CANONICALIZATION (last resort): if the manifest name is still
    # UPPERCASE/suspicious (a flashback raw value that slipped through), resolve
    # the official mixed-case spelling via Deezer - BEFORE path/tags are built.
    # Only for manifest hits and only on UPPERCASE suspicion (saves API load;
    # clean names stay untouched). If Deezer returns no safe hit, the raw value
    # stays (unresolved_upper=True -> the importer reports it via Pushover for
    # manual review but imports anyway).
    unresolved_upper = False
    if from_manifest and _looks_uppercase(artist, title):
        new_artist, new_title = _canonicalize(artist, title)
        if (new_artist, new_title) != (artist, title):
            artist, title = new_artist, new_title
            # Album was = title for chart singles -> carry it along if it
            # matched the old title (otherwise leave the album unchanged).
            if _looks_uppercase("", album) or album.lower() == title.lower():
                album = new_title
        else:
            unresolved_upper = True
    # Enforce the CANONICAL artist spelling: if the artist already exists (in
    # whatever casing), take its real folder name - for the path AND the tag.
    # Prevents duplicates like 'rod stewart' next to 'Rod Stewart' and makes the
    # established spelling win, no matter what the manifest/tags say.
    canon = canonical_artist(music_dir, artist)
    if canon:
        artist = canon
    # Clean the path components (file-system-safe), tags keep the original.
    safe_artist = _clean_component(artist, "Unknown Artist")
    safe_album = _clean_component(album, "Unknown Album")
    track_name = _clean_component(title, "track") + ext
    target = os.path.join(music_dir, safe_artist, safe_album, track_name)
    return target, (artist, title, album, from_manifest, unresolved_upper)
