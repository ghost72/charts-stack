"""
Import-Logik: sortiert fertige slskd-Downloads in die Navidrome-Library ein.

Ablauf:
  1. DOWNLOADS_DIR rekursiv nach Audiodateien scannen.
  2. Nur Dateien beruecksichtigen, die "stabil" sind (seit MIN_AGE_SECONDS nicht
     mehr veraendert) - so werden halbfertige Downloads nicht einsortiert.
  3. Zielpfad Music/<Artist>/<Album>/<Track>.<ext> aus den Tags bilden.
  4. Datei kopieren (COPY_MODE=1) oder verschieben (COPY_MODE=0).
     Beim Kopieren bleibt das Original im Downloads-Ordner, damit slskd es
     eine Weile weiterteilen kann (Seeden).
  5. Bereits vorhandene Ziele werden nicht ueberschrieben (idempotent).
"""

import json
import logging
import os
import re
import shutil
import time

from mutagen import File as MutagenFile

from . import config, cover, quality, replaygain, tags
from .i18n import t

logger = logging.getLogger(__name__)

# charts-flashback-Jahr-Map (artist|title -> Jahr). charts-flashback schreibt
# sie (siehe dessen main.write_year_map); wir lesen sie hier, um das korrekte
# Erscheinungsjahr als Tag zu setzen, OHNE es je im Datei-/Ordnernamen zu haben.
# Pfad: im geteilten Charts-Ordner. Einmal pro Lauf lazy geladen.
_YEAR_MAP_FILE = os.path.join(config.DOWNLOADS_DIR, ".charts-flashback-years.json")
_year_map: dict | None = None

# Import-Log (JSON Lines) fuer die Tageszusammenfassung (charts-digest). Pro
# wirklich einsortiertem Song eine Zeile {ts, source, artist, title}. Append-
# only; charts-digest liest es und filtert aufs Zeitfenster. Liegt im geteilten
# Charts-Ordner, den auch charts-digest sieht.
_IMPORT_LOG_FILE = os.path.join(config.DOWNLOADS_DIR, ".charts-imported-log.jsonl")


