"""
charts-songrequest – web UI + API for manually requesting songs.

Flow per request (artist + title):
  1. Search in slskd (same logic/filters as charts-download:
     MP3 >= MIN_BITRATE, no remixes/live/compilations).
  2. Trigger the download of the best result via the slskd API.
  3. Write a clean chart-metadata entry into the MANIFEST (source="manuell").
  4. charts-import automatically pulls the finished file into the library on
     its next run (tags, cover, Lidarr, Navidrome) - no further action needed.

Simple password protection (one password from the .env) via a session cookie
so the externally reachable page (reverse proxy) is not open.
"""

import json
import logging
import os
import secrets
import shutil
import tempfile
import time

from fastapi import FastAPI, Form, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import (blocklist, config, deezer, i18n, library, manifest, quality,
               slsk, status, youtube)
from .i18n import t

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("charts-songrequest")

if not config.APP_USERS:
    raise RuntimeError(t("songrequest.err.no_users"))
logger.info(t("songrequest.login.active",
              count=len(config.APP_USERS),
              users=", ".join(sorted(config.APP_USERS))))

_SESSION_SECRET = config.SESSION_SECRET or secrets.token_hex(32)
# Valid sessions in memory: token -> user name (log in again after a restart).
_sessions: dict[str, str] = {}

# --- Manual MP3 upload staging --------------------------------------------
# Pending sub-threshold uploads awaiting a "add it anyway" confirm:
#   token -> {"path": staged file, "bitrate": int, "user": name, "created": ts}
# In-memory like _sessions: a restart drops pending confirmations (the user
# simply re-uploads), and the dict is the authority (a stale token fails safe).
_pending_uploads: dict[str, dict] = {}
# Staging lives in the container's OWN /tmp, deliberately NOT under the shared
# /charts mount: an in-progress upload must never be visible to charts-import's
# recursive os.walk until it is promoted together with its manifest entry.
_STAGING_DIR = os.path.join(tempfile.gettempdir(), "songrequest-uploads")
# Hard cap for a single upload (MB). A 320 kbps ~10 min track stays well below.
_MAX_UPLOAD_MB = 40
# Staged files older than this (seconds) are swept on the next upload.
_UPLOAD_TTL_SECONDS = 3600

# Status/dashboard: this service is event-driven (no scheduler). We count
# served requests and write a status after each one (heartbeat + cumulative
# counter) so the dashboard shows "last active / X requests".
status.init("charts-songrequest")
status.heartbeat(t("songrequest.status.ready"))
_request_count = 0


def _record_request(label: str) -> None:
    """Writes a status entry after a served request."""
    global _request_count
    _request_count += 1
    try:
        status.finish("ok", t("songrequest.status.last", label=label), {"requests": 1})
    except Exception:
        pass

app = FastAPI(title="charts-songrequest", docs_url=None, redoc_url=None)

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

# --- Frontend i18n handoff -------------------------------------------------
# The browser receives a SUBSET of the central lang table (the songrequest.ui.*
# keys), re-based to short keys, injected into static/index.html as
# `const I18N = {...}`. Mirrors the charts-dashboard mechanism.
_UI_PREFIX = "songrequest.ui."


def _ui_strings() -> dict[str, str]:
    # i18n.ui_table(prefix) returns {FULL_KEY: value} for every key starting
    # with the prefix (English fallback already merged in). Re-base here so the
    # client calls t("subtitle"), not the full dotted path. The guard
    # (len(k) > len(_UI_PREFIX)) skips any stray prefix-only key defensively.
    return {k[len(_UI_PREFIX):]: v
            for k, v in i18n.ui_table(_UI_PREFIX).items()
            if len(k) > len(_UI_PREFIX)}


def _current_user(request: Request) -> str | None:
    """User name of the current session, or None if not logged in."""
    token = request.cookies.get("session")
    if not token:
        return None
    return _sessions.get(token)


def _is_authed(request: Request) -> bool:
    return _current_user(request) is not None


def _check_login(username: str, password: str) -> bool:
    """Checks user+password against the configured map (constant-time)."""
    expected = config.APP_USERS.get(username)
    if expected is None:
        # Still do a comparison to dampen timing differences.
        secrets.compare_digest(password, password)
        return False
    return secrets.compare_digest(password, expected)


