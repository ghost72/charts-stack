"""
charts-hitparade - Hauptservice.

Holt woechentlich (sonntags) die Schweizer Hitparade von hitparade.ch, matcht
gegen die Library und schreibt pro Chart eine Playlist (.m3u) + eine
missing-Datei fuer charts-download. Reiner HTTP-Abruf, kein Headless-Browser.

Ablauf pro Lauf (je konfiguriertem Chart):
  1. Chart-Liste von hitparade.ch holen (hitparade.fetch_chart).
  2. Jeden Song gegen die Library matchen (library.find_match).
  3. Treffer -> Playlist (.m3u), Fehlende -> missing-Datei (charts-download).

Monitoring: Liefert hitparade.ch 0 Songs (Seite umgebaut/blockiert) oder ist
nicht erreichbar, wird der Lauf als status=error gemeldet -> Dashboard-Kachel
orange/rot + Pushover. So faellt ein kaputter Scraper sofort auf.

Stellung in der Kette (wie charts-sync):
  charts-hitparade (hitparade.m3u + missing-hitparade.txt)
  -> charts-download (laedt die fehlenden) -> charts-import -> Navidrome
"""

import logging
import time
from datetime import date, datetime

from . import config, hitparade, library, notify, playlist, status
from .i18n import t

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("charts-hitparade")


def _match_all(lib, songs):
    """Matcht eine Chart-Liste gegen die Library. -> (matched, missing)."""
    matched = []
    missing = []
    for pos, artist, title in songs:
        track, _score = lib.find_match(artist, title, config.MATCH_THRESHOLD)
        if track:
            matched.append((pos, artist, title, track.path))
        else:
            missing.append((pos, artist, title))
    return matched, missing


def run_once() -> dict:
    """
    Fuehrt einen Hitparade-Lauf aus (alle konfigurierten Charts).
    Rueckgabe: Zaehler-Dict fuers Dashboard. Wirft bei einem Quell-Fehler eine
    Exception weiter (-> _guarded_run meldet status=error).
    """
    logger.info(t("hitparade.run.start", charts=", ".join(config.HITPARADE_CHARTS)))
    lib = library.build_index(config.MUSIC_DIR)
    day = hitparade.latest_sunday()

    total_songs = 0
    total_matched = 0
    total_missing = 0
    results = []
    errors = []        # echte Scraper-Fehler (0 Songs) -> rot
    unavailable = []   # hitparade.ch voruebergehend down (5xx) -> gelb/idle

    for chart in config.HITPARADE_CHARTS:
        meta = config.CHART_META.get(chart)
        if not meta:
            logger.warning(t("hitparade.run.unknown_chart", chart=chart))
            continue
        try:
            songs = hitparade.fetch_chart(
                chart, day,
                timeout_s=config.FETCH_TIMEOUT_SECONDS,
                retries=config.FETCH_RETRIES,
            )
        except hitparade.HitparadeUnavailable as e:
            # hitparade.ch voruebergehend nicht erreichbar (5xx/Timeout) - das
            # ist DEREN Problem, kein Scraper-Defekt. Separat merken, damit es
            # milder (idle/gelb, kein Alarm) behandelt wird.
            logger.warning(t("hitparade.run.chart_unavailable", chart=chart, error=e))
            unavailable.append(f"{chart}: {e}")
            continue
        except hitparade.HitparadeError as e:
            # Seite antwortet, aber 0 Songs -> Scraper kaputt -> harter Fehler.
            logger.error(t("hitparade.run.chart_failed", chart=chart, error=e))
            errors.append(f"{chart}: {e}")
            continue

        matched, missing = _match_all(lib, songs)
        playlist.write_playlist(matched, meta["playlist"], meta["title"])
        playlist.write_missing(missing, meta["missing"])

        logger.info(t("hitparade.run.chart_done", chart=chart, songs=len(songs),
                      matched=len(matched), missing=len(missing)))
        total_songs += len(songs)
        total_matched += len(matched)
        total_missing += len(missing)
        results.append({"chart": chart, "title": meta["title"],
                        "total": len(songs), "matched": len(matched),
                        "missing": len(missing),
                        # Volle Fehlliste (pos, artist, title) fuer die
                        # Pushover-Auflistung durchreichen (wie charts-sync).
                        "missing_songs": missing})

    _notify(results)

    # Echter Scraper-Fehler (0 Songs trotz Antwort) -> harter Fehler (rot).
    if errors and not results:
        raise hitparade.HitparadeError("; ".join(errors))
    # Teil-Fehler (ein Chart ok, einer kaputt) ebenfalls als Fehler sichtbar.
    if errors:
        logger.warning(t("hitparade.run.partial_failed", errors="; ".join(errors)))

    # KEIN echter Fehler, aber alle Quellen nur voruebergehend nicht erreichbar
    # (5xx/Timeout) und nichts geladen -> als UNAVAILABLE signalisieren, damit
    # _guarded_run den Status auf idle (gelb, kein Alarm) setzt statt rot.
    if unavailable and not results and not errors:
        raise hitparade.HitparadeUnavailable("; ".join(unavailable))
    if unavailable:
        logger.warning(t("hitparade.run.partial_unavailable", errors="; ".join(unavailable)))

    logger.info(t("hitparade.run.done"))
    return {
        "charts": len(results),
        "songs": total_songs,
        "matched": total_matched,
        "missing": total_missing,
        "_error_note": "; ".join(errors) if errors else "",
    }


