"""
Shared recanonicalize logic for UPPERCASE flashback songs.

An UPPERCASE name arises when Deezer could not provide the canonical (official)
spelling at import (alias artists, titles misnamed by FLASHBACK) OR the Deezer
search was unreachable at the time. The song is then imported in UPPERCASE and
flagged for review.

This module captures the resolver path once so both the manual one-off script
(scripts/recanonicalize_flashback.py) and the automatic metadata backfill
(app/backfill.py) use THE SAME conservative code:

  resolve_canonical() returns a new spelling only on a RELIABLE Deezer hit -
  otherwise the song stays unchanged (never guess). On a hit the library
  folder/file is renamed, the title/album tags are set (an existing year
  stays) and the manifest entry is updated.

Idempotent: already-corrected (mixed-case) entries do not count as UPPERCASE
suspects; if the target folder already exists, it is not overwritten.
"""

import logging
import os

from mutagen import File as MutagenFile

from . import config, tags, deezer
from .i18n import t

logger = logging.getLogger("charts-import.recanon")

CHOWN_UID, CHOWN_GID = 1027, 100


def is_upper(s: str) -> bool:
    """UPPERCASE heuristic: longer than 3 characters, contains letters, fully
    uppercase. Short acronyms (ABBA, R.E.M., INXS) deliberately do NOT fall
    under it if they are <= 3 characters - longer real acronyms are renamed by
    Deezer only when there is a safe alternative spelling anyway."""
    s = (s or "").strip()
    return bool(s) and len(s) > 3 and any(c.isalpha() for c in s) and s == s.upper()


def needs_fix(meta: dict) -> bool:
    """True if a manifest entry is an UPPERCASE flashback suspect."""
    if (meta.get("source") or "") != "flashback":
        return False
    return is_upper(meta.get("title") or "") or is_upper(meta.get("artist") or "")


def _read_year(path: str) -> str:
    try:
        audio = MutagenFile(path, easy=True)
        if audio is None:
            return ""
        for key in ("date", "originaldate", "year"):
            v = audio.get(key)
            if v:
                s = str(v[0] if isinstance(v, list) else v)
                if len(s) >= 4 and s[:4].isdigit():
                    return s[:4]
    except Exception:
        pass
    return ""


def _chown(path: str) -> None:
    try:
        os.chown(path, CHOWN_UID, CHOWN_GID)
    except OSError:
        pass


def _rename(src: str, dst: str, dry: bool, log) -> bool:
    if src == dst or not os.path.exists(src):
        return False
    casing_only = src.lower() == dst.lower()
    if not casing_only and os.path.exists(dst):
        log(t("import.recanon.target_exists", dst=dst))
        return False
    if dry:
        log(t("import.recanon.dry_rename", src=src, dst=dst))
        return True
    if casing_only:
        tmp = src + ".recanontmp"
        os.rename(src, tmp)
        os.rename(tmp, dst)
    else:
        os.rename(src, dst)
    _chown(dst)
    log(t("import.recanon.rename", src=src, dst=dst))
    return True


def _find_audio(album_dir: str, title: str) -> "str | None":
    if not os.path.isdir(album_dir):
        return None
    for name in os.listdir(album_dir):
        if name == "@eaDir":
            continue
        base, ext = os.path.splitext(name)
        if ext.lower() in config.AUDIO_EXTENSIONS and base.lower() == title.lower():
            return os.path.join(album_dir, name)
    return None


def clear_cache_for(items: "list[dict]") -> int:
    """Invalidates the Deezer cache entries of the given manifest metas so they
    are re-resolved with the current matching (otherwise the cache may return an
    old UPPERCASE miss from before the cache fix). Returns: number of removed
    entries."""
    try:
        cache = deezer._load_cache()
    except Exception as e:
        logger.debug(t("import.recanon.cache_unloadable", error=e))
        return 0
    removed = 0
    for meta in items:
        ck = deezer._key(meta.get("artist", ""), meta.get("title", ""))
        if ck in cache:
            del cache[ck]
            removed += 1
    if removed:
        try:
            deezer._save_cache()
        except Exception as e:
            logger.debug(t("import.recanon.cache_save_failed", error=e))
    return removed


def recanonicalize_entry(entries: dict, key: str, meta: dict, music: str,
                         dry_run: bool = False, log=None) -> str:
    """
    Tries to correct ONE UPPERCASE flashback manifest entry to the official
    Deezer spelling.

    Returns: "fixed" (safely corrected), "manual" (no reliable Deezer hit ->
    left unchanged, may need manual review) or "noop" (audio file not found or
    similar). On "fixed" updates the given entries dict in place (artist/title);
    the caller persists the manifest.

    log: optional function log(fmt, *args) (default: logger.info).
    """
    if log is None:
        def log(fmt, *args):
            logger.info(fmt, *args)

    old_a = (meta.get("artist") or "").strip()
    old_t = (meta.get("title") or "").strip()
    new_a, new_t = deezer.resolve_canonical(old_a, old_t)
    if (new_a, new_t) == (old_a, old_t):
        return "manual"  # no reliable hit -> NEVER guess

    log(t("import.recanon.resolved", old_artist=old_a, old_title=old_t,
          new_artist=new_a, new_title=new_t))

    canon_old_a = tags.canonical_artist(music, old_a) or old_a
    safe_old_a = tags._clean_component(canon_old_a, "Unknown Artist")
    safe_old_t = tags._clean_component(old_t, "track")
    safe_new_a = tags._clean_component(new_a, "Unknown Artist")
    safe_new_t = tags._clean_component(new_t, "track")

    old_artist_dir = os.path.join(music, safe_old_a)
    new_artist_dir = os.path.join(music, safe_new_a)
    if safe_old_a != safe_new_a:
        _rename(old_artist_dir, new_artist_dir, dry_run, log)
    base_artist_dir = new_artist_dir if os.path.isdir(new_artist_dir) else old_artist_dir

    old_album_dir = os.path.join(base_artist_dir, safe_old_t)
    new_album_dir = os.path.join(base_artist_dir, safe_new_t)
    if safe_old_t != safe_new_t:
        _rename(old_album_dir, new_album_dir, dry_run, log)
    target_album_dir = new_album_dir if os.path.isdir(new_album_dir) else old_album_dir

    f = _find_audio(target_album_dir, old_t) or _find_audio(target_album_dir, new_t)
    if not f:
        log(t("import.recanon.audio_not_found", dir=target_album_dir))
        return "noop"

    ext = os.path.splitext(f)[1]
    new_file = os.path.join(target_album_dir, safe_new_t + ext)
    if os.path.basename(f).lower() != os.path.basename(new_file).lower():
        _rename(f, new_file, dry_run, log)
    target_file = new_file if os.path.isfile(new_file) else f

    if not dry_run:
        year = _read_year(target_file)
        tags.write_tags(target_file, new_a, new_t, new_t, year=year or None)
        date_suffix = t("import.recanon.tags_date_suffix", year=year) if year else ""
        log(t("import.recanon.tags_set", title=new_t, date_suffix=date_suffix))
        entries[key]["artist"] = new_a
        entries[key]["title"] = new_t

    return "fixed"
