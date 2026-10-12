"""
slskd-Anbindung: Suche, Ergebnisauswahl und Download-Enqueue.

KOPIE aus charts-download (bewusst dupliziert, keine Laufzeit-Abhaengigkeit
zwischen den Diensten - wie matching.py/manifest.py). Aenderungen mit
charts-download abgleichen.

Nutzt die Library `slskd-api`. Pro Song:
  1. Suche starten und auf Abschluss warten.
  2. Ergebnisse holen und das beste passende MP3 >= MIN_BITRATE auswaehlen.
  3. Download per enqueue anstossen.
Nur MP3 >= MIN_BITRATE; andere Formate werden ignoriert.
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
        raise SlskdError(t("upgrade.slsk.no_api_key"))
    try:
        client = slskd_api.SlskdClient(
            host=config.SLSKD_HOST,
            api_key=config.SLSKD_API_KEY,
            url_base=config.SLSKD_URL_BASE,
        )
        client.application.state()
        return client
    except Exception as e:
        raise SlskdError(t("upgrade.slsk.connect_failed", error=e)) from e


_NON_MP3_EXTENSIONS = {
    "flac", "wav", "aiff", "aif", "alac", "ape", "wv", "dsf", "dff",
    "m4a", "aac", "ogg", "opus", "wma", "mp4", "m4b",
}


def _is_mp3(f: dict) -> bool:
    """True nur, wenn die Datei eindeutig eine MP3 ist (defensive Endungspruefung)."""
    import os

    name = (f.get("filename") or "").replace("\\", "/").lower()
    name_ext = os.path.splitext(name)[1].lstrip(".")
    field_ext = (f.get("extension") or "").lower().lstrip(".")
    ext = name_ext or field_ext
    if ext in _NON_MP3_EXTENSIONS:
        return False
    return ext == "mp3"


def _run_search(client, query: str) -> str | None:
    """Startet eine Suche und wartet auf Abschluss. Gibt die Such-ID zurueck."""
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
    passender MP3-Kandidaten (>= MIN_BITRATE). Bestes Ergebnis zuerst.
    """
    candidates = []
    for resp in responses or []:
        username = resp.get("username")
        has_slot = resp.get("hasFreeUploadSlot", False)
        speed = resp.get("uploadSpeed", 0)
        if config.REQUIRE_FREE_SLOT and not has_slot:
            continue
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
            bad = has_excluded_keyword(filename, config.EXCLUDE_KEYWORDS)
            if bad:
                logger.debug(t("download.slsk.rejected_keyword", keyword=bad, filename=filename))
                continue
            if looks_like_dj_edit(filename):
                logger.debug(t("download.slsk.rejected_dj_edit", filename=filename))
                continue
            if require_artist and not artist_in_path(require_artist, filename):
                logger.debug(t("download.slsk.rejected_artist_not_in_path",
                               artist=require_artist, filename=filename))
                continue
            score = match_score(query_norm, filename)
            if score < config.MATCH_THRESHOLD:
                continue
            not_compilation = 0 if is_compilation(filename) else 1
            # charts-upgrade laedt NIE Compilations als Ersatz (REJECT_COMPILATIONS
            # ist hier fest an): eine kuratierte Single/ein Album soll nicht durch
            # einen Sampler-Rip ersetzt werden.
            if config.REJECT_COMPILATIONS and not not_compilation:
                logger.debug(t("download.slsk.rejected_compilation", filename=filename))
                continue
            candidates.append((not_compilation, score, bitrate, speed, username, f))

    if not candidates:
        return []

    candidates.sort(key=lambda c: (c[0], c[1], c[2], c[3]), reverse=True)
    return [(username, f, score) for _nc, score, _br, _sp, username, f in candidates]


def _search_and_collect(client, query: str) -> list[dict]:
    """Fuehrt eine Suche aus und liefert die Antworten. Wirft SlskdError."""
    search_id = _run_search(client, query)
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
    sortierte Liste (bestes zuerst) oder []. blocked_users werden gefiltert.
    Zweistufig: Artist+Titel, sonst Titel-only mit Kuenstler-Check.
    Wirft SlskdError bei Verbindungs-/API-Fehler.
    """
    query = f"{artist} {title}".strip()
    query_norm = normalize(query)

    responses = _search_and_collect(client, query)
    ranked = _rank_candidates(responses, query_norm, blocked_users=blocked_users)
    if ranked:
        return ranked

    title_query = title.strip()
    if normalize(title_query) == query_norm:
        return []
    logger.info(t("download.slsk.title_only_fallback", title=title_query))
    responses = _search_and_collect(client, title_query)
    return _rank_candidates(responses, query_norm, require_artist=artist,
                            blocked_users=blocked_users)


def enqueue(client, username: str, file_dict: dict) -> bool:
    """Stoesst den Download an. Gibt True bei Erfolg zurueck."""
    try:
        return bool(client.transfers.enqueue(username=username, files=[file_dict]))
    except Exception as e:
        logger.error(t("download.slsk.enqueue_failed", user=username,
                       filename=file_dict.get("filename"), error=e))
        return False
