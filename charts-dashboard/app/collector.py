"""
Sammelt den Gesamtzustand des charts-stack fuer das Dashboard.

Drei Quellen:
  1. Status-JSONs (von den charts-Diensten geschrieben) -> exakte Lauf-Statistik.
  2. Docker-Engine (ueber den Socket) -> laeuft/healthy je Container.
  3. Live-Checks: slskd-Verbindung (/api/v0/server), Lidarr-Ping und die
     externen/abhaengigen Dienste (ListenBrainz/Deezer/Last.fm + iTunes +
     Pushover-validate + FLASHBACK-FM-GraphQL) via _external_status().
Plus Speicher-/Bestandszahlen aus den gemounteten Ordnern.

Alle Funktionen sind defensiv: faellt eine Quelle aus, liefert sie "unknown"
statt den ganzen Request zu sprengen.
"""

import json
import logging
import os
import re
import threading
import time

import httpx

from . import config, i18n

logger = logging.getLogger("charts-dashboard")

# Docker-SDK lazy initialisieren (faellt elegant aus, wenn Socket fehlt).
try:
    import docker  # type: ignore
    _docker_client = docker.from_env()
except Exception as e:  # pragma: no cover
    logger.warning("Docker-SDK nicht verfuegbar: %s", e)
    _docker_client = None


def _read_status_files() -> dict:
    """Liest alle <dienst>.json aus STATUS_DIR. Rueckgabe: {service: data}."""
    out: dict = {}
    d = config.STATUS_DIR
    if not os.path.isdir(d):
        return out
    for name in os.listdir(d):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(d, name), "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                out[data.get("service") or name[:-5]] = data
        except (OSError, json.JSONDecodeError):
            continue
    return out


def _container_states() -> dict:
    """
    Docker-Zustand je ueberwachtem Container.
    Rueckgabe: {name: {"state": "running"/"exited"/..., "health": "healthy"/
               "none"/..., "since": "<ISO>", "ok": bool}}
    """
    out: dict = {}
    for name in config.WATCH_CONTAINERS:
        info = {"state": "missing", "health": "none", "since": "", "ok": False}
        if _docker_client is not None:
            try:
                c = _docker_client.containers.get(name)
                st = c.attrs.get("State", {})
                info["state"] = st.get("Status", "unknown")
                health = (st.get("Health") or {}).get("Status")
                info["health"] = health or "none"
                info["since"] = st.get("StartedAt", "") or ""
                info["ok"] = (
                    info["state"] == "running"
                    and info["health"] in ("healthy", "none")
                )
            except Exception:
                # Container nicht gefunden o. a. -> missing bleibt stehen.
                pass
        out[name] = info
    return out


def _slskd_status() -> dict:
    """slskd-Verbindungsstatus + grobe Transfer-Zahlen."""
    out = {"ok": False, "state": i18n.t("dashboard.conn.slskd.state_unknown"),
           "downloads_active": None, "detail": ""}
    if not config.SLSKD_API_KEY:
        out["detail"] = i18n.t("dashboard.conn.slskd.no_api_key")
        return out
    headers = {"X-API-Key": config.SLSKD_API_KEY}
    base = config.SLSKD_HOST.rstrip("/")
    try:
        r = httpx.get(f"{base}/api/v0/server", headers=headers, timeout=8)
        r.raise_for_status()
        srv = r.json()
        out["state"] = srv.get("state", "?")
        out["ok"] = bool(srv.get("isLoggedIn"))
    except Exception as e:
        out["detail"] = i18n.t("dashboard.conn.unreachable",
                               err=e.__class__.__name__)
        return out
    # Aktive Downloads zaehlen (optional, best effort).
    try:
        r = httpx.get(f"{base}/api/v0/transfers/downloads", headers=headers, timeout=8)
        if r.status_code == 200:
            data = r.json()
            active = 0
            for user in data:
                for d in user.get("directories", []):
                    for f in d.get("files", []):
                        state = (f.get("state") or "").lower()
                        if "completed" not in state and "cancelled" not in state:
                            active += 1
            out["downloads_active"] = active
    except Exception:
        pass
    return out


