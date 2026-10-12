"""
charts-import – Hauptservice.

Verbindet die von slskd geladenen Chart-Songs mit der Navidrome-Library:

  1. Import: fertige Downloads aus DOWNLOADS_DIR nach
     MUSIC_DIR/<Artist>/<Album>/<Track> einsortieren (kopieren oder verschieben).
  2. Cleanup: DOWNLOADS_DIR aufraeumen. Standardmaessig anzahl-basiert
     (KEEP_LATEST: die N neuesten Downloads behalten), alternativ alter-basiert
     (RETENTION_DAYS). So bleibt der slskd-Share stabil gefuellt.
  3. Optional: Pushover-Benachrichtigung, wenn neue Songs importiert wurden.

Laeuft als Endlosschleife und wiederholt den Lauf alle INTERVAL_MINUTES Minuten.

Zusammenspiel mit den Schwester-Diensten:
  Deezer -> charts-sync (missing.txt) -> slskd (Download) -> charts-import
  (einsortieren) -> Navidrome sieht den Song -> charts-sync matcht ihn in die
  Playlist.
"""

import json
import logging
import os
import time
import unicodedata
import re

from . import backfill, cleanup, config, importer, lidarr, notify, status
from .i18n import t

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("charts-import")


def run_once() -> dict:
    """
    Fuehrt einen einzelnen Import- + Cleanup-Lauf aus.
    Rueckgabe: Zaehler-Dict fuer das Status-/Dashboard-Modul.
    """
    logger.info(t("import.run.start"))

    imported, rejected, flagged_upper = importer.import_downloads()
    removed = cleanup.cleanup_downloads()

    logger.info(t("import.run.done", imported=len(imported),
                  rejected=len(rejected), removed=len(removed)))

    # Abgelehnte Songs im charts-download-State freigeben, damit sie neu
    # (moeglichst besser) geladen werden.
    if rejected:
        _release_rejected(rejected)

    if imported:
        _notify_imported(imported)
        _notify_manual_available(imported)
        _unmonitor_in_lidarr(imported)
        _request_resync()

    if rejected:
        _notify_rejected(rejected)

    if flagged_upper:
        _notify_flagged_upper(flagged_upper)

    result = {
        "imported": len(imported),
        "rejected": len(rejected),
        "cleaned": len(removed),
    }
    # Reject-Gruende fuers Dashboard mitgeben (kompakt, die haeufigsten zuerst).
    # String-Wert -> wird vom Status-Modul NICHT in 'totals' kumuliert (nur
    # Zahlen), landet aber in der 'message' (siehe _guarded_run).
    if rejected:
        reasons: dict[str, int] = {}
        for r in rejected:
            reason = (r.get("reason") or "unbekannt").strip()
            reasons[reason] = reasons.get(reason, 0) + 1
        top = sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))
        result["_reject_note"] = "; ".join(f"{n}x {reason}" for reason, n in top[:3])
    return result


def _unmonitor_in_lidarr(imported: "list[tuple[str, str, str, str, str, str]]") -> None:
    """
    Haelt die neu importierten Chart-Kuenstler in Lidarr auf unmonitored
    (damit Lidarr keinen Katalog laedt). No-op, wenn Lidarr nicht konfiguriert.
    Der Kuenstler wird aus dem Zielpfad MUSIC_DIR/<Artist>/<Album>/<Track> gelesen.
    """
    artists: list[str] = []
    for target, *_rest in imported:
        try:
            rel = os.path.relpath(target, config.MUSIC_DIR)
            artist = rel.split(os.sep)[0]
            if artist and artist not in (".", ".."):
                artists.append(artist)
        except ValueError:
            continue
    if artists:
        lidarr.unmonitor_artists(artists)


def _request_resync() -> None:
    """
    Legt die Signal-Datei an, damit charts-sync die Playlist zeitnah
    aktualisiert (statt erst beim naechsten regulaeren Lauf). Nur relevant,
    wenn tatsaechlich neue Songs einsortiert wurden.
    """
    try:
        with open(config.RESYNC_SIGNAL, "w", encoding="utf-8") as fh:
            fh.write("resync")
        logger.info(t("import.resync.set"))
    except OSError as e:
        logger.warning(t("import.resync.failed", error=e))


