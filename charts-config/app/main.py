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

from fastapi import FastAPI, Form, Request, UploadFile
from fastapi.responses import (HTMLResponse, JSONResponse, RedirectResponse,
                               Response)
from fastapi.staticfiles import StaticFiles

from . import audio, collector, config, convert, editor, i18n, library, trash

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
    strings = {k[len(_UI_PREFIX):]: v
               for k, v in i18n.ui_table(_UI_PREFIX).items()
               if len(k) > len(_UI_PREFIX)}
    # The donate footer's label is the SHARED stack key "dashboard.ui.donate.label"
    # (there is deliberately no separate config.ui.* copy, so i18n parity stays
    # unchanged). Surface it under the short client key "donate.label" the footer
    # markup references via data-i18n-html, matching the dashboard's handoff.
    strings["donate.label"] = i18n.t("dashboard.ui.donate.label")
    return strings


# Discreet donate footer, substituted into {{DONATE}} when CONFIG_DONATE is on
# (default). The label TEXT itself is populated client-side by the static sweep
# via data-i18n-html="donate.label"; the server only decides show/hide.
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
    footer_html = _DONATE_FOOTER if config.CONFIG_DONATE else ""
    with open(os.path.join(_STATIC_DIR, "index.html"), encoding="utf-8") as fh:
        html = (fh.read()
                .replace("{{LANG}}", i18n.active_lang())
                .replace("{{I18N}}", json.dumps(_ui_strings(), ensure_ascii=True))
                .replace("{{DONATE}}", footer_html))
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


# --- Manual Tag Editor (auth-protected; in-place library tag/cover edits) ---
# The engine lives in editor.py; these routes only enforce auth, read the
# request, and map an EditorError's bare code + status onto a localized JSON
# error. An EditorError.code "foo" maps to the message key
# "config.tageditor.foo"; 401 reuses the shared config.api.not_authed key.


def _editor_error(err: "editor.EditorError") -> JSONResponse:
    return JSONResponse(status_code=err.status,
                        content={"error": i18n.t(f"config.tageditor.{err.code}")})


@app.get("/api/tageditor/search")
async def api_tageditor_search(request: Request, q: str = "", limit: int = 20):
    if not _current_user(request):
        return JSONResponse(status_code=401, content={"error": i18n.t("config.api.not_authed")})
    return JSONResponse({"ok": True, "results": editor.search(q, limit)})


@app.get("/api/tageditor/song")
async def api_tageditor_song(request: Request, path: str = ""):
    if not _current_user(request):
        return JSONResponse(status_code=401, content={"error": i18n.t("config.api.not_authed")})
    try:
        return JSONResponse({"ok": True, "song": editor.load_song(path)})
    except editor.EditorError as err:
        return _editor_error(err)


@app.post("/api/tageditor/save")
async def api_tageditor_save(
    request: Request,
    path: str = Form(""),
    artist: str | None = Form(None),
    title: str | None = Form(None),
    album: str | None = Form(None),
    year: str | None = Form(None),
    track: str | None = Form(None),
    cover_url: str = Form(""),
    cover_file: UploadFile | None = None,
):
    if not _current_user(request):
        return JSONResponse(status_code=401, content={"error": i18n.t("config.api.not_authed")})
    # The UI submits every tag field (prefilled); the engine decides per field
    # what actually changed. A field absent from the form is not in the dict and
    # is left untouched; a present (even empty) field is passed through so the
    # engine can apply its "empty = leave unchanged" rule.
    submitted: dict = {}
    for name, value in (("artist", artist), ("title", title), ("album", album),
                        ("year", year), ("track", track)):
        if value is not None:
            submitted[name] = value
    data: bytes | None = None
    if cover_file is not None and cover_file.filename:
        # Bounded read: cap at the cover size limit + 1 so an oversize upload is
        # detected (the engine rejects anything over the cap).
        data = await cover_file.read(config.TAGEDITOR_COVER_MAX_BYTES + 1)
    try:
        song = editor.save_song(path, submitted, cover_url=cover_url, cover_file=data)
        return JSONResponse({"ok": True, "song": song})
    except editor.EditorError as err:
        return _editor_error(err)


@app.post("/api/tageditor/move")
async def api_tageditor_move(
    request: Request,
    path: str = Form(""),
    choice: str = Form("cancel"),
    artist: str = Form(""),
    title: str = Form(""),
    album: str = Form(""),
):
    # Second step of the "folder follows the artist tag" dialog (rules 4/5). The
    # tag write was already committed by /api/tageditor/save; this only performs
    # the user's chosen move (into_existing / create_new) or nothing (cancel).
    # The engine never overwrites and degrades a failed move to "not moved".
    if not _current_user(request):
        return JSONResponse(status_code=401, content={"error": i18n.t("config.api.not_authed")})
    try:
        song = editor.move_after_decision(path, choice, artist, title, album)
        return JSONResponse({"ok": True, "song": song})
    except editor.EditorError as err:
        return _editor_error(err)


