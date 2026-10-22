"""
charts-dashboard - status UI for the charts-stack.

Shows at a glance:
  - are all containers running? (Docker status + health)
  - are the connections healthy? (slskd logged in, Lidarr reachable, Pushover)
  - what was processed last and when? (exact run statistics from the services'
    status JSONs)
  - storage/inventory numbers (charts share, quarantine).

Password-protected (multi-user, identical mechanism to charts-songrequest).
Auto-refresh via a small JSON API (/api/status).
"""

import json
import logging
import os
import secrets

from fastapi import FastAPI, Form, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import collector, config, i18n

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("charts-dashboard")

if not config.APP_USERS:
    raise RuntimeError(i18n.t("dashboard.err.no_users"))
logger.info(i18n.t("dashboard.log.login_active",
                    n=len(config.APP_USERS),
                    users=", ".join(sorted(config.APP_USERS))))

# --- Frontend i18n handoff -------------------------------------------------
# The browser receives a SUBSET of the central lang table (the dashboard.ui.*
# keys), re-based to short keys. See dashboard-design.md Decision 1.
_UI_PREFIX = "dashboard.ui."


def _ui_strings() -> dict[str, str]:
    # i18n.ui_table(prefix) returns {FULL_KEY: value} for every key starting
    # with the prefix (English fallback already merged in). Re-base here so the
    # client calls t("updated.loading"), not the full dotted path. The guard
    # (len(k) > len(_UI_PREFIX)) skips any stray prefix-only key defensively.
    return {k[len(_UI_PREFIX):]: v
            for k, v in i18n.ui_table(_UI_PREFIX).items()
            if len(k) > len(_UI_PREFIX)}


# Discreet donate footer, substituted into {{DONATE}} when DASHBOARD_DONATE is
# on (default). The label TEXT itself is populated client-side by the static
# sweep via data-i18n-html="donate.label"; the server only decides show/hide.
# NOTE (handle change): these two URLs are intentionally kept in sync with
# .github/FUNDING.yml by hand (GitHub reads FUNDING.yml statically). If the
# PayPal/Revolut handles ever change, update BOTH places.
_DONATE_FOOTER = (
    '<footer class="site-footer">'
    '<span class="donate">'
    '<span class="donate-label" data-i18n-html="donate.label"></span>'
    '<a href="https://paypal.me/rogerwyss" target="_blank" '
    'rel="noopener noreferrer">PayPal</a>'
    '<a href="https://revolut.me/rogerwyss" target="_blank" '
    'rel="noopener noreferrer">Revolut</a>'
    '</span>'
    '</footer>'
)

app = FastAPI(title="charts-dashboard", docs_url=None, redoc_url=None)
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
           f'{i18n.t("dashboard.login.bad_credentials")}</div>'
           if error else "")
    return f"""<!DOCTYPE html><html lang="{i18n.active_lang()}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{i18n.t("dashboard.login.doc_title")}</title>
<link rel="icon" href="/static/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="/static/style.css"></head>
<body class="login"><div class="card">
<h1>{i18n.t("dashboard.login.heading")}</h1><div class="sub">{i18n.t("dashboard.login.prompt")}</div>
<form method="post" action="/login">
<label for="u">{i18n.t("dashboard.login.user")}</label>
<input id="u" name="username" type="text" autofocus autocomplete="username">
<label for="p">{i18n.t("dashboard.login.password")}</label>
<input id="p" name="password" type="password" autocomplete="current-password">
<button type="submit">{i18n.t("dashboard.login.submit")}</button>
</form>{msg}</div></body></html>"""


@app.post("/login")
async def login(username: str = Form(""), password: str = Form("")):
    username = (username or "").strip()
    if _check_login(username, password):
        token = secrets.token_urlsafe(32)
        _sessions[token] = username
        logger.info(i18n.t("dashboard.log.login_ok", user=username))
        resp = RedirectResponse(url="/", status_code=303)
        resp.set_cookie("session", token, httponly=True, samesite="lax",
                        max_age=60 * 60 * 24 * 30)
        return resp
    logger.warning(i18n.t("dashboard.log.login_failed", user=username))
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
    footer_html = _DONATE_FOOTER if config.DASHBOARD_DONATE else ""
    with open(os.path.join(_STATIC_DIR, "index.html"), encoding="utf-8") as fh:
        html = (fh.read()
                .replace("{{REFRESH}}", str(config.REFRESH_SECONDS))
                .replace("{{LANG}}", i18n.active_lang())
                .replace("{{I18N}}", json.dumps(_ui_strings(), ensure_ascii=True))
                .replace("{{DONATE}}", footer_html))
    return HTMLResponse(html)


@app.get("/api/status")
async def api_status(request: Request):
    if not _current_user(request):
        return JSONResponse(status_code=401, content={"error": i18n.t("dashboard.api.not_authed")})
    return JSONResponse(collector.collect())


@app.get("/health")
async def health():
    return {"status": "ok"}


app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
