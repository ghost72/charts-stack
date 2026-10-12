"""
Periodischer Metadaten-Backfill fuer charts-import.

Hintergrund: Der Import ist "fail-open" - ist beim Einsortieren eine externe
Quelle (iTunes/Deezer-Cover) ODER ffmpeg (ReplayGain) voruebergehend nicht
erreichbar, wird der Song TROTZDEM importiert, dann aber OHNE Cover bzw. OHNE
ReplayGain. Weil charts-import idempotent gegen bereits vorhandene Songs ist,
wuerde dieser Song nie wieder angefasst - die Luecke bliebe dauerhaft.

Dieses Modul holt genau solche Luecken periodisch nach. Es laeuft im selben
Scheduler-Loop wie der Import, aber selten (BACKFILL_INTERVAL_HOURS, Default
24h), und arbeitet NUR ueber die Chart-Songs aus dem Manifest - die uebrige
(Lidarr-)Library bleibt unangetastet.

Es werden die bereits vorhandenen Bausteine wiederverwendet:
  - _iter_manifest_mp3 / _read_artist_title aus backfill_covers.py
  - cover.cover_status / cover.embed_cover
  - replaygain.available / _has_replaygain / apply_track_gain

Zusaetzlich gleicht der Backfill GROSS geschriebene flashback-Namen gegen die
offizielle Deezer-Schreibweise ab und korrigiert sie (nur bei SICHEREM Treffer;
unsichere bleiben unangetastet - nie raten). Das nutzt dieselbe Logik wie das
manuelle Skript scripts/recanonicalize_flashback.py (ausgelagert nach
app/recanon.py) und invalidiert vorher den Deezer-Cache der Kandidaten, damit
alte "eingefrorene" Misses neu aufgeloest werden. Abschaltbar ueber
BACKFILL_RECANON=0.
"""

import json
import logging
import os
import time

from . import config, cover, recanon, replaygain
from .backfill_covers import _iter_manifest_mp3, _read_artist_title
from .i18n import t

logger = logging.getLogger("charts-import.backfill")

# Zeitpunkt des letzten Backfill-Laufs (Monotonic-Clock, prozesslokal). None =
# noch nie gelaufen -> der erste Scheduler-Tick nach dem Start loest ihn aus.
_last_run_monotonic: "float | None" = None


def due() -> bool:
    """True, wenn seit dem letzten Backfill BACKFILL_INTERVAL_HOURS vergangen
    sind (bzw. noch nie gelaufen). Bei Intervall 0 ist der Backfill aus."""
    if config.BACKFILL_INTERVAL_HOURS <= 0:
        return False
    if _last_run_monotonic is None:
        return True
    elapsed = time.monotonic() - _last_run_monotonic
    return elapsed >= config.BACKFILL_INTERVAL_HOURS * 3600


def run_once() -> dict:
    """
    Ein Backfill-Durchlauf ueber die Manifest-Songs. Holt pro Song nach:
      - fehlendes eingebettetes Cover (ueber COVER_SOURCES, nur wenn status
        'none' - vorhandene Cover werden NIE angetastet)
      - fehlendes ReplayGain (nur wenn ffmpeg verfuegbar und noch kein RG-Tag)

    Rueckgabe: Zaehler-Dict (fuer Logging/Status). Fehler je Datei sind nie
    fatal - der Lauf macht mit der naechsten Datei weiter.
    """
    global _last_run_monotonic
    _last_run_monotonic = time.monotonic()

    music_dir = config.MUSIC_DIR
    rg_available = replaygain.available() if config.REPLAYGAIN else False

    scanned = 0
    cover_fixed = 0
    rg_fixed = 0
    errors = 0

    logger.info(t("import.backfill.started", music_dir=music_dir,
                  replaygain=(t("import.backfill.rg_on") if rg_available
                              else t("import.backfill.rg_off"))))

    for path in _iter_manifest_mp3(music_dir):
        scanned += 1
        did_api_call = False
        try:
            # --- Cover nachholen (nur wenn gar keins vorhanden) ---
            if cover.cover_status(path) == "none":
                artist, title = _read_artist_title(path)
                if artist and title:
                    did_api_call = True
                    if cover.embed_cover(path, artist, title, overwrite=False):
                        cover_fixed += 1
                        logger.info(t("import.backfill.cover_added",
                                      count=cover_fixed, artist=artist, title=title))

            # --- ReplayGain nachholen (nur wenn noch keins vorhanden) ---
            if rg_available and not replaygain._has_replaygain(path):
                if replaygain.apply_track_gain(path, overwrite=False):
                    rg_fixed += 1
                    logger.info(t("import.backfill.rg_added", count=rg_fixed, path=path))
        except Exception as e:  # eine kaputte Datei darf den Lauf nicht stoppen
            errors += 1
            logger.debug(t("import.backfill.file_error", path=path, error=e))

        # Rate-Limit nur schonen, wenn wir wirklich eine Cover-API gefragt haben.
        if did_api_call and config.BACKFILL_SLEEP_MS > 0:
            time.sleep(config.BACKFILL_SLEEP_MS / 1000.0)

        if config.BACKFILL_LIMIT and (cover_fixed + rg_fixed) >= config.BACKFILL_LIMIT:
            logger.info(t("import.backfill.limit_reached", limit=config.BACKFILL_LIMIT))
            break

    # --- GROSS-flashback-Namen gegen die offizielle Deezer-Schreibweise ---
    recanon_fixed, recanon_manual = _recanon_pass(music_dir)

    logger.info(t("import.backfill.done", scanned=scanned, cover_fixed=cover_fixed,
                  rg_fixed=rg_fixed, recanon_fixed=recanon_fixed,
                  recanon_manual=recanon_manual, errors=errors))
    return {
        "scanned": scanned,
        "cover_fixed": cover_fixed,
        "rg_fixed": rg_fixed,
        "recanon_fixed": recanon_fixed,
        "recanon_manual": recanon_manual,
        "errors": errors,
    }


