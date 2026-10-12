"""
Konfiguration fuer den charts-download-Service.
Alle Werte werden aus Umgebungsvariablen gelesen (siehe .env.example).
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


# --- slskd-API ---
# Basis-URL der slskd-Instanz (im Docker-Netz meist der Containername).
SLSKD_HOST = os.environ.get("SLSKD_HOST", "http://slskd:5030")
# API-Key aus der slskd.yml (web.authentication.api_keys). Rolle: readwrite.
SLSKD_API_KEY = os.environ.get("SLSKD_API_KEY", "").strip()
# url_base, falls slskd unter einem Unterpfad laeuft (Standard "/").
SLSKD_URL_BASE = os.environ.get("SLSKD_URL_BASE", "/").strip()

# --- Quelle: missing.txt ---
# Die von charts-sync geschriebene(n) Fehlliste(n). Es koennen mehrere Dateien
# (semikolon-getrennt) angegeben werden, z.B. Global- und Schweiz-Charts.
MISSING_FILES = [
    p.strip() for p in os.environ.get(
        "MISSING_FILES", "/charts/missing.txt"
    ).split(";") if p.strip()
]

# --- Auswahl-Kriterien ---
# Minimale MP3-Bitrate in kbps. Ergebnisse darunter werden verworfen.
# (Auf Wunsch: nur MP3 >= 256; FLAC/andere Formate werden ignoriert.)
MIN_BITRATE = _get_int("MIN_BITRATE", 256)

# Aehnlichkeitsschwelle 0-100 fuer das Matching von Suchergebnissen gegen
# "Artist - Titel". Hoeher = strenger. 80 ist ein guter Startwert.
MATCH_THRESHOLD = _get_int("MATCH_THRESHOLD", 80)

# Ausschluss-Keywords: Dateien, deren Name eines dieser Woerter enthaelt, werden
# komplett verworfen (nur Originalversionen laden - keine Lyrics-Videos,
# Remixe, Live-, Acapella-, Sped-up-Versionen usw.).
#
# Werte in der .env / Umgebung:
#   - nicht gesetzt ODER leer   -> eingebaute Standardliste (empfohlen)
#   - "off" / "none"            -> Filter komplett AUS
#   - "wort1,wort2,..."         -> eigene Liste (ersetzt die Standardliste)
from .matching import DEFAULT_EXCLUDE_KEYWORDS

_exclude_env = os.environ.get("EXCLUDE_KEYWORDS", "").strip()
if _exclude_env.lower() in ("off", "none"):
    EXCLUDE_KEYWORDS = []
elif _exclude_env:
    EXCLUDE_KEYWORDS = [k.strip() for k in _exclude_env.split(",") if k.strip()]
else:
    EXCLUDE_KEYWORDS = DEFAULT_EXCLUDE_KEYWORDS

# Global auszuschliessende Kuenstler: Songs dieser Kuenstler werden NIE geladen
# (egal aus welcher Chart-Quelle/missing-Datei). Ergaenzung zum playlist-
# spezifischen "exclude_artists" in charts-sync: waehrend jenes nur die
# Playlist-Referenz verhindert, sorgt DIESE Liste dafuer, dass der Song gar
# nicht erst heruntergeladen und in die Library gelegt wird. Fuer Kuenstler,
# die du generell nicht in der Sammlung willst (z.B. Mundart-Schlager wie
# Trauffer, den die APIs faelschlich als "Pop" fuehren).
# Werte: kommagetrennt ("Trauffer,Nemo") ODER leer (kein Ausschluss).
# Vergleich case-insensitiv, trifft auch feat.-Kombinationen.
EXCLUDE_ARTISTS = [
    a.strip() for a in os.environ.get("EXCLUDE_ARTISTS", "").split(",")
    if a.strip()
]

# Compilation-/Sampler-Treffer komplett ablehnen (1) statt nur nachrangig zu
# behandeln (0). Bei 1 wird ein Song NICHT geladen, wenn nur Compilation-Rips
# verfuegbar sind (z.B. "Bravo Hits", "Top 100", "Various Artists ...") - er
# bleibt dann "missing", bis eine echte Single-/Album-Version auftaucht. So
# bleibt die Library sauber (nur echte Singles/Alben, keine Sampler-Tracks).
# Bei 0 (altes Verhalten) wird die Compilation genommen, wenn es nichts
# Besseres gibt. Die Compilation-Erkennung ist eine Pfad-Heuristik
# (is_compilation), kein sicheres Signal.
REJECT_COMPILATIONS = _get_bool("REJECT_COMPILATIONS", False)

# --- Mengensteuerung / Sicherheit ---
# Wie viele Songs pro Lauf maximal heruntergeladen (bzw. im Dry-Run geplant)
# werden. Bewusst konservativ, damit nicht auf einen Schlag alles losgeht.
MAX_PER_RUN = _get_int("MAX_PER_RUN", 10)

# DRY_RUN=1: NICHTS wird heruntergeladen. Es wird nur geloggt/benachrichtigt,
#   was geladen WUERDE (zum Pruefen der Matching-Qualitaet). STANDARD.
# DRY_RUN=0: Downloads werden tatsaechlich per slskd-API angestossen.
DRY_RUN = _get_bool("DRY_RUN", True)

# --- Suche ---
# Timeout einer einzelnen slskd-Suche in ms.
SEARCH_TIMEOUT_MS = _get_int("SEARCH_TIMEOUT_MS", 15000)
# Wie lange (Sekunden) maximal auf den Abschluss einer Suche gewartet wird.
SEARCH_WAIT_SECONDS = _get_int("SEARCH_WAIT_SECONDS", 25)
# Nur Nutzer mit freiem Upload-Slot beruecksichtigen (schnellere Downloads).
REQUIRE_FREE_SLOT = _get_bool("REQUIRE_FREE_SLOT", True)

# Cooldown (Stunden) fuer erfolglose Suchen. Ein Song, der gesucht, aber nicht
# in ausreichender Qualitaet gefunden wurde, wird fuer so viele Stunden
# uebersprungen. Verhindert, dass dauerhaft nicht-findbare Songs (z.B. viele
# Mundart-Titel) in JEDEM Lauf das MAX_PER_RUN-Kontingent blockieren und die
# findbaren Songs nie drankommen. Nach Ablauf wird erneut versucht.
# 0 = kein Cooldown (jeden Lauf erneut suchen, altes Verhalten).
SEARCH_COOLDOWN_HOURS = _get_int("SEARCH_COOLDOWN_HOURS", 24)

# Verfall (Tage) der Anbieter-Blockliste pro Song. charts-import merkt sich,
# welcher Soulseek-Anbieter fuer einen Song eine schlechte/upgesampelte Datei
# geliefert hat (State: blocked_providers). Dieser Anbieter wird fuer GENAU
# DIESEN Song so lange uebersprungen. Nach Ablauf bekommt er wieder eine Chance
# (evtl. hat er seine Datei inzwischen durch eine bessere ersetzt).
# 0 = nie verfallen (dauerhaft geblockt, bis manuell entfernt).
BLOCK_TTL_DAYS = _get_int("BLOCK_TTL_DAYS", 30)

# --- Scheduler ---
INTERVAL_MINUTES = _get_int("INTERVAL_MINUTES", 60)
RUN_ON_START = _get_bool("RUN_ON_START", True)

# --- State ---
# Datei, in der bereits angefragte Songs vermerkt werden (keine Doppel-Downloads).
STATE_FILE = os.environ.get("STATE_FILE", "/charts/.charts-download-state.json")

# --- Manifest ---
# Datei, in der pro Download die sauberen Chart-Metadaten (Artist/Titel)
# hinterlegt werden. charts-import liest sie und taggt die Datei korrekt,
# statt die schlechten slskd-Tags zu uebernehmen. MUSS im selben (geteilten)
# Ordner liegen, den auch charts-import als /charts sieht.
MANIFEST_FILE = os.environ.get("MANIFEST_FILE", "/charts/.charts-manifest.json")

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
# Request). Default fuer charts-download: "falling".
PUSHOVER_SOUND = os.environ.get("PUSHOVER_SOUND", "falling").strip()
