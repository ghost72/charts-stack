"""
charts-download – Hauptservice.

Automatisiert das Herunterladen der fehlenden Chart-Songs:

  1. Liest die von charts-sync geschriebene(n) missing-Datei(en).
  2. Prueft den Status bereits angefragter Downloads - fehlgeschlagene
     (Aborted/Errored/TimedOut) werden aus dem State freigegeben und in slskd
     weggeraeumt, damit sie in diesem Lauf erneut gesucht werden.
  3. Ueberspringt Songs, die bereits erfolgreich angefragt wurden (State).
  4. Sucht pro Song via slskd-API und waehlt das beste MP3 >= MIN_BITRATE
     (Standard 256 kbps) aus.
  5. DRY_RUN=1 (Standard): loggt/meldet nur, was geladen WUERDE.
     DRY_RUN=0: stoesst die Downloads tatsaechlich per slskd-API an.
  6. Maximal MAX_PER_RUN Songs pro Lauf (Standard 10).

Stellung in der Kette:
  charts-sync (missing.txt) -> charts-download (laedt) -> slskd -> /Charts
  -> charts-import (sortiert ein) -> Navidrome
"""

import logging
import time

from . import config, manifest, missing, notify, slsk, state, status
from .i18n import t

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("charts-download")


def run_once() -> dict:
    """
    Fuehrt einen einzelnen Such-/Download-Lauf aus.
    Rueckgabe: Zaehler-Dict fuer das Status-/Dashboard-Modul.
    """
    logger.info(t("download.run.start", dry_run="1" if config.DRY_RUN else "0"))

    entries = missing.load_all(config.MISSING_FILES)
    if not entries:
        logger.info(t("download.run.no_missing"))
        return {"enqueued": 0, "retried": 0, "missing": 0}

    try:
        client = slsk.connect()
    except slsk.SlskdError as e:
        logger.error(t("download.run.slskd_unreachable", error=e))
        raise

    st = state.load()

    # Zuerst: fehlgeschlagene Downloads aus dem State freigeben, damit sie in
    # diesem Lauf erneut gesucht/geladen werden. Fehlgeschlagene Transfers in
    # slskd werden dabei weggeraeumt.
    retried = _requeue_failed(client, st)

    # Jeder Eintrag ist (label, "#pos Artist - Title (bitrate)"), damit die
    # Meldung nach Herkunfts-Playlist gruppieren kann.
    planned: list[tuple[str, str]] = []   # fuer Dry-Run / Meldung
    enqueued: list[tuple[str, str]] = []  # tatsaechlich angestossen
    processed = 0

    # Heuristik gegen den "slskd eingeloggt aber noch nicht suchfaehig"-Fall
    # (direkt nach Rebuild/Neustart liefert JEDE Suche 0 Treffer, obwohl die
    # Songs real auffindbar sind). searched = Anzahl echt durchsuchter Songs,
    # empty_results = davon mit 0 Treffern. Liefert ein ganzer Lauf fuer ALLE
    # durchsuchten Songs 0 Treffer und wurde NICHTS angestossen, werten wir das
    # als "slskd evtl. noch nicht suchfaehig" und nehmen die soeben gesetzten
    # Cooldowns wieder zurueck (sonst steht der ganze Rueckstand bis zu 6h brach).
    searched = 0
    empty_results = 0
    failed_this_run: list[tuple[str, str]] = []

    skipped_cooldown = 0
    for pos, artist, title, source in entries:
        if processed >= config.MAX_PER_RUN:
            logger.info(t("download.run.max_per_run", max=config.MAX_PER_RUN))
            break

        if state.already_requested(st, artist, title):
            logger.debug(t("download.song.already_requested", artist=artist, title=title))
            continue

        # Kuerzlich erfolglos gesucht? Dann ueberspringen, OHNE einen der
        # MAX_PER_RUN-Slots zu verbrauchen. So blockieren dauerhaft nicht
        # findbare Songs (z.B. Mundart) nicht jeden Lauf und die findbaren
        # kommen dran. Nach Ablauf des Cooldowns wird erneut versucht.
        if state.in_cooldown(st, artist, title, config.SEARCH_COOLDOWN_HOURS):
            skipped_cooldown += 1
            continue

        processed += 1
        logger.info(t("download.song.searching", pos=pos, source=source,
                      artist=artist, title=title))

        # Anbieter, die fuer diesen Song schon eine schlechte/upgesampelte Datei
        # geliefert haben (von charts-import vorgemerkt), bei der Suche
        # ausfiltern. Verfallene Eintraege werden dabei gleich aufgeraeumt.
        blocked_users = state.blocked_providers_for(
            st, artist, title, config.BLOCK_TTL_DAYS)
        if blocked_users:
            logger.info(t("download.song.providers_blocked", count=len(blocked_users)))

        try:
            candidates = slsk.find_candidates(client, artist, title,
                                              blocked_users=blocked_users)
        except slsk.SlskdError as e:
            # Verbindungs-/API-Fehler (z.B. slskd gerade neu gestartet). Das ist
            # KEIN "Song nicht findbar" -> keinen Cooldown setzen, Slot nicht
            # verbrauchen. Da die Verbindung gestoert ist, brechen wir den Lauf
            # ab (slskd nicht weiter haemmern); der naechste Lauf versucht es neu.
            logger.warning(t("download.run.slskd_aborted", error=e))
            processed -= 1
            break
        # Ein echter Suchlauf (slskd hat geantwortet) ist gelaufen.
        searched += 1
        if not candidates:
            empty_results += 1
            logger.info(t("download.song.no_match", min=config.MIN_BITRATE,
                          cooldown=config.SEARCH_COOLDOWN_HOURS))
            if not config.DRY_RUN:
                state.mark_failed(st, artist, title)
                failed_this_run.append((artist, title))
            continue

        # Bester Treffer fuer Dry-Run-Anzeige; die restlichen sind Retry-Fallbacks.
        best_user, best_file, best_score = candidates[0]
        bitrate = best_file.get("bitRate", 0)

        if config.DRY_RUN:
            logger.info(t("download.song.dry_run_would_load",
                          filename=best_file.get("filename", "?"), bitrate=bitrate,
                          score=best_score, user=best_user))
            planned.append((source, f"#{pos} {artist} - {title} ({bitrate}kbps)"))
            # Im Dry-Run NICHT als angefragt markieren, damit es beim echten
            # Lauf erneut betrachtet wird.
            continue

        # Retry ueber mehrere Anbieter: Soulseek ist P2P - der beste Treffer kann
        # von einem Peer stammen, der gerade offline ist / den Upload ablehnt.
        # Kandidaten der Reihe nach abhaken, bis ein enqueue klappt.
        chosen = None  # (username, file_dict, bitrate)
        for cand_user, cand_file, _score in candidates:
            if slsk.enqueue(client, cand_user, cand_file):
                chosen = (cand_user, cand_file, cand_file.get("bitRate", 0))
                break
            logger.info(t("download.song.provider_no_deliver", user=cand_user))

        if chosen:
            username, file_dict, bitrate = chosen
            fname = file_dict.get("filename", "?")
            logger.info(t("download.song.enqueued", filename=fname, user=username))
            state.mark_requested(st, artist, title, username, fname)
            state.clear_failed(st, artist, title)  # erfolgreicher Treffer -> Cooldown weg
            # Saubere Chart-Metadaten fuer charts-import hinterlegen, damit die
            # Datei korrekt getaggt/einsortiert wird (nicht "Unknown Album").
            manifest.record(fname, artist, title, source)
            enqueued.append((source, f"#{pos} {artist} - {title} ({bitrate}kbps)"))
        else:
            logger.warning(t("download.song.no_provider_delivered",
                             artist=artist, title=title))

    if skipped_cooldown:
        logger.info(t("download.run.cooldown_skipped", count=skipped_cooldown))

    # Verdacht "slskd eingeloggt, aber noch nicht suchfaehig": Es wurde gesucht,
    # ALLE durchsuchten Songs lieferten 0 Treffer UND nichts wurde angestossen.
    # Das ist fast sicher ein Timing-/Peer-Problem (kurz nach Start), kein echtes
    # "alle unauffindbar" - die soeben gesetzten Cooldowns zuruecknehmen, damit
    # der Rueckstand nicht bis zu SEARCH_COOLDOWN_HOURS brachliegt. Der naechste
    # Lauf versucht es dann regulaer erneut.
    if (not config.DRY_RUN and searched > 0 and empty_results == searched
            and not enqueued):
        for a, song_title in failed_this_run:
            state.clear_failed(st, a, song_title)
        logger.warning(t("download.run.all_empty_requeued", count=searched))

    if not config.DRY_RUN:
        state.save(st)

    _summary_and_notify(planned, enqueued, retried)
    return {
        "enqueued": len(enqueued),
        "planned": len(planned),
        "retried": retried,
        "missing": len(entries),
    }