def _notify(results: list[dict]) -> None:
    """Pushover-Meldung mit den Hitparade-Ergebnissen (nur bei Aenderung)."""
    if not results:
        return
    total_matched = sum(r["matched"] for r in results)
    total_missing = sum(r["missing"] for r in results)

    # Pushover-Titel = Dienst-Emoji + Dienstname (wie alle anderen charts-
    # Dienste). Der Playlist-Name mit seinen Emojis steht als fette Kopfzeile
    # je Playlist im Body - identisch zum Navidrome-Anzeigenamen.
    push_title = t("hitparade.notify.title", service="charts-hitparade")

    lines = [t("hitparade.notify.summary_header",
               matched=total_matched, missing=total_missing), ""]
    for r in results:
        lines.append(f"<b>{r['title']}</b>")
        lines.append(t("hitparade.notify.chart_line",
                       matched=r["matched"], missing=r["missing"], total=r["total"]))
        # Fehlende Songs auffuehren (wie charts-sync), damit man direkt vom
        # Handy aus sieht, was nachgeladen wird. Menge ueber
        # NOTIFY_MISSING_LIMIT begrenzt (0 = nur Anzahl, keine Songliste).
        missing_songs = r.get("missing_songs") or []
        if missing_songs and config.NOTIFY_MISSING_LIMIT > 0:
            limit = config.NOTIFY_MISSING_LIMIT
            lines.append("")  # Leerzeile vor der Songliste (bessere Lesbarkeit)
            for pos, artist, title in missing_songs[:limit]:
                lines.append(t("notify.song_entry", pos=pos, artist=artist, title=title))
            if len(missing_songs) > limit:
                rest = len(missing_songs) - limit
                lines.append(t("notify.missing.more", count=rest))
        lines.append("")
    message = _truncate_for_pushover("\n".join(lines).strip())
    # NOTIFY_ONLY_ON_CHANGE wird in notify.send selbst nicht geprueft - der
    # Scheduler laeuft eh nur 1x/Woche, da ist eine Meldung pro Lauf ok.
    notify.send(push_title, message)


def _truncate_for_pushover(message: str, max_len: int = 1024) -> str:
    """Kuerzt die Nachricht auf Pushovers 1024-Zeichen-Limit (an Zeilengrenze)."""
    if len(message) <= max_len:
        return message
    suffix = "\n" + t("notify.truncated")
    budget = max_len - len(suffix)
    cut = message[:budget]
    nl = cut.rfind("\n")
    if nl > 0:
        cut = cut[:nl]
    return cut + suffix


def _next_run_dt(now: datetime) -> datetime:
    """Naechster geplanter Lauf-Zeitpunkt (fuer die Dashboard-Anzeige)."""
    import datetime as _dt
    # Tage bis zum konfigurierten Wochentag
    days_ahead = (config.RUN_WEEKDAY - now.weekday()) % 7
    candidate = now.replace(hour=config.RUN_HOUR, minute=config.RUN_MINUTE,
                            second=0, microsecond=0) + _dt.timedelta(days=days_ahead)
    if candidate <= now:
        candidate += _dt.timedelta(days=7)
    return candidate


def _next_run_iso() -> str:
    """Naechster geplanter Lauf als lokale ISO-Zeit mit Zeitzonen-Offset."""
    return _next_run_dt(datetime.now()).astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")


