"""
charts-songrequest – Web-UI + API zum manuellen Anfordern von Songs.

Ablauf pro Request (Artist + Titel):
  1. In slskd suchen (dieselbe Logik/Filter wie charts-download:
     MP3 >= MIN_BITRATE, keine Remixe/Live/Compilations).
  2. Bestes Ergebnis per slskd-API zum Download anstossen.
  3. Sauberen Chart-Metadaten-Eintrag ins MANIFEST schreiben (source="manuell").
  4. charts-import holt die fertige Datei beim naechsten Lauf automatisch in die
     Library (Tags, Cover, Lidarr, Navidrome) - kein weiterer Handgriff noetig.

Einfacher Passwortschutz (ein Passwort aus der .env) via Session-Cookie, damit
die von aussen (Reverse Proxy) erreichbare Seite nicht offen ist.
"""

import json
import logging
import secrets

from fastapi import FastAPI, Form, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
import os

from . import config, i18n, library, manifest, slsk, status
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
# Gueltige Sessions im Speicher: token -> benutzername (nach Neustart neu
# einloggen).
_sessions: dict[str, str] = {}

# Status/Dashboard: dieser Dienst ist ereignisgesteuert (kein Scheduler). Wir
# zaehlen bediente Requests und schreiben nach jedem einen Status (heartbeat +
# kumulativer Zaehler), damit das Dashboard "zuletzt aktiv / X Requests" zeigt.
status.init("charts-songrequest")
status.heartbeat(t("songrequest.status.ready"))
_request_count = 0


def _record_request(label: str) -> None:
    """Schreibt einen Status-Eintrag nach einem bedienten Request."""
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
    """Benutzername der aktuellen Session, oder None wenn nicht angemeldet."""
    token = request.cookies.get("session")
    if not token:
        return None
    return _sessions.get(token)


def _is_authed(request: Request) -> bool:
    return _current_user(request) is not None


def _check_login(username: str, password: str) -> bool:
    """Prueft Benutzer+Passwort gegen die konfigurierte Map (zeitkonstant)."""
    expected = config.APP_USERS.get(username)
    if expected is None:
        # Trotzdem einen Vergleich machen, um Timing-Unterschiede zu daempfen.
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


# --- Hauptseite ---

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if not _is_authed(request):
        return RedirectResponse(url="/login", status_code=303)
    with open(os.path.join(_STATIC_DIR, "index.html"), encoding="utf-8") as fh:
        html = (fh.read()
                .replace("{{LANG}}", i18n.active_lang())
                .replace("{{I18N}}", json.dumps(_ui_strings(), ensure_ascii=True)))
    return HTMLResponse(html)