def _requeue_failed(client, st: dict) -> int:
    """
    Prueft den Status der bereits angefragten Downloads. Fehlgeschlagene werden
    aus dem State entfernt (damit sie neu geladen werden) und in slskd
    weggeraeumt. Gibt die Anzahl der freigegebenen Songs zurueck.

    Im Dry-Run werden nur Erkennung/Logging gemacht, keine Aenderung.
    """
    requested = st.get("requested", {})
    if not requested:
        return 0

    failed_keys, to_cleanup = slsk.check_download_states(client, requested)
    if not failed_keys:
        return 0

    if config.DRY_RUN:
        logger.info(t("download.requeue.dry_run", count=len(failed_keys)))
        return 0

    for key in failed_keys:
        requested.pop(key, None)
    slsk.cleanup_failed(client, to_cleanup)
    state.save(st)
    logger.info(t("download.requeue.freed", count=len(failed_keys)))
    return len(failed_keys)


def _breakdown(items: list[tuple[str, str]]) -> str:
    """
    Fasst zusammen, wie viele Songs aus welcher Playlist stammen, z.B.
    "3 aus charts, 2 aus 80er-90er". Reihenfolge nach Menge (haeufigste zuerst).
    """
    counts: dict[str, int] = {}
    for label, _ in items:
        counts[label] = counts.get(label, 0) + 1
    parts = [t("download.breakdown.item", count=n, label=label)
             for label, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    return ", ".join(parts)


def _summary_and_notify(planned: list[tuple[str, str]],
                        enqueued: list[tuple[str, str]],
                        retried: int) -> None:
    """Loggt eine Zusammenfassung und schickt optional Pushover."""
    if config.DRY_RUN:
        logger.info(t("download.summary.done_dry_run", count=len(planned)))
        if planned:
            header = [t("download.notify.dry_run_header", count=len(planned)),
                      f"<i>{_breakdown(planned)}</i>"]
            _notify(t("download.notify.title_dry"), header, planned)
    else:
        parts = []
        if enqueued:
            parts.append(t("download.summary.enqueued", count=len(enqueued)))
        if retried:
            parts.append(t("download.summary.retried", count=retried))
        summary = ", ".join(parts) if parts else t("download.summary.nothing")
        logger.info(t("download.summary.done", summary=summary))
        if enqueued or retried:
            header = []
            if enqueued:
                header.append(t("download.notify.enqueued_header", count=len(enqueued)))
                header.append(f"<i>{_breakdown(enqueued)}</i>")
            if retried:
                header.append(t("download.notify.retried_header", count=retried))
            _notify(t("download.notify.title_live"), header, enqueued)


def _notify(title: str, header: list[str], items: list[tuple[str, str]]) -> None:
    """
    Schickt eine Pushover-Meldung mit nach Playlist gruppierten Songs.
    header = Kopfzeilen (Anzahl + Aufschluesselung). items = (label, text).
    Format: Header, Leerzeile, dann pro Playlist eine fette Ueberschrift mit den
    Songs darunter, Leerzeilen zwischen den Gruppen. Gesamt auf 15 Songs
    begrenzt (Pushover-Nachricht kurz halten).
    """
    lines = list(header)
    # Nach Label gruppieren (Reihenfolge: groesste Gruppe zuerst).
    groups: dict[str, list[str]] = {}
    for label, text in items:
        groups.setdefault(label, []).append(text)
    ordered = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))

    shown = 0
    remaining = len(items)
    for i, (label, texts) in enumerate(ordered):
        if shown >= 15:
            break
        lines.append("")  # Leerzeile vor jeder Gruppe (auch nach dem Header)
        lines.append(f"<b>{label}</b>")
        for text in texts:
            if shown >= 15:
                break
            lines.append(text)
            shown += 1
            remaining -= 1
    if remaining > 0:
        lines.append("")
        lines.append(t("download.notify.more", count=remaining))
    notify.send(title, "\n".join(lines))


