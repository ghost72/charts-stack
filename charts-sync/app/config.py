"""
Konfiguration fuer den Charts-Sync-Service.
Alle Werte werden aus Umgebungsvariablen gelesen (siehe .env.example).
"""

import json
import logging
import os

logger = logging.getLogger(__name__)


def _get_int(name: str, default: int) -> int:
    """Liest eine Ganzzahl aus der Umgebung, faellt bei Fehlern auf den Default zurueck."""
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


# --- Pfade (im Container gemountet) ---
# MUSIC_DIR:    der Ordner, den auch Navidrome als Library-Root sieht.
#               Die Song-Pfade in den Playlists werden relativ zu diesem
#               Verzeichnis geschrieben, damit Navidrome sie zuordnen kann.
# PLAYLIST_DIR: Zielordner fuer die .m3u-Playlists. MUSS ein Ort sein, den
#               Navidrome scannt (z.B. direkt MUSIC_DIR), sonst tauchen die
#               Playlists in Navidrome nicht auf. Default = MUSIC_DIR.
# CHARTS_DIR:   Ablageort fuer die missing-Dateien (muss Navidrome NICHT sehen).
MUSIC_DIR = os.environ.get("MUSIC_DIR", "/music")
PLAYLIST_DIR = os.environ.get("PLAYLIST_DIR", "") or MUSIC_DIR
CHARTS_DIR = os.environ.get("CHARTS_DIR", "/music/Charts")

# Wie viele Chart-Songs pro Quelle beruecksichtigt werden (Default fuer Quellen
# ohne eigenes "limit").
#   "all" (Default) = alle verfuegbaren Tracks der Quelle holen
#   Zahl            = auf so viele Tracks pro Quelle begrenzen
# Intern wird "all" als sehr hohes Limit umgesetzt (Deezer liefert dann alles).
_ALL_LIMIT = 100000


def _parse_limit(value, default: int = _ALL_LIMIT) -> int:
    """Wandelt einen Limit-Wert ('all' oder Zahl) in eine Ganzzahl um."""
    if value is None:
        return default
    s = str(value).strip().lower()
    if s in ("all", "alle", ""):
        return _ALL_LIMIT
    try:
        return int(s)
    except ValueError:
        return default


CHART_LIMIT = _parse_limit(os.environ.get("CHART_LIMIT"), _ALL_LIMIT)

# --- Last.fm (optional, zweite Chart-Quelle neben Deezer) ---
# Kostenloser API-Key von https://www.last.fm/api/account/create.
# Nur noetig, wenn mindestens ein Feed vom Typ "lastfm" aktiv ist.
LASTFM_API_KEY = os.environ.get("LASTFM_API_KEY", "").strip()

# --- Genre-Filter (optional) ---
# Schliesst Chart-Tracks aus, deren dominante Last.fm-Tags in dieser Liste
# stehen (z.B. um Country/Schlager aus den Pop-Charts zu halten). Der Filter
# fragt pro Track track.getTopTags ab und braucht daher LASTFM_API_KEY.
#   GENRE_FILTER=1        -> Filter aktiv (opt-in; Shipped-Default in
#                            compose.yml/.env.example ist 0 = aus)
#   EXCLUDE_TAGS=a,b,c    -> auszuschliessende Tags (kommagetrennt)
# Ein Track fliegt raus, wenn einer seiner staerksten EXCLUDE_TAG_TOP_N Tags
# in EXCLUDE_TAGS steht UND dessen count >= EXCLUDE_TAG_MIN_COUNT ist.
GENRE_FILTER = os.environ.get("GENRE_FILTER", "0") == "1"
# Fremd-Genres, die aus den Pop-Charts gehalten werden sollen. Bewusst OHNE
# "hard rock"/"metal": Rock-Klassiker (AC/DC, Bon Jovi, Guns N' Roses, Europe)
# gehoeren in die 80er/90er-Playlist. Country/Schlager/Folk/Klassik dagegen raus.
_DEFAULT_EXCLUDE_TAGS = (
    "country,contemporary country,modern country,schlager,volksmusik,"
    "folk,americana,bluegrass,classical,classic country"
)


