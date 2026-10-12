"""
Charts-Sync – Hauptservice.

Ablauf pro Sync-Lauf:
  1. Musik-Library (MUSIC_DIR) EINMAL indexieren.
  2. Fuer JEDE konfigurierte Chart-Quelle (CHARTS):
     a. Aktuelle Charts von Deezer holen (nur Metadaten, ohne Account).
     b. Jeden Chart-Track gegen die Library matchen.
     c. Treffer als eigene M3U-Playlist in PLAYLIST_DIR schreiben.
     d. Fehlende Songs in eine eigene missing-Datei in CHARTS_DIR schreiben.
  3. Optional: Pushover-Benachrichtigung (nur bei Aenderung, wenn gewuenscht).

Der Service laeuft als Endlosschleife und wiederholt den Lauf alle
SYNC_INTERVAL_HOURS Stunden.

Hinweis: Dieser Service laedt bewusst KEINE Audiodateien herunter. Er arbeitet
ausschliesslich mit Metadaten und der bereits vorhandenen Library.
"""

import json
import logging
import os
import time
from datetime import datetime

from . import config, deezer, library, notify, playlist, status
from .i18n import t

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("charts-sync")


# --- Zustand (fuer "nur bei Aenderung"-Benachrichtigung) ---

def _state_path() -> str:
    return os.path.join(config.CHARTS_DIR, config.STATE_FILE)


def _load_state() -> dict:
    """Laedt den zuletzt gespeicherten Stand pro Playlist."""
    try:
        with open(_state_path(), encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    os.makedirs(config.CHARTS_DIR, exist_ok=True)
    tmp = _state_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, _state_path())


def _sync_source(chart_source, lib, state: dict) -> None:
    """Verarbeitet eine einzelne Chart-Quelle."""
    logger.info(t("sync.source.header", name=chart_source.title))

    # a. Charts holen
    try:
        chart_tracks = deezer.fetch_chart(chart_source)
    except Exception as e:
        logger.error(t("sync.source.fetch_failed", name=chart_source.title, error=e))
        return
    if not chart_tracks:
        logger.warning(t("sync.source.no_tracks", name=chart_source.title))
        return

    # b. Matching
    matched: list[tuple[int, str, str, str]] = []
    missing: list[tuple[int, str, str]] = []
    for ct in chart_tracks:
        track, score = lib.find_match(ct.artist, ct.title, config.MATCH_THRESHOLD)
        if track is not None:
            matched.append((ct.position, ct.artist, ct.title, track.path))
        else:
            missing.append((ct.position, ct.artist, ct.title))

    matched.sort(key=lambda x: x[0])
    missing.sort(key=lambda x: x[0])

    # Dedup: derselbe Song kann von mehreren Chart-Positionen getroffen werden.
    # Wir behalten jeweils die beste (niedrigste) Chart-Position.
    deduped: list[tuple[int, str, str, str]] = []
    seen_paths: set[str] = set()
    for entry in matched:
        if entry[3] in seen_paths:
            continue
        seen_paths.add(entry[3])
        deduped.append(entry)
    removed = len(matched) - len(deduped)
    if removed:
        logger.info(t("sync.source.dedup_removed", name=chart_source.title, count=removed))
    matched = deduped

    # Reihenfolge festlegen (Chart-Position ODER neueste zuerst).
    ordered = _order_matched(chart_source, matched, state)

    # c. + d. Schreiben
    playlist.write_playlist(ordered, chart_source.name, chart_source.title)
    playlist.write_missing(missing, chart_source.missing)

    logger.info(t(
        "sync.source.done",
        name=chart_source.title, matched=len(matched), total=len(chart_tracks),
    ))

    # Benachrichtigung (Aenderungserkennung ueber die gematchten Pfade)
    _handle_notify(chart_source, matched, missing, state)


