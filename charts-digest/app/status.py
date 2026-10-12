"""
Shared status module for all charts services (dashboard integration).

Each service writes a small JSON status file to STATUS_DIR/<service>.json at
the end of a run. The charts-dashboard reads these files and displays them.
COPY (like in the other services) - no runtime dependency.

Schema: see charts-import/app/status.py.
"""

import json
import logging
import os
import tempfile
import time

from .i18n import t

logger = logging.getLogger(__name__)

STATUS_DIR = os.environ.get("STATUS_DIR", "/charts/.charts-status")

CUMULATIVE_KEYS = {
    "imported", "enqueued", "upgraded", "rejected", "cleaned", "retried",
    "requests", "digests",
}

_service = os.environ.get("STATUS_SERVICE", "unknown")
_run_start_iso = ""


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def init(service: str) -> None:
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
        fd, tmp = tempfile.mkstemp(dir=STATUS_DIR, prefix=f".{_service}.", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, _path())
    except OSError as e:
        logger.debug(t("status.write_failed", path=_path(), error=e))


def start() -> None:
    global _run_start_iso
    _run_start_iso = _now_iso()
    data = _read()
    data["service"] = _service
    data["last_run_start"] = _run_start_iso
    data["status"] = "running"
    _write(data)


def finish(status: str = "ok", message: str = "",
           counters: dict | None = None, next_run: str = "") -> None:
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
    totals = data.get("totals") or {}
    for k, v in (counters or {}).items():
        if k in CUMULATIVE_KEYS and isinstance(v, (int, float)):
            totals[k] = (totals.get(k, 0) or 0) + v
    data["totals"] = totals
    data["next_run"] = next_run
    _write(data)


def set_next_run(next_run: str) -> None:
    if not next_run:
        return
    data = _read()
    data["service"] = _service
    data["next_run"] = next_run
    _write(data)
