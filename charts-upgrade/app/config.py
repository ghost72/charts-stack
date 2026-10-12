"""
Konfiguration fuer den charts-upgrade-Service.
Alle Werte aus Umgebungsvariablen (siehe .env.example).
"""

import logging
import os

logger = logging.getLogger(__name__)


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _get_bool(name: str, default: bool) -> bool:
    return os.environ.get(name, "1" if default else "0") == "1"


def _get_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (ValueError, TypeError):
        return default


# --- slskd-API (wie charts-download) ---
SLSKD_HOST = os.environ.get("SLSKD_HOST", "http://slskd:5030")
SLSKD_API_KEY = os.environ.get("SLSKD_API_KEY", "").strip()
SLSKD_URL_BASE = os.environ.get("SLSKD_URL_BASE", "/").strip()

# --- Scan-Ziel ---
# Navidrome-Library-Root, den charts-upgrade nach schlechten Dateien absucht.
# Read-only gemountet reicht (charts-upgrade schreibt NICHT direkt in die
# Library - das Ersetzen macht charts-import ueber den Manifest/replace-Weg).
MUSIC_DIR = os.environ.get("MUSIC_DIR", "/music/Music")

# --- Qualitaets-Schwellen ---
# Ein Song gilt als "schlecht" (Upgrade-Kandidat), wenn EINES zutrifft:
#   1. Echte Bitrate < BAD_BITRATE (kbps), ODER
#   2. Upsampling erkannt: behauptet >= REJECT_UPSAMPLED_MIN_BITRATE, echter
#      Frequenz-Cutoff aber deutlich zu niedrig (quality.is_upsampled).
BAD_BITRATE = _get_int("BAD_BITRATE", 256)

# Upsampling-Erkennung (identische Parameter wie charts-import).
CHECK_UPSAMPLED = _get_bool("CHECK_UPSAMPLED", True)
REJECT_UPSAMPLED_MIN_BITRATE = _get_int("REJECT_UPSAMPLED_MIN_BITRATE", 256)
CUTOFF_TOLERANCE_KHZ = _get_float("CUTOFF_TOLERANCE_KHZ", 2.0)

# --- Ersatz-Auswahl (wie charts-download) ---
# Nur MP3 >= MIN_BITRATE als Ersatz laden. Andere Formate ignorieren.
MIN_BITRATE = _get_int("MIN_BITRATE", 256)
MATCH_THRESHOLD = _get_int("MATCH_THRESHOLD", 80)
REQUIRE_FREE_SLOT = _get_bool("REQUIRE_FREE_SLOT", True)
# charts-upgrade lehnt Compilation-Rips als Ersatz IMMER ab (fest an): eine
# kuratierte Single/ein Album darf nie durch einen Sampler-Track ersetzt werden.
REJECT_COMPILATIONS = _get_bool("REJECT_COMPILATIONS", True)

# Ausschluss-Keywords (nur Originalversionen laden). Leer = Standardliste,
# "off"/"none" = aus, eigene kommagetrennte Liste ersetzt die Standardliste.
from .matching import DEFAULT_EXCLUDE_KEYWORDS

_exclude_env = os.environ.get("EXCLUDE_KEYWORDS", "").strip()
if _exclude_env.lower() in ("off", "none"):
    EXCLUDE_KEYWORDS = []
elif _exclude_env:
    EXCLUDE_KEYWORDS = [k.strip() for k in _exclude_env.split(",") if k.strip()]
else:
    EXCLUDE_KEYWORDS = DEFAULT_EXCLUDE_KEYWORDS

# --- Compilations ---
# Compilation-/Sampler-Ordner beim Scan komplett UEBERSPRINGEN (nie upgraden).
# Deine kuratierten Compilations (Bravo Hits, Kuschelrock, ...) sind
# vollstaendige Alben und duerfen nicht track-weise durch Soulseek-Singles
# ersetzt werden. Erkennung ueber die Pfad-Heuristik (matching.is_compilation)
# PLUS "viele verschiedene Kuenstler im Album-Ordner" (siehe library.py).
SKIP_COMPILATIONS = _get_bool("SKIP_COMPILATIONS", True)