def _download_best(client, candidates, existing, existing_bitrate):
    """
    Try the ranked candidates one by one and return the first that really
    delivers, as (username, file_dict, bitrate), or None if none did.

    Soulseek is P2P - the best hit may come from a peer that is offline,
    rejects the upload, or (worse) ACCEPTS the request but never sends a byte
    ("Queued, Remotely" forever). So go through the candidates one by one, and
    for each one that accepts the enqueue, VERIFY that the transfer actually
    starts delivering. A provider that does not deliver is cancelled and
    remembered on the blocklist (skipped on future requests), then the next
    candidate is tried - so the song reaches a peer that really sends it.

    On an upgrade (existing song present) only candidates that are strictly
    better than the existing version are considered.
    """
    blocked = blocklist.blocked_users()
    for cand_user, cand_file, _score in candidates:
        # Skip providers known to be non-delivering (within their block TTL).
        if cand_user in blocked:
            logger.info(t("songrequest.request.provider_skipped_blocked",
                          user=cand_user))
            continue
        cand_bitrate = cand_file.get("bitRate", 0) or 0
        # On an upgrade only take candidates that are really better than the
        # existing version (otherwise it would be no upgrade). effective as in
        # the caller: a missing bitrate counts as "at least MIN_BITRATE".
        cand_effective = cand_bitrate if cand_bitrate > 0 else config.MIN_BITRATE
        if existing and cand_effective <= existing_bitrate:
            continue
        if not slsk.enqueue(client, cand_user, cand_file):
            logger.info(t("download.song.provider_no_deliver", user=cand_user))
            continue
        # Enqueue accepted - but did the peer actually start sending? Verify.
        cand_fname = cand_file.get("filename", "")
        outcome = slsk.verify_transfer(
            client, cand_user, cand_fname,
            config.VERIFY_SECONDS, config.VERIFY_POLL_SECONDS)
        if outcome == "delivering":
            return (cand_user, cand_file, cand_bitrate)
        # Not delivering (stalled/failed): cancel it, remember the provider as
        # problematic, and move on to the next candidate.
        logger.info(t("songrequest.request.provider_not_delivering",
                      user=cand_user, outcome=outcome))
        slsk.cancel_transfer(client, cand_user, cand_fname)
        blocklist.block(cand_user)
    return None


def _canonicalize_manual(artist: str, title: str) -> tuple[str, str]:
    """
    Resolve the canonical (catalog) spelling of a MANUAL entry via Deezer, with
    the raw user values as the total fallback. This is the single chokepoint all
    three tabs route the STORED name through, so a lowercase 'mash'/'ewigi liäbi'
    is filed as 'Mash'/'Ewigi Liäbi' instead of the raw lowercase input - and a
    deliberate spelling ('SK.music', 'k.d. lang') survives a Deezer miss intact
    (NO naive .title()).

    Best-effort: deezer.resolve_canonical already returns the raw values on a
    miss/offline/network error, and the extra try/except guarantees a Deezer
    hiccup (or an import/runtime error) never breaks the write. Mirrors
    charts-import's tags._canonicalize guard. MATCH key is never derived from
    the canonical name - callers run the library-first check on the RAW input
    BEFORE calling this.
    """
    try:
        return deezer.resolve_canonical(artist, title)
    except Exception as e:  # runtime error never fatal -> raw fallback
        logger.debug(t("import.deezer.lookup_failed", artist=artist,
                       title=title, error=e))
        return artist, title


# --- Login ---

@app.get("/login", response_class=HTMLResponse)
async def login_page(error: str = ""):
    msg = ('<div class="status err"><span class="ic">⚠️</span><span>'
           + t("songrequest.ui.login_failed") + '</span></div>') if error else ""
    return f"""<!DOCTYPE html><html lang="{i18n.active_lang()}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{t("songrequest.ui.login_doc_title")}</title>
<link rel="icon" type="image/svg+xml" href="/static/icon.svg">
<link rel="apple-touch-icon" href="/static/icon-180.png">
<link rel="manifest" href="/static/manifest.json">
<meta name="theme-color" content="#7b4397">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="{t("songrequest.ui.apple_title")}">
<link rel="stylesheet" href="/static/style.css"></head>
<body><div class="card"><div class="logo">🎵</div><h1>{t("songrequest.ui.heading")}</h1>
<div class="subtitle">{t("songrequest.ui.login_subtitle")}</div>
<form method="post" action="/login">
<label for="usr">{t("songrequest.ui.login_user")}</label>
<input id="usr" name="username" type="text" autofocus placeholder="{t("songrequest.ui.login_user_ph")}"
       autocomplete="username">
<label for="pw">{t("songrequest.ui.login_password")}</label>
<input id="pw" name="password" type="password" placeholder="{t("songrequest.ui.login_password")}"
       autocomplete="current-password">
<button type="submit">{t("songrequest.ui.login_submit")}</button>
</form>{msg}</div></body></html>"""


@app.post("/login")
async def login(response: Response, username: str = Form(""), password: str = Form("")):
    username = (username or "").strip()
    if _check_login(username, password):
        token = secrets.token_urlsafe(32)
        _sessions[token] = username
        logger.info(t("songrequest.login.success", user=username))
        resp = RedirectResponse(url="/", status_code=303)
        resp.set_cookie("session", token, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30)
        return resp
    logger.warning(t("songrequest.login.failed", user=username))
    return RedirectResponse(url="/login?error=1", status_code=303)


@app.get("/logout")
async def logout(request: Request):
    token = request.cookies.get("session")
    if token:
        _sessions.pop(token, None)
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie("session")
    return resp


# --- Main page ---

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if not _is_authed(request):
        return RedirectResponse(url="/login", status_code=303)
    with open(os.path.join(_STATIC_DIR, "index.html"), encoding="utf-8") as fh:
        html = (fh.read()
                .replace("{{LANG}}", i18n.active_lang())
                .replace("{{I18N}}", json.dumps(_ui_strings(), ensure_ascii=True)))
    return HTMLResponse(html)


# --- API: request a song ---

