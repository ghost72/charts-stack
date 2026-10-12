"""
Gemeinsame Recanonicalize-Logik fuer GROSS geschriebene flashback-Songs.

Ein GROSS-Name entsteht, wenn Deezer beim Import die kanonische (offizielle)
Schreibweise nicht liefern konnte (Alias-Kuenstler, von FLASHBACK falsch
benannte Titel) ODER die Deezer-Suche gerade nicht erreichbar war. Der Song wird
dann GROSS importiert und zur Pruefung gemerkt.

Dieses Modul bildet den Resolver-Weg einmal ab, damit sowohl das manuelle
Einmal-Skript (scripts/recanonicalize_flashback.py) als auch der automatische
Metadaten-Backfill (app/backfill.py) DENSELBEN konservativen Code nutzen:

  resolve_canonical() liefert nur bei einem SICHEREN Deezer-Treffer eine neue
  Schreibweise - sonst bleibt der Song unveraendert (nie raten). Bei einem
  Treffer werden Library-Ordner/Datei umbenannt, die Tags title/album gesetzt
  (vorhandenes Jahr bleibt) und der Manifest-Eintrag aktualisiert.

Idempotent: bereits korrigierte (Mixed-Case) Eintraege gelten nicht als
GROSS-Verdacht; existiert der Zielordner schon, wird nicht ueberschrieben.
"""

import logging
import os

from mutagen import File as MutagenFile

from . import config, tags, deezer
from .i18n import t

logger = logging.getLogger("charts-import.recanon")

CHOWN_UID, CHOWN_GID = 1027, 100


def is_upper(s: str) -> bool:
    """GROSS-Heuristik: laenger als 3 Zeichen, enthaelt Buchstaben, komplett
    gross. Kurze Akronyme (ABBA, R.E.M., INXS) fallen bewusst NICHT drunter,
    wenn sie <= 3 Zeichen sind - laengere echte Akronyme werden von Deezer
    ohnehin nur dann umbenannt, wenn es eine sichere andere Schreibweise gibt."""
    s = (s or "").strip()
    return bool(s) and len(s) > 3 and any(c.isalpha() for c in s) and s == s.upper()


def needs_fix(meta: dict) -> bool:
    """True, wenn ein Manifest-Eintrag ein GROSS-flashback-Verdachtsfall ist."""
    if (meta.get("source") or "") != "flashback":
        return False
    return is_upper(meta.get("title") or "") or is_upper(meta.get("artist") or "")


def _read_year(path: str) -> str:
    try:
        audio = MutagenFile(path, easy=True)
        if audio is None:
            return ""
        for key in ("date", "originaldate", "year"):
            v = audio.get(key)
            if v:
                s = str(v[0] if isinstance(v, list) else v)
                if len(s) >= 4 and s[:4].isdigit():
                    return s[:4]
    except Exception:
        pass
    return ""


def _chown(path: str) -> None:
    try:
        os.chown(path, CHOWN_UID, CHOWN_GID)
    except OSError:
        pass


def _rename(src: str, dst: str, dry: bool, log) -> bool:
    if src == dst or not os.path.exists(src):
        return False
    casing_only = src.lower() == dst.lower()
    if not casing_only and os.path.exists(dst):
        log(t("import.recanon.target_exists", dst=dst))
        return False
    if dry:
        log(t("import.recanon.dry_rename", src=src, dst=dst))
        return True
    if casing_only:
        tmp = src + ".recanontmp"
        os.rename(src, tmp)
        os.rename(tmp, dst)
    else:
        os.rename(src, dst)
    _chown(dst)
    log(t("import.recanon.rename", src=src, dst=dst))
    return True


def _find_audio(album_dir: str, title: str) -> "str | None":
    if not os.path.isdir(album_dir):
        return None
    for name in os.listdir(album_dir):
        if name == "@eaDir":
            continue
        base, ext = os.path.splitext(name)
        if ext.lower() in config.AUDIO_EXTENSIONS and base.lower() == title.lower():
            return os.path.join(album_dir, name)
    return None


