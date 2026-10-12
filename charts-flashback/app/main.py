"""
charts-flashback – Hauptservice (Download-only).

Liest die "zuletzt gespielt"-Rotation von FLASHBACK FM (GraphQL) pro
konfiguriertem Dekaden-Channel und laesst die GEMEINSAME Library mitwachsen. Er
schreibt KEINE Playlists - die Dekaden-Anzeige uebernehmen die Navidrome Smart
Playlists (NSP), die nach Jahr filtern.

  1. Library-Index bauen (wie charts-sync).
  2. Pro Channel die zuletzt gespielten Songs holen (flashback.fetch_played).
  3. NEUE Songs (nicht im State, nicht schon in diesem Lauf gesehen) bis
     FLASHBACK_LIMIT verarbeiten: Jahr aufloesen (year.resolve_year, Hybrid
     MusicBrainz + Channel-Dekade) und gegen die Library matchen.
       - vorhanden -> zaehlen (nichts nachzuladen)
       - fehlt     -> in die missing-flashback.txt (mit {YYYY}-Suffix), damit
                      charts-download sie nachlaedt und charts-import das
                      Jahr-Tag setzt.
  4. State speichern, Pushover-Meldung.

Laeuft als Scheduler-Loop (alle INTERVAL_HOURS Stunden).

Fehler-Sichtbarkeit (watchdog): schlaegt der GraphQL-Abruf fuer ALLE Channels
fehl (toter Hash / API geaendert), meldet der Dienst status=error + Pushover.
Ein erfolgreicher Abruf ohne NEUE Songs ist KEIN Fehler.
"""

import json
import logging
import os
import time
from datetime import datetime

from . import config, deezer, flashback, library, notify, status, year as year_mod
from .errors import FetchError
from .i18n import t

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("charts-flashback")


# ---------------------------------------------------------------------------
# State (gesehene Songs, gegen Rotations-Reprocessing)
# ---------------------------------------------------------------------------

def _load_state() -> dict:
    try:
        with open(config.STATE_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("seen"), dict):
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {"seen": {}}


def _save_state(state: dict) -> None:
    try:
        os.makedirs(os.path.dirname(config.STATE_FILE), exist_ok=True)
        tmp = config.STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=0)
        os.replace(tmp, config.STATE_FILE)
    except OSError as e:
        logger.warning(t("flashback.state.write_failed", error=e))


def _key(artist: str, title: str) -> str:
    return f"{artist.lower()}|{title.lower()}"


# ---------------------------------------------------------------------------
# Missing-Writer (4-Tupel + optionales {YYYY}-Suffix, von charts-download gelesen)
# ---------------------------------------------------------------------------

def write_missing(missing: list[tuple[int, str, str, str]], file_name: str) -> str:
    """
    Schreibt die Fehlliste im charts-download-Format ("  <pos>. <artist> -
    <title>") - EXAKT wie charts-sync/charts-hitparade, OHNE jedes Jahr-Zeichen.
    So erstellt charts-import Pfad/Name/Tags identisch zu allen anderen Diensten
    (Lidarr-Stil). Das Jahr wird NICHT ueber den Dateinamen transportiert
    (das verseuchte frueher Titel/Album/Pfad), sondern separat ueber die
    Jahr-Map (write_year_map) rein in die Metadaten.
    missing: (position, artist, title, year) - year wird hier IGNORIERT.
    """
    os.makedirs(config.CHARTS_DIR, exist_ok=True)
    missing_path = os.path.join(config.CHARTS_DIR, file_name)

    header = t("flashback.missing_file.header",
               date=datetime.now().strftime("%d.%m.%Y %H:%M")).split("\n")
    body = [f"{pos:>3}. {artist} - {title}" for pos, artist, title, _yr in missing]
    content = "\n".join(header + body) + "\n"

    tmp_path = missing_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        fh.write(content)
    os.replace(tmp_path, missing_path)

    logger.info(t("playlist.missing_written", path=missing_path, count=len(missing)))
    return missing_path


