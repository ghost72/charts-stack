"""
ReplayGain-2.0-Normalisierung (EBU R128) fuer die einheitliche Lautstaerke.

Misst die Lautheit einer MP3 mit ffmpeg (ebur128-Filter) und schreibt den
berechneten Track-Gain als ReplayGain-Tags (TXXX:REPLAYGAIN_TRACK_GAIN und
REPLAYGAIN_TRACK_PEAK) in die Datei. Der Audio-Stream bleibt UNVERAENDERT
(verlustfrei) - ein ReplayGain-faehiger Player (Navidrome, Symfonium, ...)
regelt die Wiedergabe-Lautstaerke anhand dieser Tags.

Braucht ffmpeg im Image (siehe Dockerfile). Faellt still aus, wenn ffmpeg
fehlt oder die Messung scheitert - der Import laeuft dann normal weiter.

Zielwert: -18 LUFS (ReplayGain-2.0-Referenz).
"""

import logging
import re
import shutil
import subprocess

from mutagen.id3 import ID3, TXXX
from mutagen.id3 import ID3NoHeaderError

from . import config
from .i18n import t

logger = logging.getLogger(__name__)

# Zielpegel (LUFS). ReplayGain-2.0-Referenz ist -18; Streaming-Dienste
# (Spotify/YouTube ~-14) sind lauter. Konfigurierbar ueber REPLAYGAIN_TARGET_LUFS.
_TARGET_LUFS = float(config.REPLAYGAIN_TARGET_LUFS)

# ffmpeg ebur128 gibt am Ende eine Zusammenfassung mit "I:   -xx.x LUFS"
# (integrated loudness) und "Peak:  -x.x dBFS" aus.
_I_RE = re.compile(r"I:\s*(-?\d+(?:\.\d+)?)\s*LUFS")
_PEAK_RE = re.compile(r"Peak:\s*(-?\d+(?:\.\d+)?)\s*dBFS")


def available() -> bool:
    """True, wenn ffmpeg im PATH ist."""
    return shutil.which("ffmpeg") is not None


def _measure(path: str) -> tuple[float, float] | None:
    """
    Misst integrierte Lautheit (LUFS) und True-Peak (dBFS) via ffmpeg.
    Gibt (loudness_lufs, peak_dbfs) oder None bei Fehler.
    """
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-i", path,
             "-map", "a:0", "-filter:a", "ebur128=peak=true", "-f", "null", "-"],
            capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.debug(t("import.replaygain.measure_failed", path=path, error=e))
        return None

    out = proc.stderr  # ffmpeg schreibt die Zusammenfassung auf stderr
    # Letzte Vorkommen nehmen (die Summary am Ende).
    i_matches = _I_RE.findall(out)
    peak_matches = _PEAK_RE.findall(out)
    if not i_matches:
        logger.debug(t("import.replaygain.no_lufs", path=path))
        return None
    loudness = float(i_matches[-1])
    peak_dbfs = float(peak_matches[-1]) if peak_matches else 0.0
    return loudness, peak_dbfs


def _has_replaygain(path: str) -> bool:
    """True, wenn bereits ein REPLAYGAIN_TRACK_GAIN-Tag vorhanden ist."""
    try:
        tags = ID3(path)
    except (ID3NoHeaderError, Exception):
        return False
    for k in tags.keys():
        if k.startswith("TXXX:") and "REPLAYGAIN_TRACK_GAIN" in k.upper():
            return True
    return False


def apply_track_gain(path: str, overwrite: bool = False) -> bool:
    """
    Berechnet und schreibt ReplayGain-Track-Tags in die MP3.
    overwrite=False: vorhandene ReplayGain-Tags nicht neu berechnen.
    Rueckgabe: True, wenn Tags geschrieben wurden.
    """
    if not path.lower().endswith(".mp3"):
        return False
    if not available():
        return False
    if _has_replaygain(path) and not overwrite:
        return False

    measured = _measure(path)
    if measured is None:
        return False
    loudness, peak_dbfs = measured
    # Track-Gain = Zielpegel - gemessene Lautheit.
    gain_db = _TARGET_LUFS - loudness
    # Peak als linearer Faktor (ReplayGain-Konvention: 0..1+).
    peak_linear = 10 ** (peak_dbfs / 20.0) if peak_dbfs else 1.0

    try:
        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            tags = ID3()
        # Alte ReplayGain-TXXX entfernen, dann neu setzen.
        for k in list(tags.keys()):
            if k.startswith("TXXX:") and "REPLAYGAIN" in k.upper():
                del tags[k]
        tags.add(TXXX(encoding=3, desc="REPLAYGAIN_TRACK_GAIN",
                      text=[f"{gain_db:.2f} dB"]))
        tags.add(TXXX(encoding=3, desc="REPLAYGAIN_TRACK_PEAK",
                      text=[f"{peak_linear:.6f}"]))
        tags.save(path)
        return True
    except Exception as e:
        logger.debug(t("import.replaygain.write_failed", path=path, error=e))
        return False
