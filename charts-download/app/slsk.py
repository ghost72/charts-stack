"""
slskd-Anbindung: Suche, Ergebnisauswahl und Download-Enqueue.

Nutzt die Library `slskd-api` (https://pypi.org/project/slskd-api/), die die
slskd-REST-API kapselt. Pro fehlendem Song:
  1. Suche starten (search_text) und auf Abschluss warten.
  2. Ergebnisse holen (search_responses) und das beste passende MP3 >= MIN_BITRATE
     auswaehlen (Matching + freier Slot + Geschwindigkeit).
  3. Download per enqueue anstossen (im Dry-Run nur zurueckliefern, nichts laden).

Die Auswahl beruecksichtigt bewusst NUR MP3 >= MIN_BITRATE (Standard 256).
Andere Formate (FLAC, m4a, ...) werden ignoriert.
"""

import logging
import time
import uuid

import slskd_api

from . import config
from .i18n import t
from .matching import (
    artist_in_path, has_excluded_keyword, is_compilation, looks_like_dj_edit,
    match_score, normalize,
)

logger = logging.getLogger(__name__)


class SlskdError(Exception):
    """Fehler bei der Kommunikation mit slskd."""


def connect() -> "slskd_api.SlskdClient":
    """Baut den slskd-Client auf. Wirft SlskdError bei fehlendem Key/Host."""
    if not config.SLSKD_API_KEY:
        raise SlskdError(t("download.slsk.no_api_key"))
    try:
        client = slskd_api.SlskdClient(
            host=config.SLSKD_HOST,
            api_key=config.SLSKD_API_KEY,
            url_base=config.SLSKD_URL_BASE,
        )
        # Statusabruf als Verbindungstest.
        client.application.state()
        return client
    except Exception as e:
        raise SlskdError(t("download.slsk.connect_failed", error=e)) from e


# Bekannte Nicht-MP3-Audioendungen, die wir NIE laden wollen (nur MP3).
_NON_MP3_EXTENSIONS = {
    "flac", "wav", "aiff", "aif", "alac", "ape", "wv", "dsf", "dff",
    "m4a", "aac", "ogg", "opus", "wma", "mp4", "m4b",
}


def _is_mp3(f: dict) -> bool:
    """
    True nur, wenn die Datei eindeutig eine MP3 ist. Defensiv:
    - Endung aus dem Dateinamen hat Vorrang (slskd setzt 'extension' nicht
      immer zuverlaessig - haeufig leer, obwohl der Name .flac enthaelt).
    - Jede bekannte Nicht-MP3-Endung wird explizit ausgeschlossen.
    - Nur wenn die tatsaechliche Endung 'mp3' ist, wird True zurueckgegeben.
    """
    import os

    name = (f.get("filename") or "").replace("\\", "/").lower()
    name_ext = os.path.splitext(name)[1].lstrip(".")
    field_ext = (f.get("extension") or "").lower().lstrip(".")

    # Tatsaechliche Endung bestimmen: Dateiname zuerst, sonst das Feld.
    ext = name_ext or field_ext

    if ext in _NON_MP3_EXTENSIONS:
        return False
    return ext == "mp3"


def _run_search(client, query: str) -> str | None:
    """
    Startet eine Suche und wartet auf Abschluss. Gibt die Such-ID zurueck.
    Wirft SlskdError bei einem Verbindungs-/API-Fehler (damit der Aufrufer
    einen echten Nicht-Fund von einer Stoerung unterscheiden kann).
    """
    search_id = str(uuid.uuid1())
    try:
        client.searches.search_text(
            searchText=query,
            id=search_id,
            searchTimeout=config.SEARCH_TIMEOUT_MS,
            minimumResponseFileCount=1,
        )
    except Exception as e:
        logger.error(t("download.slsk.search_failed", query=query, error=e))
        raise SlskdError(str(e)) from e

    # Auf Abschluss warten (state.isComplete), max. SEARCH_WAIT_SECONDS.
    deadline = time.time() + config.SEARCH_WAIT_SECONDS
    while time.time() < deadline:
        try:
            state = client.searches.state(search_id)
        except Exception:
            break
        if state and state.get("isComplete"):
            break
        time.sleep(1)
    return search_id


