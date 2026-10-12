"""
Gemeinsames Status-Modul fuer alle charts-Dienste (Dashboard-Anbindung).

Jeder Dienst schreibt am Ende eines Laufs (bzw. nach einem Request) eine kleine
JSON-Statusdatei nach STATUS_DIR/<service>.json. Der charts-dashboard-Dienst
liest diese Dateien und zeigt sie an - so bekommt das UI EXAKTE Lauf-Statistiken
(wie viel verarbeitet, wann zuletzt, Erfolg/Fehler) ohne Log-Parsing.

Dieses Modul wird (wie slsk.py/manifest.py) in jeden Dienst KOPIERT - keine
Laufzeit-Abhaengigkeit zwischen den Diensten.

Schema (STATUS_DIR/<service>.json):
{
  "service": "charts-import",
  "last_run_start": "<ISO-8601>",   # Beginn des letzten Laufs
  "last_run_end":   "<ISO-8601>",   # Ende des letzten Laufs
  "last_success":   "<ISO-8601>",   # Ende des letzten ERFOLGREICHEN Laufs
  "status": "ok" | "error" | "running",
  "message": "<kurze Zusammenfassung>",
  "counters": { ... },              # Zaehler des LETZTEN Laufs (dienstspezifisch)
  "totals":   { ... },              # kumulativ ueber alle Laeufe (summiert)
  "next_run": "<ISO-8601 oder leer>"
}

Umgebungsvariablen:
  STATUS_DIR  - Zielverzeichnis (Default: /charts/.charts-status). Muss im
                gemounteten Charts-Ordner liegen, den auch das Dashboard sieht.
  STATUS_SERVICE - Name des Dienstes (Default: aus Argument an init()).
"""

import json
import logging
import os
import tempfile
import time

from .i18n import t

logger = logging.getLogger(__name__)

STATUS_DIR = os.environ.get("STATUS_DIR", "/charts/.charts-status")

# Nur diese Counter sind "geleistete Arbeit" und werden in 'totals' kumuliert.
# Alles andere (missing, library_tracks, sources, scanned, bad, planned,
# matched ...) ist ein Momentan-/Zustandswert und wird NICHT aufsummiert.
CUMULATIVE_KEYS = {
    "imported",    # charts-import: einsortierte Songs
    "enqueued",    # charts-download: angestossene Downloads
    "upgraded",    # charts-upgrade: ersetzte Songs
    "rejected",    # charts-import: aussortierte Dateien
    "cleaned",     # charts-import: aufgeraeumte Downloads
    "retried",     # charts-download: neu angestossene Fehlschlaege
    "requests",    # charts-songrequest: bediente Anfragen
}

_service = os.environ.get("STATUS_SERVICE", "unknown")
_run_start_iso = ""


def _now_iso() -> str:
    """Lokale Zeit als ISO-8601 mit Zeitzonen-Offset."""
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def init(service: str) -> None:
    """Einmal beim Start aufrufen: legt den Dienstnamen fest."""
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
        # Atomar schreiben (temp + replace), damit das Dashboard nie eine
        # halb geschriebene Datei liest.
        fd, tmp = tempfile.mkstemp(dir=STATUS_DIR, prefix=f".{_service}.", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, _path())
    except OSError as e:
        logger.debug(t("status.write_failed", path=_path(), error=e))


def start() -> None:
    """Beginn eines Laufs markieren (status=running)."""
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
    Ende eines Laufs markieren. 'counters' sind die Zaehler DIESES Laufs; sie
    werden zusaetzlich in 'totals' aufsummiert (kumulativ ueber alle Laeufe).
    status: "ok" (erfolgreich) oder "error" (fehlgeschlagen).
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
    # Totals NUR fuer echte "geleistete Arbeit" kumulieren (Durchsatz-Werte).
    # Momentanwerte wie 'missing', 'library_tracks', 'sources' sind ein ZUSTAND,
    # kein Durchsatz - sie aufzusummieren ergibt unsinnige Zahlen (z.B. 'missing'
    # waechst mit jedem Lauf, obwohl es dieselben fehlenden Songs sind). Daher
    # eine Whitelist: nur diese Keys landen in 'totals'.
    totals = data.get("totals") or {}
    for k, v in (counters or {}).items():
        if k in CUMULATIVE_KEYS and isinstance(v, (int, float)):
            totals[k] = (totals.get(k, 0) or 0) + v
    data["totals"] = totals
    data["next_run"] = next_run
    _write(data)


def set_next_run(next_run: str) -> None:
    """
    Aktualisiert NUR den naechsten geplanten Lauf-Zeitpunkt, ohne Status,
    Lauf-Zeiten oder Zaehler anzutasten. Gedacht fuer Scheduler-Dienste, die den
    next_run laufend aktuell halten wollen (sonst zeigt das Dashboard einen beim
    letzten Lauf eingefrorenen - evtl. 'ueberfaelligen' - Wert).
    """
    if not next_run:
        return
    data = _read()
    data["service"] = _service
    data["next_run"] = next_run
    _write(data)


def heartbeat(message: str = "", next_run: str = "") -> None:
    """
    Lebenszeichen ohne vollen Lauf (z.B. ein ereignisgesteuerter Dienst wie
    songrequest, der nur bei Requests arbeitet). Setzt status=ok und die Zeit,
    ohne Zaehler zu veraendern.
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