# Counter-Keys (literal, dashboard-Payload) -> i18n-Key fuer das lesbare Label.
_COUNTER_LABEL_KEYS = {
    "charts": "hitparade.counter.charts",
    "songs": "hitparade.counter.songs",
    "matched": "hitparade.counter.matched",
    "missing": "hitparade.counter.missing",
}


def _guarded_run() -> None:
    """Fuehrt run_once() aus und schreibt den Status (Erfolg/Fehler)."""
    next_run = _next_run_iso()
    if not config.HITPARADE_ENABLED:
        # Deaktiviert: Container laeuft weiter, aber kein Fetch/Playlist/Pushover.
        # Status idle (grau, kein Fehler) statt ok/error.
        status.start()
        status.finish("idle", t("hitparade.status.disabled"), {}, next_run)
        logger.info(t("hitparade.disabled_skip"))
        return
    status.start()
    try:
        result = run_once() or {}
        error_note = result.pop("_error_note", "")
        counters = {k: v for k, v in result.items() if isinstance(v, (int, float))}
        # Lesbare Status-/Dashboard-Zeile aus den Countern (uebersetztes Label je
        # Counter, keine rohen englischen Counter-Keys).
        parts = [f"{v} {t(_COUNTER_LABEL_KEYS[k])}"
                 for k, v in counters.items() if k in _COUNTER_LABEL_KEYS and v]
        msg = ", ".join(parts) if parts else t("hitparade.status.nothing_to_do")
        if error_note:
            # Teil-Fehler: Lauf lief, aber eine Quelle war kaputt -> als Fehler
            # markieren, damit es im Dashboard auffaellt.
            status.finish("error",
                          t("hitparade.status.error_suffix", msg=msg, error=error_note),
                          counters, next_run)
        else:
            status.finish("ok", msg, counters, next_run)
    except hitparade.HitparadeUnavailable as e:
        # hitparade.ch voruebergehend nicht erreichbar (5xx/Timeout) - KEIN
        # Scraper-Defekt. Idle (gelb/grau im Dashboard, kein Pushover-Alarm);
        # erholt sich beim naechsten Lauf von selbst.
        logger.warning(t("hitparade.unavailable.warn", error=e))
        status.finish("idle", t("hitparade.status.source_unavailable", error=e),
                      {}, next_run)
    except Exception as e:
        logger.exception(t("hitparade.run.error", error=e))
        status.finish("error", str(e), {}, next_run)


def main() -> None:
    status.init("charts-hitparade")
    logger.info(t(
        "hitparade.scheduler.started",
        enabled=t("hitparade.state.yes") if config.HITPARADE_ENABLED
        else t("hitparade.state.no"),
        charts=", ".join(config.HITPARADE_CHARTS),
        weekday=config.RUN_WEEKDAY,
        hour=config.RUN_HOUR, minute=config.RUN_MINUTE,
        library=config.MUSIC_DIR, playlist_dir=config.PLAYLIST_DIR,
    ))

    # next_run beim Start sofort aktuell setzen (sonst zeigt das Dashboard bis
    # zum ersten Lauf gar keinen / einen alten Wert).
    status.set_next_run(_next_run_iso())

    if config.RUN_ON_START:
        _guarded_run()

    check = max(30, config.CHECK_INTERVAL_SECONDS)
    last_run_day: date | None = date.today() if config.RUN_ON_START else None

    while True:
        time.sleep(check)
        now = datetime.now()
        # next_run bei JEDEM Check aktuell halten, damit die Dashboard-Anzeige
        # nie "ueberfaellig" zeigt, nur weil der letzte Lauf laenger her ist.
        # set_next_run beruehrt NUR next_run, nicht den status - ein per
        # _guarded_run gesetztes idle/deaktiviert bleibt also erhalten.
        status.set_next_run(_next_run_iso())
        # Lauf-Fenster: richtiger Wochentag, Stunde erreicht, heute noch nicht gelaufen.
        due = (now.weekday() == config.RUN_WEEKDAY
               and now.hour >= config.RUN_HOUR
               and (now.hour > config.RUN_HOUR or now.minute >= config.RUN_MINUTE)
               and last_run_day != now.date())
        if due:
            logger.info(t("hitparade.scheduler.due",
                          hour=config.RUN_HOUR, minute=config.RUN_MINUTE))
            _guarded_run()
            last_run_day = now.date()


if __name__ == "__main__":
    main()