def _parse_tags(value: str) -> set[str]:
    return {t.strip().lower() for t in value.split(",") if t.strip()}


# Leeres oder nicht gesetztes EXCLUDE_TAGS faellt auf die Default-Liste zurueck.
# (Ein leeres EXCLUDE_TAGS= in .env/compose soll NICHT alles durchlassen, sonst
# waere der Filter faktisch aus, obwohl GENRE_FILTER=1 ist.)
_exclude_raw = os.environ.get("EXCLUDE_TAGS", "").strip() or _DEFAULT_EXCLUDE_TAGS
EXCLUDE_TAGS = _parse_tags(_exclude_raw)
# Wie viele der staerksten Tags eines Songs geprueft werden.
EXCLUDE_TAG_TOP_N = _get_int("EXCLUDE_TAG_TOP_N", 3)
# Mindest-count eines Tags (Last.fm-Tags gehen 0-100), damit er zaehlt. 60 haelt
# Troll-/Nischen-Tags (z.B. "brutal death metal 41" bei Rick Astley) draussen;
# nur ein wirklich dominantes Genre (>=60) fuehrt zum Ausschluss.
EXCLUDE_TAG_MIN_COUNT = _get_int("EXCLUDE_TAG_MIN_COUNT", 60)
# Pause zwischen den Genre-Tag-Abfragen (ms), schont die Last.fm-API.
GENRE_FILTER_SLEEP_MS = _get_int("GENRE_FILTER_SLEEP_MS", 200)

# --- Scheduler ---
# Intervall zwischen zwei regulaeren Sync-Laeufen in Stunden (fuer neue Charts).
SYNC_INTERVAL_HOURS = _get_int("SYNC_INTERVAL_HOURS", 12)
# Beim Start sofort einmal synchronisieren (1) oder erst nach dem Intervall (0).
RUN_ON_START = os.environ.get("RUN_ON_START", "1") == "1"

# --- Resync-Trigger (von charts-import) ---
# charts-import legt nach einem Import eine Signal-Datei an, damit die Playlist
# zeitnah aktualisiert wird, statt erst beim naechsten regulaeren Lauf. charts-
# sync prueft alle RESYNC_CHECK_SECONDS, ob das Signal existiert.
# Der Check ist nur eine Datei-Existenzabfrage (praktisch kostenlos); ein
# echter (teurer) Sync-Lauf passiert nur, wenn das Signal wirklich da ist.
RESYNC_SIGNAL = os.environ.get(
    "RESYNC_SIGNAL", os.path.join(CHARTS_DIR, ".resync-requested")
)
RESYNC_CHECK_SECONDS = _get_int("RESYNC_CHECK_SECONDS", 60)

# --- Matching ---
# Ab welcher Aehnlichkeit (0-100) ein Library-Track als Treffer gilt.
MATCH_THRESHOLD = _get_int("MATCH_THRESHOLD", 87)

# Mindest-Aehnlichkeit (0-100) des KUENSTLERS, damit ein Track ueberhaupt als
# Treffer infrage kommt. Verhindert, dass ein gleicher Titel eines anderen
# Kuenstlers faelschlich matcht (z.B. Chart "BLACKPINK - Jump" -> "Van Halen -
# Jump"). Hoeher = strenger. 60 ist ein guter Wert; bei zu vielen Fehltreffern
# erhoehen, bei zu vielen faelschlich fehlenden Songs senken.
ARTIST_MIN_SCORE = _get_int("ARTIST_MIN_SCORE", 60)