@app.post("/api/request")
async def api_request(request: Request, artist: str = Form(""), title: str = Form("")):
    user = _current_user(request)
    if user is None:
        return JSONResponse(status_code=401,
                            content={"ok": False, "error": t("songrequest.ui.not_authed")})

    artist = (artist or "").strip()
    title = (title or "").strip()
    if not artist or not title:
        return JSONResponse(status_code=400,
                            content={"ok": False, "error": t("songrequest.ui.missing_fields")})

    logger.info(t("songrequest.request.received", user=user, artist=artist, title=title))

    # 0. Determine the existing version (if any) + its bitrate.
    existing = library.find_existing(config.MUSIC_DIR, artist, title)
    existing_bitrate = library.read_bitrate(existing) if existing else 0
    if existing:
        logger.info(t("songrequest.request.already_in_library",
                      bitrate=existing_bitrate,
                      path=os.path.relpath(existing, config.MUSIC_DIR)))

    # 1. Connect + search (always, to know the best available quality).
    try:
        client = slsk.connect()
    except slsk.SlskdError as e:
        logger.error(t("songrequest.request.slskd_unreachable", error=e))
        return JSONResponse(status_code=502,
                            content={"ok": False, "error": t("songrequest.ui.slskd_unreachable")})

    try:
        candidates = slsk.find_candidates(client, artist, title)
    except slsk.SlskdError as e:
        logger.warning(t("songrequest.request.search_failed", error=e))
        return JSONResponse(status_code=502,
                            content={"ok": False, "error": t("songrequest.ui.search_failed")})

    # The best hit determines the quality comparison + metadata; the remaining
    # candidates are fallbacks for the retry in case the best provider
    # (Soulseek peer) does not deliver the download.
    result = candidates[0] if candidates else None

    if not result:
        # Nothing found. If the song is already there, that's ok - otherwise error.
        if existing:
            return JSONResponse(content={
                "ok": True, "already": True, "artist": artist, "title": title,
                "bitrate": existing_bitrate,
                "message": t("songrequest.ui.already_no_better", bitrate=existing_bitrate),
            })
        logger.info(t("songrequest.request.no_match", artist=artist, title=title))
        return JSONResponse(content={
            "ok": False,
            "error": t("songrequest.ui.no_match", min=config.MIN_BITRATE),
        })

    # Quality comparison based on the BEST hit (the candidate list is sorted by
    # quality). The candidate actually downloaded is determined later in the
    # retry loop.
    _best_user, best_file, _best_score = result
    new_bitrate = best_file.get("bitRate", 0) or 0

    # 2. Quality comparison: is the song already there AND the hit NOT better?
    #    Then download nothing. IMPORTANT: slskd does not always report the
    #    bitrate in the search result (then new_bitrate=0). But since the hit
    #    passed the MIN_BITRATE filter, it is really >= MIN_BITRATE. We
    #    therefore treat a missing value as "at least MIN_BITRATE" for the
    #    comparison.
    effective_new = new_bitrate if new_bitrate > 0 else config.MIN_BITRATE
    if existing and effective_new <= existing_bitrate:
        logger.info(t("songrequest.request.existing_equal_or_better",
                      existing=existing_bitrate, new=new_bitrate, effective=effective_new))
        # Only say "best quality" if the existing one is really >= MIN_BITRATE.
        # Otherwise be honest: there is (currently) nothing better on the net.
        if existing_bitrate >= config.MIN_BITRATE:
            msg = t("songrequest.ui.good_quality", bitrate=existing_bitrate)
        else:
            msg = t("songrequest.ui.nothing_better",
                    bitrate=existing_bitrate, min=config.MIN_BITRATE)
        return JSONResponse(content={
            "ok": True, "already": True, "artist": artist, "title": title,
            "bitrate": existing_bitrate,
            "message": msg,
        })

    # 3. Trigger download (new addition OR real upgrade). See _download_best for
    #    the retry-across-providers logic.
    chosen = _download_best(client, candidates, existing, existing_bitrate)

    if not chosen:
        logger.warning(t("songrequest.request.no_provider_delivered",
                         artist=artist, title=title))
        return JSONResponse(status_code=502, content={
            "ok": False,
            "error": t("songrequest.ui.no_provider"),
        })

    username, file_dict, new_bitrate = chosen
    fname = file_dict.get("filename", "?")

    # 4. Determine the canonical spelling (instead of the possibly lowercased
    #    input). Artist: the basename of the already existing artist folder
    #    (e.g. 'rod stewart' -> 'Rod Stewart'), otherwise the input. Title: on
    #    an upgrade the existing file spelling, otherwise title-case of the
    #    input. These values go into the manifest, UI response and Pushover; the
    #    slskd search above deliberately ran with the ORIGINAL input (the
    #    matching normalizes anyway, changed search terms could worsen the hit
    #    rate).
    is_upgrade = bool(existing)
    if is_upgrade:
        # Upgrade: anchor to the EXISTING file spelling (correct), falling back
        # to title-case of the input. Deezer is not consulted here.
        canon_artist = library.canonical_artist(config.MUSIC_DIR, artist) or artist
        canon_title = library.canonical_title_from_file(existing) or library.title_case(title)
    else:
        # New song: prefer the Deezer catalog spelling (raw fallback), then
        # refine the artist to the EXISTING folder spelling if present. Only
        # when Deezer returned the raw title unchanged do we fall back to the
        # conservative title_case (title only - never forced on the artist).
        canon_artist, canon_title = _canonicalize_manual(artist, title)
        canon_artist = library.canonical_artist(config.MUSIC_DIR, artist) or canon_artist
        if canon_title == title:
            canon_title = library.title_case(title)

    # 5. Manifest entry. On an upgrade (song already there, new bitrate higher)
    #    set the replace flag -> charts-import replaces the existing file.
    #    notify_device = Pushover device of the logged-in user (only if listed
    #    in USER_PUSHOVER_DEVICES). If NOT listed, it stays empty -> charts-import
    #    then sends NO availability message (never to everyone).
    notify_device = config.USER_PUSHOVER_DEVICES.get(user, "")
    manifest.record(fname, canon_artist, canon_title, config.MANUAL_SOURCE_LABEL,
                    replace=is_upgrade, notify_device=notify_device)
    logger.info(t("songrequest.request.enqueued",
                  upgrade_marker=" [UPGRADE/replace]" if is_upgrade else "",
                  artist=canon_artist, title=canon_title, bitrate=new_bitrate,
                  user=username,
                  notify_marker=f" [notify: {notify_device}]" if notify_device else ""))

    _record_request(f"{canon_artist} - {canon_title}")

    # slskd does not always report the bitrate -> then "≥ MIN_BITRATE" instead of "0 kbps".
    new_br_label = f"{new_bitrate} kbps" if new_bitrate > 0 else f"≥ {config.MIN_BITRATE} kbps"
    if is_upgrade:
        msg = t("songrequest.ui.upgrade_started",
                new_label=new_br_label, existing=existing_bitrate)
    else:
        msg = t("songrequest.ui.download_started")
    return JSONResponse(content={
        "ok": True,
        "upgrade": is_upgrade,
        "artist": canon_artist,
        "title": canon_title,
        "bitrate": new_bitrate,
        "old_bitrate": existing_bitrate,
        "message": msg,
    })