def _normalize(text: str) -> str:
    """
    Normalisierung identisch zu charts-download (matching.normalize), damit der
    State-Schluessel uebereinstimmt. Nur die fuer den Schluessel noetigen
    Schritte (Kleinschreibung, Akzente, & -> and, nur alphanumerisch).
    """
    if not text:
        return ""
    text = text.lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _release_rejected(rejected: list[dict]) -> None:
    """
    Entfernt die abgelehnten Songs aus dem charts-download-State, damit sie
    beim naechsten Lauf erneut gesucht/geladen werden (moeglichst ein anderer,
    besserer Treffer). Robust: bei fehlender Datei/Schluessel passiert nichts.
    """
    state_file = config.DOWNLOAD_STATE_FILE
    if not state_file or not os.path.isfile(state_file):
        return
    try:
        with open(state_file, "r", encoding="utf-8") as fh:
            state = json.load(fh)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(t("import.release.state_unreadable", error=e))
        return

    requested = state.get("requested", {})
    failed = state.get("failed", {})
    blocked = state.get("blocked_providers", {})
    if not isinstance(requested, dict):
        requested = {}
    if not isinstance(failed, dict):
        failed = {}
    if not isinstance(blocked, dict):
        blocked = {}
    if not requested and not failed:
        return

    released = 0
    blocked_added = 0
    now = int(time.time())
    for entry in rejected:
        key = f"{_normalize(entry.get('artist', ''))}|{_normalize(entry.get('title', ''))}"
        # Bevor der Song freigegeben wird: den ANBIETER (Soulseek-User) der
        # abgelehnten Datei fuer GENAU DIESEN Song vormerken, damit die naechste
        # Suche in charts-download denselben schlechten Anbieter fuer diesen Song
        # ueberspringt (verhindert eine Endlosschleife "laden -> ablehnen ->
        # denselben Muell wieder laden"). Andere Songs des Anbieters bleiben
        # erlaubt (Blockade ist pro Song, nicht global). Der Quell-User steht im
        # charts-download-State unter requested[key].username.
        req_entry = requested.get(key)
        if isinstance(req_entry, dict):
            username = req_entry.get("username")
            if username:
                song_block = blocked.setdefault(key, {})
                # Nur zaehlen, wenn der Anbieter fuer diesen Song noch nicht
                # (aktuell) geblockt war.
                if username not in song_block:
                    blocked_added += 1
                song_block[username] = now  # Zeitstempel fuer den 30-Tage-Verfall
        # Aus 'requested' (schon angefragt) UND 'failed' (Cooldown) entfernen,
        # damit der Song beim naechsten Lauf sofort neu gesucht wird und nicht
        # im 24h-Cooldown haengt.
        if key in requested:
            requested.pop(key, None)
            released += 1
        if key in failed:
            failed.pop(key, None)
            released += 1

    if blocked_added:
        state["blocked_providers"] = blocked
        logger.info(t("import.release.providers_blocked", count=blocked_added))

    if released or blocked_added:
        try:
            tmp = state_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(state, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, state_file)
            logger.info(t("import.release.released", count=released))
        except OSError as e:
            logger.warning(t("import.release.state_write_failed", error=e))


def _notify_rejected(rejected: list[dict]) -> None:
    """Pushover-Meldung ueber abgelehnte (aussortierte) Dateien."""
    count = len(rejected)
    title = t("import.notify.rejected_title", count=count)
    lines = [t("import.notify.rejected_header", count=count), ""]
    for entry in rejected[:10]:
        base = os.path.basename(entry.get("path", ""))
        reason = entry.get("reason", "?")
        # Grund mit Gedankenstrich abtrennen (nicht in Klammern), damit
        # Gruende, die selbst Klammern enthalten (z.B. "zu kurz (24s < 90s)"),
        # keine doppelten Klammern erzeugen.
        lines.append(t("import.notify.rejected_entry", base=base, reason=reason))
    if count > 10:
        lines.append("")
        lines.append(t("import.notify.more", count=count - 10))
    notify.send(title, "\n".join(lines))


