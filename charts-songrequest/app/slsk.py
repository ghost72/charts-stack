"""
slskd integration: search, result selection and download enqueue.

Uses the library `slskd-api` (https://pypi.org/project/slskd-api/), which wraps
the slskd REST API. Per missing song:
  1. Start the search (search_text) and wait for completion.
  2. Fetch results (search_responses) and pick the best matching MP3 >= MIN_BITRATE
     (matching + free slot + speed).
  3. Trigger the download via enqueue (in dry-run only return it, load nothing).

The selection deliberately considers ONLY MP3 >= MIN_BITRATE (default 256).
Other formats (FLAC, m4a, ...) are ignored.
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
    """Error while communicating with slskd."""


def connect() -> "slskd_api.SlskdClient":
    """Builds the slskd client. Raises SlskdError on missing key/host."""
    if not config.SLSKD_API_KEY:
        raise SlskdError(t("songrequest.slsk.no_api_key"))
    try:
        client = slskd_api.SlskdClient(
            host=config.SLSKD_HOST,
            api_key=config.SLSKD_API_KEY,
            url_base=config.SLSKD_URL_BASE,
        )
        # Status call as a connection test.
        client.application.state()
        return client
    except Exception as e:
        raise SlskdError(t("songrequest.slsk.connect_failed", error=e)) from e


# Known non-MP3 audio extensions that we NEVER want to download (MP3 only).
_NON_MP3_EXTENSIONS = {
    "flac", "wav", "aiff", "aif", "alac", "ape", "wv", "dsf", "dff",
    "m4a", "aac", "ogg", "opus", "wma", "mp4", "m4b",
}


def _is_mp3(f: dict) -> bool:
    """
    True only if the file is unambiguously an MP3. Defensive:
    - The extension from the file name takes precedence (slskd does not always
      set 'extension' reliably - often empty although the name contains .flac).
    - Every known non-MP3 extension is explicitly excluded.
    - Only if the actual extension is 'mp3' is True returned.
    """
    import os

    name = (f.get("filename") or "").replace("\\", "/").lower()
    name_ext = os.path.splitext(name)[1].lstrip(".")
    field_ext = (f.get("extension") or "").lower().lstrip(".")

    # Determine the actual extension: file name first, otherwise the field.
    ext = name_ext or field_ext

    if ext in _NON_MP3_EXTENSIONS:
        return False
    return ext == "mp3"


def _run_search(client, query: str) -> str | None:
    """
    Starts a search and waits for completion. Returns the search ID.
    Raises SlskdError on a connection/API error (so the caller can tell a real
    non-find apart from a malfunction).
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

    # Wait for completion (state.isComplete), max. SEARCH_WAIT_SECONDS.
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


def _rank_candidates(responses: list[dict], query_norm: str, require_artist: str = ""):
    """
    Scores all search responses and returns a quality-sorted list of matching
    MP3 candidates (>= MIN_BITRATE).

    Return: list of (username, file_dict, score), best result first.
    Selection criteria (weighted in this order):
      - file is MP3 and bitRate >= MIN_BITRATE
      - matching score against "artist title" >= MATCH_THRESHOLD
      - free upload slot (if REQUIRE_FREE_SLOT)
      - prioritization: singles/albums BEFORE compilations (compilations are
        NOT discarded, only sorted to the back - so you still get them when
        there is nothing else).
      - then: highest score, then highest bitrate, then fastest upload

    require_artist: if set (title-only fallback), the artist name MUST appear in
    the file path. Protects against cover versions when searching by title only.
    """
    candidates = []
    for resp in responses or []:
        username = resp.get("username")
        has_slot = resp.get("hasFreeUploadSlot", False)
        speed = resp.get("uploadSpeed", 0)
        if config.REQUIRE_FREE_SLOT and not has_slot:
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
            # Hard-reject unwanted versions (lyrics, remix, live, acapella, ...)
            # - we only want original versions.
            bad = has_excluded_keyword(filename, config.EXCLUDE_KEYWORDS)
            if bad:
                logger.debug(t("download.slsk.rejected_keyword", keyword=bad, filename=filename))
                continue
            # DJ-pool edit (ends in BPM number)? Discard, we want originals.
            if looks_like_dj_edit(filename):
                logger.debug(t("download.slsk.rejected_dj_edit", filename=filename))
                continue
            # Title-only fallback: artist MUST be in the path (cover protection).
            if require_artist and not artist_in_path(require_artist, filename):
                logger.debug(t("download.slsk.rejected_artist_not_in_path",
                               artist=require_artist, filename=filename))
                continue
            score = match_score(query_norm, filename)
            if score < config.MATCH_THRESHOLD:
                continue
            # 1 = single/album (preferred), 0 = compilation (lower priority).
            not_compilation = 0 if is_compilation(filename) else 1
            # With REJECT_COMPILATIONS: do not even allow a compilation hit as a
            # candidate. The song then stays "missing" until a real
            # single/album version shows up (no sampler tracks in the library).
            if config.REJECT_COMPILATIONS and not not_compilation:
                logger.debug(t("download.slsk.rejected_compilation", filename=filename))
                continue
            candidates.append((not_compilation, score, bitrate, speed, username, f))

    if not candidates:
        return []

    # Order: non-compilation first, then score, bitrate, upload speed.
    candidates.sort(key=lambda c: (c[0], c[1], c[2], c[3]), reverse=True)
    return [(username, f, score) for _nc, score, _br, _sp, username, f in candidates]


