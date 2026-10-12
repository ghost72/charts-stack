"""
Konfiguration fuer den charts-flashback-Service.
Alle Werte aus Umgebungsvariablen (siehe .env.example).

charts-flashback ist ein Download-only-Dienst: er liest die "zuletzt
gespielt"-Rotation von FLASHBACK FM (GraphQL, oeffentlich) pro Dekaden-Channel
und laesst die GEMEINSAME Library mitwachsen. Er schreibt KEINE Playlists - die
Dekaden-Anzeige uebernehmen die Navidrome Smart Playlists (NSP), die nach Jahr
filtern.
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


def _get_bool(name: str, default: bool) -> bool:
    return os.environ.get(name, "1" if default else "0") == "1"


def _get_enabled(name: str, default: bool = True) -> bool:
    """
    Toleranter An/Aus-Schalter: akzeptiert true/1/yes/on (case-insensitiv) als
    "an", leer -> default. Ein nicht-leerer, nicht erkannter Wert gilt als "aus"
    (Konfigurationsfehler soll sichtbar sein) und loggt einmalig eine Warnung.
    Zusaetzlich zu _get_bool (ersetzt es nicht), damit true/false sicher greift.
    """
    raw = os.environ.get(name, "").strip().lower()
    if raw == "":
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    logger.warning(t("flashback.config.enabled_unparsed", name=name, value=raw))
    return False


def _get_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


# --- Pfade (wie charts-sync/charts-recommend) ---
MUSIC_DIR = os.environ.get("MUSIC_DIR", "/music/Music")
CHARTS_DIR = os.environ.get("CHARTS_DIR", "/charts")

# --- An/Aus-Schalter (Default an; Container bleibt immer laufen) ---
# false = Dienst pausieren (loggt "deaktiviert", tut nichts, Status idle - KEIN
# Fehler). Toleranter Parser: true/1/yes/on bzw. false/0/no/off. Bei true ist
# zusaetzlich ein FLASHBACK_HASH noetig - sonst bleibt der Dienst ebenfalls idle.
FLASHBACK_ENABLED = _get_enabled("FLASHBACK_ENABLED", True)

# --- FLASHBACK FM (GraphQL-Quelle) ---
# Persisted-Query-Hash der LastPlayed-Query. Oeffentlich, KEIN Secret - aber er
# kann sich aendern, wenn FLASHBACK FM seine App neu baut. Dann aus dem Browser-
# Netzwerktab neu holen (Request "played", URL endet auf .../LastPlayed/<HASH>?).
# Leer = Dienst laeuft leer (kein Fehler).
FLASHBACK_HASH = os.environ.get("FLASHBACK_HASH", "").strip()

# Welche Streams (streamName) abgefragt werden. Komma-getrennt.
# WICHTIG: Nur der LIVE-HAUPTSTREAM "flashbackfm.ffm" fuehrt eine
# played-Historie (ueber alle Dekaden gemischt). Die thematischen Dekaden-/
# Kuenstler-Channels liefern played=null und sind hier NICHT sinnvoll.
# Default = Hauptstream.
FLASHBACK_CHANNELS = [
    c.strip() for c in
    os.environ.get("FLASHBACK_CHANNELS", "flashbackfm.ffm").split(",")
    if c.strip()
]

# Wie viele "zuletzt gespielt"-Eintraege pro Channel abgefragt werden (limitTo).
FLASHBACK_PLAYED_LIMIT = _get_int("FLASHBACK_PLAYED_LIMIT", 50)
# Deckel: max. so viele NEUE Songs pro Lauf verarbeiten (gegen Ueberlauf, falls
# viele Channels konfiguriert sind).
FLASHBACK_LIMIT = _get_int("FLASHBACK_LIMIT", 50)

# State-Datei: schon gesehene (artist|title)-Keys, damit die Radio-Rotation
# nicht bei jedem Lauf dieselben Songs erneut verarbeitet.
STATE_FILE = os.path.join(CHARTS_DIR, ".charts-flashback-state.json")

# Ziel-Fehlliste (charts-download laedt sie nach, wenn sie in dessen
# MISSING_FILES steht). Sauberes "artist - title"-Format wie charts-sync,
# OHNE Jahr im Namen.
MISSING_FILE = os.environ.get("FLASHBACK_MISSING_FILE", "missing-flashback.txt")

# Jahr-Map (artist|title -> jahr), die charts-import beim Taggen liest, um das
# date/originaldate-Tag zu setzen - OHNE das Jahr je in Datei-/Ordnernamen zu
# schreiben. Liegt im geteilten Charts-Ordner, damit charts-import drankommt.
YEAR_MAP_FILE = os.path.join(CHARTS_DIR, ".charts-flashback-years.json")

# --- MusicBrainz (Jahr-Aufloesung, Hybrid) ---
# Genaues Erscheinungsjahr via MusicBrainz; wenn nicht plausibel/leer -> der
# Aufrufer faellt auf die Channel-Dekaden-Mitte zurueck (year.py).
MB_ENABLED = _get_bool("MB_ENABLED", True)
# MusicBrainz verlangt einen aussagekraeftigen User-Agent.
MB_USER_AGENT = os.environ.get("MB_USER_AGENT", "charts-flashback/1.0 (self-hosted)")
# Rate-Limit: MusicBrainz erlaubt ~1 Request/Sekunde. 1.1s Puffer.
MB_RATE_SLEEP = _get_float("MB_RATE_SLEEP", 1.1)
# Mindest-Score eines Recording-Treffers, damit er beruecksichtigt wird.
MB_SCORE_MIN = _get_int("MB_SCORE_MIN", 90)
# Cache (Artist|Title -> Jahr oder ""), damit wiederkehrende Songs die API
# nicht erneut belasten. Auch Misses werden gecacht.
MB_CACHE_FILE = os.path.join(CHARTS_DIR, ".charts-flashback-mbcache.json")

# --- Deezer-Casing (kanonische Schreibweise von Artist/Titel) ---
# FLASHBACK FM liefert Artist/Titel in GROSSBUCHSTABEN. Vor dem Schreiben in die
# missing-Liste (-> Ordner-/Dateiname) wird die kanonische Schreibweise ueber die
# offene Deezer-Such-API aufgeloest (kein API-Key), konsistent zu charts-sync.
DEEZER_CANONICAL_ENABLED = _get_bool("DEEZER_CANONICAL_ENABLED", True)
# Prozess-weiter Cache (Artist|Title -> [artist, title]), auch Misses (Rohwert)
# werden gecacht. Liegt im geteilten Charts-Ordner, analog MB_CACHE_FILE.
DEEZER_CACHE_FILE = os.path.join(CHARTS_DIR, ".charts-flashback-deezer-cache.json")
# Mindest-Aehnlichkeit (artist UND title, 0-100) eines Deezer-Treffers, damit er
# als kanonische Quelle gilt. Etwas unter MATCH_THRESHOLD, da normalize() ohnehin
# lowercased (Casing egal) - der Schwellwert faengt nur echte Fehltreffer ab.
DEEZER_MATCH_MIN = _get_int("DEEZER_MATCH_MIN", 80)
# Asymmetrische Validierung fuer reale Faelle, an denen die reine UND-Schwelle
# (beide >= MATCH_MIN) scheitert, obwohl es derselbe Song ist:
#   - Alias-Kuenstler (FLASHBACK "OMD" vs. Deezer "Orchestral Manoeuvres In The
#     Dark"): Titel 100, Artist niedrig.
#   - Titel mit Klammer-Zusatz (Deezer "There Must Be an Angel (Playing With My
#     Heart)"): Artist 100, Titel durch den Zusatz gedrueckt.
# Regel: Ist EIN Feld sehr sicher (>= STRONG), reicht beim anderen RELAX. Sonst
# gilt weiter die strikte UND-Schwelle MATCH_MIN. STRONG hoch (fast exakt),
# RELAX moderat, damit die Lockerung keine Fehltreffer durchlaesst.
DEEZER_MATCH_STRONG = _get_int("DEEZER_MATCH_STRONG", 97)
DEEZER_MATCH_RELAX = _get_int("DEEZER_MATCH_RELAX", 75)

# --- Matching (identisch zu charts-sync/charts-recommend) ---
MATCH_THRESHOLD = _get_int("MATCH_THRESHOLD", 87)
ARTIST_MIN_SCORE = _get_int("ARTIST_MIN_SCORE", 60)
IGNORE_COMPILATIONS = _get_bool("IGNORE_COMPILATIONS", True)
AUDIO_EXTENSIONS = {
    ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma", ".wav", ".alac",
}

# --- Nachladen (Verzahnung mit charts-download) ---
WRITE_MISSING = _get_bool("WRITE_MISSING", True)

# --- HTTP-Robustheit (FLASHBACK FM GraphQL) ---
HTTP_TIMEOUT = _get_int("HTTP_TIMEOUT", 30)
HTTP_RETRIES = _get_int("HTTP_RETRIES", 3)

# --- Scheduler ---
INTERVAL_HOURS = _get_int("FLASHBACK_INTERVAL_HOURS", 2)
RUN_ON_START = _get_bool("RUN_ON_START", True)

# --- Pushover (optional) ---
PUSHOVER_TOKEN = os.environ.get("PUSHOVER_TOKEN", "").strip()
PUSHOVER_USER = os.environ.get("PUSHOVER_USER", "").strip()
# Pushover fuer diesen Dienst global stummschalten (ohne Token/User zu loeschen).
PUSHOVER_ENABLED = os.environ.get("PUSHOVER_ENABLED", "1") == "1"
PUSHOVER_DEVICE = os.environ.get("PUSHOVER_DEVICE", "").strip()
PUSHOVER_DEVICES_TTL = _get_int("PUSHOVER_DEVICES_TTL", 3600)
# Pushover-Benachrichtigungston. Leer = Account-Default (kein sound-Feld im
# Request). Default fuer charts-flashback: "falling".
PUSHOVER_SOUND = os.environ.get("PUSHOVER_SOUND", "falling").strip()
NOTIFY_ONLY_ON_CHANGE = _get_bool("NOTIFY_ONLY_ON_CHANGE", True)