def _lidarr_status() -> dict:
    """Lidarr-Erreichbarkeit (optional)."""
    out = {"configured": bool(config.LIDARR_URL and config.LIDARR_API_KEY),
           "ok": False, "detail": ""}
    if not out["configured"]:
        out["detail"] = i18n.t("dashboard.conn.lidarr.not_configured")
        return out
    try:
        url = config.LIDARR_URL.rstrip("/") + "/api/v1/system/status"
        r = httpx.get(url, headers={"X-Api-Key": config.LIDARR_API_KEY}, timeout=8)
        r.raise_for_status()
        out["ok"] = True
        # "v{version}" bleibt bewusst ein literaler Formatstring (kosmetischer
        # Versionsmarker, keine Prosa) - nicht uebersetzt.
        out["detail"] = f"v{r.json().get('version', '?')}"
    except Exception as e:
        out["detail"] = i18n.t("dashboard.conn.unreachable",
                               err=e.__class__.__name__)
    return out


# Live-Erreichbarkeit der EXTERNEN Chart-Quellen (WAN zu Drittanbietern).
# Gecacht nach dem _dir_stats-Muster (modulglobaler Cache + Zeitstempel), weil
# das Dashboard alle 15s pollt - ohne Cache wuerde jeder Refresh drei WAN-
# Requests ausloesen. TTL: config.EXTERNAL_TTL (Default 120s).
_ext_cache: dict | None = None
_ext_ts: float = 0.0