# --- API: manual MP3 upload ------------------------------------------------
#
# A logged-in user uploads an MP3 named "<artist> - <title>.mp3". The server
# reads its bitrate and routes it through the SAME manifest -> charts-import
# pipeline every other song uses (so tags/cover/ReplayGain/year/Lidarr are not
# re-implemented here):
#   - tag bitrate >= MIN_BITRATE AND not upsampled (real FFT cutoff ok) ->
#     accept straight away (drop into /charts + manifest source="upload", no
#     slskd search).
#   - tag bitrate < MIN_BITRATE OR upsampled (a low real cutoff despite a high
#     tag bitrate) -> search slskd for something better. If a better copy
#     delivers, download THAT instead (normal upgrade/new). If nothing better is
#     found, offer "add it anyway" (confirm round-trip) with an honest message.
#     For a confirmed upsampled upload the manifest entry carries
#     accept_anyway=true so charts-import skips ONLY its upsampling rejection.


def _upload_upsampled(staged_path: str, tag_bitrate: int):
    """
    Real-quality (FFT cutoff) check for an uploaded MP3. Returns
    (is_upsampled, measured_cutoff_khz_or_None, expected_cutoff_khz). Honours the
    shared REJECT_UPSAMPLED* / CUTOFF_TOLERANCE_KHZ knobs; conservative: not
    measurable or check disabled -> (False, None, expected). is_upsampled itself
    short-circuits to not-upsampled when the tag bitrate is below
    REJECT_UPSAMPLED_MIN_BITRATE, so a sub-256 file is never flagged here and
    correctly continues down the below-threshold branch.
    """
    if not config.REJECT_UPSAMPLED:
        return False, None, 0.0
    return quality.is_upsampled(
        staged_path, tag_bitrate,
        tolerance_khz=config.CUTOFF_TOLERANCE_KHZ,
        min_check_bitrate=config.REJECT_UPSAMPLED_MIN_BITRATE,
    )


def _sweep_staging(now: float) -> None:
    """Remove pending uploads (dict + staged file) older than the TTL."""
    for token in [t_ for t_, e in _pending_uploads.items()
                  if now - e.get("created", 0) > _UPLOAD_TTL_SECONDS]:
        entry = _pending_uploads.pop(token, None)
        if entry:
            try:
                os.remove(entry["path"])
            except OSError:
                pass


def _stage_upload(upload: "UploadFile") -> tuple[str, str]:
    """
    Stream the uploaded file to the container-local staging dir in chunks,
    enforcing the size cap. Returns (token, staged_path). Raises ValueError
    with "too_large" if the cap is exceeded (partial file removed).
    """
    os.makedirs(_STAGING_DIR, exist_ok=True)
    token = secrets.token_urlsafe(16)
    staged = os.path.join(_STAGING_DIR, f"{token}.mp3")
    cap = _MAX_UPLOAD_MB * 1024 * 1024
    written = 0
    try:
        with open(staged, "wb") as fh:
            while True:
                chunk = upload.file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > cap:
                    fh.close()
                    os.remove(staged)
                    raise ValueError("too_large")
                fh.write(chunk)
    except ValueError:
        raise
    except OSError:
        try:
            os.remove(staged)
        except OSError:
            pass
        raise
    return token, staged