def _pick_best(responses: list[dict], query_norm: str, require_artist: str = ""):
    """Best single hit (username, file_dict, score) or None. Wrapper around
    _rank_candidates for callers that need exactly one hit."""
    ranked = _rank_candidates(responses, query_norm, require_artist)
    if not ranked:
        return None
    username, f, score = ranked[0]
    if is_compilation(f.get("filename", "")):
        logger.info(t("download.slsk.only_compilation"))
    return username, f, score


def _search_and_collect(client, query: str) -> list[dict]:
    """Runs a search and returns the responses. Raises SlskdError."""
    search_id = _run_search(client, query)  # may raise SlskdError
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


def find_candidates(client, artist: str, title: str) -> list[tuple[str, dict, float]]:
    """
    Searches the song and returns ALL matching hits as a quality-sorted list
    [(username, file, score), ...] (best first) or [].

    Purpose: the caller can trigger the candidates one after another for
    download in case the best provider (Soulseek peer) is currently offline /
    rejects the upload (retry across multiple providers).

    Two-stage (like find_download):
      1. Search with "artist title".
      2. If stage 1 yields nothing: fallback with the title ONLY (with a strict
         artist check so no cover version is downloaded).

    Raises SlskdError on a connection/API error.
    """
    query = f"{artist} {title}".strip()
    query_norm = normalize(query)

    # Stage 1: artist + title
    responses = _search_and_collect(client, query)
    ranked = _rank_candidates(responses, query_norm)
    if ranked:
        return ranked

    # Stage 2: fallback with the title only (with a strict artist check).
    title_query = title.strip()
    if normalize(title_query) == query_norm:
        return []  # artist was empty/identical, no extra attempt needed
    logger.info(t("download.slsk.title_only_fallback", title=title_query))
    responses = _search_and_collect(client, title_query)
    return _rank_candidates(responses, query_norm, require_artist=artist)


def find_download(client, artist: str, title: str):
    """
    Searches the song and returns the best hit as (username, file, score) or
    None. Thin wrapper around find_candidates for callers that need exactly one
    hit.
    """
    ranked = find_candidates(client, artist, title)
    return ranked[0] if ranked else None


def enqueue(client, username: str, file_dict: dict) -> bool:
    """Triggers the download. Returns True on success."""
    try:
        return bool(client.transfers.enqueue(username=username, files=[file_dict]))
    except Exception as e:
        logger.error(t("download.slsk.enqueue_failed", user=username,
                       filename=file_dict.get("filename"), error=e))
        return False


# slskd transfer states are composite strings, e.g.:
#   "Completed, Succeeded"  -> successful
#   "Completed, Errored"    -> error
#   "Completed, Aborted"    -> aborted
#   "Completed, TimedOut"   -> timeout
#   "Completed, Rejected"   -> rejected
#   "Completed, Cancelled"  -> cancelled
# A download counts as failed if it is completed ("Completed") but NOT
# "Succeeded". Running downloads (e.g. "InProgress", "Queued") are neither
# success nor failure - we leave them running in peace.
_FAILED_MARKERS = ("aborted", "errored", "cancelled", "canceled",
                   "timedout", "rejected", "failed")


