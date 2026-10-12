"""
charts-upgrade – Hauptservice.

Raeumt den ALTBESTAND der Navidrome-Library qualitativ auf: findet Songs mit
schlechter Qualitaet (niedrige echte Bitrate ODER upgesampelte/gefaelschte
Bitrate) und sucht bei slskd eine echte bessere Version. Ersetzt wird NUR, wenn
die neue Datei laut echtem Frequenz-Cutoff wirklich besser ist.

Ablauf pro Lauf:
  1. Library scannen (library.scan) - Compilations werden uebersprungen, die
     ffmpeg-Cutoff-Analyse ist im State gecacht (pro Datei nur einmal).
  2. Fuer jeden schlechten Song bei slskd suchen (Anbieter, die frueher eine
     nicht-bessere Datei lieferten, werden ausgefiltert).
  3. DRY_RUN=1 (Standard): nur melden, was gefunden/geladen WUERDE.
     DRY_RUN=0: besten Kandidaten laden, auf Abschluss warten, die geladene
     Datei per echtem Cutoff mit der vorhandenen vergleichen.
       - neue Datei besser -> Manifest-Eintrag mit replace=true; charts-import
         ersetzt die vorhandene Datei (alte in den Papierkorb).
       - neue Datei NICHT besser -> Anbieter fuer diesen Song blocken, Datei
         nicht uebernehmen (charts-import laesst sie mangels replace-Upgrade in
         Ruhe; sie bleibt im Downloads-Ordner und wird spaeter vom
         charts-import-Cleanup entfernt).

Stellung in der Kette:
  charts-upgrade (findet schlechte Altbestand-Songs, laedt Ersatz + Manifest)
  -> slskd -> /Charts -> charts-import (ersetzt bei echtem Cutoff-Upgrade)
"""

import logging
import os
import time

from . import config, library, manifest, notify, quality, slsk, state, status
from .i18n import t

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("charts-upgrade")

# Wie lange (Sekunden) maximal auf den Abschluss eines Downloads gewartet wird,
# bevor die geladene Datei gemessen werden kann. Konservativ; laeuft der
# Download laenger, wird er in einem spaeteren Lauf ausgewertet.
_DOWNLOAD_WAIT_SECONDS = 180
_BLOCK_TTL_DAYS = 30