def _order_matched(chart_source, matched, state: dict):
    """
    Bringt die gematchten Tracks in die konfigurierte Reihenfolge und pflegt
    dabei das "first_seen"-Datum je Song im State.

    matched: Liste (position, artist, title, absolute_path), bereits
             chart-sortiert + dedupliziert.
    Rueckgabe: Liste in derselben Tupel-Form, in gewuenschter Reihenfolge.

    PLAYLIST_ORDER="chart" (Default): unveraendert (Chart-Position, Platz 1 oben).

    PLAYLIST_ORDER="newest-first": zuletzt neu hinzugekommene Songs zuoberst.
      Dazu wird pro Song das Datum des ersten Auftauchens im State gefuehrt
      (first_seen[relpath] = ISO-Zeitstempel). Bekannte Songs behalten ihr
      Datum, neue bekommen "jetzt" -> wandern nach oben. Bei gleichem Datum
      (z.B. beim allerersten Lauf sind alle neu) entscheidet die Chart-Position,
      damit die Reihenfolge deterministisch bleibt.
    """
    if config.PLAYLIST_ORDER != "newest-first":
        return matched

    prev = state.get(chart_source.name, {})
    first_seen: dict[str, str] = dict(prev.get("first_seen", {}))
    now_iso = datetime.now().isoformat(timespec="seconds")

    # first_seen fuer aktuelle Songs pflegen (relativer Pfad = stabiler Key,
    # identisch zu dem, was in der m3u steht).
    current_relpaths: list[str] = []
    for _pos, _a, _t, path in matched:
        rel = os.path.relpath(path, config.MUSIC_DIR)
        current_relpaths.append(rel)
        if rel not in first_seen:
            first_seen[rel] = now_iso

    # Nur Eintraege fuer aktuell vorhandene Songs behalten (State nicht wachsen
    # lassen; ein spaeter zurueckkehrender Song gilt zu Recht wieder als neu).
    first_seen = {rel: first_seen[rel] for rel in current_relpaths}

    # Merken, damit _handle_notify es beim State-Update nicht verwirft.
    chart_source._first_seen = first_seen  # noqa: SLF001 (interner Transport)

    # Zweistufig stabil sortieren (Pythons sort ist stabil):
    #   1. nach Chart-Position aufsteigend (Tiebreaker bei gleichem Datum),
    #   2. nach first_seen absteigend (Hauptkriterium: neueste zuoberst).
    ordered = sorted(matched, key=lambda e: e[0])  # Chart-Position aufsteigend
    ordered.sort(
        key=lambda e: first_seen.get(os.path.relpath(e[3], config.MUSIC_DIR), now_iso),
        reverse=True,
    )
    return ordered


