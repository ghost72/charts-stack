"""
Konfiguration fuer den charts-import-Service.
Alle Werte werden aus Umgebungsvariablen gelesen (siehe .env.example).
"""

import logging
import os

logger = logging.getLogger(__name__)


def _get_int(name: str, default: int) -> int:
    """Liest eine Ganzzahl aus der Umgebung, faellt bei Fehlern auf den Default zurueck."""
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _get_bool(name: str, default: bool) -> bool:
    """Liest einen Bool aus der Umgebung (1/0)."""
    return os.environ.get(name, "1" if default else "0") == "1"


def _get_float(name: str, default: float) -> float:
    """Liest einen Float aus der Umgebung, faellt bei Fehlern auf den Default zurueck."""
    try:
        return float(os.environ.get(name, default))
    except (ValueError, TypeError):
        return default


# --- Pfade (im Container gemountet) ---
# DOWNLOADS_DIR: Wohin slskd die fertigen Chart-Downloads legt (= slskd
#                directories.downloads). Hier liegen die frisch geladenen Songs.
# MUSIC_DIR:     Navidrome-Library-Root. Dorthin werden die Songs einsortiert
#                (Music/<Artist>/<Album>/<Track>).
DOWNLOADS_DIR = os.environ.get("DOWNLOADS_DIR", "/downloads")
MUSIC_DIR = os.environ.get("MUSIC_DIR", "/music/Music")
# Verzeichnis, in dem charts-sync die Playlist-.m3u ablegt (charts.m3u,
# mundart.m3u, 80er-90er.m3u). Wird vom Backfill genutzt, um --fix-broken NUR
# auf die Chart-Songs (Playlist-Eintraege) zu beschraenken, damit deine
# uebrige (Lidarr-)Library unangetastet bleibt. Default = MUSIC_DIR.
PLAYLIST_DIR = os.environ.get("PLAYLIST_DIR", MUSIC_DIR)

# Manifest von charts-download: ordnet heruntergeladenen Dateien die SAUBEREN
# Chart-Metadaten (Artist/Titel) zu. Wird bevorzugt genutzt und in die Datei-
# Tags geschrieben, damit Songs nicht als "Unknown Album" landen und von
# charts-sync korrekt gematcht werden koennen.
# WICHTIG: charts-download schreibt es nach /charts/.charts-manifest.json.
# charts-import sieht denselben Ordner (physisch /volume2/music/Charts) als
# DOWNLOADS_DIR (/music/Charts) - daher liegt das Manifest hier standardmaessig
# unter DOWNLOADS_DIR/.charts-manifest.json.
MANIFEST_FILE = os.environ.get(
    "MANIFEST_FILE", os.path.join(DOWNLOADS_DIR, ".charts-manifest.json")
)

# Signal-Datei, mit der charts-import nach einem Import charts-sync anstoesst,
# damit die Playlist sofort (statt erst beim naechsten 12h-Lauf) aktualisiert
# wird. Liegt im geteilten Charts-Ordner (den auch charts-sync sieht).
RESYNC_SIGNAL = os.environ.get(
    "RESYNC_SIGNAL", os.path.join(DOWNLOADS_DIR, ".resync-requested")
)

# charts-download-State: Wird gelesen, um abgelehnte Songs (Tuersteher)
# freizugeben, damit charts-download sie neu (und moeglichst besser) laedt.
# Liegt im selben geteilten Charts-Ordner. Leer setzen, um das Freigeben zu
# deaktivieren.
DOWNLOAD_STATE_FILE = os.environ.get(
    "DOWNLOAD_STATE_FILE", os.path.join(DOWNLOADS_DIR, ".charts-download-state.json")
)

# --- Import-Verhalten ---
# COPY_MODE=1  -> Datei KOPIEREN (Original bleibt im Downloads-Ordner liegen,
#                 damit slskd sie eine Weile weiterteilen ("seeden") kann).
# COPY_MODE=0  -> Datei VERSCHIEBEN (verschwindet sofort aus dem Share).
COPY_MODE = _get_bool("COPY_MODE", True)

