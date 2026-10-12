"""
Playlist and missing-list writer.

Writes an extended M3U playlist (#EXTM3U) that Navidrome reads automatically as
a playlist, plus a text file with the chart songs that are not (yet) present in
the library.

Important for Navidrome:
  - The paths in the M3U must look the way Navidrome sees the library. Since
    Navidrome and this service mount the same music folder, we use paths
    relative to the music root directory (recommended by Navidrome).
"""

import logging
import os
from datetime import datetime

from . import config
from .i18n import t

logger = logging.getLogger(__name__)


def _relative_to_music(path: str) -> str:
    """
    Convert an absolute library path into a Navidrome-compatible relative path
    (relative to MUSIC_DIR).
    """
    try:
        return os.path.relpath(path, config.MUSIC_DIR)
    except ValueError:
        return path


def write_playlist(matched: list[tuple[int, str, str, str]],
                   file_name: str, display_title: str) -> str:
    """
    Write the M3U playlist.

    matched:       list of (position, artist, title, absolute_path), already
                   sorted by chart position.
    file_name:     file name of the .m3u (in PLAYLIST_DIR).
    display_title: display name in Navidrome ({date} is replaced).

    Returns: path of the written playlist.
    """
    os.makedirs(config.PLAYLIST_DIR, exist_ok=True)
    playlist_path = os.path.join(config.PLAYLIST_DIR, file_name)

    lines = ["#EXTM3U"]
    # Display name for Navidrome. {date} is replaced by the current date.
    title = display_title.replace("{date}", datetime.now().strftime("%d.%m.%Y"))
    lines.append(f"#PLAYLIST:{title}")

    for _position, artist, track_title, path in matched:
        rel = _relative_to_music(path)
        lines.append(f"#EXTINF:-1,{artist} - {track_title}")
        lines.append(rel)

    content = "\n".join(lines) + "\n"

    # Write atomically (temp first, then replace) so Navidrome never sees a
    # half-written file.
    tmp_path = playlist_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        fh.write(content)
    os.replace(tmp_path, playlist_path)

    logger.info(t("playlist.written", path=playlist_path, count=len(matched)))
    return playlist_path


def write_missing(missing: list[tuple[int, str, str]], file_name: str) -> str:
    """
    Write the list of missing chart songs.

    missing:   list of (position, artist, title).
    file_name: file name of the missing list (in CHARTS_DIR).
    Returns: path of the written file.
    """
    os.makedirs(config.CHARTS_DIR, exist_ok=True)
    missing_path = os.path.join(config.CHARTS_DIR, file_name)

    # The header is ONE multi-line template (a single translatable unit);
    # splitting it into lines reproduces exactly the former list elements (incl.
    # the trailing blank line), so the join with 'body' produces byte-for-byte
    # the same file as before.
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
