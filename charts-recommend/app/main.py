"""
charts-recommend – Hauptservice (Multi-User, Download-only).

Liest den oeffentlichen Hoergeschmack mehrerer ListenBrainz-User (optional
zusaetzlich Last.fm) und laesst die GEMEINSAME Library mitwachsen. Er schreibt
KEINE Playlists mehr - die Personalisierung/Anzeige uebernimmt der Client
(z.B. Substreamer) pro Account.

  1. Library-Index bauen (wie charts-sync).
  2. Fuer jeden konfigurierten ListenBrainz-User die aktiven Quellen abfragen:
       - "stats"      (meistgehoerte Recordings, sofort da)
       - "createdfor" (Weekly Jams / Weekly Exploration, evtl. erst nach Wochen)
     Optional zusaetzlich Last.fm (globale Quelle des Betreibers).
  3. Alle Ergebnisse zusammenfuehren + deduplizieren (gleicher Song nur einmal).
  4. Gegen die Library matchen:
       - vorhanden -> zaehlen (nichts nachzuladen)
       - fehlt     -> in EINE kombinierte missing-Datei (charts-download laedt
                      nach, wenn WRITE_MISSING=1 und die Datei in dessen
                      MISSING_FILES steht)
  5. Pushover-Meldung.

Laeuft als Scheduler-Loop (alle INTERVAL_HOURS Stunden).

HINWEIS: ListenBrainz generiert die createdfor-Playlists erst nach einigen
Wochen Hoerverlauf. Bis dahin ist diese Quelle leer - kein Fehler, nur fehlende
Daten (die "stats"-Quelle hat sofort Daten).
"""

import logging
import time

from . import config, lastfm, library, listenbrainz, notify, playlist, status
from .errors import FetchError
from .i18n import t

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("charts-recommend")


def _match_all(lib, tracks: list[tuple[int, str, str]]):
    """
    Matcht eine Empfehlungsliste gegen die Library.
    Rueckgabe: (matched, missing)
      matched: [(pos, artist, title, path), ...] (in Library gefunden)
      missing: [(pos, artist, title), ...] (nicht gefunden)
    """
    matched = []
    missing = []
    for pos, artist, title in tracks:
        track, score = lib.find_match(artist, title, config.MATCH_THRESHOLD)
        if track:
            matched.append((pos, artist, title, track.path))
        else:
            missing.append((pos, artist, title))
    return matched, missing


def _merge(tracks, merged: list, seen: set) -> None:
    """
    Haengt (artist, title) an merged, dedupliziert ueber seen.
    tracks sind (pos, artist, title) 3-Tupel; pos wird nach dem Merge verworfen.
    Der Dedupe-Key ist identisch zur Last.fm-Logik, damit derselbe Song, den
    mehrere User moegen, nur EINMAL nachgeladen wird.
    """
    for item in tracks:
        _pos, artist, title = item
        key = f"{artist.lower()}|{title.lower()}"
        if key in seen:
            continue
        seen.add(key)
        merged.append((artist, title))