# IGNORE_COMPILATIONS=1 (Standard): Library-Tracks, die aus einer Compilation/
#   einem Sampler stammen (z.B. "Various Artists", "Bravo Hits", "Top 100"),
#   werden beim Abgleich IGNORIERT. Ein Chart-Song, der bei dir nur als Teil
#   einer Compilation vorliegt, gilt dann als "fehlend" und wird von
#   charts-download als saubere Single-/Album-Version nachgeladen.
# IGNORE_COMPILATIONS=0: gegen die komplette Library abgleichen (inkl.
#   Compilations) - dann wird ein Song, der in einer Compilation steckt, als
#   vorhanden gewertet und nicht neu geladen.
IGNORE_COMPILATIONS = os.environ.get("IGNORE_COMPILATIONS", "1") == "1"
# Dateiendungen, die als Musikdateien betrachtet werden.
AUDIO_EXTENSIONS = {
    ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma", ".wav", ".alac",
}

# --- Pushover (optional, identisch zum jellyfin-webhook Setup) ---
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
# Request). Default fuer charts-sync: "falling".
PUSHOVER_SOUND = os.environ.get("PUSHOVER_SOUND", "falling").strip()
# Nur benachrichtigen, wenn sich die Playlist gegenueber dem letzten Lauf
# geaendert hat (1) oder nach jedem Lauf (0).
NOTIFY_ONLY_ON_CHANGE = os.environ.get("NOTIFY_ONLY_ON_CHANGE", "1") == "1"

# Wie viele fehlende Songs maximal in die Pushover-Meldung geschrieben werden.
# So bekommst du die "missing"-Liste direkt aufs Handy, ohne dich am NAS
# einzuloggen. Pushover begrenzt eine Nachricht auf 1024 Zeichen; bei sehr
# vielen fehlenden Songs wird die Liste gekuerzt und ein Hinweis angehaengt.
# 0 = keine fehlenden Songs in der Meldung auffuehren (nur die Anzahl).
NOTIFY_MISSING_LIMIT = _get_int("NOTIFY_MISSING_LIMIT", 40)

# Zustandsdatei, in der der letzte Stand pro Playlist gespeichert wird
# (fuer die "nur bei Aenderung"-Benachrichtigung und die Sortierung). Liegt in
# CHARTS_DIR.
STATE_FILE = os.environ.get("STATE_FILE", ".charts-sync-state.json")

# --- Playlist-Definition aus einer DATEI (optional, admin-freundlich) ---
# Die CHARTS-Env ist zwingend EINZEILIG (Env-Limitierung) und bei vielen
# Playlists/Quellen unuebersichtlich. Alternativ kann die komplette Definition
# aus einer mehrzeiligen, lesbaren JSON-Datei geladen werden (CHARTS_FILE).
# Prioritaet: CHARTS_FILE (wenn gesetzt UND Datei existiert UND valides JSON)
# > CHARTS-Env > eingebaute Default-Playlists. Leer = Datei-Weg aus.
CHARTS_FILE = os.environ.get("CHARTS_FILE", "").strip()

# --- Playlist-Reihenfolge ---
# In welcher Reihenfolge die Tracks in die .m3u geschrieben werden:
#   "chart"        (Default): nach Chart-Position (Platz 1 zuoberst) - so wie
#                  die Charts wirklich aussehen.
#   "newest-first": zuletzt neu in die Playlist gekommene Songs zuoberst,
#                  danach die aelteren. Praktisch, wenn die Playlist staendig
#                  wechselt und man in der Player-App (z.B. Substreamer) sofort
#                  die Neuzugaenge oben sehen will, statt immer dieselben
#                  Chart-Platz-1-Songs. Das "erste Gesehen"-Datum je Song wird
#                  im State gefuehrt (first_seen), damit die Reihenfolge ueber
#                  Laeufe hinweg stabil bleibt.
PLAYLIST_ORDER = os.environ.get("PLAYLIST_ORDER", "chart").strip().lower()