def _handle_notify(chart_source, matched, missing, state: dict) -> None:
    """
    Sendet Pushover, ggf. nur wenn sich etwas geaendert hat.

    "Aenderung" umfasst sowohl die Playlist-Treffer (gematchte Pfade) als auch
    die Liste der fehlenden Songs. So kommt auch dann eine Meldung, wenn sich
    nur die zu suchenden Songs aendern (z.B. neue Chart-Eintraege), selbst wenn
    die Trefferliste identisch bleibt.
    """
    current_paths = sorted(p for _pos, _a, _t, p in matched)
    # Fehlende Songs als vergleichbare Signatur (Kuenstler|Titel), positionsunabhaengig.
    current_missing = sorted(f"{a}|{t}" for _pos, a, t in missing)

    prev = state.get(chart_source.name, {})
    prev_paths = prev.get("paths", [])
    prev_missing = prev.get("missing", [])

    paths_changed = current_paths != prev_paths
    missing_changed = current_missing != prev_missing
    changed = paths_changed or missing_changed

    # Neu hinzugekommene Songs (fuer eine sprechende Meldung)
    new_songs = [
        (pos, a, t) for pos, a, t, p in matched if p not in set(prev_paths)
    ]

    # Zustand immer aktualisieren (Treffer UND fehlende Songs). first_seen
    # (fuer PLAYLIST_ORDER=newest-first) mitfuehren, falls _order_matched es
    # gesetzt hat - sonst ginge die Reihenfolge-Historie beim naechsten Lauf
    # verloren. Bei PLAYLIST_ORDER=chart existiert es nicht -> weglassen.
    entry = {"paths": current_paths, "missing": current_missing}
    first_seen = getattr(chart_source, "_first_seen", None)
    if first_seen is not None:
        entry["first_seen"] = first_seen
    state[chart_source.name] = entry

    if config.NOTIFY_ONLY_ON_CHANGE and not changed:
        logger.info(t("sync.notify.no_change", name=chart_source.title))
        return

    total = len(matched) + len(missing)
    # Pushover-Titel = Dienst-Emoji + Dienstname (wie alle anderen charts-
    # Dienste: "⬇️ charts-download", "🎧 charts-recommend" ...). Der Playlist-
    # Name mit seinen eigenen Emojis steht als fette Kopfzeile im Body - so ist
    # er identisch zum Navidrome-Anzeigenamen. {date} wird wie im Playlist-
    # Writer ersetzt.
    title = t("notify.title", service="charts-sync")
    playlist_name = chart_source.title.replace(
        "{date}", datetime.now().strftime("%d.%m.%Y"))
    lines = [f"<b>{playlist_name}</b>",
             t("notify.summary", matched=len(matched), total=total)]

    # Quellen-/Filter-Statistik (falls verfuegbar) – zeigt, was Last.fm netto
    # dazubringt und wie viele Fremd-Genre-Songs der Filter entfernt hat.
    stats = getattr(chart_source, "last_stats", None) or {}
    stat_bits = []
    if stats.get("deezer_new"):
        stat_bits.append(f"Deezer {stats['deezer_new']}")
    if stats.get("lastfm_new"):
        stat_bits.append(f"Last.fm {stats['lastfm_new']}")
    if stat_bits:
        lines.append(t("notify.sources", sources=", ".join(stat_bits)))
    if stats.get("genre_filtered"):
        lines.append(t("notify.genre_filtered", count=stats["genre_filtered"]))

    if new_songs and changed:
        lines.append("")
        lines.append(t("notify.new_in_playlist", count=len(new_songs)))
        for pos, a, song_title in new_songs[:8]:
            lines.append(t("notify.song_entry", pos=pos, artist=a, title=song_title))

    # Fehlende Songs auffuehren, damit man sie direkt vom Handy aus in slskd
    # suchen kann (ohne NAS-Login). Menge ueber NOTIFY_MISSING_LIMIT begrenzt.
    if missing and config.NOTIFY_MISSING_LIMIT > 0:
        limit = config.NOTIFY_MISSING_LIMIT
        shown = missing[:limit]
        lines.append("")
        lines.append(t("notify.missing.header", count=len(missing)))
        for pos, a, song_title in shown:
            lines.append(t("notify.song_entry", pos=pos, artist=a, title=song_title))
        if len(missing) > limit:
            lines.append(t("notify.missing.more", count=len(missing) - limit))
    elif missing:
        lines.append("")
        lines.append(t("notify.missing.none_listed", count=len(missing)))

    message = "\n".join(lines)
    # Pushover begrenzt eine Nachricht auf 1024 Zeichen. Bei Ueberlaenge kuerzen
    # wir am letzten vollstaendigen Song und haengen einen Hinweis an.
    message = _truncate_for_pushover(message)
    notify.send(title, message)


def _truncate_for_pushover(message: str, max_len: int = 1024) -> str:
    """Kuerzt die Nachricht auf Pushovers 1024-Zeichen-Limit (an Zeilengrenze)."""
    if len(message) <= max_len:
        return message
    suffix = "\n" + t("notify.truncated")
    budget = max_len - len(suffix)
    cut = message[:budget]
    # An der letzten kompletten Zeile abschneiden, damit kein Song halb dasteht.
    nl = cut.rfind("\n")
    if nl > 0:
        cut = cut[:nl]
    return cut + suffix


