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
import secrets

from fastapi import FastAPI, Form, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
import os

from . import blocklist, config, i18n, library, manifest, slsk, status
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

    # 3. Trigger download (new addition OR real upgrade). Retry across multiple
    #    providers: Soulseek is P2P - the best hit may come from a peer that is
    #    offline, rejects the upload, or (worse) ACCEPTS the request but never
    #    sends a byte ("Queued, Remotely" forever). So go through the candidates
    #    one by one, and for each one that accepts the enqueue, VERIFY that the
    #    transfer actually starts delivering. A provider that does not deliver
    #    is cancelled and remembered on the blocklist (skipped on future
    #    requests), then the next candidate is tried - so the song reaches a
    #    peer that really sends it.
    blocked = blocklist.blocked_users()
    chosen = None  # (username, file_dict, bitrate)
    for cand_user, cand_file, _score in candidates:
        # Skip providers known to be non-delivering (within their block TTL).
        if cand_user in blocked:
            logger.info(t("songrequest.request.provider_skipped_blocked",
                          user=cand_user))
            continue
        cand_bitrate = cand_file.get("bitRate", 0) or 0
        # On an upgrade only take candidates that are really better than the
        # existing version (otherwise it would be no upgrade). effective as above.
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
            chosen = (cand_user, cand_file, cand_bitrate)
            break
        # Not delivering (stalled/failed): cancel it, remember the provider as
        # problematic, and move on to the next candidate.
        logger.info(t("songrequest.request.provider_not_delivering",
                      user=cand_user, outcome=outcome))
        slsk.cancel_transfer(client, cand_user, cand_fname)
        blocklist.block(cand_user)

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
    canon_artist = library.canonical_artist(config.MUSIC_DIR, artist) or artist
    if is_upgrade:
        canon_title = library.canonical_title_from_file(existing) or library.title_case(title)
    else:
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


@app.get("/favicon.ico")
async def favicon():
    # Browsers request /favicon.ico hard (some ignore <link rel=icon>).
    return RedirectResponse(url="/static/icon.svg", status_code=307)


@app.get("/health")
async def health():
    return {"status": "ok"}


app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
