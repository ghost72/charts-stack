"""
ReplayGain 2.0 normalization (EBU R128) for uniform loudness.

Measures the loudness of an MP3 with ffmpeg (ebur128 filter) and writes the
computed track gain as ReplayGain tags (TXXX:REPLAYGAIN_TRACK_GAIN and
REPLAYGAIN_TRACK_PEAK) into the file. The audio stream stays UNCHANGED
(lossless) - a ReplayGain-capable player (Navidrome, Symfonium, ...) controls
the playback loudness based on these tags.

Needs ffmpeg in the image (see Dockerfile). Fails silently if ffmpeg is
missing or the measurement fails - the import then continues normally.

Target value: -18 LUFS (ReplayGain 2.0 reference).
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

# Target level (LUFS). The ReplayGain 2.0 reference is -18; streaming services
# (Spotify/YouTube ~-14) are louder. Configurable via REPLAYGAIN_TARGET_LUFS.
_TARGET_LUFS = float(config.REPLAYGAIN_TARGET_LUFS)

# ffmpeg ebur128 prints a summary at the end with "I:   -xx.x LUFS"
# (integrated loudness) and "Peak:  -x.x dBFS".
_I_RE = re.compile(r"I:\s*(-?\d+(?:\.\d+)?)\s*LUFS")
_PEAK_RE = re.compile(r"Peak:\s*(-?\d+(?:\.\d+)?)\s*dBFS")


def available() -> bool:
    """True if ffmpeg is in PATH."""
    return shutil.which("ffmpeg") is not None


def _measure(path: str) -> tuple[float, float] | None:
    """
    Measures integrated loudness (LUFS) and true peak (dBFS) via ffmpeg.
    Returns (loudness_lufs, peak_dbfs) or None on error.
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

    out = proc.stderr  # ffmpeg writes the summary to stderr
    # Take the last occurrences (the summary at the end).
    i_matches = _I_RE.findall(out)
    peak_matches = _PEAK_RE.findall(out)
    if not i_matches:
        logger.debug(t("import.replaygain.no_lufs", path=path))
        return None
    loudness = float(i_matches[-1])
    peak_dbfs = float(peak_matches[-1]) if peak_matches else 0.0
    return loudness, peak_dbfs


def _has_replaygain(path: str) -> bool:
    """True if a REPLAYGAIN_TRACK_GAIN tag is already present."""
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
    Computes and writes ReplayGain track tags into the MP3.
    overwrite=False: do not recompute existing ReplayGain tags.
    Returns: True if tags were written.
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
    # Track gain = target level - measured loudness.
    gain_db = _TARGET_LUFS - loudness
    # Peak as a linear factor (ReplayGain convention: 0..1+).
    peak_linear = 10 ** (peak_dbfs / 20.0) if peak_dbfs else 1.0

    try:
        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            tags = ID3()
        # Remove old ReplayGain TXXX, then set anew.
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
