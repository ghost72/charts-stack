"""
One-off retrofitting of covers for the existing library.

Scans MUSIC_DIR recursively for MP3s WITHOUT an embedded cover, reads artist +
title from the tags, fetches the matching cover via the configured sources
(COVER_SOURCES, default iTunes -> Deezer) and embeds it as an ID3 tag (no
separate image file). Hit selection prefers the real single/the original album
and sorts out foreign versions (remix/sped up/live/demo/compilation).

Usage (in the container):
  # only count/list, change nothing:
  python -m app.backfill_covers --dry-run
  # actually retrofit:
  python -m app.backfill_covers

Modes:
  (no flag)     retrofit only MP3s WITHOUT a cover.
  --fix-broken  repair MP3s with a FAULTY cover: rectangular covers (e.g. CD
                scans with front+back) are replaced with the square cover.
                Affects ONLY the chart songs (the files listed in the .m3u
                playlists, PLAYLIST_DIR), NOT the whole library - so your rest
                of the (Lidarr) collection stays untouched. Square covers stay
                untouched anyway.
  --force-refresh Replaces the cover of ALL chart songs with the newly searched
                cover (source per COVER_SOURCES, usually iTunes single artwork),
                even on already-square ones. Needed for cassette/CD scans
                (square but wrong image) AND to replace old, worse covers (e.g.
                a demo EP instead of the single) with the better one.
                Example: "Dominic Fike - Babydoll" had the demo-EP cover; now
                the dedicated "Babydoll / White Keys - Single" cover is embedded.
  --all-charts  Takes the scope from the MANIFEST (all songs ever loaded by
                charts-download) instead of the current playlists. Also covers
                songs that fell out of the charts but still sit in the library.
                Your rest of the (Lidarr) library stays untouched. Usually
                combined with --force-refresh to really fetch ALL ever-loaded
                chart covers anew (e.g. from iTunes).
  --dry-run     change nothing, only list/count.

Usage (in the container):
  python -m app.backfill_covers --dry-run                # count the missing ones
  python -m app.backfill_covers                          # retrofit the missing ones
  python -m app.backfill_covers --fix-broken --dry-run   # + list the faulty ones
  python -m app.backfill_covers --fix-broken             # + repair the faulty ones
  python -m app.backfill_covers --force-refresh --dry-run # ALL chart covers (preview)
  python -m app.backfill_covers --force-refresh          # re-fetch ALL chart covers

Environment variables:
  MUSIC_DIR              library root (default as in the import).
  PLAYLIST_DIR           folder with the charts-sync playlists (.m3u). Source
                         for --fix-broken (chart songs only). Default = MUSIC_DIR.
  COVER_SOURCES          source order (default itunes,deezer).
  BACKFILL_SLEEP_MS      pause between API calls in ms (default 400), eases the
                         APIs (rate limit).
  BACKFILL_LIMIT         max number of files to process (0 = all).

Safe: without --fix-broken existing covers are never touched. With --fix-broken
ONLY rectangular covers are replaced (and only if a source returns a cover -
otherwise the old one is kept). Errors on one file do not stop the run.
"""

import logging
import os
import sys
import time

from mutagen import File as MutagenFile

from . import config, cover
from .i18n import t

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backfill-covers")

_SLEEP_MS = int(os.environ.get("BACKFILL_SLEEP_MS", "400"))
_LIMIT = int(os.environ.get("BACKFILL_LIMIT", "0"))


def _read_artist_title(path: str) -> tuple[str, str]:
    """Reads artist/title from the tags (for the Deezer search)."""
    try:
        audio = MutagenFile(path, easy=True)
        if audio is None:
            return "", ""
        artist = (audio.get("artist") or audio.get("albumartist") or [""])[0]
        title = (audio.get("title") or [""])[0]
        return artist.strip(), title.strip()
    except Exception:
        return "", ""


def _iter_mp3(root: str):
    for dirpath, _d, files in os.walk(root):
        for name in files:
            if name.lower().endswith(".mp3"):
                yield os.path.join(dirpath, name)


