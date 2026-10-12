"""
Missing-list writer (only a fetch list now, no more playlists).
COPY from charts-sync. Writes a missing file with the recommendations not (yet)
present, in the charts-download format.
"""

import logging
import os
from datetime import datetime

from . import config
from .i18n import t

logger = logging.getLogger(__name__)


def write_missing(missing: list[tuple[int, str, str]], file_name: str) -> str:
    """
    Write the list of missing recommendations in the charts-download format
    ("  <pos>. <artist> - <title>") so charts-download can fetch them.
    missing: list of (position, artist, title).
    """
    os.makedirs(config.CHARTS_DIR, exist_ok=True)
    missing_path = os.path.join(config.CHARTS_DIR, file_name)

    # The header is ONE multi-line template (one translatable unit); splitting
    # it into lines reproduces exactly the former list elements (incl. the
    # trailing blank line) so the join with 'body' produces byte-for-byte the
    # same file as before.
    header = t("recommend.missing_file.header",
               date=datetime.now().strftime("%d.%m.%Y %H:%M")).split("\n")
    body = [f"{pos:>3}. {artist} - {title}" for pos, artist, title in missing]
    content = "\n".join(header + body) + "\n"

    tmp_path = missing_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        fh.write(content)
    os.replace(tmp_path, missing_path)

    logger.info(t("playlist.missing_written", path=missing_path, count=len(missing)))
    return missing_path
