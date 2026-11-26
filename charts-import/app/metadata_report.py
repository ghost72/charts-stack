"""Publish a manifest-scoped snapshot of missing library metadata."""

import json
import os
import tempfile
from datetime import datetime, timezone

from . import backfill_metadata, config
from .backfill_covers import build_manifest_index

REPORT_NAME = "missing-metadata.json"


def build(music_dir: str) -> dict:
    if not os.path.isdir(music_dir):
        raise OSError(f"Music directory is unavailable: {music_dir}")
    rows = []
    checked = 0
    paths = (path for group in build_manifest_index(music_dir, strict=True).values()
             for path in group)
    for path in paths:
        checked += 1
        state = backfill_metadata.read_state(path)
        rel = os.path.relpath(path, music_dir)
        if state is None:
            rows.append({"path": rel, "unreadable": True})
            continue
        plan = backfill_metadata.missing_fields(state)
        missing = [field for field, absent in (
            ("year", plan.fill_year), ("album", plan.fill_album),
            ("track", plan.fill_track),
        ) if absent]
        if missing:
            rows.append({"path": rel, "artist": state.artist,
                         "title": state.title, "missing": missing})
    return {"version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
            "checked": checked, "songs": rows}


def publish(music_dir: str, status_dir: str) -> None:
    report = build(music_dir)
    os.makedirs(status_dir, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".metadata-report-", dir=status_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False)
        os.replace(temporary, os.path.join(status_dir, REPORT_NAME))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