def _external_status() -> dict:
    """
    Live-Erreichbarkeit der externen/abhaengigen Dienste (ListenBrainz, Deezer,
    Last.fm, iTunes nur Info, Pushover). Nach dem _slskd_status/_lidarr_status-
    Muster: fail-open (jede Exception -> ok=False + detail, NIE nach oben
    werfen), timeout=8. Gecacht (EXTERNAL_TTL), weil das Dashboard alle 15s
    pollt - ohne Cache ginge pro Refresh u.a. ein echter Pushover-Auth-Call raus.

    Rueckgabe: {service: {"ok": bool, "detail": str}} fuer fuenf Dienste;
    lastfm + pushover zusaetzlich mit "configured": bool.
    """
    global _ext_cache, _ext_ts
    now = time.time()
    if _ext_cache is not None and (now - _ext_ts) < config.EXTERNAL_TTL:
        return _ext_cache

    # ListenBrainz: /1/validate-token OHNE Token -> HTTP 400 = erreichbar.
    # Darum KEIN raise_for_status(): ok=True, sobald ueberhaupt eine Antwort kam.
    # follow_redirects=True: ListenBrainz leitet seine Endpoints per 308 auf die
    # URL mit abschliessendem Slash um; ohne Folgen landet der Check auf dem
    # Redirect statt auf der echten validate-token-Antwort (gruen zwar, aber nur
    # weil "irgendeine Antwort kam" - mit Folgen ist der Check echt).
    lb = {"ok": False, "detail": ""}
    try:
        r = httpx.get("https://api.listenbrainz.org/1/validate-token", timeout=8,
                      follow_redirects=True)
        lb["ok"] = True
        # Kein detail bei Erfolg: die UI zeigt bereits "erreichbar" als
        # Status-Zeile. detail nur fuellen, wenn es etwas Zusaetzliches sagt.
        lb["detail"] = ""
    except Exception as e:
        lb["detail"] = i18n.t("dashboard.conn.unreachable",
                              err=e.__class__.__name__)

    # Deezer: /chart/0/tracks?limit=1 -> 200 OHNE 'error'-Feld. Deezer meldet
    # Fehler als HTTP 200 mit error-Objekt, darum beides pruefen.
    dz = {"ok": False, "detail": ""}
    try:
        r = httpx.get("https://api.deezer.com/chart/0/tracks",
                      params={"limit": 1}, timeout=8, follow_redirects=True)
        payload = r.json() if r.status_code == 200 else {}
        dz["ok"] = r.status_code == 200 and not (
            isinstance(payload, dict) and payload.get("error"))
        dz["detail"] = "" if dz["ok"] else i18n.t(
            "dashboard.conn.http_error", code=r.status_code)
    except Exception as e:
        dz["detail"] = i18n.t("dashboard.conn.unreachable",
                              err=e.__class__.__name__)

    # Last.fm: optional, nur mit API-Key (kein anonymer Ping). Ohne Key:
    # configured=False -> UI zeigt die Kachel "muted", NICHT rot.
    lf = {"configured": bool(config.LASTFM_API_KEY), "ok": False, "detail": ""}
    if lf["configured"]:
        try:
            r = httpx.get("https://ws.audioscrobbler.com/2.0/",
                          params={"method": "chart.gettoptracks", "limit": 1,
                                  "format": "json",
                                  "api_key": config.LASTFM_API_KEY},
                          timeout=8, follow_redirects=True)
            payload = r.json() if r.status_code == 200 else {}
            lf["ok"] = r.status_code == 200 and "error" not in payload
            lf["detail"] = "" if lf["ok"] else i18n.t(
                "dashboard.conn.http_error", code=r.status_code)
        except Exception as e:
            lf["detail"] = i18n.t("dashboard.conn.unreachable",
                                  err=e.__class__.__name__)
    else:
        lf["detail"] = i18n.t("dashboard.conn.lastfm.no_api_key")

    # iTunes/Apple: NUR Info, NIE an die Rot-Logik gekoppelt. 429/403 = gedros-
    # selt, nicht tot -> trotzdem ok=True (erreichbar), detail unterscheidet.
    it = {"ok": False, "detail": ""}
    try:
        r = httpx.get("https://itunes.apple.com/search",
                      params={"term": "test", "limit": 1}, timeout=8,
                      follow_redirects=True)
        if r.status_code in (429, 403):
            it["ok"] = True
            # Language-independent machine signal (like ok/configured): the
            # frontend keys the "warn" colour off this flag, NOT off a German
            # regex over the (now translated) detail text.
            it["throttled"] = True
            it["detail"] = i18n.t("dashboard.conn.itunes.throttled")
        else:
            it["ok"] = r.status_code == 200
            it["detail"] = "" if it["ok"] else i18n.t(
                "dashboard.conn.http_error", code=r.status_code)
    except Exception as e:
        it["detail"] = i18n.t("dashboard.conn.unreachable",
                              err=e.__class__.__name__)

    # Pushover: echter validate-Call (POST /1/users/validate.json). Verbraucht
    # KEIN Nachrichten-Budget, prueft aber Token+User wirklich. Nur mit beiden
    # Credentials; sonst configured=False -> UI zeigt die Kachel "muted", nicht
    # rot. Gueltig: HTTP 200 + {"status":1}; ungueltig: HTTP 400 + {"status":0}.
    # Deshalb MUSS der TTL-Cache greifen (sonst Auth-Call bei jedem 15s-Refresh).
    po = {"configured": bool(config.PUSHOVER_TOKEN and config.PUSHOVER_USER),
          "ok": False, "detail": ""}
    if po["configured"]:
        try:
            r = httpx.post("https://api.pushover.net/1/users/validate.json",
                           data={"token": config.PUSHOVER_TOKEN,
                                 "user": config.PUSHOVER_USER}, timeout=8)
            payload = r.json() if r.headers.get(
                "content-type", "").startswith("application/json") else {}
            po["ok"] = r.status_code == 200 and (
                isinstance(payload, dict) and payload.get("status") == 1)
            # detail = Klartext-Zusatzinfo, konsistent mit den anderen Checks.
            # Die Klartext-Bewertung ("Credentials gültig/ungültig") setzt die
            # UI selbst aus pu.ok – hier NICHT wiederholen, sonst steht derselbe
            # Satz doppelt in der Kachel.
            po["detail"] = (i18n.t("dashboard.conn.pushover.ok") if po["ok"]
                            else i18n.t("dashboard.conn.pushover.rejected"))
        except Exception as e:
            po["detail"] = i18n.t("dashboard.conn.unreachable",
                                  err=e.__class__.__name__)
    else:
        po["detail"] = i18n.t("dashboard.conn.pushover.not_configured")

    # FLASHBACK FM: GraphQL-Live-Check (LastPlayed persisted query). Nur mit
    # gesetztem Hash; sonst configured=False -> UI "muted". ok nur bei HTTP 200
    # OHNE GraphQL-errors-Block UND mit nicht-leerer streams-Liste. Ein toter
    # persisted-query-Hash (API neu gebaut) -> ok=False -> rote Kachel -> Signal
    # "FLASHBACK_HASH neu holen".
    fb = {"configured": bool(config.FLASHBACK_HASH), "ok": False, "detail": ""}
    if fb["configured"]:
        try:
            import json as _json
            _vars = _json.dumps({"streamName": "flashbackfm.ffm", "limitTo": 1},
                                separators=(",", ":"))
            r = httpx.get(
                "https://www.flashbackfm.ch/api/pub/gql/flashbackfm/LastPlayed/"
                + config.FLASHBACK_HASH,
                params={"variables": _vars},
                headers={"accept": "application/json"}, timeout=8,
                follow_redirects=True)
            payload = r.json() if r.status_code == 200 else {}
            has_err = isinstance(payload, dict) and payload.get("errors")
            streams = (((payload.get("data") or {}).get("audioPlayer") or {})
                       .get("streams") or []) if isinstance(payload, dict) else []
            fb["ok"] = r.status_code == 200 and not has_err and bool(streams)
            if fb["ok"]:
                # detail LEER lassen: die Kachel zeigt den Status "erreichbar"
                # schon in Zeile 1 (fb.ok). Ein zusaetzliches "erreichbar" in
                # detail wuerde die Zeile doppeln (wie bei Deezer: nur einzeilig).
                fb["detail"] = ""
            elif has_err:
                fb["detail"] = i18n.t("dashboard.conn.flashback.bad_hash")
            else:
                fb["detail"] = i18n.t("dashboard.conn.http_error",
                                      code=r.status_code)
        except Exception as e:
            fb["detail"] = i18n.t("dashboard.conn.unreachable",
                                  err=e.__class__.__name__)
    else:
        fb["detail"] = i18n.t("dashboard.conn.flashback.no_hash")

    result = {"listenbrainz": lb, "deezer": dz, "lastfm": lf, "itunes": it,
              "pushover": po, "flashback": fb}
    _ext_cache = result
    _ext_ts = now
    return result