class Feed:
    """
    Eine einzelne Chart-Quelle innerhalb einer Playlist.

    kind:    "deezer" oder "lastfm".
    enabled: False -> Feed wird uebersprungen (ohne ihn aus der Config zu
             loeschen). So kann man z.B. alle Deezer-Feeds ausschalten und nur
             Last.fm importieren lassen (oder umgekehrt).
    limit:   max. Anzahl Tracks fuer GENAU diese Quelle ("all" -> sehr hoch).

    Deezer-spezifisch:
      deezer_type = "playlist" (Playlist-ID) oder "chart" (Chart-ID)
      source      = die ID
    Last.fm-spezifisch:
      method = z.B. "chart.getTopTracks", "geo.getTopTracks",
               "tag.getTopTracks", "user.getTopTracks"
      params = dict mit method-spezifischen Zusatzwerten
               (country / tag / user / period)
    """

    def __init__(self, kind: str, limit: int, enabled: bool = True,
                 deezer_type: str = "chart", source: str = "0",
                 method: str = "", params: dict | None = None):
        self.kind = kind
        self.limit = limit
        self.enabled = enabled
        self.deezer_type = deezer_type
        self.source = source
        self.method = method
        self.params = params or {}

    def __repr__(self) -> str:
        state = "on" if self.enabled else "OFF"
        if self.kind == "lastfm":
            detail = f"{self.method}{self.params or ''}"
        else:
            detail = f"{self.deezer_type}:{self.source}"
        return f"Feed[{self.kind}/{state}]({detail} <={self.limit})"


class ChartSource:
    """
    Eine Playlist-Definition. Kann EINE oder MEHRERE Quellen (Deezer und/oder
    Last.fm) buendeln; mehrere Quellen werden zu einer Playlist zusammengefuehrt
    (gemergt) und dabei ueber Kuenstler+Titel dedupliziert.

    feeds:   Liste von Feed-Objekten.
    name:    Dateiname der .m3u (landet in PLAYLIST_DIR).
    title:   Anzeigename in Navidrome (Emojis + {date} erlaubt).
    missing: Dateiname der Fehlliste (landet in CHARTS_DIR).
    limit:   Standard-Limit des Eintrags (Fallback fuer Feeds ohne eigenes
             "limit"; faellt selbst auf CHART_LIMIT zurueck).
    exclude_artists: Kuenstler, die aus GENAU dieser Playlist entfernt werden.
    genre_filter: Pro-Playlist-Override fuer den globalen GENRE_FILTER
             (True/False). None (fehlt in der Config) -> globaler Default.
    """

    def __init__(self, feeds: list["Feed"], name: str, title: str,
                 missing: str, limit: int,
                 exclude_artists: set[str] | None = None,
                 genre_filter: bool | None = None):
        self.feeds = feeds
        self.name = name
        self.title = title
        self.missing = missing
        self.limit = limit
        # Kuenstler, die NUR aus DIESER Playlist ferngehalten werden (unabhaengig
        # vom Genre-Filter). Gross-/Kleinschreibung egal. Gedacht fuer Faelle,
        # die sich per Genre nicht sauber trennen lassen - z.B. Schweizer
        # Mundart-Schlager (Trauffer), den die APIs als "Pop" fuehren, du aber
        # nicht in dieser Playlist willst. Andere Playlists behalten den Artist.
        self.exclude_artists: set[str] = exclude_artists or set()
        # Pro-Playlist-Override fuer den Genre-Filter. True/False stechen den
        # globalen GENRE_FILTER aus; None (Feld fehlt) faellt auf den globalen
        # Default zurueck (siehe effective_genre_filter).
        self.genre_filter: bool | None = genre_filter
        # Wird von deezer.fetch_chart nach dem Lauf befuellt (fuer Pushover):
        # {"deezer_new": int, "lastfm_new": int, "genre_filtered": int}
        self.last_stats: dict[str, int] = {}

    @property
    def active_feeds(self) -> list["Feed"]:
        """Nur die aktivierten Feeds (enabled=True)."""
        return [f for f in self.feeds if f.enabled]

    @property
    def effective_genre_filter(self) -> bool:
        """
        Der tatsaechlich wirksame Genre-Filter-Schalter dieser Playlist:
        explizites genre_filter (True/False) sticht den globalen GENRE_FILTER
        aus; fehlt es (None), gilt der globale Default.
        """
        return self.genre_filter if self.genre_filter is not None else GENRE_FILTER

    def __repr__(self) -> str:
        feeds = ", ".join(repr(f) for f in self.feeds)
        return f"ChartSource({self.title!r} [{feeds}] -> {self.name})"