def write_year_map(missing: list[tuple[int, str, str, str]]) -> None:
    """
    Schreibt/aktualisiert die Jahr-Map (artist|title -> jahr) nach
    config.YEAR_MAP_FILE. charts-import liest sie beim Taggen und setzt das
    date/originaldate-Tag - OHNE dass das Jahr je im Datei-/Ordnernamen auftaucht.
    Der Key ist identisch zum Matching-Key (lowercase 'artist|title'), damit
    charts-import denselben Song wiederfindet. Bestehende Eintraege bleiben
    erhalten (die Map waechst mit).
    """
    path = config.YEAR_MAP_FILE
    data = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            loaded = json.load(fh)
        if isinstance(loaded, dict):
            data = loaded
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        data = {}

    changed = False
    for _pos, artist, title, yr in missing:
        if not yr:
            continue
        key = _key(artist, title)
        if data.get(key) != yr:
            data[key] = yr
            changed = True
    if not changed:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=0)
        os.replace(tmp, path)
        logger.info(t("flashback.year_map.updated", path=path, count=len(data)))
    except OSError as e:
        logger.warning(t("flashback.year_map.write_failed", error=e))


# ---------------------------------------------------------------------------
# Hauptlauf
# ---------------------------------------------------------------------------

def run_once() -> tuple[dict, list[str], int, int]:
    """
    Fuehrt einen FLASHBACK-Lauf aus.

    Rueckgabe: (counters, fetch_errors, transport_attempts, transport_failures)
      counters:           {channels, new, matched, missing} (Momentanwerte).
      fetch_errors:       Channel-Fehlermeldungen (FetchError nach Retries).
      transport_attempts: Wie viele Channel-Abrufe stattfanden.
      transport_failures: Davon mit FetchError.

    Jeder Channel wird einzeln abgesichert: faellt einer aus, koennen andere
    trotzdem liefern. Nur ein FetchError zaehlt als Fehler; ein erfolgreicher
    Abruf ohne neue Songs ist kein Fehler.
    """
    logger.info(t("flashback.run.start"))
    lib = library.build_index(config.MUSIC_DIR)
    state = _load_state()
    seen_state: dict = state["seen"]
    seen_run: set[str] = set()           # innerhalb dieses Laufs (erster Channel gewinnt)
    missing: list[tuple[int, str, str, str]] = []
    channels_with_data = 0
    new_count = 0
    matched_count = 0
    fetch_errors: list[str] = []
    transport_attempts = 0
    transport_failures = 0
    pos = 0

    if config.FLASHBACK_HASH and config.FLASHBACK_CHANNELS:
        for channel in config.FLASHBACK_CHANNELS:
            if new_count >= config.FLASHBACK_LIMIT:
                break
            try:
                played = flashback.fetch_played(
                    channel, config.FLASHBACK_HASH, config.FLASHBACK_PLAYED_LIMIT)
            except FetchError as e:
                fetch_errors.append(f"{channel}: {e}")
                transport_failures += 1
                continue
            finally:
                transport_attempts += 1

            if played:
                channels_with_data += 1
            for artist, title, title_year in played:
                if new_count >= config.FLASHBACK_LIMIT:
                    break
                # Casing kanonisieren BEVOR _key/seen/find_match/missing laufen:
                # FLASHBACK liefert GROSS, Deezer gibt die echte Schreibweise.
                # _key/seen bleiben casing-robust (lowercase), aber der in
                # missing.txt geschriebene Name bestimmt den spaeteren Ordner-/
                # Dateinamen und muss kanonisch sein. Fallback = Rohwert.
                artist, title = deezer.resolve_canonical(artist, title)
                k = _key(artist, title)
                if k in seen_state or k in seen_run:
                    continue
                seen_run.add(k)
                seen_state[k] = datetime.now().strftime("%Y-%m-%d")
                new_count += 1
                yr = year_mod.resolve_year(artist, title, title_year)
                track, _score = lib.find_match(artist, title, config.MATCH_THRESHOLD)
                if track:
                    matched_count += 1
                else:
                    pos += 1
                    missing.append((pos, artist, title, yr))

    _save_state(state)
    if config.WRITE_MISSING and missing:
        write_missing(missing, config.MISSING_FILE)
        write_year_map(missing)   # Jahr separat -> charts-import taggt daraus

    counters = {
        "channels": channels_with_data,
        "new": new_count,
        "matched": matched_count,
        "missing": len(missing),
    }
    suffix = (t("flashback.run.missing_written_suffix")
              if (config.WRITE_MISSING and missing) else "")
    logger.info(t("flashback.run.done",
                  channels=counters["channels"], new=counters["new"],
                  matched=counters["matched"], missing=counters["missing"],
                  suffix=suffix))
    _notify(counters)
    return counters, fetch_errors, transport_attempts, transport_failures


def _prev_status() -> str:
    data = status._read()
    val = data.get("status")
    return val if isinstance(val, str) else "ok"


# Counter-Keys (literal, dashboard-Payload) -> i18n-Key fuer das lesbare Label.
_COUNTER_LABEL_KEYS = {
    "channels": "flashback.counter.channels",
    "new": "flashback.counter.new",
    "matched": "flashback.counter.matched",
    "missing": "flashback.counter.missing",
}