# ---------------------------------------------------------------------------
# Quellen-Finder (read-only Helfer fuer das Dashboard-Panel "Quellen-Finder").
# Alle drei Funktionen folgen exakt dem _external_status-Muster: httpx mit
# timeout=8, jede Exception -> {"ok": False, "detail": "nicht erreichbar
# (<ExcClass>)"}, NIE nach oben werfen. Deezer/Last.fm melden Fehler als HTTP
# 200 + "error"-Feld, darum wird das Feld zusaetzlich geprueft. Die
# "snippet"-Strings sind fertige, parserkonforme CHARTS-Quell-JSONs zum
# Copy-paste in die .env (siehe CHARTS-SOURCES.md).
# ---------------------------------------------------------------------------

# Erlaubte Zeichen fuer Last.fm-Tags: Wortzeichen, Leerraum und ein paar
# ueblich vorkommende Sonderzeichen (mehrwortige Tags wie "classic country"
# oder "r&b" sind ZULAESSIG).
_TAG_RE = re.compile(r"^[\w\s&/+-]+$", re.UNICODE)


def search_deezer_playlists(q: str) -> dict:
    """
    Deezer-Playlist-Suche (read-only, fail-open, kein Cache).
    Gibt pro Treffer ein fertiges CHARTS-Quell-Snippet zum Copy-paste zurueck.
    """
    q = (q or "").strip()
    if not (1 <= len(q) <= 100):
        return {"ok": False, "detail": i18n.t("dashboard.finder.empty_query")}
    try:
        r = httpx.get("https://api.deezer.com/search/playlist",
                      params={"q": q, "limit": 25}, timeout=8,
                      follow_redirects=True)
        payload = r.json() if r.status_code == 200 else {}
        if r.status_code != 200 or (
                isinstance(payload, dict) and payload.get("error")):
            return {"ok": False, "detail": i18n.t(
                "dashboard.conn.http_error", code=r.status_code)}
        results = []
        for item in (payload.get("data") or []):
            pid = item.get("id")
            if pid is None:
                continue
            pid = str(pid)
            snippet = (
                '{"service":"deezer","type":"playlist","source":"'
                + pid + '","limit":"all","enabled":true}')
            results.append({
                "id": pid,
                "title": item.get("title", ""),
                "nb_tracks": item.get("nb_tracks", 0),
                "user": ((item.get("user") or {}).get("name") or ""),
                "snippet": snippet,
            })
        return {"ok": True, "results": results}
    except Exception as e:
        return {"ok": False, "detail": i18n.t(
            "dashboard.conn.unreachable", err=e.__class__.__name__)}