def _rank_candidates(responses: list[dict], query_norm: str, require_artist: str = "",
                     blocked_users: set[str] | None = None):
    """
    Bewertet alle Such-Antworten und liefert eine nach Qualitaet sortierte Liste
    passender MP3-Kandidaten (>= MIN_BITRATE).

    Rueckgabe: Liste von (username, file_dict, score), bestes Ergebnis zuerst.
    Auswahlkriterien (in dieser Reihenfolge gewichtet):
      - Datei ist MP3 und bitRate >= MIN_BITRATE
      - Matching-Score gegen "artist title" >= MATCH_THRESHOLD
      - freier Upload-Slot (falls REQUIRE_FREE_SLOT)
      - Priorisierung: Singles/Alben VOR Compilations (Compilations werden
        NICHT verworfen, nur nach hinten sortiert - so bekommt man sie noch,
        wenn es nichts anderes gibt).
      - dann: hoechster Score, dann hoechste Bitrate, dann schnellster Upload

    require_artist: Wenn gesetzt (Titel-only-Fallback), MUSS der Kuenstlername
    im Dateipfad vorkommen. Schuetzt vor Coverversionen, wenn nur nach dem Titel
    gesucht wurde.
    """
    candidates = []
    for resp in responses or []:
        username = resp.get("username")
        has_slot = resp.get("hasFreeUploadSlot", False)
        speed = resp.get("uploadSpeed", 0)
        if config.REQUIRE_FREE_SLOT and not has_slot:
            continue
        # Anbieter, der fuer DIESEN Song schon eine schlechte/upgesampelte Datei
        # geliefert hat (von charts-import vorgemerkt), ueberspringen - sonst
        # wuerde bei der Neusuche derselbe Muell wieder oben landen.
        if blocked_users and username in blocked_users:
            logger.debug(t("download.slsk.provider_blocked", user=username))
            continue
        for f in resp.get("files", []):
            if f.get("isLocked"):
                continue
            if not _is_mp3(f):
                continue
            bitrate = f.get("bitRate", 0) or 0
            if bitrate < config.MIN_BITRATE:
                continue
            filename = f.get("filename", "")
            # Unerwuenschte Versionen (Lyrics, Remix, Live, Acapella, ...) hart
            # verwerfen - wir wollen nur Originalversionen.
            bad = has_excluded_keyword(filename, config.EXCLUDE_KEYWORDS)
            if bad:
                logger.debug(t("download.slsk.rejected_keyword", keyword=bad, filename=filename))
                continue
            # DJ-Pool-Edit (endet auf BPM-Zahl)? Verwerfen, wir wollen Originale.
            if looks_like_dj_edit(filename):
                logger.debug(t("download.slsk.rejected_dj_edit", filename=filename))
                continue
            # Titel-only-Fallback: Kuenstler MUSS im Pfad stehen (Coverschutz).
            if require_artist and not artist_in_path(require_artist, filename):
                logger.debug(t("download.slsk.rejected_artist_not_in_path",
                               artist=require_artist, filename=filename))
                continue
            score = match_score(query_norm, filename)
            if score < config.MATCH_THRESHOLD:
                continue
            # 1 = Single/Album (bevorzugt), 0 = Compilation (nachrangig).
            not_compilation = 0 if is_compilation(filename) else 1
            # Bei REJECT_COMPILATIONS: Compilation-Treffer gar nicht erst als
            # Kandidat zulassen. Der Song bleibt dann "missing", bis eine echte
            # Single-/Album-Version auftaucht (keine Sampler-Tracks in der Library).
            if config.REJECT_COMPILATIONS and not not_compilation:
                logger.debug(t("download.slsk.rejected_compilation", filename=filename))
                continue
            candidates.append((not_compilation, score, bitrate, speed, username, f))

    if not candidates:
        return []

    # Reihenfolge: Nicht-Compilation zuerst, dann Score, Bitrate, Upload-Speed.
    candidates.sort(key=lambda c: (c[0], c[1], c[2], c[3]), reverse=True)
    return [(username, f, score) for _nc, score, _br, _sp, username, f in candidates]