def _recanon_pass(music_dir: str) -> "tuple[int, int]":
    """
    Gleicht GROSS geschriebene flashback-Namen aus dem Manifest gegen die
    offizielle Deezer-Schreibweise ab und korrigiert sie (nur sichere Treffer).

    Vorgehen: Manifest laden -> GROSS-Kandidaten finden -> deren Deezer-Cache
    invalidieren (sonst liefern alte eingefrorene Misses weiter GROSS) -> pro
    Kandidat recanon.recanonicalize_entry() -> bei Aenderungen Manifest
    schreiben + Navidrome-Resync anstossen.

    Rueckgabe: (korrigiert, offen/manuell). (0, 0) wenn abgeschaltet oder nichts
    zu tun. Fehler sind nicht fatal.
    """
    if not config.BACKFILL_RECANON:
        return 0, 0
    try:
        with open(config.MANIFEST_FILE, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError) as e:
        logger.debug(t("import.recanon.manifest_unreadable",
                       path=config.MANIFEST_FILE, error=e))
        return 0, 0
    entries = manifest.get("entries", {}) if isinstance(manifest, dict) else {}
    todo = [(k, v) for k, v in entries.items()
            if isinstance(v, dict) and recanon.needs_fix(v)]
    if not todo:
        return 0, 0

    logger.info(t("import.recanon.candidates", count=len(todo)))
    # Alte, evtl. "eingefrorene" Cache-Misses der Kandidaten invalidieren, damit
    # mit dem aktuellen Matching (und ohne Cache-Vergiftung) neu aufgeloest wird.
    cleared = recanon.clear_cache_for([v for _k, v in todo])
    if cleared:
        logger.info(t("import.recanon.cache_invalidated", count=cleared))

    fixed = 0
    manual = 0
    changed = False
    for key, meta in todo:
        try:
            res = recanon.recanonicalize_entry(entries, key, meta, music_dir,
                                               dry_run=False, log=_recanon_log)
        except Exception as e:
            logger.debug(t("import.recanon.entry_error", key=key, error=e))
            continue
        if res == "fixed":
            fixed += 1
            changed = True
        elif res == "manual":
            manual += 1
        # Deezer-Rate-Limit schonen (resolve_canonical macht einen API-Call).
        if config.BACKFILL_SLEEP_MS > 0:
            time.sleep(config.BACKFILL_SLEEP_MS / 1000.0)

    if changed:
        try:
            tmp = config.MANIFEST_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(manifest, fh, ensure_ascii=False, indent=0)
            os.replace(tmp, config.MANIFEST_FILE)
            logger.info(t("import.recanon.manifest_updated", count=fixed))
        except OSError as e:
            logger.error(t("import.recanon.manifest_write_failed", error=e))
        # Navidrome soll die umbenannten Pfade neu einlesen.
        _request_resync()

    return fixed, manual


def _recanon_log(fmt, *args) -> None:
    """Log-Adapter fuer recanon.recanonicalize_entry (nutzt denselben Logger)."""
    logger.info(fmt, *args)


def _request_resync() -> None:
    """Legt die Resync-Signal-Datei an, damit charts-sync/Navidrome die
    umbenannten Pfade zeitnah neu einlesen. Fehler sind nicht fatal."""
    try:
        with open(config.RESYNC_SIGNAL, "w", encoding="utf-8") as fh:
            fh.write("resync")
        logger.info(t("import.recanon.resync_set"))
    except OSError as e:
        logger.debug(t("import.recanon.resync_failed", error=e))