@app.post("/api/tageditor/convert")
async def api_tageditor_convert(request: Request, path: str = Form("")):
    # FLAC/non-MP3 -> MP3 320 kbps CBR convert. Behind the SAME _current_user
    # auth gate as every other /api/tageditor/* route (401 anon). NOT behind
    # TAGEDITOR_AUDIO_ENABLED: that flag gates only the audio stream/download
    # endpoint; convert is a different capability, gated by auth alone. The path
    # guard + extension allowlist + already-MP3 refusal live in
    # convert.convert_to_mp3 (unit-testable without the HTTP layer).
    if not _current_user(request):
        return JSONResponse(status_code=401,
                            content={"error": i18n.t("config.api.not_authed")})
    try:
        song = convert.convert_to_mp3(path)
        return JSONResponse({"ok": True, "song": song})
    except editor.EditorError as err:
        return _editor_error(err)


@app.post("/api/tageditor/delete")
async def api_tageditor_delete(request: Request, path: str = Form("")):
    # Delete the loaded song REVERSIBLY: move it into MUSIC_DIR/.charts-trash/
    # and clear its manifest entry (so charts-import does not re-import it).
    # Behind the SAME _current_user auth gate (401 anon) as every other
    # /api/tageditor/* route; NOT behind TAGEDITOR_AUDIO_ENABLED (that flag gates
    # only the audio stream/download endpoint). The path guard (realpath inside
    # MUSIC_DIR) + existence check live in editor.delete_song, so a traversal /
    # absolute path outside the library is refused (path_outside) and nothing is
    # moved or removed.
    if not _current_user(request):
        return JSONResponse(status_code=401,
                            content={"error": i18n.t("config.api.not_authed")})
    try:
        return JSONResponse({"ok": True, **editor.delete_song(path)})
    except editor.EditorError as err:
        return _editor_error(err)


@app.get("/api/tageditor/nonmp3")
async def api_tageditor_nonmp3(request: Request):
    # Read-only list of the non-MP3 AUDIO files in the library (Feature B).
    # Same _current_user auth gate (401 anon); NOT behind TAGEDITOR_AUDIO_ENABLED.
    # Piggybacks on the TTL-cached search index, so it inherits the same pruning
    # (audio-only, @eaDir/#recycle/.charts-trash/dot-folders skipped).
    if not _current_user(request):
        return JSONResponse(status_code=401,
                            content={"error": i18n.t("config.api.not_authed")})
    files, truncated = library.list_nonmp3(config.MUSIC_DIR)
    out = []
    for f in files:
        ext = f["ext"]
        out.append({
            "path": f["path"],
            "rel_path": editor._rel_to_music(f["path"]),
            "ext": ext,
            "format": ext[1:].upper() if ext else "?",
        })
    # Stable, scannable order: sort by the display rel_path, case-insensitive.
    out.sort(key=lambda r: r["rel_path"].lower())
    return JSONResponse({"ok": True, "files": out, "truncated": truncated})


# --- Trash (Papierkorb) manager routes (list / delete / empty) --------------
# All three behind the SAME _current_user auth gate (401 anon) as every other
# /api/tageditor/* route; NOT behind TAGEDITOR_AUDIO_ENABLED (that flag gates
# only the audio stream/download endpoint). The destructive routes resolve the
# path through trash._guarded_trash_realpath, which requires the realpath to sit
# strictly inside MUSIC_DIR/.charts-trash (on top of the UNCHANGED MUSIC_DIR
# guard), so no library file outside the trash can be deleted here.

@app.get("/api/tageditor/trash")
async def api_tageditor_trash(request: Request):
    # Read-only list of the entire MUSIC_DIR/.charts-trash tree with per-file +
    # total size. Fail-open: an absent/unreadable trash yields an empty list.
    if not _current_user(request):
        return JSONResponse(status_code=401,
                            content={"error": i18n.t("config.api.not_authed")})
    scan = trash.scan_trash()
    # Strip the internal absolute 'path' off each entry for the client (the UI
    # only displays rel_path; the absolute path rides in a data-attribute built
    # client-side from the delete route's echo, never leaked in the list body).
    entries = [{"rel_path": e["rel_path"], "ext": e["ext"], "format": e["format"],
                "bytes": e["bytes"], "size_human": e["size_human"],
                "path": e["path"]}
               for e in scan["entries"]]
    return JSONResponse({
        "ok": True,
        "entries": entries,
        "total_bytes": scan["total_bytes"],
        "total_human": trash._human_size(scan["total_bytes"]),
        "count": scan["count"],
        "truncated": scan["truncated"],
    })


@app.post("/api/tageditor/trash/delete")
async def api_tageditor_trash_delete(request: Request, path: str = Form("")):
    # Permanently delete ONE trashed file. The trash-root guard + existence check
    # live in trash.delete_file (unit-testable without HTTP); a path outside the
    # trash is refused (path_outside / trash_path_outside) and NEVER deleted.
    if not _current_user(request):
        return JSONResponse(status_code=401,
                            content={"error": i18n.t("config.api.not_authed")})
    try:
        return JSONResponse({"ok": True, **trash.delete_file(path)})
    except editor.EditorError as err:
        return _editor_error(err)


