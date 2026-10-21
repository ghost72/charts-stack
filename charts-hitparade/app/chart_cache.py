"""
Persist the last parsed hitparade chart so a resync trigger can re-match it
against the library WITHOUT re-fetching hitparade.ch.

The scheduled run writes the parsed chart list here; the resync trigger reads
it back and re-renders the playlist from it. The cache is a tiny JSON file in
CHARTS_DIR (100 entries of [position, artist, title] per chart). List order is
preserved on write and read so the triggered render matches the scheduled one
byte-for-byte.
"""

import json
import os

from . import config


def _path() -> str:
    """Absolute path of the chart cache file in CHARTS_DIR."""
    return os.path.join(config.CHARTS_DIR, config.CHART_CACHE_FILE)


def save(charts: "dict[str, list[tuple[int, str, str]]]") -> None:
    """Write {chart_name: [[pos, artist, title], ...]} atomically.

    A thin atomic temp+replace writer; it deliberately does NOT swallow OSError
    (the caller guards the write so a cache failure never aborts the run).
    """
    os.makedirs(config.CHARTS_DIR, exist_ok=True)
    tmp = _path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(
            {"charts": {k: [list(s) for s in v] for k, v in charts.items()}},
            fh, ensure_ascii=False,
        )
    os.replace(tmp, _path())


def load() -> "dict[str, list[tuple[int, str, str]]]":
    """Return {chart_name: [(pos, artist, title), ...]} or {} if absent/broken.

    Any read/parse error or a malformed structure degrades to {} (treated as
    "no cache"); a malformed inner entry is left as-is for the match code to
    tolerate the same way a live-fetch entry would be.
    """
    try:
        with open(_path(), encoding="utf-8") as fh:
            data = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    raw = data.get("charts", {}) if isinstance(data, dict) else {}
    if not isinstance(raw, dict):
        return {}
    result: "dict[str, list[tuple[int, str, str]]]" = {}
    for name, songs in raw.items():
        if not isinstance(songs, list):
            return {}
        result[name] = [tuple(s) for s in songs]
    return result