def search_deezer_artists(q: str) -> dict:
    """
    Deezer-Kuenstler-Suche (read-only, fail-open, kein Cache).
    Gibt pro Treffer ein fertiges CHARTS-Quell-Snippet (type:artist) zum
    Copy-paste zurueck. nb_fan hilft, gleichnamige Kuenstler zu unterscheiden
    (z.B. gibt es zwei "Hecht").
    """
    q = (q or "").strip()
    if not (1 <= len(q) <= 100):
        return {"ok": False, "detail": i18n.t("dashboard.finder.empty_query")}
    try:
        r = httpx.get("https://api.deezer.com/search/artist",
                      params={"q": q, "limit": 25}, timeout=8,
                      follow_redirects=True)
        payload = r.json() if r.status_code == 200 else {}
        if r.status_code != 200 or (
                isinstance(payload, dict) and payload.get("error")):
            return {"ok": False, "detail": i18n.t(
                "dashboard.conn.http_error", code=r.status_code)}
        results = []
        for item in (payload.get("data") or []):
            aid = item.get("id")
            if aid is None:
                continue
            aid = str(aid)
            # limit:50 als Zahl (nicht "all"), weil /artist/top von Deezer eh
            # bei ~100 gedeckelt ist - 50 ist ein guter Default.
            snippet = (
                '{"service":"deezer","type":"artist","source":"'
                + aid + '","limit":50,"enabled":true}')
            results.append({
                "id": aid,
                "name": item.get("name", ""),
                "nb_fan": item.get("nb_fan", 0),
                "snippet": snippet,
            })
        return {"ok": True, "results": results}
    except Exception as e:
        return {"ok": False, "detail": i18n.t(
            "dashboard.conn.unreachable", err=e.__class__.__name__)}


# Deezer-Genre-Liste: 1x/Tag gecacht (eigene TTL, selten geaendert). Modul-
# globaler Cache nach dem _ext_cache-Muster.
_genre_cache: dict | None = None
_genre_ts: float = 0.0