# REQUIRE_MANIFEST=1 (Default): NUR Dateien importieren, die einen Eintrag im
# charts-download-Manifest haben (= gezielt von charts-download geladene Chart-
# Songs). Der Downloads-Ordner enthaelt haeufig ganze Sampler-/Album-Ordner, die
# beim Song-Download von Soulseek mitkamen; ohne diese Sperre wuerde charts-
# import daraus wahllos Tracks (z.B. Compilation-Versionen) einsortieren - und
# das bei JEDEM Lauf erneut, solange die Quelldateien im geteilten Downloads-
# Ordner liegen. Mit der Sperre bleibt die Library sauber.
# REQUIRE_MANIFEST=0 -> altes Verhalten: alles importieren, was zuordenbar ist
# (z.B. wenn du Dateien manuell in den Downloads-Ordner legst).
REQUIRE_MANIFEST = _get_bool("REQUIRE_MANIFEST", True)

# --- Aufraeum-Strategie ---
# Es gibt zwei Strategien, wie der DOWNLOADS_DIR aufgeraeumt wird. KEEP_LATEST
# hat Vorrang, wenn beide gesetzt sind.
#
# KEEP_LATEST (Anzahl-basiert, EMPFOHLEN):
#   Behalte immer die N NEUESTEN Downloads, loesche nur den Ueberschuss.
#   Vorteil: Der Share bleibt stabil bei ~N Dateien, egal wie oft/selten du
#   manuell neue Songs suchst. Ideal, um dauerhaft genug Dateien zu teilen und
#   nicht als "Leecher" zu gelten.
#   Werte:
#     "all" = ALLE Downloads behalten (nie loeschen, maximales Seeden)
#     Zahl  = nur die N neuesten behalten, Rest loeschen
#     0     = deaktiviert (dann greift RETENTION_DAYS)
# Intern: "all" -> -1 (Sonderwert fuer "unbegrenzt behalten").
def _parse_keep(value, default: int) -> int:
    if value is None:
        return default
    s = str(value).strip().lower()
    if s in ("all", "alle"):
        return -1
    try:
        return int(s)
    except ValueError:
        return default


KEEP_LATEST = _parse_keep(os.environ.get("KEEP_LATEST"), 500)

# RETENTION_DAYS (Alter-basiert, Fallback):
#   Loescht Downloads, die aelter als so viele Tage sind. Nur aktiv, wenn
#   KEEP_LATEST = 0 ist. Passt schlecht zu unregelmaessigem manuellem Suchen,
#   weil der Share bei laengeren Pausen leerlaufen kann. 0 = nie loeschen.
RETENTION_DAYS = _get_int("RETENTION_DAYS", 0)

# Papierkorb-Aufbewahrung: Der Upgrade-Flow verschiebt ersetzte (schlechtere)
# Versionen nach MUSIC_DIR/.charts-trash/<Datum>/ (reversibel). Diese werden
# automatisch geloescht, wenn sie aelter als so viele Tage sind. 0 = nie
# loeschen (Papierkorb dann manuell leeren).
TRASH_RETENTION_DAYS = _get_int("TRASH_RETENTION_DAYS", 30)

# Import nur durchfuehren, wenn die Datei seit mind. so vielen Sekunden nicht
# mehr veraendert wurde (verhindert das Einsortieren halbfertiger Downloads).
MIN_AGE_SECONDS = _get_int("MIN_AGE_SECONDS", 120)

# --- Scheduler ---
# Intervall zwischen zwei Laeufen in Minuten.
INTERVAL_MINUTES = _get_int("INTERVAL_MINUTES", 30)
# Beim Start sofort einmal laufen (1) oder erst nach dem Intervall (0).
RUN_ON_START = _get_bool("RUN_ON_START", True)

