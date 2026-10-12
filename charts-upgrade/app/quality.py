"""
Audio-Qualitaetsanalyse: erkennt upgesampelte / gefaelschte MP3s ueber den
tatsaechlichen Frequenz-Cutoff, gemessen per FFT (ffmpeg PCM-Decode + numpy).

KOPIE aus charts-import (bewusst dupliziert, keine Laufzeit-Abhaengigkeit).
Aenderungen hier mit charts-import abgleichen.

HINTERGRUND
-----------
Die im Datei-Tag angegebene Bitrate luegt haeufig: Eine 128-kbps-Quelle wird
auf "320 kbps" hochtranskodiert (Upsampling). Der MP3-Encoder wirft beim
Kodieren oberhalb einer bitratenabhaengigen Grenze alles weg (Lowpass):
    128 kbps -> ~16 kHz, 192 -> ~19 kHz, 256/320 -> ~20 kHz.
Der hochtranskodierte "320er" behaelt die ~16-kHz-Wand der 128er-Quelle.

MESSMETHODE (WICHTIG - korrekt statt spectral_rolloff!)
-------------------------------------------------------
spectral_rolloff (Energie-Schwerpunkt) ist HIER FALSCH: bei bass-/mittenlastiger
Musik liegt der Median-Rolloff naturgemaess bei 5-10 kHz, egal ob oben noch
Inhalt ist. Der ECHTE Cutoff ist die HOECHSTE Frequenz mit nennenswerter
Energie. Deshalb:
  1. ffmpeg dekodiert die Datei zu Mono-PCM (f32le).
  2. In 2-Sekunden-Bloecken FFT (numpy.rfft), Spektrum in dBFS relativ zum Peak.
  3. Von Nyquist nach UNTEN suchen, bis ein Frequenzband ueber dem Rauschboden
     (NOISE_FLOOR_DB) auftaucht -> das ist der Cutoff dieses Blocks.
  4. Ueber alle Bloecke das MAXIMUM nehmen (der lauteste/hoechste Moment zaehlt;
     leise/bass-lastige Passagen druecken den Wert nicht faelschlich).

KONSERVATIV BY DESIGN
---------------------
  - Nicht messbar (ffmpeg/numpy fehlt, Fehler, Stille) -> None -> nie ablehnen.
  - Nur wenn der gemessene Cutoff DEUTLICH unter der Bitraten-Erwartung liegt
    (Toleranz), gilt die Datei als upgesampelt.

Braucht ffmpeg + numpy im Image.
"""

import logging
import shutil
import subprocess

from .i18n import t

logger = logging.getLogger(__name__)

# Erwarteter Mindest-Cutoff (kHz) je nach ANGEGEBENER MP3-Bitrate (kbps).
_EXPECTED_CUTOFF_BY_BITRATE = [
    (320, 19.0),
    (256, 18.0),
    (192, 17.0),
    (160, 15.5),
    (128, 15.0),
    (0,   0.0),
]

# Rauschboden (dBFS relativ zum Peak): Frequenzen darueber gelten als "Inhalt".
_NOISE_FLOOR_DB = -80.0
# Ein Band gilt als "Inhalt", wenn mind. dieser Anteil seiner Bins ueber dem
# Rauschboden liegt.
_BAND_CONTENT_RATIO = 0.5
# Bandbreite (FFT-Bins) fuer die Cutoff-Suche von oben.
_BAND_SIZE = 50
# Analyse-Blockgroesse in Sekunden.
_CHUNK_SECONDS = 2
# Max. Sekunden Audio, die dekodiert werden (CPU-Schutz: Anfang reicht fuer die
# Cutoff-Bestimmung; ganze Datei waere Verschwendung).
_MAX_DECODE_SECONDS = 60


def available() -> bool:
    """True, wenn ffmpeg im PATH ist UND numpy importierbar."""
    if shutil.which("ffmpeg") is None:
        return False
    try:
        import numpy  # noqa: F401
        return True
    except ImportError:
        return False


def _expected_cutoff_khz(bitrate_kbps: int) -> float:
    for min_br, cutoff in _EXPECTED_CUTOFF_BY_BITRATE:
        if bitrate_kbps >= min_br:
            return cutoff
    return 0.0


