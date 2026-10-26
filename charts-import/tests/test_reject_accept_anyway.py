"""
Tests for the accept_anyway upsampling-bypass gate in charts-import's
_rejection_reason.

A confirmed "add it anyway" upload (charts-songrequest writes
accept_anyway=true into the manifest entry) must skip ONLY the upsampling
rejection. The keyword / unknown-artist / too-short rejections stay in force,
and without the flag an upsampled file is still rejected as before.

The real ffmpeg FFT (quality.is_upsampled) and the manifest lookup
(tags.manifest_lookup) are monkeypatched; _read_bitrate / _duration_seconds are
stubbed. No network, no Docker, no real audio decoding.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import config, importer, quality, tags


def _force_reject_env(monkeypatch):
    """Make the three gated branches active and deterministic."""
    monkeypatch.setattr(config, "REJECT_ENABLED", True)
    monkeypatch.setattr(config, "REJECT_UPSAMPLED", True)
    monkeypatch.setattr(config, "REJECT_UNKNOWN_ARTIST", True)
    monkeypatch.setattr(config, "MIN_DURATION_SECONDS", 90)
    # A high claimed bitrate so the upsampling branch would run.
    monkeypatch.setattr(importer, "_read_bitrate", lambda p: 320)
    # Long enough so the too-short branch does not fire unless we want it.
    monkeypatch.setattr(importer, "_duration_seconds", lambda p: 200.0)
    # Scripted upsampled verdict (no real ffmpeg/numpy).
    monkeypatch.setattr(quality, "is_upsampled",
                        lambda *a, **k: (True, 16.0, 19.0))


def test_accept_anyway_bypasses_upsampling(monkeypatch):
    _force_reject_env(monkeypatch)
    monkeypatch.setattr(tags, "manifest_lookup",
                        lambda src: {"accept_anyway": True})
    # Upsampled + accept_anyway -> the upsampling branch is skipped -> None.
    assert importer._rejection_reason("Artist - Title.mp3", "Artist") is None


def test_accept_anyway_still_rejects_keyword(monkeypatch):
    _force_reject_env(monkeypatch)
    monkeypatch.setattr(tags, "manifest_lookup",
                        lambda src: {"accept_anyway": True})
    # A live/remix keyword in the name must STILL reject despite accept_anyway.
    reason = importer._rejection_reason("Artist - Title (live).mp3", "Artist")
    assert reason is not None
    assert "live" in reason.lower()


def test_accept_anyway_still_rejects_unknown_artist(monkeypatch):
    _force_reject_env(monkeypatch)
    monkeypatch.setattr(tags, "manifest_lookup",
                        lambda src: {"accept_anyway": True})
    # Empty artist must STILL reject despite accept_anyway.
    reason = importer._rejection_reason("Title.mp3", "")
    assert reason is not None


def test_accept_anyway_still_rejects_too_short(monkeypatch):
    _force_reject_env(monkeypatch)
    monkeypatch.setattr(tags, "manifest_lookup",
                        lambda src: {"accept_anyway": True})
    # A too-short duration must STILL reject despite accept_anyway.
    monkeypatch.setattr(importer, "_duration_seconds", lambda p: 24.0)
    reason = importer._rejection_reason("Artist - Title.mp3", "Artist")
    assert reason is not None


def test_without_flag_upsampled_still_rejected(monkeypatch):
    _force_reject_env(monkeypatch)
    # No manifest entry (None) -> the upsampling branch runs as before.
    monkeypatch.setattr(tags, "manifest_lookup", lambda src: None)
    reason = importer._rejection_reason("Artist - Title.mp3", "Artist")
    assert reason is not None
    # Also an entry WITHOUT the flag still rejects.
    monkeypatch.setattr(tags, "manifest_lookup", lambda src: {"artist": "A"})
    assert importer._rejection_reason("Artist - Title.mp3", "Artist") is not None


def test_quality_key_ignores_accept_anyway(monkeypatch):
    # FR7/AC7: the upgrade comparison (quality_key) never consults the manifest
    # flag. It takes only path + claimed bitrate; patching manifest_lookup to
    # carry accept_anyway must not change its result. Guard: calling it must not
    # invoke manifest_lookup at all.
    called = []
    monkeypatch.setattr(tags, "manifest_lookup",
                        lambda src: called.append(1) or {"accept_anyway": True})
    # Not measurable (no real ffmpeg) -> quality_key falls back to the bitrate.
    monkeypatch.setattr(quality, "measure_cutoff_khz", lambda *a, **k: None)
    assert quality.quality_key("Artist - Title.mp3", 320) == float(320)
    assert not called  # the comparison path never reads the flag
