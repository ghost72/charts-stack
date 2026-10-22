"""
charts-config - snippet/config UI for the charts-stack.

Two read-only tools carved out of charts-dashboard:
  - the source finder (Deezer playlist/artist search, Deezer genre catalog,
    Last.fm tag check) that builds ready charts-sync source snippets, and
  - the Energy channel browser that builds per-channel ENERGY_CHANNELS snippets
    and shows the live active status from charts-energy.

The page never changes any config: it only shows ready-to-paste snippets plus
text hints on where to paste and what to restart. Read-only toward everything:
it talks to Deezer/Last.fm (WAN) and reads the charts-energy status JSON; it has
NO Docker access and NO restart button.

Password-protected (own CONFIG_* login + shared APP_USERS/APP_PASSWORD
fallback, identical mechanism to charts-dashboard). Data is loaded on demand,
not on a timer (no /api/status polling).
"""

import json
import logging
import os
import secrets

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import collector, config, i18n

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("charts-config")

if not config.APP_USERS:
    raise RuntimeError(i18n.t("config.err.no_users"))
logger.info(i18n.t("config.log.login_active",
                    n=len(config.APP_USERS),
                    users=", ".join(sorted(config.APP_USERS))))

# --- Frontend i18n handoff -------------------------------------------------
# The browser receives a SUBSET of the central lang table (the config.ui.*
# keys), re-based to short keys. Mirrors the dashboard's dashboard.ui.* handoff.
_UI_PREFIX = "config.ui."


def _ui_strings() -> dict[str, str]:
    # i18n.ui_table(prefix) returns {FULL_KEY: value} for every key starting
    # with the prefix (English fallback already merged in). Re-base here so the
    # client calls t("finder.col.title"), not the full dotted path. The guard
    # (len(k) > len(_UI_PREFIX)) skips any stray prefix-only key defensively.
    return {k[len(_UI_PREFIX):]: v
            for k, v in i18n.ui_table(_UI_PREFIX).items()
            if len(k) > len(_UI_PREFIX)}


app = FastAPI(title="charts-config", docs_url=None, redoc_url=None)
_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

# Sessions in memory: token -> username.
_sessions: dict[str, str] = {}


def _current_user(request: Request) -> str | None:
    token = request.cookies.get("session")
    return _sessions.get(token) if token else None


def _check_login(username: str, password: str) -> bool:
    expected = config.APP_USERS.get(username)
    if expected is None:
        secrets.compare_digest(password, password)
        return False
    return secrets.compare_digest(password, expected)


# --- Login ---

@app.get("/login", response_class=HTMLResponse)
async def login_page(error: str = ""):
    msg = (f'<div class="banner err">'
           f'{i18n.t("config.login.bad_credentials")}</div>'
           if error else "")
    return f"""<!DOCTYPE html><html lang="{i18n.active_lang()}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{i18n.t("config.login.doc_title")}</title>
<link rel="icon" href="/static/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="/static/style.css"></head>
<body class="login"><div class="card">
<h1>{i18n.t("config.login.heading")}</h1><div class="sub">{i18n.t("config.login.prompt")}</div>
<form method="post" action="/login">
<label for="u">{i18n.t("config.login.user")}</label>
<input id="u" name="username" type="text" autofocus autocomplete="username">
<label for="p">{i18n.t("config.login.password")}</label>
<input id="p" name="password" type="password" autocomplete="current-password">
<button type="submit">{i18n.t("config.login.submit")}</button>
</form>{msg}</div></body></html>"""


@app.post("/login")
async def login(username: str = Form(""), password: str = Form("")):
    username = (username or "").strip()
    if _check_login(username, password):
        token = secrets.token_urlsafe(32)
        _sessions[token] = username
        logger.info(i18n.t("config.log.login_ok", user=username))
        resp = RedirectResponse(url="/", status_code=303)
        resp.set_cookie("session", token, httponly=True, samesite="lax",
                        max_age=60 * 60 * 24 * 30)
        return resp
    logger.warning(i18n.t("config.log.login_failed", user=username))
    return RedirectResponse(url="/login?error=1", status_code=303)


@app.get("/logout")
async def logout(request: Request):
    token = request.cookies.get("session")
    if token:
        _sessions.pop(token, None)
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie("session")
    return resp


# --- Main page + API ---

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if not _current_user(request):
        return RedirectResponse(url="/login", status_code=303)
    with open(os.path.join(_STATIC_DIR, "index.html"), encoding="utf-8") as fh:
        html = (fh.read()
                .replace("{{LANG}}", i18n.active_lang())
                .replace("{{I18N}}", json.dumps(_ui_strings(), ensure_ascii=True)))
    return HTMLResponse(html)


# --- Source finder (read-only, fail-open, auth-protected) ---

@app.get("/api/sources/deezer/playlists")
async def api_sources_deezer_playlists(request: Request, q: str = ""):
    if not _current_user(request):
        return JSONResponse(status_code=401, content={"error": i18n.t("config.api.not_authed")})
    return JSONResponse(collector.search_deezer_playlists(q))


@app.get("/api/sources/deezer/artists")
async def api_sources_deezer_artists(request: Request, q: str = ""):
    if not _current_user(request):
        return JSONResponse(status_code=401, content={"error": i18n.t("config.api.not_authed")})
    return JSONResponse(collector.search_deezer_artists(q))


@app.get("/api/sources/deezer/genres")
async def api_sources_deezer_genres(request: Request):
    if not _current_user(request):
        return JSONResponse(status_code=401, content={"error": i18n.t("config.api.not_authed")})
    return JSONResponse(collector.deezer_genres())


@app.get("/api/sources/lastfm/tag")
async def api_sources_lastfm_tag(request: Request, q: str = ""):
    if not _current_user(request):
        return JSONResponse(status_code=401, content={"error": i18n.t("config.api.not_authed")})
    return JSONResponse(collector.lastfm_tag_check(q))


@app.get("/api/sources/energy/channels")
async def api_sources_energy_channels(request: Request):
    if not _current_user(request):
        return JSONResponse(status_code=401, content={"error": i18n.t("config.api.not_authed")})
    return JSONResponse(collector.energy_channels())


@app.get("/health")
async def health():
    return {"status": "ok"}


app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