def _base_msg(counters: dict) -> str:
    """Lesbare Status-/Dashboard-Zeile aus den Countern (uebersetztes Label je
    Counter, keine rohen englischen Counter-Keys)."""
    parts = [f"{v} {t(_COUNTER_LABEL_KEYS[k])}"
             for k, v in counters.items()
             if k in _COUNTER_LABEL_KEYS and isinstance(v, (int, float)) and v]
    return ", ".join(parts) if parts else t("flashback.status.nothing_new")


def _notify(counters: dict) -> None:
    new = counters["new"]
    miss = counters["missing"]
    if new == 0:
        logger.info(t("flashback.notify.no_new_songs"))
        return
    if miss > 0:
        head = t("flashback.notify.new_to_load", missing=miss, new=new)
    else:
        head = t("flashback.notify.all_present", new=new)
    notify.send(t("flashback.notify.title", service="charts-flashback"), head)


def _next_run_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z",
                         time.localtime(time.time() + max(1, config.INTERVAL_HOURS) * 3600))


def _alert_fetch_error(message: str, was_error_before: bool) -> None:
    if config.NOTIFY_ONLY_ON_CHANGE and was_error_before:
        logger.info(t("flashback.alert.still_failing"))
        return
    notify.send(t("flashback.notify.title_warning", service="charts-flashback"), message)


def _guarded_run() -> None:
    # Deaktiviert: Container laeuft weiter, aber kein Abruf. Status idle (grau,
    # kein Fehler) statt ok/error. VOR prev_status/status.start()/run_once().
    if not config.FLASHBACK_ENABLED:
        status.start()
        status.finish("idle", t("flashback.status.disabled"), {}, _next_run_iso())
        logger.info(t("flashback.disabled_skip"))
        return
    # Aktiv, aber kein Hash gesetzt -> es gibt nichts abzurufen. Ehrlicher als
    # ein gruenes "ok" (es lief kein echter Abruf): Status idle.
    if not config.FLASHBACK_HASH:
        status.start()
        status.finish("idle", t("flashback.status.no_hash"),
                      {}, _next_run_iso())
        return
    prev_status = _prev_status()
    status.start()
    try:
        counters, fetch_errors, attempts, failures = run_once()
        base = _base_msg(counters)
        err_msg = "; ".join(fetch_errors)

        if attempts == 0:
            status.finish("ok", t("flashback.status.nothing_to_do"),
                          counters, _next_run_iso())
        elif failures == attempts:
            status.finish("error", err_msg, counters, _next_run_iso())
            _alert_fetch_error(err_msg, prev_status == "error")
        elif failures > 0:
            status.finish("ok",
                          t("flashback.status.channel_disrupted", base=base, error=err_msg),
                          counters, _next_run_iso())
            _alert_fetch_error(t("flashback.alert.one_channel_disrupted", error=err_msg),
                               prev_status == "error")
        else:
            status.finish("ok", base, counters, _next_run_iso())
            if prev_status == "error":
                notify.send(t("flashback.notify.title_recovered", service="charts-flashback"),
                            t("flashback.notify.fetch_recovered"))
    except Exception as e:
        logger.exception(t("flashback.run.error", error=e))
        status.finish("error", str(e), {}, _next_run_iso())
        _alert_fetch_error(str(e), prev_status == "error")


def main() -> None:
    status.init("charts-flashback")
    logger.info(t(
        "flashback.scheduler.started",
        enabled=t("flashback.state.yes") if config.FLASHBACK_ENABLED
        else t("flashback.state.no"),
        library=config.MUSIC_DIR,
        hash=t("flashback.state.hash_set") if config.FLASHBACK_HASH
        else t("flashback.state.hash_missing"),
        channels=", ".join(config.FLASHBACK_CHANNELS) or "-",
        musicbrainz=t("flashback.state.on") if config.MB_ENABLED
        else t("flashback.state.off"),
        deezer=t("flashback.state.on") if config.DEEZER_CANONICAL_ENABLED
        else t("flashback.state.off"),
        interval=config.INTERVAL_HOURS,
    ))

    if config.RUN_ON_START:
        _guarded_run()

    interval_seconds = max(1, config.INTERVAL_HOURS) * 3600
    while True:
        logger.info(t("flashback.scheduler.next_run", interval=config.INTERVAL_HOURS))
        time.sleep(interval_seconds)
        _guarded_run()


if __name__ == "__main__":
    main()
