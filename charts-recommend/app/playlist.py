"""
Missing-List-Writer (nur noch Nachlade-Liste, keine Playlists mehr).
KOPIE aus charts-sync. Schreibt eine missing-Datei mit den (noch) nicht
vorhandenen Empfehlungen im charts-download-Format.
"""

import logging
import os
from datetime import datetime

from . import config
from .i18n import t

logger = logging.getLogger(__name__)


def write_missing(missing: list[tuple[int, str, str]], file_name: str) -> str:
    """
    Schreibt die Liste der fehlenden Empfehlungen im charts-download-Format
    ("  <pos>. <artist> - <title>"), damit charts-download sie nachladen kann.
    missing: Liste von (position, artist, title).
    """
    os.makedirs(config.CHARTS_DIR, exist_ok=True)
    missing_path = os.path.join(config.CHARTS_DIR, file_name)

    # Der Header ist EIN mehrzeiliges Template (eine uebersetzbare Einheit); das
    # Aufsplitten in Zeilen reproduziert exakt die frueheren Listen-Elemente
    # (inkl. der abschliessenden Leerzeile), damit der Join mit 'body' Byte-fuer-
    # Byte dieselbe Datei erzeugt wie zuvor.
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
