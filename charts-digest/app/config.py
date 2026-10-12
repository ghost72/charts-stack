"""
Konfiguration fuer charts-digest (aus Umgebungsvariablen).

charts-digest schickt einmal taeglich eine Pushover-Zusammenfassung der am
VORTAG neu in die Library einsortierten Songs, gruppiert nach Quelle
(charts/mundart/flashback/manuell/upgrade ...). Datengrundlage ist das von
charts-import gefuehrte Import-Log (JSON Lines) - KEINE mtime-/Titel-Heuristik.
"""

import os


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _get_bool(name: str, default: bool) -> bool:
    return os.environ.get(name, "1" if default else "0") == "1"


# --- Zeitplan -------------------------------------------------------------
# Uhrzeit (lokale Zeit) der taeglichen Zusammenfassung. Default 09:00. Der
# Dienst laeuft als Scheduler-Loop und wartet bis zum naechsten Zielzeitpunkt.
DIGEST_HOUR = _get_int("DIGEST_HOUR", 9)
DIGEST_MINUTE = _get_int("DIGEST_MINUTE", 0)

# Beim Start sofort eine Zusammenfassung senden (zum Testen). Default aus -
# sonst kaeme bei jedem Container-Neustart eine Meldung.
RUN_ON_START = _get_bool("RUN_ON_START", False)

# --- Zeitfenster ----------------------------------------------------------
# Rueckblick-Fenster in Stunden. Default 24 = "seit gestern zur selben Zeit".
# Bei DIGEST_HOUR=9 deckt 24h sauber den Vortag 09:00 -> heute 09:00 ab.
DIGEST_WINDOW_HOURS = _get_int("DIGEST_WINDOW_HOURS", 24)

# --- Pfade (im Container gemountet) --------------------------------------
# Geteilter Charts-Ordner: hier liegt das von charts-import geschriebene
# Import-Log. Muss derselbe Ordner sein wie DOWNLOADS_DIR von charts-import.
CHARTS_DIR = os.environ.get("CHARTS_DIR", "/charts")
IMPORT_LOG_FILE = os.environ.get(
    "IMPORT_LOG_FILE", os.path.join(CHARTS_DIR, ".charts-imported-log.jsonl"))

# Log-Aufbewahrung: Beim Lesen werden Eintraege aelter als so viele Tage
# verworfen und das Log kompakt neu geschrieben (sonst waechst es unbegrenzt).
LOG_RETENTION_DAYS = _get_int("LOG_RETENTION_DAYS", 7)

# Max. Anzahl Songs, die pro Quelle in der Nachricht aufgelistet werden. Ziel
# ist, moeglichst ALLE Songs zu listen - der eigentliche Deckel ist das
# Zeichenbudget (PUSHOVER_MESSAGE_LIMIT), nicht diese Zahl. Hoch gesetzt, damit
# sie im Normalfall nicht greift; nur als Sicherheitsnetz gegen Extremfaelle.
# Rest je Quelle wird als "... und N weitere" zusammengefasst.
MAX_PER_SOURCE = _get_int("MAX_PER_SOURCE", 100)

# Lange Songtitel werden am ENDE gekuerzt ("Anfang…"), damit mehr Songs ins
# Zeichenbudget passen. Der Artist bleibt IMMER voll; Artist + Songanfang
# reichen zur Erkennung. Greift erst ab dieser Titellaenge (Zeichen); 0
# schaltet die Kuerzung aus.
TITLE_MAX_LEN = _get_int("TITLE_MAX_LEN", 34)

# Hartes Zeichenbudget der Pushover-Nachricht. Pushover kappt eine 'message'
# bei 1024 Zeichen KOMMENTARLOS (dann fehlt z.B. der Footer / ein Song bricht
# mitten im Titel ab). Deshalb bauen wir die Nachricht budgetbewusst auf und
# ersetzen den Ueberlauf durch ein sauberes "... und N weitere". Etwas Reserve
# unter 1024 fuer den garantierten Footer.
PUSHOVER_MESSAGE_LIMIT = _get_int("PUSHOVER_MESSAGE_LIMIT", 1024)

# Auch dann eine Meldung senden, wenn es NICHTS Neues gab (Default aus - kein
# "0 neue Songs"-Spam; der Dienst meldet nur, wenn wirklich etwas dazukam).
NOTIFY_WHEN_EMPTY = _get_bool("NOTIFY_WHEN_EMPTY", False)

# --- Pushover -------------------------------------------------------------
PUSHOVER_TOKEN = os.environ.get("PUSHOVER_TOKEN", "").strip()
PUSHOVER_USER = os.environ.get("PUSHOVER_USER", "").strip()
# Pushover fuer diesen Dienst global stummschalten (ohne Token/User zu loeschen).
PUSHOVER_ENABLED = os.environ.get("PUSHOVER_ENABLED", "1") == "1"
PUSHOVER_DEVICE = os.environ.get("PUSHOVER_DEVICE", "").strip()
PUSHOVER_DEVICES_TTL = _get_int("PUSHOVER_DEVICES_TTL", 3600)
# Pushover-Benachrichtigungston. Leer = Account-Default (kein sound-Feld im
# Request). Default fuer charts-digest: "magic".
PUSHOVER_SOUND = os.environ.get("PUSHOVER_SOUND", "magic").strip()

# --- Status (Dashboard-Anbindung) ----------------------------------------
TZ = os.environ.get("TZ", "Europe/Zurich")
