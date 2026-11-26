"""Read the latest manifest-scoped metadata report for the tag editor."""

import json
import os

from mutagen import File as MutagenFile
from mutagen import MutagenError

from . import config, editor, tags


def load() -> dict:
    with open(os.path.join(config.STATUS_DIR, "missing-metadata.json"),
              encoding="utf-8") as fh:
        report = json.load(fh)
    if (not isinstance(report, dict) or report.get("version") != 1
            or not isinstance(report.get("songs"), list)
            or not isinstance(report.get("generated_at"), str)
            or not isinstance(report.get("checked"), int)):
        raise ValueError("Invalid metadata report")

    rows = []
    for row in report["songs"]:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            raise ValueError("Invalid metadata report entry")
        try:
            path = editor._guarded_realpath(os.path.join(config.MUSIC_DIR, row["path"]))
        except editor.EditorError as exc:
            raise ValueError("Invalid metadata report path") from exc
        try:
            audio = MutagenFile(path, easy=True)
            if audio is None:
                raise ValueError("Unsupported audio")
            current = {
                "artist": tags._first_tag(audio, "albumartist", "artist"),
                "title": tags._first_tag(audio, "title"),
                "album": tags._first_tag(audio, "album"),
                "year": tags._leading_year(tags._first_tag(audio, "date", "originaldate")),
                "track": tags._leading_track(tags._first_tag(audio, "tracknumber")),
            }
        except (OSError, ValueError, MutagenError):
            rows.append({"path": path, "unreadable": True})
            continue
        album = current["album"].strip()
        missing = []
        if not current["year"]:
            missing.append("year")
        if not album or album.lower() == "unknown album":
            missing.append("album")
        if not current["track"]:
            missing.append("track")
        if missing:
            rows.append({"path": path, "artist": current["artist"],
                         "title": current["title"], "missing": missing})
    return {"generated_at": report["generated_at"], "checked": report["checked"],
            "songs": rows}
