"""
Konfiguration fuer charts-songrequest (Web-UI + API).
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


# --- slskd-API (identisch zu charts-download) ---
SLSKD_HOST = os.environ.get("SLSKD_HOST", "http://slskd:5030")
SLSKD_API_KEY = os.environ.get("SLSKD_API_KEY", "").strip()
SLSKD_URL_BASE = os.environ.get("SLSKD_URL_BASE", "/").strip()

# --- Auswahl-Kriterien (identisch zu charts-download) ---
MIN_BITRATE = _get_int("MIN_BITRATE", 256)
MATCH_THRESHOLD = _get_int("MATCH_THRESHOLD", 80)
REQUIRE_FREE_SLOT = _get_bool("REQUIRE_FREE_SLOT", True)

# Ausschluss-Keywords (nur Originalversionen laden). Wie charts-download:
# leer/nicht gesetzt -> eingebaute Standardliste; "off"/"none" -> Filter aus;
# eigene kommagetrennte Liste -> ersetzt die Standardliste.
from .matching import DEFAULT_EXCLUDE_KEYWORDS

_exclude_env = os.environ.get("EXCLUDE_KEYWORDS", "").strip()
if _exclude_env.lower() in ("off", "none"):
    EXCLUDE_KEYWORDS = []
elif _exclude_env:
    EXCLUDE_KEYWORDS = [k.strip() for k in _exclude_env.split(",") if k.strip()]
else:
    EXCLUDE_KEYWORDS = DEFAULT_EXCLUDE_KEYWORDS

# Compilation-/Sampler-Treffer komplett ablehnen (wie charts-download).
# Manuelle Requests sollen eine echte Single/Album-Version bekommen, keinen
# Sampler-Track. Default an.
REJECT_COMPILATIONS = _get_bool("REJECT_COMPILATIONS", True)

# --- Suche ---
SEARCH_TIMEOUT_MS = _get_int("SEARCH_TIMEOUT_MS", 15000)
SEARCH_WAIT_SECONDS = _get_int("SEARCH_WAIT_SECONDS", 25)

# --- Library (fuer die Vorab-Pruefung "schon vorhanden?") ---
# Navidrome-Library-Root. Muss (read-only reicht) in den Container gemountet
# sein, damit vor der Suche geprueft werden kann, ob der Song schon existiert.
# Leer/nicht gemountet -> Pruefung wird uebersprungen (dann laedt es wie bisher).
MUSIC_DIR = os.environ.get("MUSIC_DIR", "/music/Music")

# --- Manifest (dieselbe Datei wie charts-download/charts-import!) ---
# WICHTIG: Muss auf dieselbe Datei zeigen, die charts-import liest, damit der
# manuell angeforderte Song beim naechsten Import-Lauf sauber einsortiert wird.
MANIFEST_FILE = os.environ.get("MANIFEST_FILE", "/charts/.charts-manifest.json")

# Playlist-Label fuer das Manifest (taucht in der charts-import-Meldung auf).
MANUAL_SOURCE_LABEL = os.environ.get("MANUAL_SOURCE_LABEL", "manuell")

# --- Web-UI / Auth ---
# Reiner Mehrbenutzer-Login ueber SONGREQUEST_USERS = "user1:pass1,user2:pass2".
# Mindestens ein Benutzer MUSS definiert sein, sonst startet die App nicht
# (kein ungeschuetzter Zugriff von aussen). Der frueher unterstuetzte
# Einfach-Login (APP_PASSWORD/APP_USERS) wurde entfernt.


def _parse_users() -> dict:
    """
    Baut die Benutzer->Passwort-Map aus SONGREQUEST_USERS.
    Format "user1:pass1,user2:pass2": der Benutzername wird am ERSTEN ':'
    getrennt, der Rest ist das Passwort (ein Passwort darf also keinen
    Doppelpunkt enthalten; Komma trennt Paare). charts-songrequest ist reiner
    Mehrbenutzer-Login - es gibt KEINEN Einfach-Passwort-Fallback mehr.
    """
    users: dict = {}
    raw = os.environ.get("SONGREQUEST_USERS", "").strip()
    if raw:
        for pair in raw.split(","):
            pair = pair.strip()
            if not pair or ":" not in pair:
                continue
            name, pw = pair.split(":", 1)
            name = name.strip()
            if name and pw:
                users[name] = pw
    return users


# Benutzer->Passwort-Map. Leer = App verweigert den Start.
APP_USERS = _parse_users()


def _parse_user_devices() -> dict:
    """
    Baut die Benutzer->Pushover-Geraet-Map aus USER_PUSHOVER_DEVICES.
    Format "user1:Geraet1,user2:Geraet2" (Komma trennt Paare, erster ':'
    trennt Benutzername vom Geraetenamen). Das MATCHING erfolgt spaeter auf
    den eingeloggten BENUTZERNAMEN.

    Zweck: Wird ein manuell angeforderter Song spaeter von charts-import in die
    Library einsortiert, bekommt der anfordernde User eine Pushover-Meldung an
    GENAU DIESES Geraet. Steht ein User NICHT in der Map, wird kein Geraet ins
    Manifest geschrieben -> es gibt KEINE Meldung (bewusst: nie an alle senden).
    Leer/nicht gesetzt -> leeres dict -> fuer niemanden eine Verfuegbarkeits-
    Meldung.
    """
    out: dict = {}
    raw = os.environ.get("USER_PUSHOVER_DEVICES", "").strip()
    if raw:
        for pair in raw.split(","):
            pair = pair.strip()
            if not pair or ":" not in pair:
                continue
            name, device = pair.split(":", 1)
            name = name.strip()
            device = device.strip()
            if name and device:
                out[name] = device
    return out


# Benutzer->Pushover-Geraet. Leer = fuer niemanden eine Verfuegbarkeits-Meldung.
USER_PUSHOVER_DEVICES = _parse_user_devices()

# Secret fuer die Session-Cookies (zufaelliger String). Fehlt er, wird ein
# zufaelliger generiert (dann sind bestehende Sessions nach Neustart ungueltig).
SESSION_SECRET = os.environ.get("SESSION_SECRET", "").strip()

# --- Pushover (optional, Bestaetigung bei Request) ---
PUSHOVER_TOKEN = os.environ.get("PUSHOVER_TOKEN", "").strip()
PUSHOVER_USER = os.environ.get("PUSHOVER_USER", "").strip()
PUSHOVER_DEVICE = os.environ.get("PUSHOVER_DEVICE", "").strip()
# Cache-Dauer (Sekunden) fuer die vom Pushover-Account abgerufene Geräteliste.
# Gegen die wird die PUSHOVER_DEVICE-Liste vor dem Senden gefiltert, damit ein
# ungültiger/deaktivierter Gerätename nicht Pushovers "an alle"-Fallback
# ausloest (Doppel-Push). Standard 3600s = 1h.
PUSHOVER_DEVICES_TTL = _get_int("PUSHOVER_DEVICES_TTL", 3600)
# Pushover-Benachrichtigungston. Leer = Account-Default (kein sound-Feld im
# Request). Default fuer charts-songrequest: "magic". HINWEIS: aktuell inert,
# da charts-songrequest selbst keine Pushover-Meldung sendet - die
# Verfuegbarkeits-Meldung ("Dein Song ist da!") kommt von charts-import. Die
# Variable bleibt der Einheitlichkeit halber vorhanden und greift erst, falls
# songrequest spaeter selbst sendet.
PUSHOVER_SOUND = os.environ.get("PUSHOVER_SOUND", "magic").strip()