# --- Metadaten-Backfill (periodisches Nachholen) ---
# Holt fehlende Cover/ReplayGain nach, wenn eine externe Quelle beim Import
# voruebergehend nicht erreichbar war (fail-open laesst den Song dann ohne
# Cover/RG in der Library). Laeuft im selben Scheduler-Loop, aber selten
# (eigenes Intervall in Stunden). Scope: NUR die Chart-Songs aus dem Manifest -
# die uebrige (Lidarr-)Library bleibt unangetastet.
# 0 = Backfill aus.
BACKFILL_INTERVAL_HOURS = _get_int("BACKFILL_INTERVAL_HOURS", 24)
# Pause zwischen den API-Calls (ms), schont iTunes/Deezer-Rate-Limits.
BACKFILL_SLEEP_MS = _get_int("BACKFILL_SLEEP_MS", 400)
# Max. Dateien pro Backfill-Lauf (0 = alle). Deckel gegen sehr lange Laeufe.
BACKFILL_LIMIT = _get_int("BACKFILL_LIMIT", 0)
# GROSS geschriebene flashback-Namen im Backfill gegen die offizielle Deezer-
# Schreibweise abgleichen und korrigieren (nur sichere Treffer; benennt
# Library-Ordner/Datei um + aktualisiert Tags/Manifest). 1 = an, 0 = aus.
BACKFILL_RECANON = _get_bool("BACKFILL_RECANON", True)

# --- Dateitypen ---
# Dateiendungen, die als Musikdateien betrachtet werden (identisch zu charts-sync).
AUDIO_EXTENSIONS = {
    ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma", ".wav", ".alac",
}

# --- Tuersteher / Qualitaetskontrolle ---
# Dateien, die diese Pruefung nicht bestehen, werden NICHT in die Library
# einsortiert, sondern nach REJECTED_DIR verschoben (Quarantaene). So landet
# kein Muell in der Library, egal woher die Datei kommt (Download, Altbestand,
# manuell). Der Song bleibt in missing.txt -> charts-download laedt eine
# bessere Version.

# 1 = Tuersteher aktiv, 0 = aus (alles wird einsortiert wie frueher).
REJECT_ENABLED = _get_bool("REJECT_ENABLED", True)

# --- Upsampling-/Fake-Bitrate-Erkennung (Frequenz-Cutoff) ---
# Erkennt Dateien, die eine hohe Bitrate BEHAUPTEN (z.B. 320 kbps), real aber
# nur den Frequenzumfang einer niedrigen Bitrate haben (upgesampelt/gefaelscht).
# Gemessen wird der echte Frequenz-Cutoff via ffmpeg (quality.py). Bewusst
# KONSERVATIV: greift nur bei behaupteter Bitrate >= REJECT_UPSAMPLED_MIN_BITRATE
# und nur, wenn der gemessene Cutoff DEUTLICH (Toleranz) unter der Erwartung
# liegt. Ist der Cutoff nicht messbar (ffmpeg/Filter fehlt), wird NICHT
# abgelehnt. 1 = aktiv, 0 = aus.
#
# ACHTUNG CPU: Jede Pruefung dekodiert die Datei einmal mit ffmpeg. Auf
# schwacher Hardware (NAS ohne HW-Transcoding) spuerbar. Nur Manifest-Dateien
# (echte Chart-Downloads) werden geprueft, nicht der ganze Downloads-Ordner.
REJECT_UPSAMPLED = _get_bool("REJECT_UPSAMPLED", True)
# Erst ab dieser BEHAUPTETEN Bitrate (kbps) auf Upsampling pruefen. Darunter
# ist ein niedriger Cutoff normal (keine Faelschung) und die Datei wird ohnehin
# vom Bitrate-Kriterium erfasst.
REJECT_UPSAMPLED_MIN_BITRATE = _get_int("REJECT_UPSAMPLED_MIN_BITRATE", 256)
# Toleranz (kHz): Wie weit der gemessene Cutoff unter der Erwartung liegen darf,
# BEVOR die Datei als upgesampelt gilt. Groesser = nachsichtiger (weniger
# Fehlalarme, findet aber weniger Faelschungen). 2.0 ist ein sicherer Startwert.
CUTOFF_TOLERANCE_KHZ = _get_float("CUTOFF_TOLERANCE_KHZ", 2.0)