# Standard: ZWEI Playlists.
#   1) charts.m3u      = globale Chart (auf Top 100 begrenzt) + Schweiz-Chart (alle).
#      So bekommt man die weltweiten Top-Hits ohne den globalen Longtail
#      (Platz 100-300) plus die komplette Schweizer Chart.
#   2) mundart.m3u     = kuratierte Schweizer-Mundart-Sammlung (alle), separat, damit
#      sie die Hits-Playlist nicht verwaessert.
# Die Dekaden (80er/90er usw.) laufen NICHT mehr ueber charts-sync, sondern
# ueber den Nachlade-Dienst charts-flashback (FLASHBACK FM) - er fuellt die
# Library, die Dekaden-Anzeige uebernehmen die Navidrome Smart Playlists (NSP).
# IDs:
#   chart/0     = globale Deezer-Chart
#   1313617925  = offizielle Deezer-Playlist "Top Switzerland"
#   658561775   = Deezer-Playlist "Swiss Mundart Hits"
# Feed-Schreibweisen in der CHARTS-Config:
#   Deezer:  {"type": "chart"|"playlist", "source": "<id>", "limit": ..,
#            "enabled": true}
#   Last.fm: {"type": "lastfm", "method": "<methode>", "limit": ..,
#            "enabled": true, <method-parameter: country|tag|user|period>}
#
# Der "enabled"-Schalter erlaubt es, eine Quelle stillzulegen, ohne sie zu
# loeschen -> z.B. Deezer aus, nur Last.fm importieren (oder umgekehrt).
_DEFAULT_CHARTS = [
    {
        # Aktuelle Pop-Hits. Deezer-Charts (global Top 100 + Schweiz) liefern die
        # Neuheiten; der optionale Genre-Filter (GENRE_FILTER=1) haelt
        # Country/Schlager draussen. Last.fm-Feeds sind vordefiniert, aber
        # per "enabled": false vorerst aus - einfach umschalten zum Testen.
        "sources": [
            {"type": "chart", "source": "0", "limit": 100, "enabled": True},
            {"type": "playlist", "source": "1313617925", "limit": "all", "enabled": True},
            {"type": "lastfm", "method": "chart.getTopTracks", "limit": 100,
             "enabled": False},
            {"type": "lastfm", "method": "geo.getTopTracks", "country": "Switzerland",
             "limit": 100, "enabled": False},
        ],
        "name": "charts.m3u",
        "title": "Aktuelle Hits & Charts \U0001F525\U0001F3B6",
        "missing": "missing.txt",
    },
    {
        "sources": [
            {"type": "playlist", "source": "658561775", "limit": "all", "enabled": True},
        ],
        "name": "mundart.m3u",
        "title": "Schweizer Mundart \U0001F1E8\U0001F1ED",
        "missing": "missing-mundart.txt",
    },
]

# Deezer-Feed-Typen (alles andere ausser "lastfm" wird als Deezer behandelt).
#   chart    = Genre-/Global-Chart (/chart/<id>/tracks, id 0 = global)
#   playlist = kuratierte Playlist (/playlist/<id>/tracks)
#   artist   = Top-Tracks eines Kuenstlers (/artist/<id>/top) - fuer gezielte
#              Kuenstler-Quellen (z.B. Mundart: Goelae, Polo Hofer, Trauffer).
_DEEZER_TYPES = {"chart", "playlist", "artist"}
# Nicht-Feed-Schluessel eines Last.fm-Eintrags (der Rest sind method-Parameter).
# "service" und "source" sind KEINE Last.fm-Query-Parameter: sie werden bewusst
# ignoriert statt an die API durchgereicht (ein versehentlich an einer Last.fm-
# Quelle gesetztes Deezer-"source" wuerde sonst als &source=.. mitgeschickt).
_LASTFM_META_KEYS = {"type", "method", "limit", "enabled", "service", "source"}