def _iter_manifest_mp3(music_dir: str):
    """
    Yields the MP3 paths of ALL songs ever loaded by charts-download, by
    reading the manifest (MANIFEST_FILE). Unlike _iter_playlist_mp3 this also
    covers chart songs that are currently in NO playlist anymore (e.g. fell out
    of the charts) but still sit in the library.

    Your rest of the (Lidarr) library stays untouched, because only files from
    the manifest are considered. The song is located via artist/album/title
    from the manifest in MUSIC_DIR (the file basename in the manifest is the
    download name, not necessarily the final library path - so we search via
    the tags/the title in the artist folder).
    """
    import json

    seen: set[str] = set()
    try:
        with open(config.MANIFEST_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        entries = data.get("entries", {}) if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError) as e:
        logger.error(t("import.covers.manifest_unreadable", path=config.MANIFEST_FILE, error=e))
        return
    if not entries:
        logger.warning(t("import.covers.manifest_empty", path=config.MANIFEST_FILE))
        return

    logger.info(t("import.covers.manifest_scan", count=len(entries), music_dir=music_dir))
    # Build the index title(normalized) -> paths in MUSIC_DIR once.
    from .importer import _title_key, _extract_title_from_filename
    for artist_entry, meta in entries.items():
        artist = (meta or {}).get("artist", "")
        title = (meta or {}).get("title", "")
        if not artist or not title:
            continue
        # Determine the (cleaned) artist folder.
        from .tags import _clean_component
        safe_artist = _clean_component(artist, "Unknown Artist")
        artist_dir = os.path.join(music_dir, safe_artist)
        if not os.path.isdir(artist_dir):
            continue
        want = _title_key(title)
        # Search the artist folder for a file with a matching title.
        for dirpath, _d, files in os.walk(artist_dir):
            for name in files:
                if not name.lower().endswith(".mp3"):
                    continue
                if _title_key(_extract_title_from_filename(name)) == want:
                    p = os.path.normpath(os.path.join(dirpath, name))
                    if p not in seen:
                        seen.add(p)
                        yield p


