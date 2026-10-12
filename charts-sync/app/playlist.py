"""
Playlist- und Missing-List-Writer.

Schreibt eine erweiterte M3U-Playlist (#EXTM3U), die Navidrome automatisch als
Playlist einliest, sowie eine Textdatei mit den Chart-Songs, die (noch) nicht
in der Library vorhanden sind.

Wichtig fuer Navidrome:
  - Die Pfade in der M3U muessen so aussehen, wie Navidrome die Library sieht.
    Da Navidrome und dieser Service denselben Musik-Ordner mounten, verwenden
    wir Pfade relativ zum Musik-Wurzelverzeichnis (empfohlen von Navidrome).
"""

import logging
import os
from datetime import datetime

from . import config
from .i18n import t

logger = logging.getLogger(__name__)


def _relative_to_music(path: str) -> str:
    """
    Wandelt einen absoluten Library-Pfad in einen zu Navidrome passenden
    relativen Pfad (bezogen auf MUSIC_DIR) um.
    """
    try:
        return os.path.relpath(path, config.MUSIC_DIR)
    except ValueError:
        return path


def write_playlist(matched: list[tuple[int, str, str, str]],
                   file_name: str, display_title: str) -> str:
    """
    Schreibt die M3U-Playlist.

    matched:       Liste von (position, artist, title, absolute_path),
                   bereits nach Chart-Position sortiert.
    file_name:     Dateiname der .m3u (in PLAYLIST_DIR).
    display_title: Anzeigename in Navidrome ({date} wird ersetzt).

    Rueckgabe: Pfad der geschriebenen Playlist.
    """
    os.makedirs(config.PLAYLIST_DIR, exist_ok=True)
    playlist_path = os.path.join(config.PLAYLIST_DIR, file_name)

    lines = ["#EXTM3U"]
    # Anzeigename fuer Navidrome. {date} wird durch das aktuelle Datum ersetzt.
    title = display_title.replace("{date}", datetime.now().strftime("%d.%m.%Y"))
    lines.append(f"#PLAYLIST:{title}")

    for _position, artist, track_title, path in matched:
        rel = _relative_to_music(path)
        lines.append(f"#EXTINF:-1,{artist} - {track_title}")
        lines.append(rel)

    content = "\n".join(lines) + "\n"

    # Atomar schreiben (erst temp, dann ersetzen), damit Navidrome nie eine
    # halb geschriebene Datei sieht.
    tmp_path = playlist_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        fh.write(content)
    os.replace(tmp_path, playlist_path)

    logger.info(t("playlist.written", path=playlist_path, count=len(matched)))
    return playlist_path


def write_missing(missing: list[tuple[int, str, str]], file_name: str) -> str:
    """
    Schreibt die Liste der fehlenden Chart-Songs.

    missing:   Liste von (position, artist, title).
    file_name: Dateiname der Fehlliste (in CHARTS_DIR).
    Rueckgabe: Pfad der geschriebenen Datei.
    """
    os.makedirs(config.CHARTS_DIR, exist_ok=True)
    missing_path = os.path.join(config.CHARTS_DIR, file_name)

    # Der Header ist EIN mehrzeiliges Template (eine uebersetzbare Einheit); das
    # Aufsplitten in Zeilen reproduziert exakt die frueheren Listen-Elemente
    # (inkl. der abschliessenden Leerzeile), damit der Join mit 'body' Byte-fuer-
    # Byte dieselbe Datei erzeugt wie zuvor.
    header = t("playlist.missing_file.header",
               date=datetime.now().strftime("%d.%m.%Y %H:%M")).split("\n")
    body = [f"{pos:>3}. {artist} - {title}" for pos, artist, title in missing]
    content = "\n".join(header + body) + "\n"

    tmp_path = missing_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        fh.write(content)
    os.replace(tmp_path, missing_path)

    logger.info(t("playlist.missing_written", path=missing_path, count=len(missing)))
    return missing_path
