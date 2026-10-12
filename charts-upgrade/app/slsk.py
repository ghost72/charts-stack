"""
slskd integration: search, result selection and download enqueue.

COPY from charts-download (deliberately duplicated, no runtime dependency
between the services - like matching.py/manifest.py). Reconcile changes with
charts-download.

Uses the `slskd-api` library. Per song:
  1. Start a search and wait for completion.
  2. Fetch the results and pick the best matching MP3 >= MIN_BITRATE.
  3. Trigger the download via enqueue.
Only MP3 >= MIN_BITRATE; other formats are ignored.
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
    """Error communicating with slskd."""


def connect() -> "slskd_api.SlskdClient":
    """Builds the slskd client. Raises SlskdError on a missing key/host."""
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
    """True only if the file is unambiguously an MP3 (defensive extension check)."""
    import os

    name = (f.get("filename") or "").replace("\\", "/").lower()
    name_ext = os.path.splitext(name)[1].lstrip(".")
    field_ext = (f.get("extension") or "").lower().lstrip(".")
    ext = name_ext or field_ext
    if ext in _NON_MP3_EXTENSIONS:
        return False
    return ext == "mp3"


def _run_search(client, query: str) -> str | None:
    """Starts a search and waits for completion. Returns the search ID."""
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
    Scores all search responses and returns a quality-sorted list of matching
    MP3 candidates (>= MIN_BITRATE). Best result first.
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
            # charts-upgrade NEVER loads compilations as a replacement
            # (REJECT_COMPILATIONS is hard-wired on here): a curated single/album
            # must not be replaced by a sampler rip.
            if config.REJECT_COMPILATIONS and not not_compilation:
                logger.debug(t("download.slsk.rejected_compilation", filename=filename))
                continue
            candidates.append((not_compilation, score, bitrate, speed, username, f))

    if not candidates:
        return []

    candidates.sort(key=lambda c: (c[0], c[1], c[2], c[3]), reverse=True)
    return [(username, f, score) for _nc, score, _br, _sp, username, f in candidates]


def _search_and_collect(client, query: str) -> list[dict]:
    """Runs a search and returns the responses. Raises SlskdError."""
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
    Searches the song and returns ALL matching hits as a quality-sorted list
    (best first) or []. blocked_users are filtered out. Two-stage:
    artist+title, otherwise title-only with an artist check.
    Raises SlskdError on a connection/API error.
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
    """Triggers the download. Returns True on success."""
    try:
        return bool(client.transfers.enqueue(username=username, files=[file_dict]))
    except Exception as e:
        logger.error(t("download.slsk.enqueue_failed", user=username,
                       filename=file_dict.get("filename"), error=e))
        return False