def run_once() -> dict:
    """Fuehrt einen Upgrade-Lauf aus. Rueckgabe: Zaehler fuer das Dashboard."""
    logger.info(t("upgrade.run.start", dry_run="1" if config.DRY_RUN else "0"))

    if not quality.available():
        logger.warning(t("upgrade.run.ffmpeg_unavailable"))

    st = state.load()

    # 1. Library scannen (mit gecachtem Analyse-Ergebnis).
    bad_songs, scanned, skipped_comp = library.scan(st["seen"], config.MAX_SCAN_PER_RUN)
    state.save(st)  # Analyse-Cache sichern (auch wenn danach nichts geladen wird)

    # Scan-Fortschritt fuers Dashboard: wie viele MP3s insgesamt pruefbar sind
    # (scan_total) und wie viele davon schon analysiert wurden (scan_seen).
    # scan_seen aus dem 'seen'-Cache; auf scan_total deckeln, falls der Cache
    # verwaiste Eintraege (geloeschte/umbenannte Dateien) enthaelt -> nie >100%.
    scan_total = library.count_candidates()
    scan_seen = min(len(st.get("seen", {})), scan_total) if scan_total else 0

    logger.info(t("upgrade.scan.summary", scanned=scanned, skipped=skipped_comp,
                  bad=len(bad_songs), scan_seen=scan_seen, scan_total=scan_total))

    if not bad_songs:
        logger.info(t("upgrade.run.no_bad_songs"))
        _notify_summary([], [], scanned, skipped_comp)
        return {"scanned": scanned, "bad": 0, "upgraded": 0,
                "scan_seen": scan_seen, "scan_total": scan_total}

    # 2./3. Ersatz suchen (+ ggf. laden/pruefen/ersetzen).
    try:
        client = slsk.connect()
    except slsk.SlskdError as e:
        logger.error(t("upgrade.run.slskd_unreachable", error=e))
        raise

    planned: list[dict] = []    # DRY_RUN / Meldung
    upgraded: list[dict] = []   # tatsaechlich als Upgrade angestossen
    processed = 0

    for song in bad_songs:
        if processed >= config.MAX_PER_RUN:
            logger.info(t("upgrade.run.max_per_run", max=config.MAX_PER_RUN))
            break

        artist, title = song["artist"], song["title"]
        if state.already_requested(st, artist, title):
            logger.debug(t("upgrade.song.already_requested", artist=artist, title=title))
            continue

        processed += 1
        logger.info(t("upgrade.song.bad", reason=song["reason"], artist=artist, title=title))

        blocked = state.blocked_providers_for(st, artist, title, _BLOCK_TTL_DAYS)
        try:
            candidates = slsk.find_candidates(client, artist, title, blocked_users=blocked)
        except slsk.SlskdError as e:
            logger.warning(t("upgrade.run.slskd_aborted", error=e))
            processed -= 1
            break

        if not candidates:
            logger.info(t("upgrade.song.no_replacement", min=config.MIN_BITRATE))
            continue

        best_user, best_file, best_score = candidates[0]
        new_bitrate = best_file.get("bitRate", 0) or 0

        if config.DRY_RUN:
            logger.info(t("upgrade.song.dry_run_would_load",
                          filename=best_file.get("filename", "?"),
                          bitrate=new_bitrate, user=best_user))
            planned.append({**song, "new_bitrate": new_bitrate})
            continue

        # --- LIVE: laden, auf Abschluss warten, messen, vergleichen ---
        chosen = _download_first_working(client, candidates)
        if not chosen:
            logger.warning(t("upgrade.song.no_provider_delivered"))
            continue
        username, file_dict = chosen
        fname = file_dict.get("filename", "?")

        local_path = _wait_for_file(client, username, fname)
        if not local_path:
            logger.info(t("upgrade.song.download_not_ready"))
            # Als angefragt vermerken, damit nicht sofort erneut geladen wird.
            state.mark_requested(st, artist, title, username, fname)
            continue

        # Echten Cutoff-Vergleich: ist die neue Datei WIRKLICH besser?
        old_q = quality.quality_key(song["path"], song["bitrate"])
        new_q = quality.quality_key(local_path, new_bitrate)
        if new_q > old_q:
            logger.info(t("upgrade.song.upgrade_confirmed", old_q=old_q, new_q=new_q))
            manifest.record(fname, artist, title, config.SOURCE_LABEL, replace=True)
            state.mark_requested(st, artist, title, username, fname)
            upgraded.append({**song, "new_bitrate": new_bitrate})
        else:
            logger.info(t("upgrade.song.not_better", new_q=new_q, old_q=old_q))
            state.block_provider(st, artist, title, username)

    state.save(st)
    _notify_summary(planned, upgraded, scanned, skipped_comp)
    return {
        "scanned": scanned,
        "bad": len(bad_songs),
        "planned": len(planned),
        "upgraded": len(upgraded),
        "scan_seen": scan_seen,
        "scan_total": scan_total,
    }


def _download_first_working(client, candidates):
    """Stoesst die Kandidaten der Reihe nach an, bis ein enqueue klappt."""
    for cand_user, cand_file, _score in candidates:
        if slsk.enqueue(client, cand_user, cand_file):
            return cand_user, cand_file
        logger.info(t("upgrade.song.provider_no_deliver", user=cand_user))
    return None


def _wait_for_file(client, username: str, remote_filename: str) -> str | None:
    """
    Wartet, bis der Download abgeschlossen ist, und liefert den LOKALEN Pfad der
    fertigen Datei im Downloads-Ordner (config.MUSIC_DIR ist die Library; die
    Downloads liegen im geteilten /charts-Ordner, den slskd als downloads nutzt
    - wir suchen die Datei ueber ihren Basename dort). None bei Timeout/Fehler.

    Der Downloads-Ordner ist ueber DOWNLOADS_DIR bekannt (identisch zum
    charts-import DOWNLOADS_DIR = /charts). Wir leiten ihn aus MANIFEST_FILE ab,
    das im selben Ordner liegt.
    """
    downloads_dir = os.path.dirname(config.MANIFEST_FILE)
    base = os.path.basename(remote_filename.replace("\\", "/"))

    deadline = time.time() + _DOWNLOAD_WAIT_SECONDS
    while time.time() < deadline:
        found = _find_file(downloads_dir, base)
        if found and _is_stable(found):
            return found
        time.sleep(5)
    return None


