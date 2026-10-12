"""
Audio quality analysis: detects upsampled / faked MP3s via the actual frequency
cutoff, measured by FFT (ffmpeg PCM decode + numpy).

Taken over by charts-upgrade as a copy (no runtime dependency). Reconcile
changes here with charts-upgrade.

BACKGROUND
----------
The bitrate stated in the file tag often lies: a 128 kbps source is
transcoded up to "320 kbps" (upsampling). When encoding, the MP3 encoder
discards everything above a bitrate-dependent limit (lowpass):
    128 kbps -> ~16 kHz, 192 -> ~19 kHz, 256/320 -> ~20 kHz.
The up-transcoded "320" keeps the ~16 kHz wall of the 128 source.

MEASUREMENT METHOD (IMPORTANT - correct, not spectral_rolloff!)
---------------------------------------------------------------
spectral_rolloff (energy centroid) is WRONG HERE: for bass/mid-heavy music the
median rolloff naturally sits at 5-10 kHz, regardless of whether there is still
content up top. The REAL cutoff is the HIGHEST frequency with significant
energy. Therefore:
  1. ffmpeg decodes the file to mono PCM (f32le).
  2. FFT in 2-second blocks (numpy.rfft), spectrum in dBFS relative to the peak.
  3. Search DOWN from Nyquist until a frequency band appears above the noise
     floor (NOISE_FLOOR_DB) -> that is the cutoff of this block.
  4. Take the MAXIMUM over all blocks (the loudest/highest moment counts;
     quiet/bass-heavy passages do not wrongly push the value down).

CONSERVATIVE BY DESIGN
----------------------
  - Not measurable (ffmpeg/numpy missing, error, silence) -> None -> never
    reject.
  - Only if the measured cutoff is CLEARLY below the bitrate expectation
    (tolerance) does the file count as upsampled.

Needs ffmpeg + numpy in the image.
"""

import logging
import shutil
import subprocess

from .i18n import t

logger = logging.getLogger(__name__)

# Expected minimum cutoff (kHz) depending on the STATED MP3 bitrate (kbps).
_EXPECTED_CUTOFF_BY_BITRATE = [
    (320, 19.0),
    (256, 18.0),
    (192, 17.0),
    (160, 15.5),
    (128, 15.0),
    (0,   0.0),
]

# Noise floor (dBFS relative to the peak): frequencies above it count as "content".
_NOISE_FLOOR_DB = -80.0
# A band counts as "content" if at least this fraction of its bins is above the
# noise floor.
_BAND_CONTENT_RATIO = 0.5
# Bandwidth (FFT bins) for the cutoff search from the top.
_BAND_SIZE = 50
# Analysis block size in seconds.
_CHUNK_SECONDS = 2
# Max seconds of audio that are decoded (CPU protection: the start is enough to
# determine the cutoff; the whole file would be a waste).
_MAX_DECODE_SECONDS = 60


def available() -> bool:
    """True if ffmpeg is in PATH AND numpy is importable."""
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
    """Reads the sample rate via ffprobe (0/None if not readable)."""
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
    Measures the real frequency cutoff (kHz): the highest frequency with
    significant energy (FFT, from Nyquist downward). None if not measurable
    (ffmpeg/numpy missing, decode error, silence).
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

    # Decode the file to mono PCM float32 (only the first _MAX_DECODE_SECONDS).
    try:
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-t", str(_MAX_DECODE_SECONDS),
             "-i", path, "-ac", "1", "-f", "f32le", "-"],
            capture_output=True, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.debug(t("import.quality.decode_failed", path=path, error=e))
        return None
    if proc.returncode != 0 or not proc.stdout:
        return None

    audio = np.frombuffer(proc.stdout, dtype=np.float32)
    if audio.size == 0:
        return None

    chunk_size = sr * _CHUNK_SECONDS
    bin_res = sr / chunk_size  # Hz per FFT bin
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

        # From the top (Nyquist) down: first band with enough content = cutoff.
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
    Judges whether a file has too low a real cutoff for its STATED bitrate
    (= probably upsampled). Conservative: not measurable -> (False, None,
    expected). Returns:
    (is_upsampled, measured_cutoff_khz_or_None, expected_cutoff_khz).
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
    Quality score for the comparison "is file B better than A" (higher =
    better). The real cutoff dominates (immune to faked bitrate), bitrate is
    only a tie-breaker. Not measurable -> only bitrate. Intended only for
    comparisons of the same song.
    """
    measured = measure_cutoff_khz(path)
    if measured is not None:
        return measured * 1000.0 + claimed_bitrate_kbps
    return float(claimed_bitrate_kbps)
