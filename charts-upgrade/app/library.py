"""
Library scanner: searches the Navidrome library for files with bad quality
(low real bitrate OR upsampled/faked bitrate) and provides the necessary
metadata (artist/title) for the replacement search.

IMPORTANT - CONSERVATIVE:
  - Compilation/sampler folders are completely SKIPPED (never replace curated
    albums like Bravo Hits/Kuschelrock track by track). Detection via the path
    heuristic (matching.is_compilation) PLUS "many different artists in the
    same album folder" (album-artist spread).
  - Song metadata primarily from the file tags (mutagen), path only as a
    fallback.
  - The trash (.charts-trash) and hidden folders are skipped.
"""

import logging
import os

from mutagen import File as MutagenFile

from . import config
from .i18n import t
from .matching import is_compilation, normalize

logger = logging.getLogger(__name__)

AUDIO_EXTENSIONS = {
    ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma", ".wav", ".alac",
}

# Folders that are never scanned (trash, quarantine, Synology index).
# @eaDir are Synology DSM index/thumbnail folders (sometimes contain file
# copies) - they must NEVER be analyzed or "improved".
_SKIP_DIR_NAMES = {".charts-trash", "_rejected", "@eaDir"}


def read_bitrate(path: str) -> int:
    """Bitrate (kbps) of an audio file, 0 if not readable."""
    try:
        audio = MutagenFile(path)
        if audio is not None and audio.info is not None:
            return int((getattr(audio.info, "bitrate", 0) or 0) / 1000)
    except Exception:
        pass
    return 0


def _read_artist_title(path: str) -> tuple[str, str]:
    """
    Reads (artist, title) primarily from the file tags (mutagen). Fallback:
    artist = name of the artist folder (music_dir/<Artist>/<Album>/file),
    title = file name without extension/leading track number.
    """
    artist = title = ""
    try:
        audio = MutagenFile(path, easy=True)
        if audio is not None:
            def _first(*keys):
                for k in keys:
                    v = audio.get(k)
                    if v:
                        return (v[0] if isinstance(v, list) else str(v)).strip()
                return ""
            artist = _first("albumartist", "artist")
            title = _first("title")
    except Exception as e:
        logger.debug(t("upgrade.library.tags_unreadable", path=path, error=e))

    if not artist:
        # Fallback: artist folder (two levels above the file).
        rel = os.path.relpath(path, config.MUSIC_DIR)
        parts = rel.split(os.sep)
        if len(parts) >= 2 and parts[0] not in (".", ".."):
            artist = parts[0]
    if not title:
        import re
        base = os.path.splitext(os.path.basename(path))[0]
        base = re.sub(r"^\s*\d{1,3}\s*[-.\)]\s*", "", base)  # leading track no.
        if " - " in base:
            base = base.rsplit(" - ", 1)[-1]
        title = base.strip()
    return artist.strip(), title.strip()


def _album_is_compilation(album_dir: str, audio_files: list[str]) -> bool:
    """
    True if the album folder is a compilation:
      - the path heuristic (matching.is_compilation) matches, OR
      - the artists in the folder spread widely (many different artist tags =
        sampler). A regular album is predominantly the same artist.
    """
    if is_compilation(album_dir):
        return True

    # Measure artist spread via the tags.
    artists: set[str] = set()
    checked = 0
    for f in audio_files:
        try:
            audio = MutagenFile(f, easy=True)
            if audio is None:
                continue
            a = audio.get("albumartist") or audio.get("artist")
            if a:
                artists.add(normalize(a[0] if isinstance(a, list) else str(a)))
            checked += 1
        except Exception:
            continue
        if checked >= 12:  # a sample is enough
            break
    # A "Various Artists" tag or >3 different artists in the album =
    # compilation. Regular albums have 1 (possibly 2 with a guest) album artist.
    if any(a in ("various artists", "various", "va", "verschiedene") for a in artists):
        return True
    return len(artists) > 3


def count_candidates() -> int:
    """
    Counts how many MP3 files charts-upgrade WOULD check at all - i.e. the total
    against which scan progress is measured. Uses EXACTLY the same skip rules as
    scan() (hidden/special folders out, compilation folders out, only .mp3) so
    'checked X / Y' is consistent.

    Cheaper than scan(): only os.walk + extension check, NO ffmpeg analysis.
    """
    if not os.path.isdir(config.MUSIC_DIR):
        return 0
    total = 0
    for dirpath, dirnames, filenames in os.walk(config.MUSIC_DIR):
        dirnames[:] = [d for d in dirnames
                       if d not in _SKIP_DIR_NAMES and not d.startswith(".")]
        audio_files = [os.path.join(dirpath, n) for n in filenames
                       if os.path.splitext(n)[1].lower() in AUDIO_EXTENSIONS]
        if not audio_files:
            continue
        if config.SKIP_COMPILATIONS and _album_is_compilation(dirpath, audio_files):
            continue
        total += sum(1 for p in audio_files
                     if os.path.splitext(p)[1].lower() == ".mp3")
    return total