def _pick_best(responses: list[dict], query_norm: str, require_artist: str = ""):
    """Bester Einzeltreffer (username, file_dict, score) oder None. Wrapper um
    _rank_candidates fuer Aufrufer, die nur genau einen Treffer brauchen."""
    ranked = _rank_candidates(responses, query_norm, require_artist)
    if not ranked:
        return None
    username, f, score = ranked[0]
    if is_compilation(f.get("filename", "")):
        logger.info(t("download.slsk.only_compilation"))
    return username, f, score


def _search_and_collect(client, query: str) -> list[dict]:
    """Fuehrt eine Suche aus und liefert die Antworten. Wirft SlskdError."""
    search_id = _run_search(client, query)  # kann SlskdError werfen
    if not search_id:
        return []
    try:
        return client.searches.search_responses(search_id) or []
    except Exception as e:
        logger.error(t("download.slsk.responses_failed", query=query, error=e))
        raise SlskdError(str(e)) from e
    finally:
        try:
            client.searches.delete(search_id)
        except Exception:
            pass


def find_candidates(client, artist: str, title: str,
                    blocked_users: set[str] | None = None) -> list[tuple[str, dict, float]]:
    """
    Sucht den Song und liefert ALLE passenden Treffer als nach Qualitaet
    sortierte Liste [(username, file, score), ...] (bestes zuerst) oder [].

    Zweck: Der Aufrufer kann die Kandidaten der Reihe nach zum Download
    anstossen, falls der beste Anbieter (Soulseek-Peer) gerade offline ist /
    den Upload ablehnt (Retry ueber mehrere Anbieter).

    blocked_users: Anbieter (Soulseek-User), die fuer GENAU DIESEN Song schon
    eine schlechte/upgesampelte Datei geliefert haben (von charts-import
    vorgemerkt). Werden aus den Treffern gefiltert, damit nicht derselbe Muell
    erneut geladen wird.

    Zweistufig (wie find_download):
      1. Suche mit "Artist Titel".
      2. Wenn Stufe 1 nichts bringt: Fallback NUR mit dem Titel (mit striktem
         Kuenstler-Check, damit keine Coverversion geladen wird).

    Wirft SlskdError bei einem Verbindungs-/API-Fehler.
    """
    query = f"{artist} {title}".strip()
    query_norm = normalize(query)

    # Stufe 1: Artist + Titel
    responses = _search_and_collect(client, query)
    ranked = _rank_candidates(responses, query_norm, blocked_users=blocked_users)
    if ranked:
        return ranked

    # Stufe 2: Fallback nur mit dem Titel (mit striktem Kuenstler-Check).
    title_query = title.strip()
    if normalize(title_query) == query_norm:
        return []  # Artist war leer/identisch, kein zusaetzlicher Versuch noetig
    logger.info(t("download.slsk.title_only_fallback", title=title_query))
    responses = _search_and_collect(client, title_query)
    return _rank_candidates(responses, query_norm, require_artist=artist,
                            blocked_users=blocked_users)


def find_download(client, artist: str, title: str,
                  blocked_users: set[str] | None = None):
    """
    Sucht den Song und liefert den besten Treffer als (username, file, score)
    oder None. Duenner Wrapper um find_candidates fuer Aufrufer, die nur genau
    einen Treffer brauchen.
    """
    ranked = find_candidates(client, artist, title, blocked_users=blocked_users)
    return ranked[0] if ranked else None


def enqueue(client, username: str, file_dict: dict) -> bool:
    """Stoesst den Download an. Gibt True bei Erfolg zurueck."""
    try:
        return bool(client.transfers.enqueue(username=username, files=[file_dict]))
    except Exception as e:
        logger.error(t("download.slsk.enqueue_failed", user=username,
                       filename=file_dict.get("filename"), error=e))
        return False