def _promote_upload(staged_path: str, artist: str, title: str,
                    is_upgrade: bool, notify_device: str,
                    accept_anyway: bool = False,
                    source: str = "upload") -> str:
    """
    Move a validated staged file into the /charts drop folder under a
    collision-free "<artist> - <title>.mp3" basename and write the manifest
    entry. Returns the final basename. source labels the origin in the manifest
    (default "upload"; "youtube" for the YouTube tab). accept_anyway=True is
    passed through to the manifest only for a confirmed upsampled upload / a
    YouTube download (tells charts-import to skip ONLY its upsampling rejection
    for this file).

    This is the single promotion chokepoint for both the upload and the YouTube
    path, so it is also where the "manifest artist is never empty" invariant
    lives.
    """
    charts_dir = os.path.dirname(config.MANIFEST_FILE)
    os.makedirs(charts_dir, exist_ok=True)
    # Canonicalize the STORED name via Deezer (raw fallback). The library-first
    # find_existing check already ran on the RAW input in the callers, so this
    # only affects the on-disk basename + the manifest spelling, never the match
    # key. A lowercase 'mash'/'ewigi liäbi' is filed as the catalog spelling; a
    # deliberate 'SK.music' survives a Deezer miss unchanged.
    canon_artist, canon_title = _canonicalize_manual(artist, title)
    clean_artist = library._clean_component(canon_artist, "Unknown Artist")
    clean_title = library._clean_component(canon_title, "untitled")
    base = f"{clean_artist} - {clean_title}"
    final = os.path.join(charts_dir, f"{base}.mp3")
    n = 2
    while os.path.exists(final):
        final = os.path.join(charts_dir, f"{base} ({n}).mp3")
        n += 1
    shutil.move(staged_path, final)
    fname = os.path.basename(final)
    # Record the artist floored to "Unknown Artist" when empty (same value used
    # for the on-disk basename). charts-import SKIPS an entry whose manifest
    # artist is empty (it does NOT floor it), so writing the floor here is what
    # keeps the file from being filed as "no manifest entry". The title is the
    # canonical (Deezer-resolved) spelling to match the on-disk basename.
    manifest.record(fname, clean_artist, canon_title, source=source,
                    replace=is_upgrade, notify_device=notify_device,
                    accept_anyway=accept_anyway)
    return fname