def _is_bad(path: str) -> tuple[bool, str, int, int, float | None]:
    """
    Judges a single file.
    Returns: (is_bad, reason, bitrate, quality_key_int, cutoff_khz_or_None)
      reason: "" if ok, otherwise the i18n reason for low bitrate or upsampling
              (German runtime text, e.g. "niedrige Bitrate (Xkbps)").
    """
    from . import quality

    bitrate = read_bitrate(path)

    # 1. Low real bitrate.
    if 0 < bitrate < config.BAD_BITRATE:
        return True, t("upgrade.reason.low_bitrate", bitrate=bitrate), bitrate, bitrate, None

    # 2. Upsampling (claims a high bitrate, real cutoff too low).
    if config.CHECK_UPSAMPLED:
        upsampled, measured, expected = quality.is_upsampled(
            path, bitrate,
            tolerance_khz=config.CUTOFF_TOLERANCE_KHZ,
            min_check_bitrate=config.REJECT_UPSAMPLED_MIN_BITRATE,
        )
        if upsampled:
            return (True,
                    t("upgrade.reason.upsampled", bitrate=bitrate,
                      measured=measured, expected=expected),
                    bitrate, int((measured or 0) * 1000 + bitrate), measured)

    return False, "", bitrate, bitrate, None


def scan(state_seen: dict, max_scan: int):
    """
    Scans MUSIC_DIR and returns bad songs.

    state_seen: dict {abspath: "mtime:size"} - already-analyzed files (cache),
      so the expensive ffmpeg analysis runs only once per file.
    max_scan: max number of NEW/changed files analyzed in this run (CPU
      protection).

    Returns: (bad_songs, scanned_count, skipped_compilations)
      bad_songs: list of dicts {path, artist, title, reason, bitrate,
                 quality_key, cutoff}.
    The cache (state_seen) is extended in-place with the newly analyzed files
    (including the good ones, so they are not checked again).
    """
    if not os.path.isdir(config.MUSIC_DIR):
        logger.warning(t("upgrade.library.music_dir_missing", dir=config.MUSIC_DIR))
        return [], 0, 0

    bad_songs: list[dict] = []
    scanned = 0
    skipped_comp = 0

    for dirpath, dirnames, filenames in os.walk(config.MUSIC_DIR):
        # Skip hidden / special folders.
        dirnames[:] = [d for d in dirnames
                       if d not in _SKIP_DIR_NAMES and not d.startswith(".")]

        audio_files = [os.path.join(dirpath, n) for n in filenames
                       if os.path.splitext(n)[1].lower() in AUDIO_EXTENSIONS]
        if not audio_files:
            continue

        # Completely skip compilation folders.
        if config.SKIP_COMPILATIONS and _album_is_compilation(dirpath, audio_files):
            skipped_comp += len(audio_files)
            logger.debug(t("upgrade.library.compilation_skipped", dir=dirpath))
            continue

        for path in audio_files:
            # Only check MP3 (the cutoff/bitrate logic is MP3-oriented;
            # FLAC/lossless is by definition not an upgrade candidate).
            if os.path.splitext(path)[1].lower() != ".mp3":
                continue

            # Cache: already analyzed (unchanged)? Then skip.
            try:
                stt = os.stat(path)
                sig = f"{int(stt.st_mtime)}:{stt.st_size}"
            except OSError:
                continue
            if state_seen.get(path) == sig:
                continue

            if scanned >= max_scan:
                # Quota for this run exhausted - rest in the next run.
                return bad_songs, scanned, skipped_comp

            scanned += 1
            is_bad, reason, bitrate, qkey, cutoff = _is_bad(path)
            # Update the cache (also remember good files).
            state_seen[path] = sig

            if is_bad:
                artist, title = _read_artist_title(path)
                if not artist or not title:
                    logger.debug(t("upgrade.library.no_artist_title", path=path))
                    continue
                bad_songs.append({
                    "path": path,
                    "artist": artist,
                    "title": title,
                    "reason": reason,
                    "bitrate": bitrate,
                    "quality_key": qkey,
                    "cutoff": cutoff,
                })

    return bad_songs, scanned, skipped_comp