# slskd-Transfer-States sind zusammengesetzte Strings, z.B.:
#   "Completed, Succeeded"  -> erfolgreich
#   "Completed, Errored"    -> Fehler
#   "Completed, Aborted"    -> abgebrochen
#   "Completed, TimedOut"   -> Timeout
#   "Completed, Rejected"   -> abgelehnt
#   "Completed, Cancelled"  -> abgebrochen
# Ein Download gilt als fehlgeschlagen, wenn er abgeschlossen ist ("Completed"),
# aber NICHT "Succeeded". Laufende Downloads (z.B. "InProgress", "Queued") sind
# weder Erfolg noch Fehlschlag - die lassen wir in Ruhe weiterlaufen.
_FAILED_MARKERS = ("aborted", "errored", "cancelled", "canceled",
                   "timedout", "rejected", "failed")


def _classify_state(state_str: str) -> str:
    """
    Ordnet einen slskd-Transfer-State ein: 'succeeded', 'failed' oder 'pending'.
    """
    s = (state_str or "").lower()
    if "succeeded" in s:
        return "succeeded"
    if "completed" in s and any(m in s for m in _FAILED_MARKERS):
        return "failed"
    if any(m in s for m in _FAILED_MARKERS):
        return "failed"
    return "pending"


def _all_downloads(client) -> list[dict]:
    """Holt alle Downloads (inkl. entfernter) als flache TransferedFile-Liste."""
    files: list[dict] = []
    try:
        transfers = client.transfers.get_all_downloads(includeRemoved=False)
    except Exception as e:
        logger.error(t("download.slsk.downloads_list_failed", error=e))
        return files
    for user in transfers or []:
        username = user.get("username")
        for directory in user.get("directories", []) or []:
            for f in directory.get("files", []) or []:
                f = dict(f)
                f.setdefault("username", username)
                files.append(f)
    return files


def check_download_states(client, requested: dict) -> tuple[list[str], list[tuple[str, dict]]]:
    """
    Prueft fuer die im State vermerkten Downloads den aktuellen slskd-Status.

    Rueckgabe:
      - failed_keys: Liste der State-Schluessel, deren Download fehlgeschlagen
        ist (sollen aus dem State entfernt und neu geladen werden).
      - to_cleanup: Liste (username, file_dict) der fehlgeschlagenen slskd-
        Transfers, die in slskd weggeraeumt werden sollten.
    """
    downloads = _all_downloads(client)
    if not downloads:
        return [], []

    # Index: (username, filename) -> state
    index: dict[tuple[str, str], str] = {}
    raw: dict[tuple[str, str], dict] = {}
    for f in downloads:
        key = (f.get("username"), f.get("filename"))
        index[key] = f.get("state", "")
        raw[key] = f

    failed_keys: list[str] = []
    to_cleanup: list[tuple[str, dict]] = []

    for state_key, entry in requested.items():
        if not isinstance(entry, dict):
            continue  # altes Format ohne username/filename -> nicht pruefbar
        username = entry.get("username")
        filename = entry.get("filename")
        if not username or not filename:
            continue
        dl_state = index.get((username, filename))
        if dl_state is None:
            continue  # Download (mehr) nicht bekannt -> in Ruhe lassen
        if _classify_state(dl_state) == "failed":
            logger.info(t("download.slsk.failed_detected", state=dl_state,
                          artist=entry.get("artist"), title=entry.get("title")))
            failed_keys.append(state_key)
            to_cleanup.append((username, raw[(username, filename)]))

    return failed_keys, to_cleanup


def cleanup_failed(client, to_cleanup: list[tuple[str, dict]]) -> None:
    """Entfernt fehlgeschlagene Downloads in slskd (damit die Liste sauber bleibt)."""
    for username, f in to_cleanup:
        try:
            client.transfers.cancel_download(
                username=username, id=f.get("id"), remove=True
            )
        except Exception as e:
            logger.debug(t("download.slsk.cleanup_failed", error=e))