def _append_import_log(source: str, artist: str, title: str) -> None:
    """Haengt EINEN Import-Eintrag ans JSONL-Log an. Fehler sind nicht fatal."""
    try:
        rec = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "source": source or "",
            "artist": artist or "",
            "title": title or "",
        }
        with open(_IMPORT_LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.debug(t("import.log.write_failed", path=_IMPORT_LOG_FILE, error=e))


def _load_year_map() -> dict:
    global _year_map
    if _year_map is not None:
        return _year_map
    import json
    try:
        with open(_YEAR_MAP_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        _year_map = data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        _year_map = {}
    return _year_map


def _flashback_year(artist: str, title: str) -> str:
    """Jahr aus der charts-flashback-Jahr-Map (key = lowercase 'artist|title').
    Leer, wenn der Song nicht aus flashback stammt / kein Jahr bekannt ist."""
    key = f"{(artist or '').lower()}|{(title or '').lower()}"
    val = _load_year_map().get(key, "")
    return str(val) if val else ""


def _iter_audio_files(root: str):
    """
    Liefert alle Audiodateien unterhalb von root (absolute Pfade).
    Der Quarantaene-Ordner (REJECTED_DIR) wird ausgeschlossen, damit abgelehnte
    Dateien nicht immer wieder geprueft werden.
    """
    rejected_abs = os.path.abspath(config.REJECTED_DIR)
    for dirpath, dirnames, filenames in os.walk(root):
        # _rejected nicht betreten
        if os.path.abspath(dirpath).startswith(rejected_abs):
            dirnames[:] = []
            continue
        for name in filenames:
            ext = os.path.splitext(name)[1].lower()
            if ext in config.AUDIO_EXTENSIONS:
                yield os.path.join(dirpath, name)


def _rejection_reason(src: str, artist: str) -> str | None:
    """
    Prueft, ob eine Datei abgelehnt werden soll. Gibt den Grund zurueck
    (fuer Logging) oder None, wenn sie in Ordnung ist.
    """
    if not config.REJECT_ENABLED:
        return None

    name = os.path.basename(src.replace("\\", "/")).lower()
    # Klammern/Zeichen zu Leerzeichen, damit "(lyrics)" als "lyrics" matcht.
    cleaned = f" {re.sub(r'[^a-z0-9]+', ' ', name)} "
    for kw in config.REJECT_KEYWORDS:
        if f" {kw.lower().strip()} " in cleaned:
            return t("import.reject.keyword", keyword=kw.strip())

    if config.REJECT_UNKNOWN_ARTIST:
        a = (artist or "").strip().lower()
        if not a or a == "unknown artist":
            return t("import.reject.no_artist")

    # Zu kurze Dateien = abgebrochene/unvollstaendige Downloads (z.B. 24s-
    # Fragment). Chart-Singles sind praktisch immer laenger. Wird abgelehnt und
    # (ueber die rejected-Liste) im charts-download-State freigegeben, damit der
    # Song neu gesucht wird, bis eine vollstaendige Version kommt.
    if config.MIN_DURATION_SECONDS > 0:
        dur = _duration_seconds(src)
        if dur is not None and dur < config.MIN_DURATION_SECONDS:
            # Kein '<' verwenden: Die Pushover-Meldung wird als HTML gesendet,
            # ein '<' wuerde als Tag-Anfang interpretiert und der Rest der Zeile
            # verschluckt. Daher "min" statt "<".
            return t("import.reject.too_short", duration=f"{dur:.0f}",
                     min=config.MIN_DURATION_SECONDS)

    # Upsampling-/Fake-Bitrate-Pruefung (Frequenz-Cutoff). Konservativ: greift
    # nur bei hoher BEHAUPTETER Bitrate und deutlich zu niedrigem echten Cutoff.
    # Nicht messbar -> kein Reject (quality.is_upsampled gibt dann False).
    if config.REJECT_UPSAMPLED:
        claimed = _read_bitrate(src)
        upsampled, measured, expected = quality.is_upsampled(
            src, claimed,
            tolerance_khz=config.CUTOFF_TOLERANCE_KHZ,
            min_check_bitrate=config.REJECT_UPSAMPLED_MIN_BITRATE,
        )
        if upsampled:
            # Kein '<'/'>' im Grund (Pushover-HTML). measured/expected in kHz.
            return t("import.reject.upsampled", claimed=claimed,
                     measured=f"{measured:.1f}", expected=f"{expected:.0f}")

    return None


def _duration_seconds(path: str) -> float | None:
    """Laenge der Audiodatei in Sekunden, oder None wenn nicht lesbar."""
    try:
        audio = MutagenFile(path)
        if audio is not None and audio.info is not None:
            return float(audio.info.length)
    except Exception:
        pass
    return None


def _quarantine(src: str) -> None:
    """Verschiebt eine abgelehnte Datei nach REJECTED_DIR (flach, nur Dateiname)."""
    try:
        os.makedirs(config.REJECTED_DIR, exist_ok=True)
        target = _unique_target(os.path.join(config.REJECTED_DIR, os.path.basename(src)))
        shutil.move(src, target)
    except OSError as e:
        logger.error(t("import.quarantine.move_failed", path=src, error=e))


def _is_stable(path: str, min_age_seconds: int) -> bool:
    """True, wenn die Datei seit min_age_seconds nicht mehr veraendert wurde."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return False
    return (time.time() - mtime) >= min_age_seconds


def _read_bitrate(path: str) -> int:
    """Bitrate (kbps) einer Audiodatei, 0 wenn nicht lesbar. Fuer Upgrade-Vergleich."""
    try:
        audio = MutagenFile(path)
        if audio is not None and audio.info is not None:
            return int((getattr(audio.info, "bitrate", 0) or 0) / 1000)
    except Exception:
        pass
    return 0


def _trash(path: str) -> bool:
    """
    Verschiebt eine ersetzte (schlechtere) Datei in einen datierten Papierkorb
    unter MUSIC_DIR/.charts-trash/<YYYYMMDD>/ statt sie hart zu loeschen.
    Reversibel - der Papierkorb kann spaeter manuell geleert werden.
    Gibt True bei Erfolg zurueck.
    """
    try:
        day = time.strftime("%Y%m%d")
        trash_dir = os.path.join(config.MUSIC_DIR, ".charts-trash", day)
        os.makedirs(trash_dir, exist_ok=True)
        dst = _unique_target(os.path.join(trash_dir, os.path.basename(path)))
        shutil.move(path, dst)
        logger.info(t("import.trash.moved", path=os.path.relpath(dst, config.MUSIC_DIR)))
        return True
    except OSError as e:
        logger.error(t("import.trash.move_failed", path=path, error=e))
        return False


def _unique_target(target: str) -> str:
    """
    Falls das Ziel schon existiert, wird geprueft, ob es dieselbe Datei ist.
    Ist es eine andere Datei gleichen Namens, wird ein Zaehler-Suffix angehaengt.
    """
    if not os.path.exists(target):
        return target
    base, ext = os.path.splitext(target)
    counter = 2
    while os.path.exists(f"{base} ({counter}){ext}"):
        counter += 1
    return f"{base} ({counter}){ext}"


def _same_file(a: str, b: str) -> bool:
    """Grober Gleichheits-Check ueber Groesse (guenstig, ausreichend fuer Dedup)."""
    try:
        return os.path.getsize(a) == os.path.getsize(b)
    except OSError:
        return False


def _track_key(filename: str) -> str:
    """
    Normalisierter Track-Name fuer den Duplikat-Vergleich.
    Entfernt Endung, (n)-Duplikat-Suffixe und slskd-typische numerische
    Suffixe (_639241038209671901 = Snowflake-ID), damit derselbe Song
    unabhaengig vom Quell-Dateinamen erkannt wird. Beispiele:
      'Golden (2).mp3'                       -> 'golden'
      'Stargazing_639241038209671901.mp3'    -> 'stargazing'
      'Myles Smith - Stargazing.flac'        -> 'myles smith - stargazing'
    """
    base = os.path.splitext(os.path.basename(filename))[0]
    base = re.sub(r"\s*\(\d+\)\s*$", "", base)   # (2), (3) ...
    base = re.sub(r"_\d{6,}$", "", base)          # _<lange Zahl> (slskd-Suffix)
    return base.strip().lower()


def _song_already_present(target: str) -> bool:
    """
    True, wenn im Ziel-Album-Ordner bereits eine Datei mit demselben
    Track-Namen liegt (unabhaengig von (n)-Suffix und Endung). Verhindert
    Mehrfach-Importe desselben Songs von verschiedenen Anbietern IN DENSELBEN
    Ordner. Fuer die albumuebergreifende Pruefung siehe _song_present_for_artist.
    """
    target_dir = os.path.dirname(target)
    if not os.path.isdir(target_dir):
        return False
    want = _track_key(target)
    try:
        for name in os.listdir(target_dir):
            ext = os.path.splitext(name)[1].lower()
            if ext in config.AUDIO_EXTENSIONS and _track_key(name) == want:
                return True
    except OSError:
        pass
    return False


def _title_key(title: str) -> str:
    """
    Normalisiert einen SONGTITEL fuer den albumuebergreifenden Vergleich:
    Kleinschreibung, (feat.../remaster/version/edit)-Zusaetze raus, nur
    Buchstaben/Ziffern. So matcht 'Into You' gegen 'Into You (Remastered)'.
    """
    t = (title or "").lower()
    t = re.sub(r"\((?:feat|ft|with)\.?[^)]*\)", " ", t)
    t = re.sub(r"\[(?:feat|ft|with)\.?[^\]]*\]", " ", t)
    t = re.sub(r"\((?:remaster|remastered|.*version|.*edit|official|audio|video|lyric[s]?)[^)]*\)", " ", t)
    return re.sub(r"[^a-z0-9]+", "", t)


def _extract_title_from_filename(filename: str) -> str:
    """
    Holt den Songtitel aus einem Dateinamen. Behandelt beide Schemata:
      'Into You.mp3'                                  -> 'Into You'
      'Artist - Album - 01 - Into You.mp3'            -> 'Into You'
      '01 - Into You.mp3'                             -> 'Into You'
    Nimmt bei ' - '-getrennten Namen den LETZTEN Teil (der Titel steht bei
    Lidarr-Benennung hinten), sonst den bereinigten ganzen Namen.
    """
    base = os.path.splitext(os.path.basename(filename))[0]
    base = re.sub(r"\s*\(\d+\)\s*$", "", base)   # (2), (3)
    base = re.sub(r"_\d{6,}$", "", base)          # slskd-Suffix
    if " - " in base:
        base = base.rsplit(" - ", 1)[-1]
    base = re.sub(r"^\s*\d{1,3}\s*[-.\)]\s*", "", base)  # fuehrende Tracknr.
    return base.strip()


def _song_present_for_artist(music_dir: str, artist: str, title: str) -> str | None:
    """
    True (als Pfad), wenn IRGENDWO unter Music/<Artist>/ bereits derselbe Song
    liegt - egal in welchem Album-Ordner. Deckt den Fall ab, dass Lidarr den
    Song als Album-Track (z.B. 'Into You (2016)/Artist - Into You - 01 - Into
    You.mp3') fuehrt, waehrend charts-download ihn als Single ('Into You/Into
    You.mp3') laedt. Ohne diese Pruefung entstuenden zwei Album-Ordner (Dublette).

    Vergleicht ueber den normalisierten SONGTITEL (_title_key), damit die
    unterschiedlichen Datei-/Album-Benennungen keine Rolle spielen. Der
    Kuenstler-Ordner wird ueber den bereinigten Artist-Namen bestimmt.
    Rueckgabe: Pfad der bereits vorhandenen Datei, sonst None.
    """
    from .tags import _clean_component  # lokal, um Zirkularimport zu vermeiden
    safe_artist = _clean_component(artist, "Unknown Artist")
    artist_dir = os.path.join(music_dir, safe_artist)
    if not os.path.isdir(artist_dir):
        return None
    want = _title_key(title)
    if not want:
        return None
    try:
        for album in os.listdir(artist_dir):
            album_path = os.path.join(artist_dir, album)
            if not os.path.isdir(album_path):
                continue
            for name in os.listdir(album_path):
                ext = os.path.splitext(name)[1].lower()
                if ext not in config.AUDIO_EXTENSIONS:
                    continue
                if _title_key(_extract_title_from_filename(name)) == want:
                    return os.path.join(album_path, name)
    except OSError:
        pass
    return None


def import_downloads() -> "tuple[list[tuple[str, str, str, str, str, str]], list[dict], list[tuple[str, str]]]":
    """
    Fuehrt einen Import-Lauf durch.
    Rueckgabe: (importierte Songs als Tupel
    (zielpfad, source, artist, title, album, notify_device), abgelehnte
    Eintraege als Dicts mit path/artist/title/reason). "source" ist das
    Playlist-Label aus dem Manifest (z.B. "charts", "mundart"), leer wenn
    unbekannt. "notify_device" ist nur bei manuell via charts-songrequest
    angeforderten Songs gesetzt (Pushover-Geraet des Users), sonst leer.
    """
    if not os.path.isdir(config.DOWNLOADS_DIR):
        logger.warning(t("import.scan.no_downloads_dir", path=config.DOWNLOADS_DIR))
        return [], []

    # Manifest zu Beginn jedes Laufs frisch laden (charts-download aktualisiert
    # es zwischen den Laeufen).
    tags.reload_manifest()

    imported: list[tuple[str, str]] = []
    rejected: list[dict] = []
    flagged_upper: list[tuple[str, str]] = []   # GROSS, von Deezer nicht aufloesbar
    skipped_no_manifest = 0
    for src in _iter_audio_files(config.DOWNLOADS_DIR):
        if not _is_stable(src, config.MIN_AGE_SECONDS):
            logger.debug(t("import.scan.not_stable", path=src))
            continue

        target, meta = tags.build_target_path(config.MUSIC_DIR, src)
        artist, title, album, from_manifest, unresolved_upper = meta

        # Upgrade-Wunsch? charts-songrequest setzt "replace": true im Manifest,
        # wenn eine bessere Version einer bereits vorhandenen Datei geladen wurde.
        _m = tags.manifest_lookup(src)
        want_replace = bool(_m and _m.get("replace"))
        is_upgrade = False   # wird True, wenn eine vorhandene Datei ersetzt wird

        # Nur gezielt von charts-download angeforderte Songs importieren
        # (REQUIRE_MANIFEST). Der Downloads-Ordner enthaelt oft ganze Sampler-/
        # Album-Ordner, die beim Song-Download von Soulseek mitkamen. Ohne diese
        # Sperre wuerde charts-import daraus wahllos Tracks einsortieren (z.B.
        # Compilation-Versionen) - und zwar bei JEDEM Lauf erneut, solange die
        # Quelldateien im (geteilten) Downloads-Ordner liegen. Mit der Sperre
        # zaehlt nur, was im Manifest steht (= von charts-download geladen).
        if config.REQUIRE_MANIFEST and not from_manifest:
            skipped_no_manifest += 1
            logger.debug(t("import.scan.no_manifest_entry", path=src))
            continue

        # Tuersteher: verdaechtige/unbrauchbare Dateien nicht einsortieren,
        # sondern in Quarantaene verschieben.
        reason = _rejection_reason(src, artist)
        if reason:
            logger.info(t("import.scan.rejected", reason=reason, path=src))
            _quarantine(src)
            # Chart-Metadaten (aus dem Manifest) mitgeben, damit der Song im
            # charts-download-State freigegeben und neu (besser) geladen wird.
            m = tags.manifest_lookup(src)
            rejected.append({
                "path": src,
                "artist": (m or {}).get("artist", artist),
                "title": (m or {}).get("title", title),
                "reason": reason,
            })
            continue

        # Schon in der Library?
        # Wichtig gegen (2)/(3)/(4)-Duplikate: Derselbe Song wird oft von
        # mehreren Anbietern mit UNTERSCHIEDLICHEN Dateinamen geladen, wuerde
        # aber im selben Ziel-Album-Ordner landen. Wir pruefen, ob dort schon
        # eine Datei mit demselben Track-Namen liegt (ignoriert (n)-Suffixe und
        # Dateiendung), und ueberspringen dann - egal wie die Quelldatei heisst.
        # Albumuebergreifende Pruefung: Liegt derselbe Song schon irgendwo beim
        # Kuenstler (Single ODER Lidarr-Album-Track)? Diese Datei ist die
        # "vorhandene" - Kandidat fuer ein Upgrade oder Grund zum Ueberspringen.
        existing = _song_present_for_artist(config.MUSIC_DIR, artist, title)
        # Falls im ZIEL-Ordner selbst schon dieselbe da ist, diesen Pfad nehmen.
        if not existing and _song_already_present(target):
            existing = target  # markiert "vorhanden" (Pfad nur symbolisch)

        if existing and os.path.isfile(existing):
            old_br = _read_bitrate(existing)
            new_br = _read_bitrate(src)
            # Qualitaetsvergleich ueber den ECHTEN Frequenz-Cutoff (nicht nur
            # die evtl. gefaelschte Bitrate): quality_key laesst den gemessenen
            # Cutoff dominieren, die Bitrate ist nur Tie-Breaker. So wird ein
            # upgesampelter "320er" NICHT als besser eingestuft als eine ehrliche
            # Datei - und ein echtes Upgrade (hoeherer Cutoff) wird erkannt,
            # selbst wenn beide dieselbe Bitrate behaupten.
            old_q = quality.quality_key(existing, old_br)
            new_q = quality.quality_key(src, new_br)
            if want_replace and new_q > old_q:
                # QUALITAETS-UPGRADE: alte Version in Papierkorb, neue an DEREN
                # Stelle (gleicher Ordner/Name), damit keine Dublette entsteht.
                logger.info(t("import.upgrade.replacing", artist=artist, title=title,
                              old_br=old_br, old_q=f"{old_q:.0f}", new_br=new_br,
                              new_q=f"{new_q:.0f}",
                              path=os.path.relpath(existing, config.MUSIC_DIR)))
                target = existing            # an denselben Ort schreiben
                _trash(existing)             # alte Version wegsichern
                is_upgrade = True            # _unique_target ueberspringen
                # weiter unten wird die neue Datei nach `target` kopiert
            else:
                if want_replace:
                    logger.info(t("import.upgrade.not_better", artist=artist,
                                  title=title, old_q=f"{old_q:.0f}",
                                  new_q=f"{new_q:.0f}"))
                else:
                    logger.info(t("import.upgrade.already_present", artist=artist,
                                  title=title,
                                  path=os.path.relpath(existing, config.MUSIC_DIR)))
                continue
        elif _song_already_present(target):
            logger.debug(t("import.upgrade.already_in_library", path=src))
            continue

        # Beim Upgrade an DENSELBEN Ort schreiben (nicht "(2)" anhaengen).
        if not is_upgrade:
            target = _unique_target(target)
        os.makedirs(os.path.dirname(target), exist_ok=True)

        try:
            if config.COPY_MODE:
                shutil.copy2(src, target)
                logger.info(t("import.file.copied", src=src, target=target))
            else:
                shutil.move(src, target)
                logger.info(t("import.file.moved", src=src, target=target))
            # Bei Manifest-Treffer: saubere Chart-Tags in die Zieldatei
            # schreiben, damit Navidrome/charts-sync korrekte Werte sehen.
            if from_manifest:
                # Jahr NICHT aus dem Dateinamen/Titel (das verseuchte frueher
                # Pfad+Name) - sondern aus der charts-flashback-Jahr-Map, die
                # artist|title -> Jahr abbildet. So bleibt der Dateiname sauber
                # (Lidarr-Stil) und nur das Tag traegt das Jahr.
                year = _flashback_year(artist, title)
                tags.write_tags(target, artist, title, album, year=year)
                logger.info(t("import.file.tags_set", artist=artist, title=title,
                              album=album))
                # Passendes Cover ueber COVER_SOURCES (iTunes->Deezer) suchen und
                # einbetten. Bei Chart-Songs (Manifest) das Single-Cover bevorzugen
                # und ein evtl. vorhandenes Compilation-/Sampler-Cover ersetzen
                # (steuerbar ueber OVERWRITE_COVER).
                if cover.embed_cover(target, artist, title,
                                     overwrite=config.OVERWRITE_COVER):
                    logger.info(t("import.file.cover_embedded", artist=artist, title=title))
                # Lautstaerke-Normalisierung: ReplayGain-Track-Tag berechnen und
                # einbetten (verlustfrei; ein RG-faehiger Player regelt die
                # Wiedergabe). Nur wenn REPLAYGAIN aktiv und ffmpeg vorhanden.
                if config.REPLAYGAIN and replaygain.apply_track_gain(target):
                    logger.info(t("import.file.replaygain_set", artist=artist, title=title))
            # Playlist-Herkunft + optionales Pushover-Zielgeraet aus dem Manifest.
            # notify_device ist nur bei manuell via charts-songrequest
            # angeforderten Songs gesetzt -> dann meldet main.py die
            # Verfuegbarkeit an genau dieses Geraet.
            m = tags.manifest_lookup(src)
            source = (m or {}).get("source", "") if m else ""
            notify_device = (m or {}).get("notify_device", "") if m else ""
            imported.append((target, source, artist, title, album, notify_device))
            # Vertrauenswuerdiges Import-Log fuer charts-digest: pro WIRKLICH
            # einsortiertem Song eine Zeile (Zeit + source + artist/title). So
            # muss die Tageszusammenfassung nicht ueber mtime/Titel raten -
            # charts-import weiss hier exakt, was mit welcher Quelle reinkam.
            _append_import_log(source, artist, title)
            # GROSS-Name, den Deezer nicht sicher aufloesen konnte (Alias-
            # Kuenstler / von FLASHBACK falsch benannter Titel): trotzdem
            # importiert (keine Luecke), aber zur manuellen Pruefung vormerken.
            if unresolved_upper:
                flagged_upper.append((artist, title))
                logger.info(t("import.file.flagged_upper", artist=artist, title=title))
        except Exception as e:
            logger.error(t("import.file.failed", path=src, error=e))

    if skipped_no_manifest:
        logger.info(t("import.scan.skipped_no_manifest", count=skipped_no_manifest))

    return imported, rejected, flagged_upper