def _iter_playlist_mp3(playlist_dir: str, music_dir: str):
    """
    Yields the MP3 paths listed in the charts-sync playlists (.m3u in
    playlist_dir) - i.e. ONLY the chart songs. This keeps the rest of the
    (Lidarr) library untouched on --fix-broken.

    Handles both absolute paths and relative ones (to playlist_dir).
    Deduplicates so a song in several playlists is checked only once.
    """
    seen: set[str] = set()
    if not os.path.isdir(playlist_dir):
        logger.error(t("import.covers.no_playlist_dir", path=playlist_dir))
        return
    m3us = [os.path.join(playlist_dir, f) for f in os.listdir(playlist_dir)
            if f.lower().endswith(".m3u")]
    if not m3us:
        logger.warning(t("import.covers.no_playlists", path=playlist_dir))
        return
    logger.info(t("import.covers.playlists", playlists=", ".join(
        os.path.basename(m) for m in m3us)))
    for m3u in m3us:
        try:
            with open(m3u, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    # Resolve the path: absolute, otherwise relative to the playlist folder.
                    path = line if os.path.isabs(line) else os.path.join(playlist_dir, line)
                    path = os.path.normpath(path)
                    if not path.lower().endswith(".mp3"):
                        continue
                    if path in seen:
                        continue
                    seen.add(path)
                    if os.path.isfile(path):
                        yield path
                    else:
                        logger.debug(t("import.covers.playlist_entry_missing", path=path))
        except Exception as e:
            logger.warning(t("import.covers.playlist_read_failed", path=m3u, error=e))


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    fix_broken = "--fix-broken" in sys.argv
    # Generic flag name (the source is configurable via COVER_SOURCES).
    force_refresh = "--force-refresh" in sys.argv
    # --all-charts: over ALL ever-loaded chart songs (manifest) instead of only
    # the ones currently listed in playlists. Also covers songs fallen out of the charts.
    all_charts = "--all-charts" in sys.argv
    music_dir = config.MUSIC_DIR

    # Scope: manifest (--all-charts) takes precedence, otherwise playlist on
    # --fix-broken/--force-refresh, otherwise the whole library.
    playlist_scope = fix_broken or force_refresh or all_charts

    mode = t("import.covers.mode_dry") if dry_run else t("import.covers.mode_embed")
    if force_refresh:
        scope = t("import.covers.scope_refresh", sources=",".join(config.COVER_SOURCES))
    elif fix_broken:
        scope = t("import.covers.scope_broken")
    else:
        scope = t("import.covers.scope_missing")
    # --fix-broken/--force-refresh DELIBERATELY restrict themselves to the chart
    # songs (playlist entries) so the rest of your (Lidarr) library stays untouched.
    if all_charts:
        quelle = t("import.covers.source_manifest", music_dir=music_dir)
    elif playlist_scope:
        quelle = t("import.covers.source_playlists", path=config.PLAYLIST_DIR)
    else:
        quelle = t("import.covers.source_library", path=music_dir)
    logger.info(t("import.covers.started", source=quelle, mode=mode,
                  scope=scope, sleep=_SLEEP_MS))

    if not os.path.isdir(music_dir):
        logger.error(t("import.covers.no_music_dir", path=music_dir))
        return

    # Choose the iterator: --all-charts -> manifest, otherwise playlist scope ->
    # chart playlists, otherwise the whole library.
    if all_charts:
        source_iter = _iter_manifest_mp3(music_dir)
    elif playlist_scope:
        source_iter = _iter_playlist_mp3(config.PLAYLIST_DIR, music_dir)
    else:
        source_iter = _iter_mp3(music_dir)

    total = 0
    without_cover = 0
    broken_cover = 0
    embedded = 0
    replaced = 0
    not_found = 0
    skipped_no_tags = 0

    for path in source_iter:
        total += 1
        status = cover.cover_status(path)  # none / square / nonsquare / unknown

        if force_refresh:
            # Touch every chart song and re-fetch the cover (source per
            # COVER_SOURCES) - including square ones. Needed for cassette/CD
            # scans (square but wrong image) and to replace old, worse covers
            # (e.g. demo EP) with the better single cover.
            if status == "none":
                without_cover += 1
            else:
                broken_cover += 1
        else:
            # Skip square and (without --fix-broken) also unclear/present covers.
            if status == "square":
                continue
            if status in ("unknown",) and not fix_broken:
                continue
            if status == "none":
                without_cover += 1
            elif status == "nonsquare":
                if not fix_broken:
                    continue  # without --fix-broken do not touch rectangular ones
                broken_cover += 1
            else:
                # unknown + fix_broken: conservatively do not replace (could be ok)
                continue

        artist, title = _read_artist_title(path)
        if not artist or not title:
            skipped_no_tags += 1
            logger.debug(t("import.covers.no_tags_skipped", path=path))
            continue

        rel = os.path.relpath(path, music_dir)
        if dry_run:
            if force_refresh:
                label = t("import.covers.label_refresh")
            elif status == "none":
                label = t("import.covers.label_missing")
            else:
                label = t("import.covers.label_nonsquare")
            logger.info(t("import.covers.dry_entry", label=label, artist=artist,
                          title=title, rel=rel))
            continue

        if _LIMIT and (embedded + replaced) >= _LIMIT:
            logger.info(t("import.covers.limit_reached", limit=_LIMIT))
            break

        # overwrite=True on a rectangular cover or --force-refresh (replace),
        # otherwise embed normally (only if none is present).
        overwrite = force_refresh or status == "nonsquare"
        if cover.embed_cover(path, artist, title, overwrite=overwrite):
            if overwrite:
                replaced += 1
                logger.info(t("import.covers.replaced", count=replaced,
                              artist=artist, title=title))
            else:
                embedded += 1
                logger.info(t("import.covers.embedded", count=embedded,
                              artist=artist, title=title))
        else:
            not_found += 1
            logger.info(t("import.covers.not_found", artist=artist, title=title))

        time.sleep(_SLEEP_MS / 1000.0)

    logger.info(t("import.covers.summary", total=total, without_cover=without_cover,
                  broken=broken_cover, embedded=embedded, replaced=replaced,
                  not_found=not_found, no_tags=skipped_no_tags))
    if dry_run:
        logger.info(t("import.covers.dry_notice"))


if __name__ == "__main__":
    main()
