"""
charts-digest - taegliche Pushover-Zusammenfassung der neu einsortierten Songs.

Scheduler-Loop: wartet bis zum naechsten Zielzeitpunkt (DIGEST_HOUR:DIGEST_MINUTE,
Default 09:00 lokale Zeit), sammelt dann aus dem charts-import-Import-Log die
Songs der letzten DIGEST_WINDOW_HOURS (Default 24 h, = Rueckblick auf den
Vortag), gruppiert sie nach Quelle und sendet eine Pushover-Meldung.

Datengrundlage: das von charts-import gefuehrte Import-Log (vertrauenswuerdig,
keine mtime-/Titel-Heuristik). Siehe digest.py.
"""

import argparse
import logging
import time
from datetime import datetime, timedelta

from . import config, digest, notify, status
from .i18n import t

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("charts-digest")


def _next_run_dt(now: datetime) -> datetime:
    """Naechster Zielzeitpunkt (heute DIGEST_HOUR:MINUTE, sonst morgen)."""
    target = now.replace(hour=config.DIGEST_HOUR, minute=config.DIGEST_MINUTE,
                         second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def run_once(force_notify: bool = False) -> None:
    """
    Einen Zusammenfassungs-Lauf ausfuehren.

    force_notify: Wenn True, wird auch bei 0 neuen Songs eine Meldung gesendet
    (fuer die MANUELLE Ausloesung via `--now` - dann will man eine Rueckmeldung,
    dass es lief, auch wenn nichts Neues da ist). Beim regulaeren Scheduler-Lauf
    greift stattdessen NOTIFY_WHEN_EMPTY (Default: still bei 0).
    """
    status.start()
    manual_suffix = t("digest.run.manual_suffix") if force_notify else ""
    logger.info(t("digest.run.start", hours=config.DIGEST_WINDOW_HOURS,
                  manual=manual_suffix))
    try:
        groups, total = digest.collect()
        title, message, unique = digest.build_message(groups, total)

        if unique == 0 and not config.NOTIFY_WHEN_EMPTY and not force_notify:
            logger.info(t("digest.run.no_new_silent"))
            status.finish(
                status="ok",
                message=t("digest.status.no_new"),
                counters={"digests": 1, "new_songs": 0},
                next_run=_next_run_dt(datetime.now()).strftime("%Y-%m-%dT%H:%M:%S%z"),
            )
            return

        if unique == 0:
            # Manuell ausgeloest (oder NOTIFY_WHEN_EMPTY=1): ehrliche
            # "nichts Neues"-Rueckmeldung statt der Song-Liste.
            title = t("digest.notify.empty_title")
            message = t("digest.notify.empty_body", hours=config.DIGEST_WINDOW_HOURS)

        notify.send(title, message)
        logger.info(t("digest.run.sent", count=unique, sources=len(groups)))
        status.finish(
            status="ok",
            message=t("digest.status.reported", count=unique, sources=len(groups)),
            counters={"digests": 1, "new_songs": unique},
            next_run=_next_run_dt(datetime.now()).strftime("%Y-%m-%dT%H:%M:%S%z"),
        )
    except Exception as e:
        logger.exception(t("digest.run.error", error=e))
        status.finish(
            status="error",
            message=t("digest.status.error", error=e),
            next_run=_next_run_dt(datetime.now()).strftime("%Y-%m-%dT%H:%M:%S%z"),
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="charts-digest - tägliche Zusammenfassung neuer Library-Songs.")
    parser.add_argument(
        "--now", action="store_true",
        help="SOFORT eine Zusammenfassung senden und beenden (manuelle "
             "Auslösung, läuft parallel zum Scheduler im Container). Sendet "
             "auch bei 0 neuen Songs eine Rückmeldung.")
    args = parser.parse_args()

    status.init("charts-digest")

    # Manuelle Einmal-Ausloesung: jetzt senden und raus (kein Scheduler-Loop).
    # Aufruf z.B.: docker exec charts-digest python -m app.main --now
    if args.now:
        logger.info(t("digest.scheduler.manual_trigger"))
        run_once(force_notify=True)
        return

    logger.info(t("digest.scheduler.started",
                  hour=config.DIGEST_HOUR, minute=config.DIGEST_MINUTE,
                  hours=config.DIGEST_WINDOW_HOURS, log=config.IMPORT_LOG_FILE))

    if config.RUN_ON_START:
        logger.info(t("digest.scheduler.run_on_start"))
        run_once()

    while True:
        now = datetime.now()
        target = _next_run_dt(now)
        wait_s = (target - now).total_seconds()
        next_iso = target.strftime("%Y-%m-%dT%H:%M:%S%z")
        status.set_next_run(next_iso)
        logger.info(t("digest.scheduler.next_run", next=next_iso, hours=wait_s / 3600))
        # In Haeppchen schlafen, damit set_next_run/Signale nicht ewig blockieren
        # und ein Neustart zeitnah greift.
        while wait_s > 0:
            chunk = min(wait_s, 3600)
            time.sleep(chunk)
            wait_s -= chunk
        run_once()


if __name__ == "__main__":
    main()