@app.post("/api/upload")
async def api_upload(request: Request, file: UploadFile = None,
                     artist: str = Form(""), title: str = Form("")):
    user = _current_user(request)
    if user is None:
        return JSONResponse(status_code=401,
                            content={"ok": False, "error": t("songrequest.ui.not_authed")})

    artist = (artist or "").strip()
    title = (title or "").strip()
    name = (file.filename if file is not None else "") or ""

    # Title is required (it drives the manifest key / tagging). Artist may be
    # empty (the no-separator parse case); the user is nudged to fill it.
    if file is None or not title:
        return JSONResponse(status_code=400,
                            content={"ok": False, "error": t("songrequest.ui.missing_fields")})

    # 1. Extension gate (cheapest): must look like an MP3.
    if not name.lower().endswith(".mp3"):
        logger.info(t("songrequest.request.upload_not_mp3", name=name))
        return JSONResponse(status_code=415,
                            content={"ok": False, "error": t("songrequest.ui.upload_not_mp3")})

    # Sweep any stale staged files before adding a new one (event-driven, no
    # scheduler).
    _sweep_staging(time.time())

    # 2. Stream to staging with the size cap.
    try:
        token, staged = _stage_upload(file)
    except ValueError:
        logger.info(t("songrequest.request.upload_too_large",
                      name=name, max=_MAX_UPLOAD_MB))
        return JSONResponse(status_code=413, content={
            "ok": False, "error": t("songrequest.ui.upload_too_large", max=_MAX_UPLOAD_MB)})
    except OSError as e:
        logger.error(t("songrequest.request.upload_stage_failed", error=e))
        return JSONResponse(status_code=500,
                            content={"ok": False, "error": t("songrequest.ui.search_failed")})

    # 3. Content gate: must really be an MP3 (mutagen), else reject.
    measured = library.read_mp3_bitrate(staged)
    if measured is None:
        try:
            os.remove(staged)
        except OSError:
            pass
        logger.info(t("songrequest.request.upload_not_mp3", name=name))
        return JSONResponse(status_code=415,
                            content={"ok": False, "error": t("songrequest.ui.upload_not_mp3")})

    logger.info(t("songrequest.request.upload_received",
                  user=user, artist=artist, title=title, bitrate=measured))

    existing = library.find_existing(config.MUSIC_DIR, artist, title)
    existing_bitrate = library.read_bitrate(existing) if existing else 0
    notify_device = config.USER_PUSHOVER_DEVICES.get(user, "")

    # Real-quality check (FFT cutoff) on the staged file, computed BEFORE the
    # library-first reject because an upsampled file can never count as a genuine
    # upgrade over a decent existing copy (regardless of its high tag bitrate).
    # An upsampled file (low real cutoff despite a high tag bitrate) must NOT be
    # accepted on the tag bitrate alone - it takes the same slskd-search ->
    # confirm route as a sub-threshold upload.
    upsampled, cutoff_khz, expected_khz = _upload_upsampled(staged, measured)

    # Library-first reject (authoritative, BEFORE the quality/upsampling branch):
    # if the song is already present at DECENT quality (existing >= MIN_BITRATE),
    # keep the upload ONLY when it is a genuine upgrade = not upsampled AND
    # strictly higher tag bitrate than the existing copy. An upsampled upload is
    # NEVER a valid upgrade over a decent existing copy, no matter what its tag
    # bitrate claims (the live SK.music bug: a fake-320 upsample beating an
    # honest 257). When the existing copy is POOR (< MIN_BITRATE) this always-
    # reject does NOT fire, so a non-upsampled, strictly-better upload can still
    # improve a poor copy. New songs (existing is None) are unaffected.
    if (existing and existing_bitrate >= config.MIN_BITRATE
            and (upsampled or measured <= existing_bitrate)):
        # Already present and not a genuine upgrade -> discard the staged upload
        # and report honestly (same shape as the request flow's "good quality" /
        # "nothing better" case; renderUpload handles it).
        try:
            os.remove(staged)
        except OSError:
            pass
        logger.info(t("songrequest.request.existing_equal_or_better",
                      existing=existing_bitrate, new=measured, effective=measured))
        if existing_bitrate >= config.MIN_BITRATE:
            msg = t("songrequest.ui.good_quality", bitrate=existing_bitrate)
        else:
            msg = t("songrequest.ui.nothing_better",
                    bitrate=existing_bitrate, min=config.MIN_BITRATE)
        return JSONResponse(content={
            "ok": True, "already": True, "artist": artist, "title": title,
            "bitrate": existing_bitrate, "message": msg})

    # 4a. Genuinely good (good tag bitrate AND not upsampled) -> accept. The
    #     already-present reject ran above, so reaching here means the file is
    #     NEW or genuinely better than the existing copy.
    if measured >= config.MIN_BITRATE and not upsampled:
        fname = _promote_upload(staged, artist, title,
                                is_upgrade=bool(existing), notify_device=notify_device)
        logger.info(t("songrequest.request.upload_accepted",
                      artist=artist, title=title, bitrate=measured))
        _record_request(f"{artist} - {title}")
        return JSONResponse(content={
            "ok": True, "accepted": True, "artist": artist, "title": title,
            "bitrate": measured,
            "message": t("songrequest.ui.upload_accepted", bitrate=measured)})

    # 4b. Below threshold OR upsampled -> search slskd for something better.
    #     Same slskd search + _download_best logic for both reasons; the reason
    #     only differs for the honest confirm message and the accept_anyway flag.
    reason = "upsampled" if upsampled else "below_min"
    if upsampled:
        logger.info(t("songrequest.request.upload_upsampled",
                      bitrate=measured,
                      cutoff=f"{cutoff_khz:.0f}" if cutoff_khz is not None else "?",
                      expected=f"{expected_khz:.0f}"))
    else:
        logger.info(t("songrequest.request.upload_sub_threshold",
                      bitrate=measured, min=config.MIN_BITRATE))
    try:
        client = slsk.connect()
    except slsk.SlskdError as e:
        try:
            os.remove(staged)
        except OSError:
            pass
        logger.error(t("songrequest.request.slskd_unreachable", error=e))
        return JSONResponse(status_code=502,
                            content={"ok": False, "error": t("songrequest.ui.slskd_unreachable")})
    try:
        candidates = slsk.find_candidates(client, artist, title)
    except slsk.SlskdError as e:
        try:
            os.remove(staged)
        except OSError:
            pass
        logger.warning(t("songrequest.request.search_failed", error=e))
        return JSONResponse(status_code=502,
                            content={"ok": False, "error": t("songrequest.ui.search_failed")})

    chosen = _download_best(client, candidates, existing, existing_bitrate)
    if chosen:
        # A better copy delivers -> download THAT, discard the uploaded file.
        # Behaves exactly like a normal request upgrade/new.
        try:
            os.remove(staged)
        except OSError:
            pass
        username, file_dict, new_bitrate = chosen
        fname = file_dict.get("filename", "?")
        is_upgrade = bool(existing)
        if is_upgrade:
            # Upgrade: anchor to the EXISTING file spelling (correct).
            canon_artist = library.canonical_artist(config.MUSIC_DIR, artist) or artist
            canon_title = library.canonical_title_from_file(existing) or library.title_case(title)
        else:
            # New song: prefer the Deezer catalog spelling (raw fallback), then
            # refine the artist to an existing folder spelling; title_case only
            # as a last resort when Deezer returned the raw title unchanged.
            canon_artist, canon_title = _canonicalize_manual(artist, title)
            canon_artist = library.canonical_artist(config.MUSIC_DIR, artist) or canon_artist
            if canon_title == title:
                canon_title = library.title_case(title)
        manifest.record(fname, canon_artist, canon_title, config.MANUAL_SOURCE_LABEL,
                        replace=is_upgrade, notify_device=notify_device)
        logger.info(t("songrequest.request.enqueued",
                      upgrade_marker=" [UPGRADE/replace]" if is_upgrade else "",
                      artist=canon_artist, title=canon_title, bitrate=new_bitrate,
                      user=username,
                      notify_marker=f" [notify: {notify_device}]" if notify_device else ""))
        _record_request(f"{canon_artist} - {canon_title}")
        new_br_label = f"{new_bitrate} kbps" if new_bitrate > 0 else f"≥ {config.MIN_BITRATE} kbps"
        if is_upgrade:
            msg = t("songrequest.ui.upgrade_started",
                    new_label=new_br_label, existing=existing_bitrate)
        else:
            msg = t("songrequest.ui.download_started")
        return JSONResponse(content={
            "ok": True, "upgrade": is_upgrade,
            "artist": canon_artist, "title": canon_title,
            "bitrate": new_bitrate, "old_bitrate": existing_bitrate,
            "message": msg})

    # 4c. Nothing better online -> keep the staged file and ask the user to
    #     confirm "add it anyway". Carry the reason (+ measured/expected cutoff)
    #     so the confirm path knows whether to set accept_anyway and the message
    #     can be honest about the real quality.
    _pending_uploads[token] = {
        "path": staged, "bitrate": measured, "user": user,
        "created": time.time(), "reason": reason,
        "cutoff": cutoff_khz, "expected": expected_khz}
    logger.info(t("songrequest.request.upload_confirm_pending", bitrate=measured))
    if reason == "upsampled":
        msg = t("songrequest.ui.upload_upsampled_prompt",
                bitrate=measured,
                cutoff=f"{cutoff_khz:.0f}" if cutoff_khz is not None else "?",
                expected=f"{expected_khz:.0f}")
    else:
        msg = t("songrequest.ui.upload_confirm_prompt",
                bitrate=measured, min=config.MIN_BITRATE)
    return JSONResponse(content={
        "ok": True, "needs_confirm": True, "token": token,
        "artist": artist, "title": title, "bitrate": measured,
        "message": msg})