def _notify_flagged_upper(flagged: "list[tuple[str, str]]") -> None:
    """
    Pushover-Hinweis fuer Songs, die GROSS geschrieben importiert wurden, weil
    Deezer keine sichere kanonische Schreibweise liefern konnte (Alias-Kuenstler
    wie OMD, oder von FLASHBACK falsch benannte Titel). Sie sind in der Library
    (keine Luecke), sollten aber manuell geprueft/umbenannt werden. Sammel-
    Meldung am Lauf-Ende (kein Spam pro Song).
    """
    count = len(flagged)
    title = t("import.notify.flagged_title", count=count)
    lines = [t("import.notify.flagged_header", count=count), ""]
    for artist, song_title in flagged[:10]:
        lines.append(t("import.notify.flagged_entry", artist=artist, title=song_title))
    if count > 10:
        lines.append("")
        lines.append(t("import.notify.more", count=count - 10))
    notify.send(title, "\n".join(lines))


def _notify_imported(imported: "list[tuple[str, str, str, str, str, str]]") -> None:
    """
    Schickt eine Pushover-SAMMEL-Meldung ueber neu importierte Songs.
    imported = Liste von (zielpfad, source, artist, title, album,
    notify_device). "source" ist das Playlist-Label aus dem Manifest (leer,
    wenn unbekannt).
    """
    count = len(imported)
    title = t("import.notify.imported_title", count=count)
    lines = [t("import.notify.imported_header", count=count)]

    # Songs nach Playlist gruppieren (Reihenfolge: groesste Gruppe zuerst).
    groups: dict[str, list[str]] = {}
    for path, source, _artist, _title, _album, _dev in imported:
        label = source or t("import.notify.unknown_source")
        try:
            rel = os.path.relpath(path, config.MUSIC_DIR)
        except ValueError:
            rel = os.path.basename(path)
        groups.setdefault(label, []).append(rel)
    ordered = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))

    # Aufschluesselung nach Playlist (z.B. "3 aus 80er-90er, 3 aus charts"),
    # gefolgt von einer Leerzeile.
    breakdown = ", ".join(t("import.breakdown.item", count=len(songs), label=label)
                          for label, songs in ordered)
    lines.append(t("import.notify.imported_breakdown", breakdown=breakdown))
    lines.append("")

    # Songs pro Playlist als eigene Gruppe, mit Leerzeile dazwischen. Insgesamt
    # auf 10 Songs begrenzt (Pushover-Nachricht kurz halten).
    shown = 0
    remaining = count
    for i, (label, songs) in enumerate(ordered):
        if shown >= 10:
            break
        if i > 0:
            lines.append("")  # Leerzeile zwischen den Gruppen
        lines.append(t("import.notify.imported_group", label=label))
        for rel in songs:
            if shown >= 10:
                break
            lines.append(rel)
            shown += 1
            remaining -= 1
    if remaining > 0:
        lines.append("")
        lines.append(t("import.notify.more", count=remaining))
    notify.send(title, "\n".join(lines))


def _notify_manual_available(imported: "list[tuple[str, str, str, str, str, str]]") -> None:
    """
    Schickt fuer jeden MANUELL (via charts-songrequest) angeforderten Song eine
    eigene Pushover-Meldung an das Geraet des anfordernden Users, sobald der Song
    in der Library ist. Nur Songs mit gesetztem notify_device loesen eine Meldung
    aus (das Geraet stammt aus USER_PUSHOVER_DEVICES in charts-songrequest).

    WICHTIG: Ohne notify_device wird KEINE Meldung gesendet. notify.send() mit
    einem konkreten device-Argument sendet NIE device-los an alle (siehe
    notify.send: leeres/ungueltiges Zielgeraet -> gar nicht senden). Diese
    Einzelmeldungen kommen ZUSAETZLICH zur unveraenderten Sammelmeldung.
    """
    for _target, source, artist, title, album, notify_device in imported:
        if not notify_device:
            continue  # kein manueller Request mit Zielgeraet -> keine Meldung

        push_title = t("import.notify.manual_title")
        msg_lines = [t("import.notify.manual_song", artist=artist, title=title), ""]
        if album and album != title:
            msg_lines.append(t("import.notify.manual_album", album=album))
        if source:
            msg_lines.append(t("import.notify.manual_requested_via", source=source))
        msg_lines.append("")
        msg_lines.append(t("import.notify.manual_ready"))

        # device explizit uebergeben -> notify.send filtert gegen die gueltige
        # Geraeteliste und sendet NUR an dieses eine Geraet (oder gar nicht, wenn
        # es ungueltig ist). Nie an alle.
        # Eigener Ton fuer die manuelle Verfuegbarkeits-Meldung (Default
        # "magic") - uebersteuert den Dienst-Default "incoming", damit der User
        # am Ton hoert, dass SEIN angeforderter Song da ist.
        notify.send(push_title, "\n".join(msg_lines), device=notify_device,
                    sound=config.PUSHOVER_SOUND_MANUAL)
        logger.info(t("import.notify.manual_sent", device=notify_device,
                      artist=artist, title=title))