# Gueltige Last.fm-Methoden als Spiegelmenge zu lastfm._METHOD_PARAM. Bewusst
# KEIN Import von lastfm hier (Import-Zyklus config->lastfm->deezer->config,
# deezer.py importiert `from . import config` auf Modulebene). Ein Drift-Test in
# den charts-sync-Tests prueft die Gleichheit zu lastfm._METHOD_PARAM.
#   key = lowercase Methode, value = (pflicht-paramname|None, required)
_VALID_LASTFM_METHODS = {
    "chart.gettoptracks": (None, False),
    "geo.gettoptracks": ("country", True),
    "tag.gettoptracks": ("tag", True),
    "user.gettoptracks": ("user", True),
}


def _build_feed(s: dict, entry_limit: int, context: str,
                index: int, warnings: list[str]) -> Feed:
    """
    Baut aus einem Quell-Dict ein Feed-Objekt (Deezer oder Last.fm).

    Der effektive Dienst (feed.kind) wird aus dem optionalen Feld "service"
    bestimmt; fehlt es, gilt die abwaertskompatible type-Heuristik. Alle
    Validierungsbefunde werden an `warnings` angehaengt (das ruft _parse_charts
    danach gebuendelt ins Log) - es wird NICHT abgebrochen.

    context: Playlist-Name/-Titel fuer sprechende Meldungen.
    index:   1-basierte Quellen-Nummer innerhalb der Playlist.
    """
    service = str(s.get("service", "")).strip().lower()
    type_ = str(s.get("type", "")).strip().lower()
    method_raw = str(s.get("method", "")).strip().lower()
    limit = _parse_limit(s.get("limit"), entry_limit)
    enabled = bool(s.get("enabled", True))

    # 1) Effektiven Dienst bestimmen.
    if service == "lastfm":
        kind = "lastfm"
        if type_ == "chart" or type_ == "playlist":
            # Harmlos, aber verwirrend: Deezer-type an einer Last.fm-Quelle.
            warnings.append(
                f"Playlist '{context}', Quelle #{index}: service=lastfm, aber "
                f"Deezer-type '{type_}' gesetzt - type wird ignoriert.")
    elif service == "deezer":
        kind = "deezer"
        if type_ == "lastfm":
            warnings.append(
                f"Playlist '{context}', Quelle #{index}: widersprüchlich - "
                f"service=deezer, aber type=lastfm. Behandle als Deezer.")
    elif service == "":
        # Abwaertskompatibler Fallback (unveraendertes Alt-Verhalten).
        kind = "lastfm" if type_ == "lastfm" else "deezer"
    else:
        warnings.append(
            f"Playlist '{context}', Quelle #{index}: unbekannter service "
            f"'{service}' (erlaubt: deezer, lastfm). Behandle als deezer.")
        kind = "deezer"

    # 2) Feed je nach Dienst bauen + validieren.
    if kind == "lastfm":
        method = method_raw or "chart.gettoptracks"
        if method not in _VALID_LASTFM_METHODS:
            warnings.append(
                f"Playlist '{context}', Quelle #{index}: unbekannte Last.fm-"
                f"method '{method}' (erlaubt: chart.gettoptracks, "
                f"geo.gettoptracks, tag.gettoptracks, user.gettoptracks). "
                f"Quelle deaktiviert.")
            enabled = False
        else:
            param, required = _VALID_LASTFM_METHODS[method]
            if required and not str(s.get(param, "")).strip():
                warnings.append(
                    f"Playlist '{context}', Quelle #{index}: Last.fm-method "
                    f"'{method}' braucht Parameter '{param}'. Quelle deaktiviert.")
                enabled = False
        if str(s.get("source", "")).strip():
            warnings.append(
                f"Playlist '{context}', Quelle #{index}: Last.fm-Quelle mit "
                f"Deezer-Feld 'source' - wird ignoriert.")
        # Alle nicht-Meta-Schluessel sind method-Parameter (country/tag/user/period).
        params = {k: v for k, v in s.items() if k not in _LASTFM_META_KEYS}
        return Feed(kind="lastfm", limit=limit, enabled=enabled,
                    method=method, params=params)

    # Deezer
    if type_ and type_ not in _DEEZER_TYPES:
        warnings.append(
            f"Playlist '{context}', Quelle #{index}: unbekannter Deezer-type "
            f"'{type_}' (erlaubt: chart, playlist, artist). Nutze 'chart'.")
    deezer_type = type_ if type_ in _DEEZER_TYPES else "chart"
    source = str(s.get("source", "0")).strip() or "0"
    # playlist und artist brauchen eine echte ID (leer/"0" liefert nichts).
    if deezer_type in ("playlist", "artist") and source in ("", "0"):
        warnings.append(
            f"Playlist '{context}', Quelle #{index}: Deezer-{deezer_type} ohne "
            f"gültige 'source'-ID.")
    return Feed(kind="deezer", limit=limit, enabled=enabled,
                deezer_type=deezer_type, source=source)