def run_once() -> tuple[dict, list[str], int, int]:
    """
    Fuehrt einen Empfehlungs-Lauf aus.

    Rueckgabe: (counters, fetch_errors, transport_attempts, transport_failures)
      counters:           Zaehler fuer das Dashboard (sources/candidates/
                          matched/missing, Momentanwerte).
      fetch_errors:       Liste der Quellen-Fehlermeldungen mit user/source-
                          Praefix (Transport-Fehler nach allen Wiederholungen).
      transport_attempts: Wie viele (user,source)-Abrufe stattfanden.
      transport_failures: Davon mit FetchError.

    Jede Quelle wird einzeln abgesichert: Faellt ein User/eine Quelle aus, kann
    eine andere trotzdem Ergebnisse liefern (teilweiser Erfolg). Nur ein
    FetchError zaehlt als Fehler - ein leeres, aber erfolgreiches Ergebnis (noch
    keine Daten) ist kein Fehler.
    """
    logger.info(t("recommend.run.start"))
    lib = library.build_index(config.MUSIC_DIR)
    merged: list[tuple[str, str]] = []   # [(artist, title)] in Reihenfolge
    seen: set[str] = set()               # key = f"{artist.lower()}|{title.lower()}"
    source_results: list[dict] = []      # [{"user":.., "source":.., "n":..}]
    fetch_errors: list[str] = []         # FetchError-Meldungen (user/source)
    transport_attempts = 0               # wie viele Abrufe fanden statt
    transport_failures = 0               # davon mit FetchError

    if config.LISTENBRAINZ_ENABLED and config.LISTENBRAINZ_USERS:
        for user in config.LISTENBRAINZ_USERS:
            if "stats" in config.RECOMMEND_SOURCES:
                try:
                    recs = listenbrainz.fetch_stats_recordings(
                        user, config.LISTENBRAINZ_TOKEN,
                        config.RECOMMEND_STATS_RANGE, config.RECOMMEND_LIMIT)
                    _merge(recs[:config.RECOMMEND_LIMIT], merged, seen)
                    source_results.append({"user": user, "source": "stats",
                                           "n": len(recs)})
                except FetchError as e:
                    fetch_errors.append(f"{user}/stats: {e}")
                    transport_failures += 1
                finally:
                    transport_attempts += 1
            if "createdfor" in config.RECOMMEND_SOURCES:
                try:
                    by_kind = listenbrainz.fetch_recommendations(
                        user, config.LISTENBRAINZ_TOKEN,
                        config.LISTENBRAINZ_KINDS, config.RECOMMEND_LIMIT)
                    flat = [t for k in config.LISTENBRAINZ_KINDS
                            for t in by_kind.get(k, [])]
                    _merge(flat[:config.RECOMMEND_LIMIT], merged, seen)
                    source_results.append({"user": user, "source": "createdfor",
                                           "n": len(flat)})
                except FetchError as e:
                    fetch_errors.append(f"{user}/createdfor: {e}")
                    transport_failures += 1
                finally:
                    transport_attempts += 1

    if config.LASTFM_ENABLED:
        try:
            lfm = lastfm.fetch_recommendations(
                config.LASTFM_MODE, config.LASTFM_USER, config.LASTFM_API_KEY,
                config.LASTFM_SEEDS, config.RECOMMEND_LIMIT)
            _merge(lfm, merged, seen)   # eigener Cap in lastfm
            source_results.append({"user": "-", "source": "lastfm",
                                   "n": len(lfm)})
        except FetchError as e:
            fetch_errors.append(f"lastfm: {e}")
            transport_failures += 1
        finally:
            transport_attempts += 1

    # merged (2-Tupel) -> flache 3-Tupel fuer das unveraenderte _match_all().
    tracks = [(pos, artist, title)
              for pos, (artist, title) in enumerate(merged, 1)]
    matched, missing = _match_all(lib, tracks)
    if config.WRITE_MISSING and missing:
        playlist.write_missing(missing, config.MISSING_FILE)

    counters = {
        "sources": len([s for s in source_results if s["n"] > 0]),
        "candidates": len(merged),
        "matched": len(matched),
        "missing": len(missing),
    }
    suffix = (t("recommend.run.missing_written_suffix")
              if (config.WRITE_MISSING and missing) else "")
    logger.info(t("recommend.run.done",
                  sources=counters["sources"], candidates=counters["candidates"],
                  matched=counters["matched"], missing=counters["missing"],
                  suffix=suffix))
    _notify(source_results, counters)
    return counters, fetch_errors, transport_attempts, transport_failures


def _prev_status() -> str:
    """
    Status des VORHERIGEN Laufs aus der Status-JSON lesen (bevor status.start()
    sie auf 'running' setzt). Dient dem Nur-bei-Wechsel-Alarm (B): so laesst
    sich erkennen, ob der letzte Lauf bereits ein Fehler war. Wir lesen die
    Datei bewusst ueber das Status-Modul (dieselbe Datei/Pfad), legen aber
    kein zusaetzliches State-File an. Fehlt die Datei, gilt der Lauf als 'ok'
    (kein vorheriger Fehler).
    """
    data = status._read()
    val = data.get("status")
    return val if isinstance(val, str) else "ok"


# Counter-Keys (literal, dashboard-Payload) -> i18n-Key fuer das lesbare Label.
_COUNTER_LABEL_KEYS = {
    "sources": "recommend.counter.sources",
    "candidates": "recommend.counter.candidates",
    "matched": "recommend.counter.matched",
    "missing": "recommend.counter.missing",
}


def _base_msg(counters: dict) -> str:
    """Baut die lesbare Status-/Dashboard-Zeile aus den Countern (uebersetztes
    Label je Counter, keine rohen englischen Counter-Keys)."""
    parts = [f"{v} {t(_COUNTER_LABEL_KEYS[k])}"
             for k, v in counters.items()
             if k in _COUNTER_LABEL_KEYS and isinstance(v, (int, float)) and v]
    return ", ".join(parts) if parts else t("recommend.status.nothing_found")


def _notify(source_results: list[dict], counters: dict) -> None:
    cand = counters["candidates"]
    miss = counters["missing"]
    if cand == 0:
        logger.info(t("recommend.notify.none_received"))
        return
    # Pro User gruppieren, Reihenfolge der ersten Nennung beibehalten.
    by_user: dict[str, list[str]] = {}
    for s in source_results:
        if s["n"] <= 0:
            continue
        by_user.setdefault(s["user"], []).append(f"{s['source']} {s['n']}")
    quellen = ", ".join(f"{u} ({', '.join(parts)})"
                        for u, parts in by_user.items())
    n_users = len(by_user)
    if miss > 0:
        head = t("recommend.notify.new_to_load", missing=miss, users=n_users,
                 candidates=cand)
    else:
        head = t("recommend.notify.all_present", candidates=cand, users=n_users)
    body = head + (t("recommend.notify.sources", sources=quellen) if quellen else "")
    notify.send(t("recommend.notify.title", service="charts-recommend"), body)