def _next_run_iso() -> str:
    """Zeitpunkt des naechsten Laufs als ISO-8601 (fuer das Dashboard)."""
    return time.strftime("%Y-%m-%dT%H:%M:%S%z",
                         time.localtime(time.time() + max(1, config.INTERVAL_MINUTES) * 60))


def _guarded_run() -> None:
    """Fuehrt run_once() aus und schreibt den Status (Erfolg/Fehler)."""
    status.start()
    try:
        counters = run_once() or {}
        parts = [f"{v} {k}" for k, v in counters.items() if isinstance(v, (int, float)) and v]
        msg = ", ".join(parts) if parts else "nichts zu tun"
        status.finish("ok", msg, counters, _next_run_iso())
    except Exception as e:
        logger.exception(t("download.run.error", error=e))
        status.finish("error", str(e), {}, _next_run_iso())


def main() -> None:
    status.init("charts-download")
    mode = t("download.mode.dry_run") if config.DRY_RUN else t("download.mode.live")
    logger.info(t(
        "download.scheduler.started",
        slskd=config.SLSKD_HOST,
        missing=", ".join(config.MISSING_FILES),
        mode=mode,
        max=config.MAX_PER_RUN,
        min=config.MIN_BITRATE,
        interval=config.INTERVAL_MINUTES,
    ))

    if config.RUN_ON_START:
        _guarded_run()

    interval_seconds = max(1, config.INTERVAL_MINUTES) * 60
    while True:
        logger.info(t("download.scheduler.next_run", interval=config.INTERVAL_MINUTES))
        time.sleep(interval_seconds)
        _guarded_run()


if __name__ == "__main__":
    main()