# --- API: Song anfordern ---

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

    # 0. Vorhandene Version (falls da) + deren Bitrate ermitteln.
    existing = library.find_existing(config.MUSIC_DIR, artist, title)
    existing_bitrate = library.read_bitrate(existing) if existing else 0
    if existing:
        logger.info(t("songrequest.request.already_in_library",
                      bitrate=existing_bitrate,
                      path=os.path.relpath(existing, config.MUSIC_DIR)))

    # 1. Verbindung + Suche (immer, um die beste verfuegbare Qualitaet zu kennen).
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

    # Bester Treffer bestimmt Qualitaetsvergleich + Metadaten; die restlichen
    # Kandidaten sind Fallbacks fuer den Retry, falls der beste Anbieter
    # (Soulseek-Peer) den Download nicht liefert.
    result = candidates[0] if candidates else None

    if not result:
        # Nichts gefunden. Wenn der Song schon da ist, ist das ok - sonst Fehler.
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

    # Qualitaetsvergleich anhand des BESTEN Treffers (die Kandidatenliste ist
    # nach Qualitaet sortiert). Der tatsaechlich geladene Kandidat wird spaeter
    # in der Retry-Schleife bestimmt.
    _best_user, best_file, _best_score = result
    new_bitrate = best_file.get("bitRate", 0) or 0

    # 2. Qualitaetsvergleich: Ist der Song schon da UND der Treffer NICHT besser?
    #    Dann nichts laden. WICHTIG: slskd meldet die Bitrate im Suchergebnis
    #    nicht immer (dann new_bitrate=0). Da der Treffer aber den MIN_BITRATE-
    #    Filter passiert hat, ist er real >= MIN_BITRATE. Wir behandeln einen
    #    fehlenden Wert daher als "mindestens MIN_BITRATE" fuer den Vergleich.
    effective_new = new_bitrate if new_bitrate > 0 else config.MIN_BITRATE
    if existing and effective_new <= existing_bitrate:
        logger.info(t("songrequest.request.existing_equal_or_better",
                      existing=existing_bitrate, new=new_bitrate, effective=effective_new))
        # Nur "beste Qualitaet" sagen, wenn die vorhandene wirklich >= MIN_BITRATE
        # ist. Sonst ehrlich: es gibt (aktuell) nichts Besseres im Netz.
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

    # 3. Download anstossen (Neuzugang ODER echtes Upgrade). Retry ueber
    #    mehrere Anbieter: Soulseek ist P2P - der beste Treffer kann von einem
    #    Peer stammen, der gerade offline ist / den Upload ablehnt. Darum die
    #    Kandidaten der Reihe nach abhaken, bis ein enqueue klappt.
    chosen = None  # (username, file_dict, bitrate)
    for cand_user, cand_file, _score in candidates:
        cand_bitrate = cand_file.get("bitRate", 0) or 0
        # Beim Upgrade nur Kandidaten nehmen, die auch wirklich besser sind als
        # die vorhandene Version (sonst waere es kein Upgrade). effective wie oben.
        cand_effective = cand_bitrate if cand_bitrate > 0 else config.MIN_BITRATE
        if existing and cand_effective <= existing_bitrate:
            continue
        if slsk.enqueue(client, cand_user, cand_file):
            chosen = (cand_user, cand_file, cand_bitrate)
            break
        logger.info(t("download.song.provider_no_deliver", user=cand_user))

    if not chosen:
        logger.warning(t("songrequest.request.no_provider_delivered",
                         artist=artist, title=title))
        return JSONResponse(status_code=502, content={
            "ok": False,
            "error": t("songrequest.ui.no_provider"),
        })

    username, file_dict, new_bitrate = chosen
    fname = file_dict.get("filename", "?")

    # 4. Kanonische Schreibweise bestimmen (statt der evtl. klein getippten
    #    Eingabe). Kuenstler: der Basename des bereits vorhandenen Kuenstler-
    #    Ordners (z.B. 'rod stewart' -> 'Rod Stewart'), sonst Eingabe. Titel:
    #    beim Upgrade die vorhandene Datei-Schreibweise, sonst Title-Case der
    #    Eingabe. Diese Werte landen in Manifest, UI-Antwort und Pushover; die
    #    slskd-Suche oben lief bewusst mit der ORIGINAL-Eingabe (das Matching
    #    normalisiert ohnehin, geaenderte Suchbegriffe koennten die Trefferquote
    #    verschlechtern).
    is_upgrade = bool(existing)
    canon_artist = library.canonical_artist(config.MUSIC_DIR, artist) or artist
    if is_upgrade:
        canon_title = library.canonical_title_from_file(existing) or library.title_case(title)
    else:
        canon_title = library.title_case(title)

    # 5. Manifest-Eintrag. Bei einem Upgrade (Song schon da, neue Bitrate hoeher)
    #    das replace-Flag setzen -> charts-import ersetzt die vorhandene Datei.
    #    notify_device = Pushover-Geraet des eingeloggten Users (nur wenn er in
    #    USER_PUSHOVER_DEVICES gelistet ist). Steht er NICHT drin, bleibt es leer
    #    -> charts-import schickt dann KEINE Verfuegbarkeits-Meldung (nie an alle).
    notify_device = config.USER_PUSHOVER_DEVICES.get(user, "")
    manifest.record(fname, canon_artist, canon_title, config.MANUAL_SOURCE_LABEL,
                    replace=is_upgrade, notify_device=notify_device)
    logger.info(t("songrequest.request.enqueued",
                  upgrade_marker=" [UPGRADE/replace]" if is_upgrade else "",
                  artist=canon_artist, title=canon_title, bitrate=new_bitrate,
                  user=username,
                  notify_marker=f" [notify: {notify_device}]" if notify_device else ""))

    _record_request(f"{canon_artist} - {canon_title}")

    # slskd meldet die Bitrate nicht immer -> dann "≥ MIN_BITRATE" statt "0 kbps".
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
    # Browser fragen /favicon.ico hart an (ignorieren teils <link rel=icon>).
    return RedirectResponse(url="/static/icon.svg", status_code=307)


@app.get("/health")
async def health():
    return {"status": "ok"}


app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