# --- Cover / Artwork ---
# EMBED_COVERS=1: Fuer Songs mit sauberen Chart-Metadaten (Manifest) wird das
# passende Cover ueber die konfigurierten Quellen (COVER_SOURCES, iTunes->Deezer)
# gesucht und in die MP3 eingebettet (nur falls noch kein Cover vorhanden). So
# haben die Songs in Navidrome ein Artwork, ohne externe Agents. 0 = aus.
# Mindestlaenge (Sekunden). Kuerzere Dateien gelten als abgebrochene/unvoll-
# staendige Downloads (z.B. 24s-Fragment) und werden abgelehnt + im
# charts-download-State freigegeben (neu gesucht). Chart-Singles sind praktisch
# immer >90s. 0 = Pruefung aus. Betrifft nur den Import (Chart-Songs), nicht die
# uebrige Library.
MIN_DURATION_SECONDS = _get_int("MIN_DURATION_SECONDS", 90)

# --- Deezer-Casing-Kanonisierung (letzte Instanz beim Import) ---
# charts-import loest beim Einsortieren eines Chart-Songs die kanonische
# Schreibweise (Mixed-Case) ueber die offene Deezer-Such-API auf, falls der
# Manifest-Name noch GROSS/verdaechtig ist (durchgerutschte flashback-Rohwerte).
# Zentrale letzte Instanz, damit NIE ein GROSS-Name in die Library kommt.
DEEZER_CANONICAL_ENABLED = _get_bool("DEEZER_CANONICAL_ENABLED", True)
# Cache (artist|title -> [artist, title], auch Misses). Liegt im geteilten
# Charts-Ordner (= DOWNLOADS_DIR), gemeinsam nutzbar mit charts-flashback.
DEEZER_CACHE_FILE = os.environ.get(
    "DEEZER_CACHE_FILE",
    os.path.join(DOWNLOADS_DIR, ".charts-flashback-deezer-cache.json"),
)
# Mindest-Aehnlichkeit (0-100) eines Deezer-Treffers; asymmetrische Lockerung
# ueber STRONG/RELAX (siehe deezer._passes). Konservativ -> keine Fehltreffer.
DEEZER_MATCH_MIN = _get_int("DEEZER_MATCH_MIN", 80)
DEEZER_MATCH_STRONG = _get_int("DEEZER_MATCH_STRONG", 97)
DEEZER_MATCH_RELAX = _get_int("DEEZER_MATCH_RELAX", 75)
# HTTP-Timeout fuer den Deezer-Lookup (Sekunden).
HTTP_TIMEOUT = _get_int("HTTP_TIMEOUT", 15)

