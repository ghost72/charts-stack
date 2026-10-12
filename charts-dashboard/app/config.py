"""
Konfiguration fuer charts-dashboard (Status-UI).
Alle Werte aus Umgebungsvariablen (siehe .env.example).
"""

import os


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


# --- Status-Dateien (von den charts-Diensten geschrieben) ---
# Verzeichnis mit den <dienst>.json-Statusdateien. Zeigt auf denselben
# physischen Ordner wie STATUS_DIR der anderen Dienste (/volume2/music/Charts/
# .charts-status), hier read-only gemountet.
STATUS_DIR = os.environ.get("STATUS_DIR", "/charts/.charts-status")

# --- Ordner fuer die Speicher-/Bestandsanzeige (read-only) ---
CHARTS_DIR = os.environ.get("CHARTS_DIR", "/charts")
MUSIC_DIR = os.environ.get("MUSIC_DIR", "/music/Music")

# --- Welche Container ueberwacht werden (Docker-Status) ---
WATCH_CONTAINERS = os.environ.get(
    "WATCH_CONTAINERS",
    "slskd charts-sync charts-hitparade charts-flashback charts-download "
    "charts-import charts-recommend charts-upgrade charts-songrequest "
    "charts-digest charts-watchdog charts-dashboard",
).split()

# --- slskd (Verbindungsstatus + Transfer-Zahlen) ---
SLSKD_HOST = os.environ.get("SLSKD_HOST", "http://slskd:5030")
SLSKD_API_KEY = os.environ.get("SLSKD_API_KEY", "").strip()

# --- Lidarr (Erreichbarkeits-Check) ---
LIDARR_URL = os.environ.get("LIDARR_URL", "").strip()
LIDARR_API_KEY = os.environ.get("LIDARR_API_KEY", "").strip()

# --- Last.fm (optionaler Live-Erreichbarkeits-Check der externen Quelle) ---
# Nur fuer den Last.fm-Live-Ping genutzt. Leer = Last.fm-Kachel bleibt "muted"
# (nicht konfiguriert), nie rot. Last.fm hat keinen key-freien Ping.
LASTFM_API_KEY = os.environ.get("LASTFM_API_KEY", "").strip()

# --- FLASHBACK FM (GraphQL-Live-Check der externen Quelle) ---
# Persisted-Query-Hash; leer = FLASHBACK-Kachel bleibt "muted" (nicht
# konfiguriert). Ein toter Hash -> Kachel rot (Signal: Hash neu holen).
FLASHBACK_HASH = os.environ.get("FLASHBACK_HASH", "").strip()

# --- TTL-Cache fuer die externen Live-Checks (Sekunden) ---
# Das Dashboard pollt alle 15s; die externen Pings gehen aber ueber WAN zu
# Drittanbietern. Darum gecacht (Default 2 min), entkoppelt vom 15s-Poll.
EXTERNAL_TTL = _get_int("DASHBOARD_EXTERNAL_TTL", 120)

# --- TTL-Cache fuer die Deezer-Genre-Liste des Quellen-Finders (Sekunden) ---
# Die Genre-Liste aendert sich selten. Der Quellen-Finder laedt sie beim ersten
# Seitenaufruf und frischt sie erst nach Ablauf dieser TTL (Default 24 h) beim
# naechsten Aufruf - das deckt den "taeglich aktualisieren"-Wunsch ab (kein
# Cronjob). Konsistent zum DASHBOARD_*_TTL-Stil.
DASHBOARD_GENRE_CACHE_TTL = _get_int("DASHBOARD_GENRE_CACHE_TTL", 86400)

# --- Pushover (fuer den Live-validate-Check in _external_status) ---
# Beide gesetzt -> echter POST /1/users/validate.json (kein Nachrichten-Budget).
# Leer -> Kachel "muted" (nicht konfiguriert), nie rot.
PUSHOVER_TOKEN = os.environ.get("PUSHOVER_TOKEN", "").strip()
PUSHOVER_USER = os.environ.get("PUSHOVER_USER", "").strip()

# --- Auto-Refresh der Seite (Sekunden) ---
REFRESH_SECONDS = _get_int("DASHBOARD_REFRESH_SECONDS", 15)

# --- Dezenter Spenden-Footer (PayPal/Revolut) ---
# Standardmaessig AN. Mit DASHBOARD_DONATE=0/false/no abschaltbar; dann wird der
# Footer serverseitig gar nicht erst in die Seite gerendert.
DASHBOARD_DONATE = os.environ.get(
    "DASHBOARD_DONATE", "1").strip() not in ("0", "false", "no")

# --- Web-UI / Auth ---
# Das Dashboard hat einen EIGENEN Login (getrennt von charts-songrequest), weil
# es interne Zustaende zeigt. Fester Benutzer "admin" + DASHBOARD_PASSWORD.
# Benutzername ueber DASHBOARD_USERNAME aenderbar (Default "admin").
# Fallback: ist DASHBOARD_PASSWORD leer, werden die gemeinsamen
# APP_USERS/APP_PASSWORD genutzt (damit nichts bricht, wenn nichts gesetzt ist).
DASHBOARD_USERNAME = os.environ.get("DASHBOARD_USERNAME", "admin").strip()
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "").strip()

APP_PASSWORD = os.environ.get("APP_PASSWORD", "").strip()
APP_PASSWORD_USERNAME = os.environ.get("APP_PASSWORD_USERNAME", "youruser").strip()


def _parse_users() -> dict:
    """
    Benutzer->Passwort-Map. Vorrang:
      1. DASHBOARD_PASSWORD gesetzt -> NUR {DASHBOARD_USERNAME: DASHBOARD_PASSWORD}
         (eigener, getrennter Dashboard-Login).
      2. sonst Fallback auf die gemeinsamen APP_USERS + APP_PASSWORD.
    """
    if DASHBOARD_PASSWORD:
        return {DASHBOARD_USERNAME: DASHBOARD_PASSWORD}

    users: dict = {}
    raw = os.environ.get("APP_USERS", "").strip()
    if raw:
        for pair in raw.split(","):
            pair = pair.strip()
            if not pair or ":" not in pair:
                continue
            name, pw = pair.split(":", 1)
            name = name.strip()
            if name and pw:
                users[name] = pw
    if APP_PASSWORD and APP_PASSWORD_USERNAME not in users:
        users[APP_PASSWORD_USERNAME] = APP_PASSWORD
    return users


APP_USERS = _parse_users()
SESSION_SECRET = os.environ.get("SESSION_SECRET", "").strip()
