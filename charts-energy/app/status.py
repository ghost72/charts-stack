"""
Shared status module for all charts services (dashboard integration).

Each service writes a small JSON status file to STATUS_DIR/<service>.json at the
end of a run (or after a request). The charts-dashboard service reads these
files and displays them - so the UI gets EXACT run statistics (how much
processed, when last, success/error) without log parsing.

This module is COPIED into every service (like slsk.py/manifest.py) - no
runtime dependency between the services.

Schema (STATUS_DIR/<service>.json):
{
  "service": "charts-import",
  "last_run_start": "<ISO-8601>",   # start of the last run
  "last_run_end":   "<ISO-8601>",   # end of the last run
  "last_success":   "<ISO-8601>",   # end of the last SUCCESSFUL run
  "status": "ok" | "error" | "running" | "idle",
  # idle = deliberately did nothing (disabled/empty), not an error.
  "message": "<short summary>",
  "counters": { ... },              # counters of the LAST run (service-specific)
  "totals":   { ... },              # cumulative over all runs (summed)
  "next_run": "<ISO-8601 or empty>"
}

Environment variables:
  STATUS_DIR  - target directory (default: /charts/.charts-status). Must lie in
                the mounted charts folder that the dashboard also sees.
  STATUS_SERVICE - name of the service (default: from the argument to init()).
"""

import json
import logging
import os
import tempfile
import time

from .i18n import t

logger = logging.getLogger(__name__)

STATUS_DIR = os.environ.get("STATUS_DIR", "/charts/.charts-status")

# Only these counters are "work done" and are accumulated in 'totals'.
# Everything else (missing, library_tracks, sources, scanned, bad, planned,
# matched ...) is a point-in-time/state value and is NOT summed.
CUMULATIVE_KEYS = {
    "imported",    # charts-import: sorted-in songs
    "enqueued",    # charts-download: triggered downloads
    "upgraded",    # charts-upgrade: replaced songs
    "rejected",    # charts-import: discarded files
    "cleaned",     # charts-import: cleaned-up downloads
    "retried",     # charts-download: re-triggered failures
    "requests",    # charts-songrequest: served requests
}

_service = os.environ.get("STATUS_SERVICE", "unknown")
_run_start_iso = ""


def _now_iso() -> str:
    """Local time as ISO-8601 with timezone offset."""
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def init(service: str) -> None:
    """Call once at startup: sets the service name."""
    global _service
    _service = os.environ.get("STATUS_SERVICE", service)


def _path() -> str:
    return os.path.join(STATUS_DIR, f"{_service}.json")


def _read() -> dict:
    try:
        with open(_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {}


def _write(data: dict) -> None:
    try:
        os.makedirs(STATUS_DIR, exist_ok=True)
        # Write atomically (temp + replace) so the dashboard never reads a
        # half-written file.
        fd, tmp = tempfile.mkstemp(dir=STATUS_DIR, prefix=f".{_service}.", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, _path())
    except OSError as e:
        logger.debug(t("status.write_failed", path=_path(), error=e))


def start() -> None:
    """Mark the start of a run (status=running)."""
    global _run_start_iso
    _run_start_iso = _now_iso()
    data = _read()
    data["service"] = _service
    data["last_run_start"] = _run_start_iso
    data["status"] = "running"
    _write(data)


def finish(status: str = "ok", message: str = "",
           counters: dict | None = None, next_run: str = "") -> None:
    """
    Mark the end of a run. 'counters' are the counters of THIS run; they are
    additionally summed into 'totals' (cumulative over all runs).
    status: "ok" (successful), "error" (failed) or "idle" (deliberately did
    nothing).
    """
    now = _now_iso()
    data = _read()
    data["service"] = _service
    if not data.get("last_run_start"):
        data["last_run_start"] = _run_start_iso or now
    data["last_run_end"] = now
    data["status"] = status
    data["message"] = message
    data["counters"] = counters or {}
    if status == "ok":
        data["last_success"] = now
    # Accumulate totals ONLY for real "work done" (throughput values).
    # Point-in-time values like 'missing', 'library_tracks', 'sources' are a
    # STATE, not throughput - summing them yields nonsensical numbers (e.g.
    # 'missing' grows with every run even though it is the same missing songs).
    # Hence a whitelist: only these keys land in 'totals'.
    totals = data.get("totals") or {}
    for k, v in (counters or {}).items():
        if k in CUMULATIVE_KEYS and isinstance(v, (int, float)):
            totals[k] = (totals.get(k, 0) or 0) + v
    data["totals"] = totals
    data["next_run"] = next_run
    _write(data)


def set_next_run(next_run: str) -> None:
    """
    Update ONLY the next scheduled run time, without touching status, run times
    or counters. Intended for scheduler services that want to keep next_run
    continuously up to date (otherwise the dashboard shows a value frozen at the
    last run - possibly 'overdue').
    """
    if not next_run:
        return
    data = _read()
    data["service"] = _service
    data["next_run"] = next_run
    _write(data)


def set_channels(channels: list[dict]) -> None:
    """
    Update ONLY the 'channels' key (the parsed channel set), without touching
    status, run times or counters, so the dashboard can read the ACTUAL running
    config of this service.

    This is an ADDITIVE helper that lives only in charts-energy's copy of the
    otherwise-shared status.py (same discipline as the dashboard's additive
    ui_table/active_lang readers). Other services' copies stay unchanged and
    never call it - do NOT propagate it. Mirrors set_next_run: the shared
    _write does temp+os.replace atomically, logs at debug on OSError and never
    raises.
    """
    data = _read()
    data["service"] = _service
    data["channels"] = channels
    _write(data)


def heartbeat(message: str = "", next_run: str = "") -> None:
    """
    Sign of life without a full run (e.g. an event-driven service like
    songrequest that only works on requests). Sets status=ok and the time
    without changing counters.
    """
    now = _now_iso()
    data = _read()
    data["service"] = _service
    data["last_run_end"] = now
    data["last_success"] = now
    data["status"] = "ok"
    if message:
        data["message"] = message
    if next_run:
        data["next_run"] = next_run
    _write(data)