def _extract_feeds(entry: dict, entry_limit: int, context: str,
                   warnings: list[str]) -> list["Feed"]:
    """
    Ermittelt die Quellen eines CHARTS-Eintrags als Liste von Feed-Objekten.

    Jede Quelle kann ein eigenes "limit" ('all' oder Zahl) und "enabled" haben;
    fehlt das Limit, gilt das Eintrags-Limit (entry_limit, das seinerseits auf
    CHART_LIMIT zurueckfaellt).

    Unterstuetzt beide Schreibweisen:
      - mehrere:  "sources": [{...}, {...}]
      - einzeln:  Felder direkt am Eintrag (abwaertskompatibel)
    """
    if isinstance(entry.get("sources"), list):
        if not entry["sources"]:
            warnings.append(
                f"Playlist '{context}' hat eine leere sources-Liste.")
        return [_build_feed(s, entry_limit, context, i, warnings)
                for i, s in enumerate(entry["sources"], start=1)]
    # Einzelquelle
    return [_build_feed(entry, entry_limit, context, 1, warnings)]


def _parse_exclude_artists(value) -> set[str]:
    """
    Liest die 'exclude_artists' eines CHARTS-Eintrags. Unterstuetzt eine Liste
    (["Nemo", "Trauffer"]) ODER einen kommagetrennten String ("Nemo,Trauffer").
    Vergleich spaeter case-insensitiv, daher hier schon lowercased. Default leer.
    """
    if not value:
        return set()
    if isinstance(value, str):
        parts = value.split(",")
    elif isinstance(value, (list, tuple)):
        parts = value
    else:
        return set()
    return {str(a).strip().lower() for a in parts if str(a).strip()}


