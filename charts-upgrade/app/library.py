"""
Library-Scanner: durchsucht die Navidrome-Library nach Dateien mit schlechter
Qualitaet (niedrige echte Bitrate ODER upgesampelt/gefaelschte Bitrate) und
liefert die noetigen Metadaten (Artist/Titel) fuer die Ersatz-Suche.

WICHTIG - KONSERVATIV:
  - Compilation-/Sampler-Ordner werden komplett UEBERSPRUNGEN (kuratierte Alben
    wie Bravo Hits/Kuschelrock nie track-weise ersetzen). Erkennung ueber die
    Pfad-Heuristik (matching.is_compilation) PLUS "viele verschiedene Kuenstler
    im selben Album-Ordner" (Album-Artist-Streuung).
  - Song-Metadaten primaer aus den Datei-Tags (mutagen), Pfad nur als Fallback.
  - Der Papierkorb (.charts-trash) und versteckte Ordner werden ausgelassen.
"""

import logging
import os

from mutagen import File as MutagenFile

from . import config
from .i18n import t
from .matching import is_compilation, normalize

logger = logging.getLogger(__name__)

AUDIO_EXTENSIONS = {
    ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma", ".wav", ".alac",
}

# Ordner, die nie gescannt werden (Papierkorb, Quarantaene, Synology-Index).
# @eaDir sind Synology-DSM-Index-/Thumbnail-Ordner (enthalten teils Datei-
# Kopien) - die duerfen NIE analysiert oder "verbessert" werden.
_SKIP_DIR_NAMES = {".charts-trash", "_rejected", "@eaDir"}


def read_bitrate(path: str) -> int:
    """Bitrate (kbps) einer Audiodatei, 0 wenn nicht lesbar."""
    try:
        audio = MutagenFile(path)
        if audio is not None and audio.info is not None:
            return int((getattr(audio.info, "bitrate", 0) or 0) / 1000)
    except Exception:
        pass
    return 0


def _read_artist_title(path: str) -> tuple[str, str]:
    """
    Liest (artist, title) primaer aus den Datei-Tags (mutagen). Fallback:
    Kuenstler = Name des Kuenstler-Ordners (music_dir/<Artist>/<Album>/file),
    Titel = Dateiname ohne Endung/fuehrende Tracknummer.
    """
    artist = title = ""
    try:
        audio = MutagenFile(path, easy=True)
        if audio is not None:
            def _first(*keys):
                for k in keys:
                    v = audio.get(k)
                    if v:
                        return (v[0] if isinstance(v, list) else str(v)).strip()
                return ""
            artist = _first("albumartist", "artist")
            title = _first("title")
    except Exception as e:
        logger.debug(t("upgrade.library.tags_unreadable", path=path, error=e))

    if not artist:
        # Fallback: Kuenstler-Ordner (zwei Ebenen ueber der Datei).
        rel = os.path.relpath(path, config.MUSIC_DIR)
        parts = rel.split(os.sep)
        if len(parts) >= 2 and parts[0] not in (".", ".."):
            artist = parts[0]
    if not title:
        import re
        base = os.path.splitext(os.path.basename(path))[0]
        base = re.sub(r"^\s*\d{1,3}\s*[-.\)]\s*", "", base)  # fuehrende Tracknr.
        if " - " in base:
            base = base.rsplit(" - ", 1)[-1]
        title = base.strip()
    return artist.strip(), title.strip()


def _album_is_compilation(album_dir: str, audio_files: list[str]) -> bool:
    """
    True, wenn der Album-Ordner eine Compilation ist:
      - Pfad-Heuristik (matching.is_compilation) trifft, ODER
      - im Ordner streuen die Kuenstler stark (viele verschiedene artist-Tags
        = Sampler). Ein regulaeres Album hat ueberwiegend denselben Kuenstler.
    """
    if is_compilation(album_dir):
        return True

    # Kuenstler-Streuung ueber die Tags messen.
    artists: set[str] = set()
    checked = 0
    for f in audio_files:
        try:
            audio = MutagenFile(f, easy=True)
            if audio is None:
                continue
            a = audio.get("albumartist") or audio.get("artist")
            if a:
                artists.add(normalize(a[0] if isinstance(a, list) else str(a)))
            checked += 1
        except Exception:
            continue
        if checked >= 12:  # Stichprobe reicht
            break
    # Ein "Various Artists"-Tag oder >3 verschiedene Kuenstler im Album =
    # Compilation. Regulaere Alben haben 1 (ggf. 2 mit Gast) Album-Artists.
    if any(a in ("various artists", "various", "va", "verschiedene") for a in artists):
        return True
    return len(artists) > 3


