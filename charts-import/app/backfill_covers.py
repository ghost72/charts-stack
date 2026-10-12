"""
Einmaliges Nachruesten von Covern fuer die bestehende Library.

Scannt MUSIC_DIR rekursiv nach MP3s OHNE eingebettetes Cover, liest Kuenstler +
Titel aus den Tags und holt das passende Cover ueber die konfigurierten Quellen
(COVER_SOURCES, Default iTunes -> Deezer) und bettet es als ID3-Tag ein (keine
separate Bilddatei). Die Trefferauswahl bevorzugt die echte Single/das Original-
Album und sortiert Fremd-Versionen (Remix/Sped Up/Live/Demo/Compilation) aus.

Nutzung (im Container):
  # nur zaehlen/auflisten, nichts aendern:
  python -m app.backfill_covers --dry-run
  # tatsaechlich nachruesten:
  python -m app.backfill_covers

Modi:
  (ohne Flag)   nur MP3s OHNE Cover nachruesten.
  --fix-broken  MP3s mit FEHLERHAFTEM Cover reparieren: rechteckige Cover (z.B.
                CD-Scans mit Vorder+Rueckseite) werden durch das quadratische
                Cover ersetzt. Betrifft NUR die Chart-Songs (die in den
                .m3u-Playlists gelisteten Dateien, PLAYLIST_DIR), NICHT die ganze
                Library - so bleibt deine uebrige (Lidarr-)Sammlung unangetastet.
                Quadratische Cover bleiben ohnehin unangetastet.
  --force-refresh Ersetzt bei ALLEN Chart-Songs das Cover durch das neu gesuchte
                Cover (Quelle laut COVER_SOURCES, i.d.R. iTunes-Single-Artwork),
                auch bei bereits quadratischen. Noetig fuer Kassetten-/CD-Scans
                (quadratisch, aber falsches Bild) UND um alte, schlechtere Cover
                (z.B. Demo-EP statt Single) durch das bessere zu ersetzen.
                Beispiel: "Dominic Fike - Babydoll" hatte das Demo-EP-Cover;
                jetzt wird das dedizierte "Babydoll / White Keys - Single"-Cover
                eingebettet.
  --all-charts  Bezieht den Umfang aus dem MANIFEST (alle je von charts-download
                geladenen Songs) statt aus den aktuellen Playlists. Erfasst auch
                Songs, die aus den Charts gefallen sind, aber noch in der Library
                liegen. Deine uebrige (Lidarr-)Library bleibt unangetastet. Meist
                kombiniert mit --force-refresh, um wirklich ALLE je geladenen
                Chart-Cover neu (z.B. von iTunes) zu holen.
  --dry-run     nichts aendern, nur auflisten/zaehlen.

Nutzung (im Container):
  python -m app.backfill_covers --dry-run                # fehlende zaehlen
  python -m app.backfill_covers                          # fehlende nachruesten
  python -m app.backfill_covers --fix-broken --dry-run   # + fehlerhafte auflisten
  python -m app.backfill_covers --fix-broken             # + fehlerhafte reparieren
  python -m app.backfill_covers --force-refresh --dry-run # ALLE Chart-Cover (Vorschau)
  python -m app.backfill_covers --force-refresh          # ALLE Chart-Cover neu holen

Umgebungsvariablen:
  MUSIC_DIR              Library-Root (Default wie im Import).
  PLAYLIST_DIR           Ordner mit den charts-sync-Playlists (.m3u). Quelle
                         fuer --fix-broken (nur Chart-Songs). Default = MUSIC_DIR.
  COVER_SOURCES          Quellen-Reihenfolge (Default itunes,deezer).
  BACKFILL_SLEEP_MS      Pause zwischen API-Calls in ms (Default 400),
                         schont die APIs (Rate-Limit).
  BACKFILL_LIMIT         Max. Anzahl zu bearbeitender Dateien (0 = alle).

Sicher: Ohne --fix-broken werden vorhandene Cover nie angetastet. Mit
--fix-broken werden NUR rechteckige Cover ersetzt (und nur, wenn eine Quelle ein
Cover liefert - sonst bleibt das alte erhalten). Fehler bei einer Datei stoppen
den Lauf nicht.
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
    """Liest Artist/Titel aus den Tags (fuer die Deezer-Suche)."""
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
    Liefert die MP3-Pfade ALLER je von charts-download geladenen Songs, indem
    das Manifest (MANIFEST_FILE) gelesen wird. Anders als _iter_playlist_mp3
    erfasst das auch Chart-Songs, die aktuell in KEINER Playlist mehr stehen
    (z.B. aus den Charts gefallen), aber weiterhin in der Library liegen.

    Deine uebrige (Lidarr-)Library bleibt unangetastet, weil nur Dateien aus dem
    Manifest betrachtet werden. Gesucht wird der Song ueber Artist/Album/Titel
    aus dem Manifest im MUSIC_DIR (der Datei-Basename im Manifest ist der
    Download-Name, nicht zwingend der finale Library-Pfad - deshalb suchen wir
    ueber die Tags/den Titel im Artist-Ordner).
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
    # Index Titel(normalisiert) -> Pfade im MUSIC_DIR, einmal aufbauen.
    from .importer import _title_key, _extract_title_from_filename
    for artist_entry, meta in entries.items():
        artist = (meta or {}).get("artist", "")
        title = (meta or {}).get("title", "")
        if not artist or not title:
            continue
        # Kuenstler-Ordner (bereinigt) bestimmen.
        from .tags import _clean_component
        safe_artist = _clean_component(artist, "Unknown Artist")
        artist_dir = os.path.join(music_dir, safe_artist)
        if not os.path.isdir(artist_dir):
            continue
        want = _title_key(title)
        # Im Artist-Ordner nach einer Datei mit passendem Titel suchen.
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
    Liefert die MP3-Pfade, die in den charts-sync-Playlists (.m3u in
    playlist_dir) gelistet sind - also NUR die Chart-Songs. So bleibt die
    uebrige (Lidarr-)Library beim --fix-broken unangetastet.

    Behandelt sowohl absolute Pfade als auch relative (zum playlist_dir).
    Dedupliziert, damit ein Song aus mehreren Playlists nur einmal geprueft wird.
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
                    # Pfad aufloesen: absolut, sonst relativ zum Playlist-Ordner.
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
    # Generischer Flag-Name (Quelle ist ueber COVER_SOURCES konfigurierbar).
    force_refresh = "--force-refresh" in sys.argv
    # --all-charts: ueber ALLE je geladenen Chart-Songs (Manifest) statt nur die
    # aktuell in Playlists gelisteten. Erfasst auch aus den Charts gefallene Songs.
    all_charts = "--all-charts" in sys.argv
    music_dir = config.MUSIC_DIR

    # Scope: Manifest (--all-charts) hat Vorrang, sonst Playlist bei
    # --fix-broken/--force-refresh, sonst ganze Library.
    playlist_scope = fix_broken or force_refresh or all_charts

    mode = t("import.covers.mode_dry") if dry_run else t("import.covers.mode_embed")
    if force_refresh:
        scope = t("import.covers.scope_refresh", sources=",".join(config.COVER_SOURCES))
    elif fix_broken:
        scope = t("import.covers.scope_broken")
    else:
        scope = t("import.covers.scope_missing")
    # --fix-broken/--force-refresh beschraenken sich BEWUSST auf die Chart-Songs
    # (Playlist-Eintraege), damit deine uebrige (Lidarr-)Library unangetastet bleibt.
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

    # Iterator waehlen: --all-charts -> Manifest, sonst Playlist-Scope ->
    # Chart-Playlists, sonst ganze Library.
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
            # Jeden Chart-Song anfassen und das Cover neu holen (Quelle laut
            # COVER_SOURCES) - auch quadratische. Noetig fuer Kassetten-/CD-Scans
            # (quadratisch, aber falsches Bild) und um alte, schlechtere Cover
            # (z.B. Demo-EP) durch das bessere Single-Cover zu ersetzen.
            if status == "none":
                without_cover += 1
            else:
                broken_cover += 1
        else:
            # Quadratische und (ohne --fix-broken) auch unklare/vorhandene Cover
            # ueberspringen.
            if status == "square":
                continue
            if status in ("unknown",) and not fix_broken:
                continue
            if status == "none":
                without_cover += 1
            elif status == "nonsquare":
                if not fix_broken:
                    continue  # ohne --fix-broken rechteckige nicht anfassen
                broken_cover += 1
            else:
                # unknown + fix_broken: konservativ nicht ersetzen (koennte ok sein)
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

        # overwrite=True bei rechteckigem Cover oder --force-refresh (ersetzen),
        # sonst normal einbetten (nur wenn keins vorhanden).
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