EMBED_COVERS = _get_bool("EMBED_COVERS", True)
# OVERWRITE_COVER=1: Bei Chart-Songs (Manifest) ein bereits eingebettetes Cover
# ERSETZEN durch das neu gesuchte Single-Cover (Quelle laut COVER_SOURCES,
# iTunes->Deezer). Wichtig, wenn der Song aus einer Compilation/Sampler stammt -
# dort ist oft das Sampler-Cover eingebettet, nicht das der Single. Das Cover
# wird ueber Kuenstler+Titel gesucht und liefert das Original-Single-/Album-
# Cover. Ersetzt nur, wenn eine Quelle wirklich ein Cover liefert (sonst bleibt
# das vorhandene erhalten). 0 = vorhandenes Cover nie ueberschreiben.
OVERWRITE_COVER = _get_bool("OVERWRITE_COVER", True)
# Cover-Quellen und Reihenfolge (kommagetrennt). iTunes/Apple hat den groessten
# Katalog mit dedizierten Single-Artworks in hoher Aufloesung und ist immer
# quadratisch; Deezer dient als Fallback, falls iTunes einen Song nicht kennt.
#   COVER_SOURCES=itunes,deezer  (Default) = erst iTunes, dann Deezer
#   COVER_SOURCES=deezer         = nur Deezer (altes Verhalten)
COVER_SOURCES = [
    s.strip().lower()
    for s in os.environ.get("COVER_SOURCES", "itunes,deezer").split(",")
    if s.strip()
]
# Gewuenschte Cover-Kantenlaenge bei iTunes (Apple liefert beliebige Groessen
# ueber den Dateinamen; 1000 ist ein guter Kompromiss aus Qualitaet/Groesse).
ITUNES_COVER_SIZE = _get_int("ITUNES_COVER_SIZE", 1000)
# iTunes/Apple drosselt die kostenlose Search-API (HTTP 429/403) nach vielen
# Anfragen in kurzer Zeit. Bei einer Drosselung wartet der Client kurz und
# versucht es erneut (exponentiell steigend), bevor er auf Deezer ausweicht.
# So kommen mehr echte iTunes-Single-Cover durch.
ITUNES_MAX_RETRIES = _get_int("ITUNES_MAX_RETRIES", 3)
# Basis-Wartezeit in ms fuer den ersten Retry (danach verdoppelt: 800, 1600 ...).
ITUNES_RETRY_BASE_MS = _get_int("ITUNES_RETRY_BASE_MS", 800)

# ReplayGain-Normalisierung: beim Import die Lautheit (EBU R128) messen und den
# Track-Gain als Tag schreiben, damit alle Songs gleich laut abgespielt werden.
# Verlustfrei (nur Tags). Braucht ffmpeg im Image. WICHTIG: Der Abspieler
# (Navidrome/App) muss den ReplayGain-Modus aktiviert haben, sonst wirkt es
# nicht. 0 = aus.
REPLAYGAIN = _get_bool("REPLAYGAIN", True)
# Ziel-Lautheit in LUFS. ReplayGain-2.0-Referenz = -18 (leiser, viel Headroom).
# Streaming-Dienste sind lauter: Spotify/YouTube ~-14, Apple Music ~-16.
# Kleinerer (negativerer) Wert = leiser, groesserer = lauter. Default -14
# (Streaming-Niveau, ~+4 dB lauter als die alte -18-Referenz).
REPLAYGAIN_TARGET_LUFS = _get_float("REPLAYGAIN_TARGET_LUFS", -14.0)

# Unterordner in DOWNLOADS_DIR, in den abgelehnte Dateien verschoben werden.
REJECTED_DIR = os.environ.get(
    "REJECTED_DIR", os.path.join(DOWNLOADS_DIR, "_rejected")
)

# Quarantaene-Aufbewahrung: Abgelehnte Dateien in REJECTED_DIR werden
# automatisch geloescht, wenn sie aelter als so viele Tage sind. Ohne diese
# Regel waechst die Quarantaene unbegrenzt (sie ist vom KEEP_LATEST-Aufraeumen
# ausgenommen). Der betroffene Song wird ohnehin neu/besser nachgeladen, die
# aussortierte Datei nicht gebraucht. 0 = nie loeschen (manuell leeren).
REJECTED_RETENTION_DAYS = _get_int("REJECTED_RETENTION_DAYS", 14)

# Ablehnen, wenn kein brauchbarer Artist ermittelbar ist (leer / "Unknown
# Artist"). Album wird bewusst NICHT geprueft, da Chart-Singles oft kein Album
# haben. 1 = aktiv.
REJECT_UNKNOWN_ARTIST = _get_bool("REJECT_UNKNOWN_ARTIST", True)