def _load_charts_data():
    """
    Liefert die rohe Playlist-Definition (Liste von Dicts) gemaess der
    Prioritaet CHARTS_FILE > CHARTS-Env > _DEFAULT_CHARTS. Kaputtes JSON auf
    einer gesetzten Stufe -> klare Fehlermeldung + Default (kein stilles
    Durchreichen auf die naechste Stufe).
    """
    # 1. Datei-Weg (wenn CHARTS_FILE gesetzt).
    if CHARTS_FILE:
        if os.path.isfile(CHARTS_FILE):
            try:
                with open(CHARTS_FILE, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                logger.info("CHARTS aus Datei geladen: %s", CHARTS_FILE)
                return data
            except json.JSONDecodeError as e:
                logger.error(
                    "CHARTS_FILE %s ist kein gültiges JSON an Position %d (%s). "
                    "Schema siehe CHARTS-SOURCES.md. Nutze die eingebauten "
                    "Default-Playlists, bis die Datei korrigiert ist.",
                    CHARTS_FILE, e.pos, e.msg,
                )
                return _DEFAULT_CHARTS
            except OSError as e:
                logger.error(
                    "CHARTS_FILE %s nicht lesbar (%s). Nutze Default-Playlists.",
                    CHARTS_FILE, e,
                )
                return _DEFAULT_CHARTS
        else:
            logger.error(
                "CHARTS_FILE gesetzt (%s), aber Datei existiert nicht. "
                "Prüfe den Pfad/Volume-Mount. Nutze bis dahin CHARTS-Env bzw. "
                "Default-Playlists.", CHARTS_FILE,
            )
            # bewusst weiter zur Env-Stufe (Datei fehlt != kaputte Datei)

    # 2. CHARTS-Env (einzeiliges JSON).
    raw = os.environ.get("CHARTS", "").strip()
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error(
                "CHARTS ist kein gültiges JSON an Position %d (%s). "
                "Beispiel/Schema siehe CHARTS-SOURCES.md. Nutze die eingebauten "
                "Default-Playlists, bis CHARTS korrigiert ist.",
                e.pos, e.msg,
            )
            return _DEFAULT_CHARTS

    # 3. Default.
    return _DEFAULT_CHARTS


def _parse_charts() -> list[ChartSource]:
    """
    Liest die Chart-Quellen. Prioritaet:
      1. CHARTS_FILE (mehrzeilige, lesbare JSON-Datei) - wenn gesetzt, existiert
         und valides JSON.
      2. CHARTS-Env (einzeiliges JSON).
      3. Eingebaute Default-Playlists.
    Bei kaputtem JSON wird NICHT auf die naechste Stufe durchgereicht, sondern
    (wie bei der Env) auf die Default-Playlists gefallen und klar geloggt - so
    merkt der Admin, dass seine Datei/Env fehlerhaft ist, statt stillschweigend
    etwas anderes geladen zu bekommen.
    """
    data = _load_charts_data()

    warnings: list[str] = []
    sources: list[ChartSource] = []
    for i, entry in enumerate(data):
        if not isinstance(entry, dict):
            warnings.append(
                f"CHARTS-Eintrag #{i} ist kein Objekt -> übersprungen.")
            continue
        name = str(entry.get("name", "")).strip()
        if not name:
            warnings.append(
                f"CHARTS-Eintrag #{i} hat kein 'name' (Playlist-Datei) "
                f"-> übersprungen.")
            continue
        context = str(entry.get("title") or name)
        try:
            entry_limit = _parse_limit(entry.get("limit"), CHART_LIMIT)
            feeds = _extract_feeds(entry, entry_limit, context, warnings)
            if not feeds:
                warnings.append(
                    f"Playlist '{context}' hat keine Quellen (weder 'sources' "
                    f"noch Einzelquelle).")
            # Pro-Playlist-Genre-Filter: nur ein echter bool ueberschreibt den
            # globalen GENRE_FILTER; fehlt das Feld oder ist es kein bool,
            # bleibt es None -> globaler Default.
            gf = entry.get("genre_filter")
            sources.append(ChartSource(
                feeds=feeds,
                name=name,
                title=entry.get("title", name),
                missing=entry.get("missing", f"missing-{i}.txt"),
                limit=entry_limit,
                exclude_artists=_parse_exclude_artists(entry.get("exclude_artists")),
                genre_filter=gf if isinstance(gf, bool) else None,
            ))
        except (KeyError, TypeError, ValueError) as e:
            warnings.append(f"CHARTS-Eintrag #{i} übersprungen: {e}")

    # Alle gesammelten Validierungsbefunde gebuendelt loggen (jeweils eine WARN).
    for w in warnings:
        logger.warning("CHARTS: %s", w)

    # Aktive Quellen NACH dem enabled=False-Downgrade zaehlen.
    active = sum(len(cs.active_feeds) for cs in sources)
    if warnings:
        logger.info(
            "CHARTS geladen: %d Playlist(s), %d aktive Quelle(n), %d Warnung(en). "
            "Details oben; Schema siehe CHARTS-SOURCES.md.",
            len(sources), active, len(warnings),
        )
    else:
        logger.info(
            "CHARTS geladen: %d Playlist(s), %d aktive Quelle(n). "
            "Schema siehe CHARTS-SOURCES.md.",
            len(sources), active,
        )
    return sources


CHARTS = _parse_charts()
