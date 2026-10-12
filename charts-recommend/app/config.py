"""
Konfiguration fuer den charts-recommend-Service.
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


# --- Pfade (wie charts-sync) ---
MUSIC_DIR = os.environ.get("MUSIC_DIR", "/music/Music")
CHARTS_DIR = os.environ.get("CHARTS_DIR", "/charts")
# PLAYLIST_DIR entfaellt - der Dienst schreibt keine .m3u mehr (Download-only).

# --- ListenBrainz (Multi-User, Download-only) ---
# Der Dienst liest den OEFFENTLICHEN Hoergeschmack einer Liste von ListenBrainz-
# Usern und laesst die gemeinsame Library mitwachsen. Er schreibt KEINE Playlists.
LISTENBRAINZ_ENABLED = _get_bool("LISTENBRAINZ_ENABLED", True)

# Liste oeffentlicher ListenBrainz-USERNAMEN, deren Geschmack die gemeinsame
# Library mitwachsen laesst. NUR Benutzernamen (oeffentlich, KEINE Secrets).
# Leer = Quelle aus (Dienst laeuft leer, kein Fehler).
_users_raw = os.environ.get("RECOMMEND_LISTENBRAINZ_USERS", "").strip()
# Backward-Compat: alter Einzelwert LISTENBRAINZ_USER, wenn die Liste leer ist.
LISTENBRAINZ_USER = os.environ.get("LISTENBRAINZ_USER", "").strip()
if _users_raw:
    LISTENBRAINZ_USERS = [u.strip() for u in _users_raw.split(",") if u.strip()]
elif LISTENBRAINZ_USER:
    LISTENBRAINZ_USERS = [LISTENBRAINZ_USER]
else:
    LISTENBRAINZ_USERS = []

# Token NUR zum Anheben des Rate-Limits beim Lesen der oeffentlichen Profile.
# NICHT noetig, um die Profile ueberhaupt zu lesen. KEIN Passwort - weder
# ListenBrainz noch Navidrome.
LISTENBRAINZ_TOKEN = os.environ.get("LISTENBRAINZ_TOKEN", "").strip()

# Welche Datenquellen: "stats" (meistgehoerte Recordings, sofort da) und/oder
# "createdfor" (Weekly Jams/Exploration, evtl. erst nach 2-4 Wochen). Default
# beide. Kommagetrennt.
RECOMMEND_SOURCES = [
    s.strip().lower() for s in
    os.environ.get("RECOMMEND_SOURCES", "stats,createdfor").split(",")
    if s.strip()
]

# Fuer die "createdfor"-Quelle: welche "Created for you"-Playlists geholt werden:
# "jams" (Bekanntes/Beliebtes) und/oder "exploration" (Neuentdeckungen).
LISTENBRAINZ_KINDS = [
    k.strip().lower() for k in
    os.environ.get("LISTENBRAINZ_KINDS", "jams,exploration").split(",")
    if k.strip()
]

# Zeitraum fuer die "stats"-Quelle: week | month | quarter | half_yearly |
# year | all_time (ListenBrainz-Range-Werte). Default "month".
RECOMMEND_STATS_RANGE = os.environ.get("RECOMMEND_STATS_RANGE", "month").strip()

# --- Last.fm (optional, sofort nutzbar mit vorhandenem API-Key) ---
LASTFM_ENABLED = _get_bool("LASTFM_ENABLED", False)
LASTFM_API_KEY = os.environ.get("LASTFM_API_KEY", "").strip()
# Modus: "top" (user.getTopTracks) oder "similar" (track.getSimilar).
LASTFM_MODE = os.environ.get("LASTFM_MODE", "similar").strip().lower()
# Last.fm-Username (fuer "top" bzw. als Seed-Quelle bei "similar"). Optional.
LASTFM_USER = os.environ.get("LASTFM_USER", "").strip()
# Optionale Seed-Songs fuer "similar", falls du keinen Last.fm-User hast.
# Format: "Artist - Title; Artist2 - Title2".
_seeds_raw = os.environ.get("LASTFM_SEEDS", "").strip()
LASTFM_SEEDS: list[tuple[str, str]] = []
for part in _seeds_raw.split(";"):
    part = part.strip()
    if " - " in part:
        a, _, t = part.partition(" - ")
        if a.strip() and t.strip():
            LASTFM_SEEDS.append((a.strip(), t.strip()))

# --- Mengen ---
# Per-User-Obergrenze der Empfehlungen (gilt pro ListenBrainz-User und Quelle
# sowie als Gesamt-Cap der Last.fm-Quelle, wie bisher).
RECOMMEND_LIMIT = _get_int("RECOMMEND_LIMIT", 100)

# --- HTTP-Robustheit (ListenBrainz/Last.fm) ---
# Timeout pro HTTP-Request (Sekunden) und Anzahl der Versuche je Request. Ein
# Transport-Fehler (Timeout/HTTP/Verbindung) wird bis HTTP_RETRIES-mal mit
# kurzem Backoff wiederholt, erst danach als Fehler an den Aufrufer gemeldet.
HTTP_TIMEOUT = _get_int("HTTP_TIMEOUT", 30)
HTTP_RETRIES = _get_int("HTTP_RETRIES", 3)

# --- Matching (identisch zu charts-sync) ---
MATCH_THRESHOLD = _get_int("MATCH_THRESHOLD", 87)
ARTIST_MIN_SCORE = _get_int("ARTIST_MIN_SCORE", 60)
IGNORE_COMPILATIONS = _get_bool("IGNORE_COMPILATIONS", True)
AUDIO_EXTENSIONS = {
    ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma", ".wav", ".alac",
}

# --- Nachladen (Verzahnung mit charts-download) ---
# Fehlende Empfehlungen werden in EINE kombinierte missing-Datei geschrieben, die
# charts-download nachlaedt (dessen MISSING_FILES muss diese Datei enthalten).
# 1 = missing-Datei schreiben (nachladen), 0 = nur zeigen was schon da ist.
WRITE_MISSING = _get_bool("WRITE_MISSING", True)
# Fester Name der EINEN kombinierten Missing-Liste (keine Playlists mehr, daher
# keine Aufteilung nach Quelle). Interner Override-Haken, nicht in compose/.env
# verdrahtet - der Default genuegt.
MISSING_FILE = os.environ.get("RECOMMEND_MISSING_FILE", "missing-recommend.txt")

# --- Scheduler ---
# ListenBrainz aktualisiert woechentlich; taeglich pruefen schadet nicht.
INTERVAL_HOURS = _get_int("INTERVAL_HOURS", 24)
RUN_ON_START = _get_bool("RUN_ON_START", True)

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
# Request). Default fuer charts-recommend: "falling".
PUSHOVER_SOUND = os.environ.get("PUSHOVER_SOUND", "falling").strip()
# Fehler-Alarm (Abruf fehlgeschlagen) nur bei Zustandswechsel senden (1) statt
# bei jedem Lauf (0) - verhindert taeglichen Spam waehrend einer laengeren
# Stoerung. Analog zu charts-sync NOTIFY_ONLY_ON_CHANGE.
NOTIFY_ONLY_ON_CHANGE = _get_bool("NOTIFY_ONLY_ON_CHANGE", True)