# Ausschluss-Keywords: Datei ablehnen, wenn der Name eines dieser Woerter
# enthaelt (Lyrics-Videos, Remixe, Live-, Acapella-Versionen usw.). So werden
# auch Alt-/Fremddateien aussortiert, die nicht ueber charts-download kamen.
# Leer lassen fuer die Standardliste; "off"/"none" schaltet den Keyword-Check
# aus; eigene kommagetrennte Liste ersetzt die Standardliste.
_DEFAULT_REJECT_KEYWORDS = [
    "lyrics", "lyric video", "official video", "official audio", "music video",
    "visualizer", "video edit",
    "acapella", "a capella", "instrumental", "karaoke",
    "remix", "mashup", "bootleg", "flip", "rework", "vip mix",
    "extended mix", "club mix", "radio mix", "dance mix", "club intro",
    "live", "cover", "tribute", "unplugged",
    "sped up", "spedup", "slowed", "reverb", "nightcore", "8d", "8d audio",
    "snippet", "teaser", "preview", "loop",
    "break in", "clean short edit", "clean radio edit",
]
_reject_kw_env = os.environ.get("REJECT_KEYWORDS", "").strip()
if _reject_kw_env.lower() in ("off", "none"):
    REJECT_KEYWORDS = []
elif _reject_kw_env:
    REJECT_KEYWORDS = [k.strip() for k in _reject_kw_env.split(",") if k.strip()]
else:
    REJECT_KEYWORDS = _DEFAULT_REJECT_KEYWORDS

# --- Pushover (optional, identisch zum charts-sync / jellyfin-webhook Setup) ---
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
# Request). Default fuer charts-import: "incoming".
PUSHOVER_SOUND = os.environ.get("PUSHOVER_SOUND", "incoming").strip()
# Eigener Ton NUR fuer die manuell via charts-songrequest angeforderte
# Verfuegbarkeits-Meldung ("Dein Song ist da!"). Default "magic", damit der
# User am Ton hoert, dass SEIN angeforderter Song da ist (statt eines
# Routine-Imports). Leer = Account-Default. Die normale Import-Sammelmeldung
# nutzt weiterhin PUSHOVER_SOUND ("incoming").
PUSHOVER_SOUND_MANUAL = os.environ.get("PUSHOVER_SOUND_MANUAL", "magic").strip()

# --- Lidarr-Integration (optional) ---
# Haelt Chart-Single-Kuenstler in Lidarr auf "unmonitored", damit Lidarr sie
# zwar taggt/kennt, aber NICHT den ganzen Katalog laedt. So kann das
# Standard-Monitoring in Lidarr fuer echte Alben/Compilations aktiv bleiben.
# Nur aktiv, wenn LIDARR_URL + LIDARR_API_KEY gesetzt sind.
LIDARR_URL = os.environ.get("LIDARR_URL", "").strip()
LIDARR_API_KEY = os.environ.get("LIDARR_API_KEY", "").strip()
# Master-Schalter (1 = an). Ohne URL/Key ohnehin inaktiv.
LIDARR_UNMONITOR = _get_bool("LIDARR_UNMONITOR", True)
# Fehlende Kuenstler in Lidarr NEU anlegen (direkt unmonitored)? Wenn 0, werden
# nur bereits vorhandene Kuenstler unmonitored, nicht neu angelegt.
LIDARR_ADD_MISSING = _get_bool("LIDARR_ADD_MISSING", True)
# Profile/Rootfolder fuer die Neuanlage (IDs aus deiner Lidarr-Instanz).
LIDARR_QUALITY_PROFILE_ID = _get_int("LIDARR_QUALITY_PROFILE_ID", 4)
LIDARR_METADATA_PROFILE_ID = _get_int("LIDARR_METADATA_PROFILE_ID", 1)
LIDARR_ROOT_FOLDER = os.environ.get("LIDARR_ROOT_FOLDER", "/music/Music").strip()
