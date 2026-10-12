"""
Konfiguration fuer charts-hitparade.
Alle Werte aus Umgebungsvariablen (siehe .env.example).
"""

import logging
import os

from .i18n import t

logger = logging.getLogger(__name__)


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _get_enabled(name: str, default: bool = True) -> bool:
    """
    Toleranter An/Aus-Schalter: akzeptiert true/1/yes/on (case-insensitiv) als
    "an", leer -> default. Ein nicht-leerer, nicht erkannter Wert gilt als "aus"
    (Konfigurationsfehler soll sichtbar sein) und loggt einmalig eine Warnung.
    """
    raw = os.environ.get(name, "").strip().lower()
    if raw == "":
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    logger.warning(t("hitparade.config.enabled_unparsed", name=name, value=raw))
    return False


# --- An/Aus-Schalter (Default an; Container bleibt immer laufen) ---
# false = Dienst pausieren (loggt "deaktiviert", tut nichts, Status idle - KEIN
# Fehler). Toleranter Parser: true/1/yes/on bzw. false/0/no/off.
HITPARADE_ENABLED = _get_enabled("HITPARADE_ENABLED", True)


# --- Pfade (im Container gemountet, wie charts-sync) ---
MUSIC_DIR = os.environ.get("MUSIC_DIR", "/music/Music")
PLAYLIST_DIR = os.environ.get("PLAYLIST_DIR", "") or MUSIC_DIR
CHARTS_DIR = os.environ.get("CHARTS_DIR", "/charts")

# --- Welche hitparade.ch-Charts geholt werden ---
# Komma-getrennt. Moegliche Werte:
#   singles      -> offizielle Single-Top-100 (FUNKTIONIERT, HTML).
#   singles-ch   -> CH-Artist-Charts. ACHTUNG: laedt per AJAX nach -> im HTML
#                   0 Songs -> wuerde dauerhaft als Fehler gemeldet. NICHT im
#                   Default. Die CH-Hits laufen ohnehin in der Top-100 mit.
# Pro Chart entsteht eine eigene Playlist + missing-Datei:
#   singles    -> hitparade.m3u       / missing-hitparade.txt
#   singles-ch -> hitparade-ch.m3u    / missing-hitparade-ch.txt
HITPARADE_CHARTS = [
    c.strip() for c in os.environ.get("HITPARADE_CHARTS", "singles").split(",")
    if c.strip()
]

# Playlist-/Datei-/Anzeigenamen je Chart.
CHART_META = {
    "singles": {
        "playlist": "hitparade.m3u",
        "missing": "missing-hitparade.txt",
        "title": "Schweizer Hitparade \U0001F1E8\U0001F1ED",
    },
    "singles-ch": {
        "playlist": "hitparade-ch.m3u",
        "missing": "missing-hitparade-ch.txt",
        "title": "Schweizer Hitparade - CH-Artists \U0001F1E8\U0001F1ED",
    },
}

# --- Scheduler ---
# Die Hitparade wird woechentlich (sonntags) aktualisiert. Default: einmal pro
# Woche am Sonntag um 20:00 Uhr pruefen. Wochentag 0=Montag ... 6=Sonntag.
RUN_WEEKDAY = _get_int("HITPARADE_WEEKDAY", 6)      # 6 = Sonntag
RUN_HOUR = _get_int("HITPARADE_HOUR", 20)           # 20 Uhr
RUN_MINUTE = _get_int("HITPARADE_MINUTE", 0)
# Wie oft der Scheduler prueft, ob der Lauf-Zeitpunkt erreicht ist (Sekunden).
CHECK_INTERVAL_SECONDS = _get_int("HITPARADE_CHECK_SECONDS", 300)
# Beim Container-Start sofort einmal laufen (1) oder aufs Fenster warten (0).
RUN_ON_START = os.environ.get("RUN_ON_START", "1") == "1"

# --- HTTP ---
FETCH_TIMEOUT_SECONDS = _get_int("HITPARADE_TIMEOUT_SECONDS", 15)
FETCH_RETRIES = _get_int("HITPARADE_RETRIES", 3)

# --- Matching (identisch zu charts-sync) ---
MATCH_THRESHOLD = _get_int("MATCH_THRESHOLD", 87)
ARTIST_MIN_SCORE = _get_int("ARTIST_MIN_SCORE", 60)
IGNORE_COMPILATIONS = os.environ.get("IGNORE_COMPILATIONS", "1") == "1"
AUDIO_EXTENSIONS = {
    ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma", ".wav", ".alac",
}

# --- Pushover (optional) ---
PUSHOVER_TOKEN = os.environ.get("PUSHOVER_TOKEN", "").strip()
PUSHOVER_USER = os.environ.get("PUSHOVER_USER", "").strip()
# Pushover fuer diesen Dienst global stummschalten (ohne Token/User zu loeschen).
PUSHOVER_ENABLED = os.environ.get("PUSHOVER_ENABLED", "1") == "1"
PUSHOVER_DEVICE = os.environ.get("PUSHOVER_DEVICE", "").strip()
# Pushover-Benachrichtigungston. Leer = Account-Default (kein sound-Feld im
# Request). Default fuer charts-hitparade: "falling".
PUSHOVER_SOUND = os.environ.get("PUSHOVER_SOUND", "falling").strip()
NOTIFY_ONLY_ON_CHANGE = os.environ.get("NOTIFY_ONLY_ON_CHANGE", "1") == "1"
NOTIFY_MISSING_LIMIT = _get_int("NOTIFY_MISSING_LIMIT", 40)

# Zustandsdatei (fuer "nur bei Aenderung"-Benachrichtigung). Liegt in CHARTS_DIR.
STATE_FILE = os.environ.get("STATE_FILE", ".charts-hitparade-state.json")