def run_sync() -> None:
    """
    Fuehrt einen einzelnen Sync-Lauf ueber alle Chart-Quellen aus.
    Rueckgabe: Zaehler-Dict fuer das Status-/Dashboard-Modul.
    """
    logger.info(t("sync.run.start", sources=len(config.CHARTS)))

    if not config.CHARTS:
        logger.warning(t("sync.run.no_sources"))
        return {"sources": 0, "library_tracks": 0}

    # 1. Library EINMAL indexieren (der teure Teil, fuer alle Quellen wiederverwendet)
    lib = library.build_index(config.MUSIC_DIR)
    if not lib.tracks:
        logger.warning(t("sync.run.library_empty", dir=config.MUSIC_DIR))

    # 2. Zustand laden, alle Quellen verarbeiten, Zustand speichern
    state = _load_state()
    ok_sources = 0
    for chart_source in config.CHARTS:
        try:
            _sync_source(chart_source, lib, state)
            ok_sources += 1
        except Exception as e:
            logger.exception(t("sync.source.exception", name=chart_source.title, error=e))
    _save_state(state)

    logger.info(t("sync.run.done"))
    return {
        "sources": ok_sources,
        "library_tracks": len(lib.tracks),
    }


def _next_run_iso() -> str:
    """Zeitpunkt des naechsten regulaeren Laufs als ISO-8601 (fuer das Dashboard)."""
    return time.strftime("%Y-%m-%dT%H:%M:%S%z",
                         time.localtime(time.time() + max(1, config.SYNC_INTERVAL_HOURS) * 3600))


def _guarded_sync() -> None:
    """Fuehrt run_sync() aus und schreibt den Status (Erfolg/Fehler)."""
    status.start()
    try:
        counters = run_sync() or {}
        parts = [f"{v} {k}" for k, v in counters.items() if isinstance(v, (int, float)) and v]
        msg = ", ".join(parts) if parts else "nichts zu tun"
        status.finish("ok", msg, counters, _next_run_iso())
    except Exception as e:
        logger.exception(t("sync.unexpected_error", error=e))
        status.finish("error", str(e), {}, _next_run_iso())


def main() -> None:
    """Startet den Scheduler-Loop."""
    status.init("charts-sync")
    logger.info(t(
        "sync.scheduler.started",
        sources=", ".join(f"{c.title} ({c.name})" for c in config.CHARTS) or "keine",
        interval=config.SYNC_INTERVAL_HOURS,
        resync=config.RESYNC_CHECK_SECONDS,
        playlist_dir=config.PLAYLIST_DIR,
        charts_dir=config.CHARTS_DIR,
    ))

    if config.RUN_ON_START:
        _guarded_sync()
        _clear_resync_signal()  # evtl. waehrend des Laufs gesetztes Signal abraeumen
    else:
        logger.info(t("sync.scheduler.run_on_start_off"))

    interval_seconds = max(1, config.SYNC_INTERVAL_HOURS) * 3600
    check_seconds = max(5, config.RESYNC_CHECK_SECONDS)

    logger.info(t("sync.scheduler.waiting", interval=config.SYNC_INTERVAL_HOURS))
    next_regular = time.time() + interval_seconds
    while True:
        time.sleep(check_seconds)

        trigger = None
        if _resync_requested():
            trigger = t("sync.scheduler.trigger.resync")
        elif time.time() >= next_regular:
            trigger = t("sync.scheduler.trigger.interval")

        if not trigger:
            continue

        logger.info(t("sync.scheduler.triggered", trigger=trigger))
        _guarded_sync()
        _clear_resync_signal()
        next_regular = time.time() + interval_seconds


def _resync_requested() -> bool:
    """True, wenn charts-import ein Resync-Signal hinterlegt hat."""
    return os.path.exists(config.RESYNC_SIGNAL)


def _clear_resync_signal() -> None:
    """Entfernt das Resync-Signal nach einem Lauf (idempotent)."""
    try:
        os.remove(config.RESYNC_SIGNAL)
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.debug(t("sync.resync.clear_failed", error=e))


if __name__ == "__main__":
    main()