@app.post("/api/upload/confirm")
async def api_upload_confirm(request: Request, token: str = Form(""),
                             artist: str = Form(""), title: str = Form("")):
    user = _current_user(request)
    if user is None:
        return JSONResponse(status_code=401,
                            content={"ok": False, "error": t("songrequest.ui.not_authed")})

    artist = (artist or "").strip()
    title = (title or "").strip()

    entry = _pending_uploads.get(token)
    # Token must exist AND belong to the current user (fails safe after a
    # restart, when the dict is empty). Confirm does NOT re-run the slskd
    # search even if artist/title were edited here - it only files the bytes.
    if entry is None or entry.get("user") != user:
        logger.info(t("songrequest.request.upload_confirm_expired", token=token))
        return JSONResponse(status_code=410,
                            content={"ok": False, "error": t("songrequest.ui.upload_confirm_expired")})

    if not title:
        return JSONResponse(status_code=400,
                            content={"ok": False, "error": t("songrequest.ui.missing_fields")})

    _pending_uploads.pop(token, None)
    measured = entry.get("bitrate", 0)
    existing = library.find_existing(config.MUSIC_DIR, artist, title)
    existing_bitrate = library.read_bitrate(existing) if existing else 0
    notify_device = config.USER_PUSHOVER_DEVICES.get(user, "")
    # The pending entry carries whether the staged file is upsampled (the only
    # reason that would otherwise set accept_anyway below). An upsampled upload
    # is never a genuine upgrade over a decent existing copy.
    upsampled = entry.get("reason") == "upsampled"

    # Library-first reject (same rule as api_upload): if a DECENT copy
    # (existing >= MIN_BITRATE) is present, reject the confirm as already-present
    # UNLESS the upload is a genuine upgrade = not upsampled AND strictly higher
    # bitrate. This also closes the "add it anyway" route for a song already
    # present in decent quality (an upsampled confirm can no longer slip through
    # via accept_anyway). The guard also covers the race where the song became
    # present while this confirm token was held. A genuinely absent song
    # (existing is None) still confirms and files as before; a POOR existing copy
    # (< MIN_BITRATE) does not trigger the always-reject.
    if (existing and existing_bitrate >= config.MIN_BITRATE
            and (upsampled or measured <= existing_bitrate)):
        try:
            os.remove(entry["path"])
        except OSError:
            pass
        logger.info(t("songrequest.request.existing_equal_or_better",
                      existing=existing_bitrate, new=measured, effective=measured))
        if existing_bitrate >= config.MIN_BITRATE:
            msg = t("songrequest.ui.good_quality", bitrate=existing_bitrate)
        else:
            msg = t("songrequest.ui.nothing_better",
                    bitrate=existing_bitrate, min=config.MIN_BITRATE)
        return JSONResponse(content={
            "ok": True, "already": True, "artist": artist, "title": title,
            "bitrate": existing_bitrate, "message": msg})

    # Only a confirmed UPSAMPLED upload carries accept_anyway (defensive default:
    # anything other than "upsampled" -> False, so a sub-256 confirm files with
    # no flag, exactly as before).
    accept_anyway = upsampled
    _promote_upload(entry["path"], artist, title,
                    is_upgrade=bool(existing), notify_device=notify_device,
                    accept_anyway=accept_anyway)
    logger.info(t("songrequest.request.upload_accepted",
                  artist=artist, title=title, bitrate=measured))
    _record_request(f"{artist} - {title}")
    return JSONResponse(content={
        "ok": True, "accepted": True, "artist": artist, "title": title,
        "bitrate": measured,
        "message": t("songrequest.ui.upload_accepted", bitrate=measured)})


# --- API: YouTube URL import -----------------------------------------------
#
# A logged-in user pastes a single-video YouTube URL. /api/youtube/fetch reads
# the video metadata and returns a cleaned artist/title suggestion to pre-fill
# the editable fields. /api/youtube downloads bestaudio, extracts to MP3 with
# the thumbnail embedded as the cover, checks the library first, and (if the
# song is absent or below MIN_BITRATE) files it through the SAME manifest ->
# charts-import pipeline every other source uses: source="youtube" and
# accept_anyway=True so charts-import skips ONLY its upsampling rejection (the
# YouTube copy is accepted deliberately; keyword/unknown-artist/too-short
# rejections still apply). No confirm round-trip. All yt-dlp usage lives in
# app/youtube.py; the blocking download runs in a threadpool so it never blocks
# the single-worker event loop.