def deezer_genres() -> dict:
    """
    Deezer-Genre-Katalog (read-only, fail-open, 24-h-Cache).
    Rueckgabe bei Erfolg: {"ok": True, "genres": [{"id","name"}], "fetched":
    "<ISO>"}; bei Fehler: {"ok": False, "detail": ..., "genres": []} (das
    Frontend faellt dann auf die statische Liste zurueck).
    """
    global _genre_cache, _genre_ts
    now = time.time()
    if _genre_cache is not None and (
            now - _genre_ts) < config.DASHBOARD_GENRE_CACHE_TTL:
        return _genre_cache
    try:
        r = httpx.get("https://api.deezer.com/genre", timeout=8,
                      follow_redirects=True)
        payload = r.json() if r.status_code == 200 else {}
        if r.status_code != 200 or (
                isinstance(payload, dict) and payload.get("error")):
            return {"ok": False, "detail": i18n.t(
                "dashboard.conn.http_error", code=r.status_code),
                "genres": []}
        genres = []
        for item in (payload.get("data") or []):
            gid = item.get("id")
            if gid is None:
                continue
            genres.append({"id": str(gid), "name": item.get("name", "")})
        result = {"ok": True, "genres": genres,
                  "fetched": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        _genre_cache = result
        _genre_ts = now
        return result
    except Exception as e:
        return {"ok": False,
                "detail": i18n.t("dashboard.conn.unreachable",
                                 err=e.__class__.__name__),
                "genres": []}


def lastfm_tag_check(q: str) -> dict:
    """
    Last.fm-Tag-Validierung via tag.getTopTracks (read-only, fail-open).
    Ohne API-Key -> configured:false. Ein Tag gilt als brauchbar, wenn der
    Aufruf HTTP 200 ohne error-Feld und eine nicht-leere Trackliste liefert.
    Bei Erfolg kommt ein fertiges CHARTS-Quell-Snippet mit zurueck.
    """
    if not config.LASTFM_API_KEY:
        return {"ok": False, "configured": False,
                "detail": i18n.t("dashboard.finder.lastfm.no_api_key")}
    q = (q or "").strip()
    if not (1 <= len(q) <= 50) or not _TAG_RE.match(q):
        return {"ok": False, "configured": True,
                "detail": i18n.t("dashboard.finder.lastfm.invalid_tag")}
    try:
        r = httpx.get("https://ws.audioscrobbler.com/2.0/", follow_redirects=True,
                      params={"method": "tag.gettoptracks", "tag": q,
                              "api_key": config.LASTFM_API_KEY,
                              "format": "json", "limit": 5}, timeout=8)
        payload = r.json() if r.status_code == 200 else {}
        if r.status_code != 200 or not isinstance(payload, dict) or \
                "error" in payload:
            # payload.message = third-party Last.fm API text -> passed through
            # verbatim (not our prose, no key); only the HTTP fallback is keyed.
            detail = (payload.get("message")
                      if isinstance(payload, dict) else None) \
                or i18n.t("dashboard.conn.http_error", code=r.status_code)
            return {"ok": False, "configured": True, "detail": detail}
        tracks = ((payload.get("tracks") or {}).get("track") or [])
        if not tracks:
            return {"ok": False, "configured": True,
                    "detail": i18n.t("dashboard.finder.lastfm.no_tracks")}
        sample = []
        for t in tracks[:3]:
            artist = ((t.get("artist") or {}).get("name") or "").strip()
            title = (t.get("name") or "").strip()
            if artist or title:
                sample.append(f"{artist} - {title}".strip(" -"))
        snippet = (
            '{"service":"lastfm","method":"tag.getTopTracks","tag":"'
            + q + '","limit":100,"enabled":true}')
        return {"ok": True, "configured": True, "tag": q,
                "sample_count": len(tracks), "sample": sample,
                "snippet": snippet}
    except Exception as e:
        return {"ok": False, "configured": True,
                "detail": i18n.t("dashboard.conn.unreachable",
                                 err=e.__class__.__name__)}


_AUDIO_EXT = (".mp3", ".flac", ".m4a", ".ogg", ".opus", ".aac", ".wav", ".wma")

# Verzeichnis-Statistik ist teuer (os.walk ueber ggf. zehntausende Dateien,
# v.a. die Music-Library auf HDD). Daher cachen und nur alle STATS_TTL Sekunden
# neu berechnen - nicht bei jedem 15s-Abruf.
def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


# Charts-Share + Quarantaene sind klein -> haeufiger neu zaehlen ist billig.
_STATS_TTL = _env_int("DASHBOARD_STATS_TTL", 300)
# Die Library ist GROSS (zehntausende Dateien auf HDD). Ihr Ordner-Scan dauert
# ~10s -> nur SEHR selten neu zaehlen (Default 24h). Die Sammlung waechst eh
# nur langsam. Getrennter Cache, damit die haeufigen Charts-Stats nicht jedes
# Mal den teuren Library-Scan ausloesen.
_LIBRARY_TTL = _env_int("DASHBOARD_LIBRARY_TTL", 86400)
_stats_cache: dict | None = None
_stats_ts: float = 0.0
_library_cache: dict | None = None
_library_ts: float = 0.0


def _walk_count_bytes(root: str) -> tuple[int, int]:
    """
    Zaehlt Audiodateien + Gesamtgroesse in EINEM Walk. Fuer die KLEINEN Ordner
    (Charts-Share, Quarantaene) - dort ist der volle Walk guenstig.
    """
    files_n = 0
    total = 0
    if not os.path.isdir(root):
        return 0, 0
    for dp, _dn, files in os.walk(root):
        for f in files:
            path = os.path.join(dp, f)
            try:
                total += os.path.getsize(path)
            except OSError:
                pass
            if f.lower().endswith(_AUDIO_EXT):
                files_n += 1
    return files_n, total


def _library_counts(root: str) -> dict:
    """
    Systemschonende Zaehlung der Library: NUR Verzeichnis-Eintraege ueber zwei
    Ebenen (Kuenstler-Ordner + deren Album-Unterordner). KEIN Datei-Walk, KEIN
    getsize ueber zehntausende Dateien.

    Es wird BEWUSST keine Songzahl geschaetzt - die Library enthaelt viele
    Single-/Chart-Ordner mit nur 1 Track, ein pauschaler "pro Album"-Faktor
    waere stark irrefuehrend. Angezeigt werden nur die EXAKT gezaehlten
    Kuenstler + Alben.

    Diese Funktion ist teuer (~10s auf HDD) und wird daher nur sehr selten
    aufgerufen (siehe _LIBRARY_TTL, Default 24h).
    Rueckgabe: {"artists", "albums"}
    """
    if not os.path.isdir(root):
        return {"artists": 0, "albums": 0}
    artists = 0
    albums = 0
    try:
        with os.scandir(root) as it:
            for entry in it:
                if not entry.is_dir():
                    continue
                artists += 1
                try:
                    with os.scandir(entry.path) as it2:
                        for sub in it2:
                            if sub.is_dir():
                                albums += 1
                except OSError:
                    pass
    except OSError:
        pass
    return {"artists": artists, "albums": albums}


_library_lock = threading.Lock()
_library_running = False


def _library_refresh_bg() -> None:
    """Berechnet die Library-Zahlen im Hintergrund und legt sie in den Cache."""
    global _library_cache, _library_ts, _library_running
    try:
        counts = _library_counts(config.MUSIC_DIR)
        counts["computed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        _library_cache = counts
        _library_ts = time.time()
    finally:
        with _library_lock:
            _library_running = False


def _library_stats() -> dict:
    """
    Library-Zahlen mit eigenem LANGEM Cache (_LIBRARY_TTL, Default 24h).
    NICHT-BLOCKIEREND: ist der Cache leer/veraltet, wird die (teure, ~10s)
    Zaehlung in einem Hintergrund-Thread angestossen und der ALTE Wert (oder
    "wird berechnet") sofort zurueckgegeben. So blockiert kein /api/status-
    Abruf 10 Sekunden auf dem Library-Scan.
    """
    global _library_running
    now = time.time()
    stale = _library_cache is None or (now - _library_ts) >= _LIBRARY_TTL
    if stale:
        with _library_lock:
            if not _library_running:
                _library_running = True
                threading.Thread(target=_library_refresh_bg, daemon=True).start()
    if _library_cache is not None:
        return _library_cache
    return {"artists": None, "albums": None, "pending": True}


def _dir_stats() -> dict:
    """
    Datei-/Groessen-Zahlen (read-only). Gecacht (STATS_TTL).
    - Charts-Share + Quarantaene: exakt (kleine Ordner, guenstig).
    - Library: nur GROBE Schaetzung ueber Ordnerzahl, KEIN teurer Datei-Walk.
    """
    global _stats_cache, _stats_ts
    now = time.time()
    if _stats_cache is not None and (now - _stats_ts) < _STATS_TTL:
        return _stats_cache

    charts = config.CHARTS_DIR
    rejected = os.path.join(charts, "_rejected")
    charts_files, charts_bytes = _walk_count_bytes(charts)
    rejected_files, rejected_bytes = _walk_count_bytes(rejected)

    _stats_cache = {
        "charts_files": charts_files,
        "charts_bytes": charts_bytes,
        "rejected_files": rejected_files,
        "rejected_bytes": rejected_bytes,
        # Library hat einen EIGENEN, langen Cache (teurer Scan) - separat holen.
        "library": _library_stats(),
        "computed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now)),
    }
    _stats_ts = now
    return _stats_cache


def collect() -> dict:
    """Baut den kompletten Dashboard-Zustand zusammen."""
    return {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "services": _read_status_files(),
        "containers": _container_states(),
        "slskd": _slskd_status(),
        "lidarr": _lidarr_status(),
        # Pushover kommt jetzt aus dem Live-Check (external.pushover: echter
        # validate-Call) statt aus einem reinen Config-Flag.
        "external": _external_status(),
        "storage": _dir_stats(),
    }