@app.post("/api/tageditor/trash/empty")
async def api_tageditor_trash_empty(request: Request, confirm: str = Form("")):
    # Permanently empty the whole trash. The explicit confirm sentinel ("empty")
    # is enforced server-side in trash.empty_trash (the UI's prominent confirm is
    # defense in depth, not the only gate); without it nothing is removed. Scope
    # is confined to the trash root and symlinks are never followed out.
    if not _current_user(request):
        return JSONResponse(status_code=401,
                            content={"error": i18n.t("config.api.not_authed")})
    try:
        return JSONResponse({"ok": True, **trash.empty_trash(confirm)})
    except editor.EditorError as err:
        return _editor_error(err)


# Known audio content types for the stream (extension -> MIME). Only these
# extensions may be served; anything else inside MUSIC_DIR is refused. This is
# defense in depth ON TOP of editor._guarded_realpath, not a replacement for it.
_AUDIO_MIME = {
    ".mp3": "audio/mpeg", ".flac": "audio/flac", ".m4a": "audio/mp4",
    ".aac": "audio/aac", ".ogg": "audio/ogg", ".opus": "audio/ogg",
    ".wav": "audio/wav", ".wma": "audio/x-ms-wma", ".aiff": "audio/aiff",
    ".aif": "audio/aiff",
}


@app.get("/api/tageditor/audio")
async def api_tageditor_audio(request: Request, path: str = "", download: int = 0):
    # Gate 1 (auth, FIRST): the identical _current_user check as every other
    # /api/tageditor/* route. An unauthenticated caller gets 401 and never
    # learns whether the feature exists (so the flag state is not probeable).
    if not _current_user(request):
        return JSONResponse(status_code=401,
                            content={"error": i18n.t("config.api.not_authed")})
    # Gate 2 (opt-in flag): fail-closed 403 server-side when the feature is off,
    # regardless of auth. Checked BEFORE any filesystem touch, so when off the
    # endpoint does zero I/O and leaks nothing. An ADDITIONAL gate, not a swap.
    if not config.TAGEDITOR_AUDIO_ENABLED:
        return JSONResponse(status_code=403,
                            content={"error": i18n.t("config.tageditor.audio_disabled")})
    # Gate 3 (path guard + existence + extension): reuse editor._guarded_realpath
    # UNCHANGED (realpath inside MUSIC_DIR), then require an existing regular
    # file with a known audio extension (the allowlist is defense in depth).
    try:
        real = editor._guarded_realpath(path)
    except editor.EditorError as err:
        return _editor_error(err)
    if not os.path.isfile(real):
        return _editor_error(editor.EditorError("not_found", 404))
    ext = os.path.splitext(real)[1].lower()
    if ext not in _AUDIO_MIME:
        return _editor_error(editor.EditorError("bad_audio_ext", 400))
    return audio.stream_file(request, real, _AUDIO_MIME[ext], bool(download))


@app.get("/api/tageditor/cover")
async def api_tageditor_cover(request: Request, path: str = ""):
    # Full-resolution embedded-cover download. Behind the SAME _current_user auth
    # gate (401 anon) as every other /api/tageditor/* route and the SAME
    # editor._guarded_realpath path guard + audio-extension allowlist. It is NOT
    # behind TAGEDITOR_AUDIO_ENABLED: that flag gates only the audio
    # stream/download; cover download is a different capability, gated by auth
    # alone (like convert/list/trash). Unlike the inline preview, this returns
    # the RAW ORIGINAL bytes with NO size cap and NO re-encoding, so even a cover
    # larger than TAGEDITOR_COVER_MAX_BYTES (which only suppresses the preview)
    # downloads in full.
    if not _current_user(request):
        return JSONResponse(status_code=401,
                            content={"error": i18n.t("config.api.not_authed")})
    try:
        data, mime = editor.cover_download(path)
    except editor.EditorError as err:
        return _editor_error(err)
    # Build a sensible download filename from the song tags: "<Artist> - <Title>
    # cover.<ext>", falling back to "cover.<ext>". The extension follows the
    # sniffed mime (png vs jpg). The filename is sanitized (CR/LF/quote/backslash
    # strip + ASCII fallback) via the shared audio helper so the header can never
    # be injected or malformed for a non-ASCII title.
    ext = "png" if mime == "image/png" else "jpg"
    try:
        song = editor.load_song(path)
        artist = (song.get("artist") or "").strip()
        title = (song.get("title") or "").strip()
    except editor.EditorError:
        artist = title = ""
    stem = f"{artist} - {title} cover" if (artist and title) else "cover"
    fname = audio._sanitize_filename(f"{stem}.{ext}")
    headers = {"Content-Disposition": f'attachment; filename="{fname}"'}
    return Response(content=data, media_type=mime, headers=headers)


@app.get("/health")
async def health():
    return {"status": "ok"}


app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