def count_candidates() -> int:
    """
    Zaehlt, wie viele MP3-Dateien charts-upgrade ueberhaupt pruefen WUERDE -
    also die Gesamtmenge, gegen die sich der Scan-Fortschritt bemisst. Nutzt
    EXAKT dieselben Skip-Regeln wie scan() (versteckte/Sonder-Ordner raus,
    Compilation-Ordner raus, nur .mp3), damit 'geprueft X / Y' stimmig ist.

    Billiger als scan(): nur os.walk + Endungs-Check, KEINE ffmpeg-Analyse.
    """
    if not os.path.isdir(config.MUSIC_DIR):
        return 0
    total = 0
    for dirpath, dirnames, filenames in os.walk(config.MUSIC_DIR):
        dirnames[:] = [d for d in dirnames
                       if d not in _SKIP_DIR_NAMES and not d.startswith(".")]
        audio_files = [os.path.join(dirpath, n) for n in filenames
                       if os.path.splitext(n)[1].lower() in AUDIO_EXTENSIONS]
        if not audio_files:
            continue
        if config.SKIP_COMPILATIONS and _album_is_compilation(dirpath, audio_files):
            continue
        total += sum(1 for p in audio_files
                     if os.path.splitext(p)[1].lower() == ".mp3")
    return total


def _is_bad(path: str) -> tuple[bool, str, int, int, float | None]:
    """
    Beurteilt eine einzelne Datei.
    Rueckgabe: (ist_schlecht, grund, bitrate, quality_key_int, cutoff_khz_or_None)
      grund: "" wenn ok, sonst "niedrige Bitrate (Xkbps)" oder
             "upgesampelt (echter Cutoff YkHz)".
    """
    from . import quality

    bitrate = read_bitrate(path)

    # 1. Niedrige echte Bitrate.
    if 0 < bitrate < config.BAD_BITRATE:
        return True, t("upgrade.reason.low_bitrate", bitrate=bitrate), bitrate, bitrate, None

    # 2. Upsampling (behauptet hohe Bitrate, echter Cutoff zu niedrig).
    if config.CHECK_UPSAMPLED:
        upsampled, measured, expected = quality.is_upsampled(
            path, bitrate,
            tolerance_khz=config.CUTOFF_TOLERANCE_KHZ,
            min_check_bitrate=config.REJECT_UPSAMPLED_MIN_BITRATE,
        )
        if upsampled:
            return (True,
                    t("upgrade.reason.upsampled", bitrate=bitrate,
                      measured=measured, expected=expected),
                    bitrate, int((measured or 0) * 1000 + bitrate), measured)

    return False, "", bitrate, bitrate, None


def scan(state_seen: dict, max_scan: int):
    """
    Durchsucht MUSIC_DIR und liefert schlechte Songs.

    state_seen: dict {abspath: "mtime:size"} - bereits analysierte Dateien
      (Cache), damit die teure ffmpeg-Analyse pro Datei nur einmal laeuft.
    max_scan: max. Anzahl NEUER/geaenderter Dateien, die in diesem Lauf
      analysiert werden (CPU-Schutz).

    Rueckgabe: (bad_songs, scanned_count, skipped_compilations)
      bad_songs: Liste von dicts {path, artist, title, reason, bitrate,
                 quality_key, cutoff}.
    Der Cache (state_seen) wird in-place um die neu analysierten Dateien
    ergaenzt (auch die guten, damit sie nicht erneut geprueft werden).
    """
    if not os.path.isdir(config.MUSIC_DIR):
        logger.warning(t("upgrade.library.music_dir_missing", dir=config.MUSIC_DIR))
        return [], 0, 0

    bad_songs: list[dict] = []
    scanned = 0
    skipped_comp = 0

    for dirpath, dirnames, filenames in os.walk(config.MUSIC_DIR):
        # Versteckte / Sonderordner ueberspringen.
        dirnames[:] = [d for d in dirnames
                       if d not in _SKIP_DIR_NAMES and not d.startswith(".")]

        audio_files = [os.path.join(dirpath, n) for n in filenames
                       if os.path.splitext(n)[1].lower() in AUDIO_EXTENSIONS]
        if not audio_files:
            continue

        # Compilation-Ordner komplett ueberspringen.
        if config.SKIP_COMPILATIONS and _album_is_compilation(dirpath, audio_files):
            skipped_comp += len(audio_files)
            logger.debug(t("upgrade.library.compilation_skipped", dir=dirpath))
            continue

        for path in audio_files:
            # Nur MP3 sinnvoll pruefen (Cutoff/Bitrate-Logik ist MP3-orientiert;
            # FLAC/verlustfrei ist per Definition kein Upgrade-Kandidat).
            if os.path.splitext(path)[1].lower() != ".mp3":
                continue

            # Cache: schon analysiert (unveraendert)? Dann ueberspringen.
            try:
                stt = os.stat(path)
                sig = f"{int(stt.st_mtime)}:{stt.st_size}"
            except OSError:
                continue
            if state_seen.get(path) == sig:
                continue

            if scanned >= max_scan:
                # Kontingent fuer diesen Lauf erschoepft - Rest im naechsten Lauf.
                return bad_songs, scanned, skipped_comp

            scanned += 1
            is_bad, reason, bitrate, qkey, cutoff = _is_bad(path)
            # Cache aktualisieren (auch gute Dateien merken).
            state_seen[path] = sig

            if is_bad:
                artist, title = _read_artist_title(path)
                if not artist or not title:
                    logger.debug(t("upgrade.library.no_artist_title", path=path))
                    continue
                bad_songs.append({
                    "path": path,
                    "artist": artist,
                    "title": title,
                    "reason": reason,
                    "bitrate": bitrate,
                    "quality_key": qkey,
                    "cutoff": cutoff,
                })

    return bad_songs, scanned, skipped_comp