def _next_run_iso() -> str:
    """Zeitpunkt des naechsten Laufs als ISO-8601 (fuer das Dashboard)."""
    return time.strftime("%Y-%m-%dT%H:%M:%S%z",
                         time.localtime(time.time() + max(1, config.INTERVAL_HOURS) * 3600))


def _alert_fetch_error(message: str, was_error_before: bool) -> None:
    """
    Pushover-Alarm bei Abruf-Fehler - nur bei Zustandswechsel (B), wenn
    NOTIFY_ONLY_ON_CHANGE gesetzt ist. So gibt es nicht bei jedem 24h-Lauf
    einen neuen Alarm, solange die Stoerung anhaelt. notify.send() ist ohne
    PUSHOVER_* ohnehin ein No-op.
    """
    if config.NOTIFY_ONLY_ON_CHANGE and was_error_before:
        logger.info(t("recommend.alert.still_failing"))
        return
    notify.send(t("recommend.notify.title_warning", service="charts-recommend"), message)


def _guarded_run() -> None:
    """Fuehrt run_once() aus und schreibt den Status (Erfolg/Fehler)."""
    # Vorherigen Status VOR start() merken (start() setzt 'running').
    prev_status = _prev_status()
    status.start()
    try:
        counters, fetch_errors, attempts, failures = run_once()
        base = _base_msg(counters)
        err_msg = "; ".join(fetch_errors)

        if attempts == 0:
            # Keine User konfiguriert / Feature aus -> idle, kein Fehler.
            status.finish("ok", t("recommend.status.nothing_to_do"),
                          counters, _next_run_iso())
        elif failures == attempts:
            # Alle Abrufe transport-fehlgeschlagen -> echter Fehler.
            status.finish("error", err_msg, counters, _next_run_iso())
            _alert_fetch_error(err_msg, prev_status == "error")
        elif failures > 0:
            # Teilweiser Erfolg: eine Quelle lieferte, eine andere fiel aus.
            status.finish("ok",
                          t("recommend.status.source_disrupted", base=base, error=err_msg),
                          counters, _next_run_iso())
            _alert_fetch_error(t("recommend.alert.one_source_disrupted", error=err_msg),
                               prev_status == "error")
        else:
            # Alles ok (auch ein leeres Ergebnis ist hier ein Erfolg).
            # Sonderfall: Abrufe liefen fehlerfrei, aber es kam NICHTS (0
            # Empfehlungen) - typisch, wenn ListenBrainz fuer die konfigurierten
            # User noch keine Stats/Playlists generiert hat (zu wenig
            # Hoerverlauf; die stats-/createdfor-Endpoints geben dann 404).
            # Statt des nichtssagenden "nichts gefunden" eine sprechende Zeile,
            # damit im Dashboard klar ist WARUM nichts kommt (bleibt gruen/ok,
            # kein Fehler).
            if counters["candidates"] == 0:
                msg = t("recommend.status.no_recommendations")
            else:
                msg = base
            status.finish("ok", msg, counters, _next_run_iso())
            if prev_status == "error":
                notify.send(t("recommend.notify.title_recovered", service="charts-recommend"),
                            t("recommend.notify.fetch_recovered"))
    except Exception as e:
        logger.exception(t("recommend.run.error", error=e))
        status.finish("error", str(e), {}, _next_run_iso())
        _alert_fetch_error(str(e), prev_status == "error")


def main() -> None:
    status.init("charts-recommend")
    logger.info(t(
        "recommend.scheduler.started",
        library=config.MUSIC_DIR,
        listenbrainz=t("recommend.state.on") if config.LISTENBRAINZ_ENABLED
        else t("recommend.state.off"),
        users=", ".join(config.LISTENBRAINZ_USERS) or "-",
        sources=", ".join(config.RECOMMEND_SOURCES) or "-",
        lastfm=t("recommend.state.on") if config.LASTFM_ENABLED
        else t("recommend.state.off"),
        interval=config.INTERVAL_HOURS,
    ))

    if config.RUN_ON_START:
        _guarded_run()

    interval_seconds = max(1, config.INTERVAL_HOURS) * 3600
    while True:
        logger.info(t("recommend.scheduler.next_run", interval=config.INTERVAL_HOURS))
        time.sleep(interval_seconds)
        _guarded_run()


if __name__ == "__main__":
    main()
