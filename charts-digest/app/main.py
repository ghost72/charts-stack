"""
charts-digest - daily Pushover summary of the newly filed songs.

Scheduler loop: waits until the next target time (DIGEST_HOUR:DIGEST_MINUTE,
default 09:00 local time), then collects from the charts-import import log the
songs of the last DIGEST_WINDOW_HOURS (default 24 h, = look-back on the
previous day), groups them by source and sends a Pushover message.

Data basis: the import log kept by charts-import (trustworthy, no mtime/title
heuristic). See digest.py.
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
    """Next target time (today DIGEST_HOUR:MINUTE, otherwise tomorrow)."""
    target = now.replace(hour=config.DIGEST_HOUR, minute=config.DIGEST_MINUTE,
                         second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def run_once(force_notify: bool = False) -> None:
    """
    Run one summary pass.

    force_notify: if True, a message is sent even with 0 new songs (for the
    MANUAL trigger via `--now` - then you want feedback that it ran, even if
    there is nothing new). On the regular scheduler run NOTIFY_WHEN_EMPTY
    applies instead (default: silent at 0).
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
            # Manually triggered (or NOTIFY_WHEN_EMPTY=1): honest "nothing new"
            # feedback instead of the song list.
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
        description=t("digest.cli.description"))
    parser.add_argument(
        "--now", action="store_true",
        help=t("digest.cli.help_now"))
    args = parser.parse_args()

    status.init("charts-digest")

    # Manual one-off trigger: send now and exit (no scheduler loop).
    # Call e.g.: docker exec charts-digest python -m app.main --now
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
        # Sleep in chunks so set_next_run/signals do not block forever and a
        # restart takes effect promptly.
        while wait_s > 0:
            chunk = min(wait_s, 3600)
            time.sleep(chunk)
            wait_s -= chunk
        run_once()


if __name__ == "__main__":
    main()
