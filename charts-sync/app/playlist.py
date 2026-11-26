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

import json
import logging
import os
import re
import stat
from datetime import datetime

from . import config
from .i18n import t

logger = logging.getLogger(__name__)
_SCENE_MARKER = "#CHARTS-STACK:MUNDART-SCENE\n"
_GENRE_MARKER = "#CHARTS-STACK:GENRE-PLAYLIST\n"
SCENE_FILE_RE = re.compile(r"(?:mundart-szene-[a-z0-9-]{1,48}-[a-f0-9]{10}|charts-[a-z0-9-]{1,48})\.m3u")


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
                   file_name: str, display_title: str, *,
                   marker: str = "", no_overwrite: bool = False) -> str:
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
    if marker:
        lines.append(marker.rstrip("\n"))

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
    if no_overwrite:
        try:
            os.link(tmp_path, playlist_path)
        finally:
            os.unlink(tmp_path)
    else:
        os.replace(tmp_path, playlist_path)

    logger.info(t("playlist.written", path=playlist_path, count=len(matched)))
    return playlist_path


def _scene_path(file_name: str) -> str | None:
    if not SCENE_FILE_RE.fullmatch(file_name) or any(
        source.name.casefold() == file_name for source in config.CHARTS
    ):
        return None
    return os.path.join(config.PLAYLIST_DIR, file_name)


def _managed_marker(path: str) -> str:
    if not stat.S_ISREG(os.lstat(path).st_mode):
        return ""
    with open(path, encoding="utf-8", errors="replace") as fh:
        if fh.readline(128) != "#EXTM3U\n" or not fh.readline(512).startswith("#PLAYLIST:"):
            return ""
        marker = fh.readline(128)
        return marker if marker in (_SCENE_MARKER, _GENRE_MARKER) else ""


def _is_managed_scene(path: str) -> bool:
    return _managed_marker(path) == _SCENE_MARKER


def remove_scene_playlist(file_name: str, *, strict: bool = False) -> None:
    path = _scene_path(file_name)
    if path is None:
        if strict:
            raise ValueError(f"Invalid or reserved playlist deletion: {file_name}")
        return
    try:
        if _managed_marker(path):
            os.unlink(path)
            logger.info("Removed Mundart scene playlist: %s", path)
        elif strict:
            raise ValueError(f"Refusing to delete an unmanaged playlist: {path}")
    except FileNotFoundError:
        pass


def rename_scene_playlist(file_name: str, scene: str) -> None:
    """Update only the title of an existing managed M3U, preserving its entries."""
    path = _scene_path(file_name)
    if path is None:
        logger.warning("Skipping invalid or reserved scene playlist: %s", file_name)
        return
    try:
        if not _managed_marker(path):
            logger.warning("Skipping unmanaged playlist: %s", path)
            return
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
    except FileNotFoundError:
        return
    title = f"#PLAYLIST:{scene}\n"
    if lines[1] == title:
        return
    lines[1] = title
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        fh.writelines(lines)
    os.replace(tmp_path, path)
    logger.info("Renamed Mundart scene playlist: %s", path)