# --- Mengensteuerung / Sicherheit ---
# Wie viele Upgrades pro Lauf maximal angestossen werden. Konservativ, damit
# nicht auf einen Schlag die halbe Library neu geladen wird.
MAX_PER_RUN = _get_int("MAX_PER_RUN", 10)

# Wie viele Dateien pro Lauf maximal ANALYSIERT werden (ffmpeg-Cutoff ist CPU-
# teuer). Der Scan merkt sich im State, welche Dateien schon geprueft wurden
# (Pfad + mtime + Groesse), und prueft pro Lauf nur bis zu diesem Kontingent
# NEUE/geaenderte Dateien. So arbeitet sich der Dienst schonend durch die
# Library, statt bei jedem Lauf alles neu zu dekodieren.
MAX_SCAN_PER_RUN = _get_int("MAX_SCAN_PER_RUN", 200)

# DRY_RUN=1 (STANDARD): nichts laden/ersetzen. Es wird nur analysiert und
# gemeldet, welche Songs als schlecht/upgesampelt erkannt wurden und was geladen
# WUERDE. Erst auf 0 setzen, wenn die Erkennung im Testlauf ueberzeugt.
DRY_RUN = _get_bool("DRY_RUN", True)

# --- Suche ---
SEARCH_TIMEOUT_MS = _get_int("SEARCH_TIMEOUT_MS", 15000)
SEARCH_WAIT_SECONDS = _get_int("SEARCH_WAIT_SECONDS", 25)

# --- Scheduler ---
# charts-upgrade laeuft selten (Altbestand-Aufraeumen ist kein Eiltask).
# Default: alle 24h.
INTERVAL_MINUTES = _get_int("INTERVAL_MINUTES", 1440)
RUN_ON_START = _get_bool("RUN_ON_START", True)

# --- State / Manifest ---
# Eigener State (analysierte Dateien-Cache + angestossene Upgrades). Liegt im
# geteilten /charts-Ordner wie die State-Dateien der Schwester-Dienste.
STATE_FILE = os.environ.get("STATE_FILE", "/charts/.charts-upgrade-state.json")
# Gemeinsames Manifest mit charts-import (dort wird der replace-Eintrag gelesen).
MANIFEST_FILE = os.environ.get("MANIFEST_FILE", "/charts/.charts-manifest.json")
# Quell-Label fuer das Manifest (taucht in der charts-import-Meldung auf).
SOURCE_LABEL = os.environ.get("SOURCE_LABEL", "upgrade")

# --- Pushover (optional) ---
PUSHOVER_TOKEN = os.environ.get("PUSHOVER_TOKEN", "").strip()
PUSHOVER_USER = os.environ.get("PUSHOVER_USER", "").strip()
# Pushover fuer diesen Dienst global stummschalten (ohne Token/User zu loeschen).
PUSHOVER_ENABLED = os.environ.get("PUSHOVER_ENABLED", "1") == "1"
PUSHOVER_DEVICE = os.environ.get("PUSHOVER_DEVICE", "").strip()
# Cache-Dauer (Sekunden) fuer die vom Pushover-Account abgerufene Geräteliste.
# Gegen die wird die PUSHOVER_DEVICE-Liste vor dem Senden gefiltert, damit ein
# ungültiger/deaktivierter Gerätename nicht Pushovers "an alle"-Fallback
# ausloest (Doppel-Push). Standard 3600s = 1h.
PUSHOVER_DEVICES_TTL = _get_int("PUSHOVER_DEVICES_TTL", 3600)
# Pushover-Benachrichtigungston. Leer = Account-Default (kein sound-Feld im
# Request). Default fuer charts-upgrade: "cosmic".
PUSHOVER_SOUND = os.environ.get("PUSHOVER_SOUND", "cosmic").strip()