def clear_cache_for(items: "list[dict]") -> int:
    """Invalidiert die Deezer-Cache-Eintraege der uebergebenen Manifest-Metas,
    damit mit dem aktuellen Matching NEU aufgeloest wird (sonst liefert der
    Cache evtl. einen alten GROSS-Miss von vor dem Cache-Fix). Rueckgabe: Anzahl
    entfernter Eintraege."""
    try:
        cache = deezer._load_cache()
    except Exception as e:
        logger.debug(t("import.recanon.cache_unloadable", error=e))
        return 0
    removed = 0
    for meta in items:
        ck = deezer._key(meta.get("artist", ""), meta.get("title", ""))
        if ck in cache:
            del cache[ck]
            removed += 1
    if removed:
        try:
            deezer._save_cache()
        except Exception as e:
            logger.debug(t("import.recanon.cache_save_failed", error=e))
    return removed


def recanonicalize_entry(entries: dict, key: str, meta: dict, music: str,
                         dry_run: bool = False, log=None) -> str:
    """
    Versucht EINEN GROSS-flashback-Manifest-Eintrag auf die offizielle Deezer-
    Schreibweise zu korrigieren.

    Rueckgabe: "fixed" (sicher korrigiert), "manual" (kein sicherer Deezer-
    Treffer -> unveraendert gelassen, muss ggf. manuell geprueft werden) oder
    "noop" (Audiodatei nicht gefunden o.ae.). Aktualisiert bei "fixed" den
    uebergebenen entries-Dict in place (artist/title); der Aufrufer persistiert
    das Manifest.

    log: optionale Funktion log(fmt, *args) (Default: logger.info).
    """
    if log is None:
        def log(fmt, *args):
            logger.info(fmt, *args)

    old_a = (meta.get("artist") or "").strip()
    old_t = (meta.get("title") or "").strip()
    new_a, new_t = deezer.resolve_canonical(old_a, old_t)
    if (new_a, new_t) == (old_a, old_t):
        return "manual"  # kein sicherer Treffer -> NIE raten

    log(t("import.recanon.resolved", old_artist=old_a, old_title=old_t,
          new_artist=new_a, new_title=new_t))

    canon_old_a = tags.canonical_artist(music, old_a) or old_a
    safe_old_a = tags._clean_component(canon_old_a, "Unknown Artist")
    safe_old_t = tags._clean_component(old_t, "track")
    safe_new_a = tags._clean_component(new_a, "Unknown Artist")
    safe_new_t = tags._clean_component(new_t, "track")

    old_artist_dir = os.path.join(music, safe_old_a)
    new_artist_dir = os.path.join(music, safe_new_a)
    if safe_old_a != safe_new_a:
        _rename(old_artist_dir, new_artist_dir, dry_run, log)
    base_artist_dir = new_artist_dir if os.path.isdir(new_artist_dir) else old_artist_dir

    old_album_dir = os.path.join(base_artist_dir, safe_old_t)
    new_album_dir = os.path.join(base_artist_dir, safe_new_t)
    if safe_old_t != safe_new_t:
        _rename(old_album_dir, new_album_dir, dry_run, log)
    target_album_dir = new_album_dir if os.path.isdir(new_album_dir) else old_album_dir

    f = _find_audio(target_album_dir, old_t) or _find_audio(target_album_dir, new_t)
    if not f:
        log(t("import.recanon.audio_not_found", dir=target_album_dir))
        return "noop"

    ext = os.path.splitext(f)[1]
    new_file = os.path.join(target_album_dir, safe_new_t + ext)
    if os.path.basename(f).lower() != os.path.basename(new_file).lower():
        _rename(f, new_file, dry_run, log)
    target_file = new_file if os.path.isfile(new_file) else f

    if not dry_run:
        year = _read_year(target_file)
        tags.write_tags(target_file, new_a, new_t, new_t, year=year or None)
        date_suffix = t("import.recanon.tags_date_suffix", year=year) if year else ""
        log(t("import.recanon.tags_set", title=new_t, date_suffix=date_suffix))
        entries[key]["artist"] = new_a
        entries[key]["title"] = new_t

    return "fixed"