@app.post("/api/youtube/fetch")
async def api_youtube_fetch(request: Request, url: str = Form("")):
    user = _current_user(request)
    if user is None:
        return JSONResponse(status_code=401,
                            content={"ok": False, "error": t("songrequest.ui.not_authed")})

    url = (url or "").strip()
    if not url or not youtube.is_valid_url(url):
        return JSONResponse(status_code=400,
                            content={"ok": False, "error": t("songrequest.ui.yt_bad_url")})

    try:
        info = await run_in_threadpool(youtube.fetch_metadata, url)
    except Exception as e:
        # Network, extractor change, unavailable video: recoverable - the user
        # can still type artist/title and submit (the download re-resolves).
        logger.warning(t("songrequest.request.yt_fetch_failed", error=e))
        return JSONResponse(status_code=502,
                            content={"ok": False, "error": t("songrequest.ui.yt_fetch_failed")})

    artist, title = youtube.suggest_artist_title(info)
    logger.info(t("songrequest.request.yt_fetched", artist=artist, title=title))
    return JSONResponse(content={"ok": True, "artist": artist, "title": title})


@app.post("/api/youtube")
async def api_youtube(request: Request, url: str = Form(""),
                      artist: str = Form(""), title: str = Form("")):
    user = _current_user(request)
    if user is None:
        return JSONResponse(status_code=401,
                            content={"ok": False, "error": t("songrequest.ui.not_authed")})

    url = (url or "").strip()
    artist = (artist or "").strip()
    title = (title or "").strip()

    if not url or not youtube.is_valid_url(url):
        return JSONResponse(status_code=400,
                            content={"ok": False, "error": t("songrequest.ui.yt_bad_url")})
    # Title drives the manifest key / tagging and is required. Artist may be
    # empty here (floored to "Unknown Artist" at record time).
    if not title:
        return JSONResponse(status_code=400,
                            content={"ok": False, "error": t("songrequest.ui.missing_fields")})

    logger.info(t("songrequest.request.yt_received",
                  user=user, artist=artist, title=title))

    # Library-first (mirror the upload flow's branch-4a intent): if the song is
    # already present at good quality (>= MIN_BITRATE) there is no point
    # downloading. A YouTube download's real bitrate is unknown before the fact,
    # so the rule is: existing >= MIN_BITRATE -> skip; below MIN_BITRATE ->
    # download with is_upgrade=True and let charts-import's quality_key make the
    # final replace decision.
    existing = library.find_existing(config.MUSIC_DIR, artist, title)
    existing_bitrate = library.read_bitrate(existing) if existing else 0
    if existing and existing_bitrate >= config.MIN_BITRATE:
        logger.info(t("songrequest.request.existing_equal_or_better",
                      existing=existing_bitrate, new=0, effective=existing_bitrate))
        return JSONResponse(content={
            "ok": True, "already": True, "artist": artist, "title": title,
            "bitrate": existing_bitrate,
            "message": t("songrequest.ui.good_quality", bitrate=existing_bitrate)})

    notify_device = config.USER_PUSHOVER_DEVICES.get(user, "")

    # Sweep stale staged files before adding a new one (event-driven, no
    # scheduler), same as the upload endpoint.
    _sweep_staging(time.time())

    # Download bestaudio -> MP3 (+ embedded thumbnail) in a threadpool so the
    # blocking yt-dlp call does not stall the event loop.
    try:
        staged = await run_in_threadpool(youtube.download_audio, url, _STAGING_DIR)
    except OSError as e:
        logger.error(t("songrequest.request.yt_download_failed", error=e))
        return JSONResponse(status_code=500,
                            content={"ok": False, "error": t("songrequest.ui.search_failed")})
    except Exception as e:
        # yt_dlp.utils.DownloadError (unavailable/too long/live/no audio) and
        # anything else from the downloader: recoverable, localized error.
        logger.warning(t("songrequest.request.yt_download_failed", error=e))
        return JSONResponse(status_code=502,
                            content={"ok": False, "error": t("songrequest.ui.yt_download_failed")})

    # File it: source="youtube", accept_anyway=True (deliberate quality accept).
    fname = _promote_upload(staged, artist, title,
                            is_upgrade=bool(existing), notify_device=notify_device,
                            source="youtube", accept_anyway=True)
    logger.info(t("songrequest.request.yt_accepted",
                  artist=artist, title=title,
                  notify_marker=f" [notify: {notify_device}]" if notify_device else ""))
    _record_request(f"{artist} - {title}")
    return JSONResponse(content={
        "ok": True, "accepted": True, "artist": artist, "title": title,
        "message": t("songrequest.ui.yt_accepted")})


@app.get("/favicon.ico")
async def favicon():
    # Browsers request /favicon.ico hard (some ignore <link rel=icon>).
    return RedirectResponse(url="/static/icon.svg", status_code=307)


@app.get("/health")
async def health():
    return {"status": "ok"}


app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