def _classify_state(state_str: str) -> str:
    """
    Classifies a slskd transfer state: 'succeeded', 'failed' or 'pending'.
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
    """Fetches all downloads (incl. removed) as a flat TransferedFile list."""
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
    Checks the current slskd status for the downloads recorded in the state.

    Return:
      - failed_keys: list of state keys whose download failed (should be
        removed from the state and re-downloaded).
      - to_cleanup: list of (username, file_dict) of the failed slskd transfers
        that should be cleaned up in slskd.
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
            continue  # old format without username/filename -> not checkable
        username = entry.get("username")
        filename = entry.get("filename")
        if not username or not filename:
            continue
        dl_state = index.get((username, filename))
        if dl_state is None:
            continue  # download no longer known -> leave alone
        if _classify_state(dl_state) == "failed":
            logger.info(t("download.slsk.failed_detected", state=dl_state,
                          artist=entry.get("artist"), title=entry.get("title")))
            failed_keys.append(state_key)
            to_cleanup.append((username, raw[(username, filename)]))

    return failed_keys, to_cleanup


def cleanup_failed(client, to_cleanup: list[tuple[str, dict]]) -> None:
    """Removes failed downloads in slskd (so the list stays clean)."""
    for username, f in to_cleanup:
        try:
            client.transfers.cancel_download(
                username=username, id=f.get("id"), remove=True
            )
        except Exception as e:
            logger.debug(t("download.slsk.cleanup_failed", error=e))


def _find_transfer(client, username: str, filename: str) -> dict | None:
    """Return the current slskd transfer dict for (username, filename) or None."""
    for f in _all_downloads(client):
        if f.get("username") == username and f.get("filename") == filename:
            return f
    return None


def verify_transfer(client, username: str, filename: str,
                    verify_seconds: int, poll_seconds: int) -> str:
    """
    After an enqueue, check whether the provider ACTUALLY starts delivering.

    A Soulseek peer can accept a request (enqueue returns True, state
    "Queued, Remotely") yet never send a byte. We poll the transfer for up to
    verify_seconds and classify the outcome:

      - "delivering": bytes are flowing (bytesTransferred > 0) or the state is
        already InProgress / Completed+Succeeded -> the provider is good.
      - "failed":     the transfer reached a terminal failure state
        (Errored/TimedOut/Rejected/Cancelled/Aborted) -> try the next provider.
      - "stalled":    verify_seconds elapsed with 0 bytes and still only queued
        -> treat as non-delivering, try the next provider.

    verify_seconds <= 0 disables verification and returns "delivering"
    immediately (old fire-and-forget behaviour).

    Never raises: any slskd/API error during polling is treated as "delivering"
    (we do not punish a provider for our own transient read error).
    """
    if verify_seconds <= 0:
        return "delivering"
    poll = max(1, poll_seconds)
    deadline = time.time() + verify_seconds
    while True:
        try:
            tr = _find_transfer(client, username, filename)
        except Exception:
            return "delivering"  # read error on our side -> do not blame peer
        if tr is not None:
            state = tr.get("state", "")
            cls = _classify_state(state)
            if cls == "failed":
                return "failed"
            bytes_done = tr.get("bytesTransferred", 0) or 0
            s = state.lower()
            if bytes_done > 0 or "inprogress" in s or cls == "succeeded":
                return "delivering"
        if time.time() >= deadline:
            return "stalled"
        time.sleep(poll)


def cancel_transfer(client, username: str, filename: str) -> None:
    """
    Cancel + remove a (stalled) transfer in slskd so it does not sit in the
    queue forever. Looks the transfer up by (username, filename) to get its id.
    Never fatal.
    """
    try:
        tr = _find_transfer(client, username, filename)
        if tr and tr.get("id"):
            client.transfers.cancel_download(
                username=username, id=tr.get("id"), remove=True)
    except Exception as e:
        logger.debug(t("download.slsk.cleanup_failed", error=e))