def _next_run_iso() -> str:
    """Zeitpunkt des naechsten Laufs als ISO-8601 (fuer das Dashboard)."""
    return time.strftime("%Y-%m-%dT%H:%M:%S%z",
                         time.localtime(time.time() + max(1, config.INTERVAL_MINUTES) * 60))


def _guarded_run() -> None:
    """Fuehrt run_once() aus und schreibt den Status (Erfolg/Fehler)."""
    status.start()
    try:
        result = run_once() or {}
        # Reject-Note (String) rausziehen - gehoert in die Message, nicht in die
        # Zahlen-Zusammenfassung/Counters.
        reject_note = result.pop("_reject_note", "")
        counters = {k: v for k, v in result.items() if isinstance(v, (int, float))}
        parts = [f"{v} {k}" for k, v in counters.items() if v]
        msg = ", ".join(parts) if parts else t("import.summary.nothing")
        if reject_note:
            msg += t("import.summary.reject_suffix", note=reject_note)
        status.finish("ok", msg, counters, _next_run_iso())
    except Exception as e:
        logger.exception(t("import.run.error", error=e))
        status.finish("error", str(e), {}, _next_run_iso())


def _maybe_backfill() -> None:
    """Stoesst den periodischen Metadaten-Backfill an, wenn er faellig ist.

    Laeuft bewusst OHNE eigenes Status-Schreiben (der Backfill ist ein stiller
    Hintergrund-Helfer; der Dienst-Status spiegelt weiter den Import-Lauf).
    Fehler sind nie fatal fuer den Scheduler-Loop.
    """
    if not backfill.due():
        return
    try:
        backfill.run_once()
    except Exception as e:
        logger.exception(t("import.backfill.failed", error=e))


def main() -> None:
    """Startet den Scheduler-Loop."""
    status.init("charts-import")
    if config.KEEP_LATEST == -1:
        cleanup_desc = t("import.cleanup_desc.keep_all")
    elif config.KEEP_LATEST > 0:
        cleanup_desc = t("import.cleanup_desc.keep_latest", count=config.KEEP_LATEST)
    elif config.RETENTION_DAYS > 0:
        cleanup_desc = t("import.cleanup_desc.by_age", days=config.RETENTION_DAYS)
    else:
        cleanup_desc = t("import.cleanup_desc.none")

    logger.info(t("import.scheduler.started",
                  downloads=config.DOWNLOADS_DIR,
                  library=config.MUSIC_DIR,
                  mode=(t("import.mode.copy") if config.COPY_MODE
                        else t("import.mode.move")),
                  cleanup=cleanup_desc,
                  interval=config.INTERVAL_MINUTES))

    if config.BACKFILL_INTERVAL_HOURS > 0:
        logger.info(t("import.backfill.active", hours=config.BACKFILL_INTERVAL_HOURS))

    if config.RUN_ON_START:
        _guarded_run()
        _maybe_backfill()
    else:
        logger.info(t("import.scheduler.wait_interval"))

    interval_seconds = max(1, config.INTERVAL_MINUTES) * 60
    while True:
        logger.info(t("import.scheduler.next_run", interval=config.INTERVAL_MINUTES))
        time.sleep(interval_seconds)
        _guarded_run()
        # Nach dem Import pruefen, ob ein Metadaten-Backfill faellig ist
        # (eigenes, selteneres Intervall - due() drosselt selbst).
        _maybe_backfill()


if __name__ == "__main__":
    main()