def migrate_scene_playlists(aliases: dict[str, str], skip: set[str]) -> None:
    """Move managed M3Us without replacing destinations or changing track entries."""
    from .artist_catalog import scene_filename

    moves: dict[str, tuple[str, str]] = {}
    for name in os.listdir(config.PLAYLIST_DIR):
        if name in skip or _scene_path(name) is None:
            continue
        path = os.path.join(config.PLAYLIST_DIR, name)
        if not _managed_marker(path):
            continue
        scene = aliases.get(name)
        if scene is None:
            with open(path, encoding="utf-8") as fh:
                fh.readline()
                title = fh.readline().removeprefix("#PLAYLIST:").rstrip("\n")
            stripped = title.removeprefix("Mundart: ")
            if name.startswith("mundart-szene-") or (
                    title != stripped and scene_filename(stripped) == name):
                scene = stripped
        if scene is None:
            continue
        destination = scene_filename(scene)
        target = _scene_path(destination)
        if not scene.strip() or len(scene) > 80 or target is None or destination in skip:
            raise ValueError(f"Invalid or reserved scene migration destination: {destination}")
        if destination in moves and moves[destination][0] != name:
            previous = moves[destination][0]
            if destination not in (previous, name) or not os.path.samefile(
                    os.path.join(config.PLAYLIST_DIR, previous), path):
                raise ValueError(f"Scene migration filename collision: {destination}")
            if name == destination:
                continue
        if name != destination and os.path.lexists(target):
            if not _managed_marker(target) or not os.path.samefile(path, target):
                raise FileExistsError(f"Scene migration destination already exists: {target}")
        moves[destination] = (name, scene)
    sources = {name: destination for destination, (name, _) in moves.items()}
    if any(destination in sources and sources[destination] != destination for destination in moves):
        raise ValueError("Overlapping scene migration paths.")
    for destination, (name, scene) in moves.items():
        if name != destination:
            source = os.path.join(config.PLAYLIST_DIR, name)
            target = os.path.join(config.PLAYLIST_DIR, destination)
            # Hard-link installation refuses overwrites; an interrupted move is retryable.
            try:
                os.link(source, target, follow_symlinks=False)
            except FileExistsError:
                if not _managed_marker(target) or not os.path.samefile(source, target):
                    raise
            os.unlink(source)
            logger.info("Moved scene playlist: %s -> %s", name, destination)
        rename_scene_playlist(destination, scene)
    for name, scene in aliases.items():
        if name not in skip and scene_filename(scene) not in skip:
            rename_scene_playlist(scene_filename(scene), scene)


def write_scene_playlist(matched: list[tuple[int, str, str, str]],
                         file_name: str, scene: str) -> list[tuple[int, str, str, str]]:
    """Write a managed scene and return tracks absent from its previous M3U."""
    path = _scene_path(file_name)
    if path is None:
        logger.warning("Skipping invalid or reserved scene playlist: %s", file_name)
        return []
    previous_paths: set[str] = set()
    try:
        managed = _is_managed_scene(path)
        if managed:
            with open(path, encoding="utf-8") as fh:
                previous_paths = {line.rstrip("\n") for line in fh if not line.startswith("#")}
    except FileNotFoundError:
        managed = True
    if not managed:
        logger.warning("Skipping unmanaged playlist: %s", path)
        return []
    write_playlist(matched, file_name, scene, marker=_SCENE_MARKER)
    return [entry for entry in matched if _relative_to_music(entry[3]) not in previous_paths]


def write_genre_playlist(matched: list[tuple[int, str, str, str]],
                         file_name: str, title: str) -> None:
    path = _scene_path(file_name)
    if path is None:
        raise ValueError(f"Invalid or reserved genre playlist: {file_name}")
    new_file = False
    try:
        if _managed_marker(path) != _GENRE_MARKER:
            raise FileExistsError(f"Refusing to overwrite another playlist: {path}")
    except FileNotFoundError:
        new_file = True
    write_playlist(matched, file_name, title, marker=_GENRE_MARKER, no_overwrite=new_file)


def write_missing(missing: list[tuple[int, str, str]], file_name: str, *,
                  scene_sources: dict[tuple[str, str], list[str]] | None = None) -> str:
    """
    Write the list of missing chart songs.

    missing:   list of (position, artist, title).
    file_name: file name of the missing list (in CHARTS_DIR).
    scene_sources: optional scene names per (artist, title), written as JSON
                   comments before the song rows.
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
    body = []
    for pos, artist, title in missing:
        if scene_sources is not None:
            # Older downloaders ignore comments and still read the same song rows.
            body.append("#SCENES: " + json.dumps(scene_sources[(artist, title)], ensure_ascii=False))
        body.append(f"{pos:>3}. {artist} - {title}")
    content = "\n".join(header + body) + "\n"

    tmp_path = missing_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        fh.write(content)
    os.replace(tmp_path, missing_path)

    logger.info(t("playlist.missing_written", path=missing_path, count=len(missing)))
    return missing_path