def _find_file(root: str, basename: str) -> str | None:
    """Sucht rekursiv nach einer Datei mit passendem Basename (case-insensitiv)."""
    target = basename.lower()
    if not os.path.isdir(root):
        return None
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if name.lower() == target:
                return os.path.join(dirpath, name)
    return None


def _is_stable(path: str, min_age: int = 10) -> bool:
    """True, wenn die Datei seit min_age Sekunden nicht mehr veraendert wurde."""
    try:
        return (time.time() - os.path.getmtime(path)) >= min_age
    except OSError:
        return False


def _notify_summary(planned: list[dict], upgraded: list[dict],
                    scanned: int, skipped_comp: int) -> None:
    """Loggt und meldet (Pushover) eine Zusammenfassung."""
    if config.DRY_RUN:
        if not planned:
            logger.info(t("upgrade.notify.dry_run_done_nothing"))
            return
        logger.info(t("upgrade.notify.dry_run_done", count=len(planned)))
        lines = [t("upgrade.notify.dry_run_header", count=len(planned)), ""]
        for s in planned[:15]:
            lines.append(t("upgrade.notify.song_entry", artist=s['artist'], title=s['title']))
            lines.append(t("upgrade.notify.reason", reason=s['reason']))
        if len(planned) > 15:
            lines.append(t("upgrade.notify.more", count=len(planned) - 15))
        notify.send(t("upgrade.notify.title_dry"), "\n".join(lines))
    else:
        if not upgraded:
            logger.info(t("upgrade.notify.live_done_nothing"))
            return
        logger.info(t("upgrade.notify.live_done", count=len(upgraded)))
        lines = [t("upgrade.notify.upgraded_header", count=len(upgraded)), ""]
        for s in upgraded[:15]:
            lines.append(t("upgrade.notify.song_entry", artist=s['artist'], title=s['title']))
            lines.append(t("upgrade.notify.reason_bitrate", reason=s['reason'],
                           bitrate=s.get('new_bitrate', '?')))
        if len(upgraded) > 15:
            lines.append(t("upgrade.notify.more", count=len(upgraded) - 15))
        notify.send(t("upgrade.notify.title_live"), "\n".join(lines))


def _next_run_iso() -> str:
    """Zeitpunkt des naechsten Laufs als ISO-8601 (fuer das Dashboard)."""
    return time.strftime("%Y-%m-%dT%H:%M:%S%z",
                         time.localtime(time.time() + max(1, config.INTERVAL_MINUTES) * 60))


def _guarded_run() -> None:
    """Fuehrt run_once() aus und schreibt den Status (Erfolg/Fehler)."""
    status.start()
    try:
        counters = run_once() or {}
        # scan_seen/scan_total sind Fortschritts-Metadaten (eigene Dashboard-
        # Zeile), nicht Teil der Lauf-Zusammenfassung -> aus der 'message'
        # raushalten. Sie bleiben in 'counters' fuers Dashboard erhalten.
        _msg_skip = {"scan_seen", "scan_total"}
        parts = [f"{v} {k}" for k, v in counters.items()
                 if k not in _msg_skip and isinstance(v, (int, float)) and v]
        msg = ", ".join(parts) if parts else "nichts zu tun"
        status.finish("ok", msg, counters, _next_run_iso())
    except Exception as e:
        logger.exception(t("upgrade.run.error", error=e))
        status.finish("error", str(e), {}, _next_run_iso())


def main() -> None:
    status.init("charts-upgrade")
    mode = t("upgrade.mode.dry_run") if config.DRY_RUN else t("upgrade.mode.live")
    logger.info(t(
        "upgrade.scheduler.started",
        library=config.MUSIC_DIR, slskd=config.SLSKD_HOST, mode=mode,
        bad_bitrate=config.BAD_BITRATE,
        upsampling=t("upgrade.upsampling.on") if config.CHECK_UPSAMPLED
        else t("upgrade.upsampling.off"),
        max_per_run=config.MAX_PER_RUN, max_scan=config.MAX_SCAN_PER_RUN,
        interval=config.INTERVAL_MINUTES,
    ))

    if config.RUN_ON_START:
        _guarded_run()

    interval_seconds = max(1, config.INTERVAL_MINUTES) * 60
    while True:
        logger.info(t("upgrade.scheduler.next_run", interval=config.INTERVAL_MINUTES))
        time.sleep(interval_seconds)
        _guarded_run()


if __name__ == "__main__":
    main()