def _sample_rate(path: str) -> int | None:
    """Liest die Sample-Rate via ffprobe (0/None wenn nicht lesbar)."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=sample_rate",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=30,
        ).stdout.strip()
        return int(out) if out else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None


def measure_cutoff_khz(path: str, timeout: int = 120) -> float | None:
    """
    Misst den echten Frequenz-Cutoff (kHz): die hoechste Frequenz mit
    nennenswerter Energie (FFT, von Nyquist nach unten). None, wenn nicht
    messbar (ffmpeg/numpy fehlt, Decode-Fehler, Stille).
    """
    if not available():
        return None
    try:
        import numpy as np
    except ImportError:
        return None

    sr = _sample_rate(path)
    if not sr or sr <= 0:
        return None

    # Datei zu Mono-PCM float32 dekodieren (nur die ersten _MAX_DECODE_SECONDS).
    try:
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-t", str(_MAX_DECODE_SECONDS),
             "-i", path, "-ac", "1", "-f", "f32le", "-"],
            capture_output=True, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.debug(t("upgrade.quality.decode_failed", path=path, error=e))
        return None
    if proc.returncode != 0 or not proc.stdout:
        return None

    audio = np.frombuffer(proc.stdout, dtype=np.float32)
    if audio.size == 0:
        return None

    chunk_size = sr * _CHUNK_SECONDS
    bin_res = sr / chunk_size  # Hz pro FFT-Bin
    max_cutoff_hz = 0.0

    for i in range(0, len(audio), chunk_size):
        chunk = audio[i:i + chunk_size]
        if len(chunk) < chunk_size:
            break
        windowed = chunk * np.hanning(len(chunk))
        spectrum = np.abs(np.fft.rfft(windowed))
        peak = spectrum.max()
        if peak == 0:
            continue
        spectrum_db = 20 * np.log10(spectrum / peak + 1e-12)

        # Von oben (Nyquist) nach unten: erstes Band mit genug Inhalt = Cutoff.
        n = len(spectrum_db)
        for j in range(n, _BAND_SIZE, -1):
            band = spectrum_db[j - _BAND_SIZE:j]
            if (band > _NOISE_FLOOR_DB).mean() >= _BAND_CONTENT_RATIO:
                cutoff_hz = (j - 1) * bin_res
                if cutoff_hz > max_cutoff_hz:
                    max_cutoff_hz = cutoff_hz
                break

    if max_cutoff_hz <= 0:
        return None
    return round(max_cutoff_hz / 1000.0, 2)


def is_upsampled(path: str, claimed_bitrate_kbps: int,
                 tolerance_khz: float = 2.0,
                 min_check_bitrate: int = 192) -> tuple[bool, float | None, float]:
    """
    Beurteilt, ob eine Datei fuer ihre ANGEGEBENE Bitrate einen zu niedrigen
    echten Cutoff hat (= wahrscheinlich upgesampelt). Konservativ: nicht messbar
    -> (False, None, erwartet). Rueckgabe:
    (ist_upsampled, gemessener_cutoff_khz_oder_None, erwarteter_cutoff_khz).
    """
    expected = _expected_cutoff_khz(claimed_bitrate_kbps)
    if claimed_bitrate_kbps < min_check_bitrate or expected <= 0:
        return False, None, expected

    measured = measure_cutoff_khz(path)
    if measured is None:
        return False, None, expected

    upsampled = measured < (expected - tolerance_khz)
    return upsampled, measured, expected


def quality_key(path: str, claimed_bitrate_kbps: int) -> float:
    """
    Qualitaets-Score fuer den Vergleich "ist Datei B besser als A" (hoeher =
    besser). Echter Cutoff dominiert (immun gegen gefaelschte Bitrate), Bitrate
    nur Tie-Breaker. Nicht messbar -> nur Bitrate. Nur fuer Vergleiche desselben
    Songs gedacht.
    """
    measured = measure_cutoff_khz(path)
    if measured is not None:
        return measured * 1000.0 + claimed_bitrate_kbps
    return float(claimed_bitrate_kbps)
