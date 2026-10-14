"""
mtime-preservation tests for charts-import.

Navidrome derives a song's "date added" from the file mtime. charts-import must
NOT bump that mtime when it only retouches an existing library file (retag,
cover embed, ReplayGain write), and an in-place upgrade must inherit the OLD
file's mtime instead of looking freshly added. These tests pin that behavior.

All tests run WITHOUT network/ffmpeg:
  * a tiny hand-built silent MP3 (one repeated MPEG-1 Layer III frame) is enough
    for mutagen to detect the format and write tags;
  * cover/ReplayGain network/ffmpeg parts are monkeypatched so only the
    mtime-around-save behavior is exercised.
"""

import os
import time

import pytest

from app import config, cover, replaygain, tags


# --- tiny valid MP3 (no network, no ffmpeg) --------------------------------

def _write_dummy_mp3(path: str) -> None:
    """
    Writes a minimal but valid MP3: a silent MPEG-1 Layer III frame
    (128 kbps, 44.1 kHz, mono -> 417-byte frames) repeated a few times. mutagen
    detects it as MP3 and can read/write ID3 tags on it.
    """
    header = b"\xff\xfb\x90\x40"          # sync + MPEG1/L3/no-CRC/128k/44.1k/mono
    frame = header + b"\x00" * (417 - 4)
    with open(path, "wb") as fh:
        fh.write(frame * 30)


# "Old" reference mtime: well in the past so a fresh bump is unmistakable.
_OLD_TS = 1_000_000_000.0   # 2001-09-09
_TOL = 1.0                  # seconds tolerance for mtime comparisons


def _set_mtime(path: str, ts: float) -> None:
    os.utime(path, (ts, ts))


@pytest.fixture(autouse=True)
def _preserve_on(monkeypatch):
    """Default every test to PRESERVE_MTIME on (restore to real value after)."""
    monkeypatch.setattr(config, "PRESERVE_MTIME", True)


@pytest.fixture
def mp3(tmp_path):
    p = tmp_path / "song.mp3"
    _write_dummy_mp3(str(p))
    _set_mtime(str(p), _OLD_TS)
    return str(p)


# --- write_tags -------------------------------------------------------------

def test_write_tags_preserves_mtime_when_on(mp3):
    tags.write_tags(mp3, artist="A", title="T", album="Alb")
    assert abs(os.stat(mp3).st_mtime - _OLD_TS) <= _TOL


def test_write_tags_bumps_mtime_when_off(mp3, monkeypatch):
    monkeypatch.setattr(config, "PRESERVE_MTIME", False)
    tags.write_tags(mp3, artist="A", title="T", album="Alb")
    # With preservation off, the save rewrites the file -> mtime moves to ~now.
    assert os.stat(mp3).st_mtime > _OLD_TS + 100


# --- strip_provenance_tags --------------------------------------------------

def test_strip_provenance_tags_preserves_mtime(mp3):
    # Add a provenance frame (COMM) so strip has something to delete and saves.
    from mutagen.id3 import ID3, COMM
    try:
        id3 = ID3(mp3)
    except Exception:
        id3 = ID3()
    id3.add(COMM(encoding=3, lang="eng", desc="", text=["uploaded by someone"]))
    with _no_preserve(config):  # write the COMM frame without our preservation
        id3.save(mp3)
    _set_mtime(mp3, _OLD_TS)

    changed = tags.strip_provenance_tags(mp3)
    assert changed is True                      # it actually saved
    assert abs(os.stat(mp3).st_mtime - _OLD_TS) <= _TOL


class _no_preserve:
    """Context helper: temporarily force PRESERVE_MTIME off (test setup only)."""
    def __init__(self, cfg):
        self.cfg = cfg

    def __enter__(self):
        self._prev = self.cfg.PRESERVE_MTIME
        self.cfg.PRESERVE_MTIME = False

    def __exit__(self, *exc):
        self.cfg.PRESERVE_MTIME = self._prev


# --- cover.fetch_and_embed --------------------------------------------------

def test_cover_fetch_and_embed_preserves_mtime(mp3, monkeypatch):
    # Monkeypatch the network parts: pretend we found + downloaded a cover.
    monkeypatch.setattr(cover, "_find_cover_url", lambda a, t: "http://x/y.jpg")
    monkeypatch.setattr(cover, "_download", lambda url: b"\xff\xd8\xff\xd9")
    # _make_square: return the bytes unchanged (skip JPEG/Pillow handling).
    monkeypatch.setattr(cover, "_make_square", lambda data: data)

    ok = cover.fetch_and_embed(mp3, "A", "T")
    assert ok is True                           # it saved an APIC frame
    assert abs(os.stat(mp3).st_mtime - _OLD_TS) <= _TOL


# --- replaygain.apply_track_gain --------------------------------------------

def test_replaygain_apply_track_gain_preserves_mtime(mp3, monkeypatch):
    # Pretend ffmpeg is present and returns a measurement (no real ffmpeg call).
    monkeypatch.setattr(replaygain, "available", lambda: True)
    monkeypatch.setattr(replaygain, "_measure", lambda path: (-20.0, -1.0))

    ok = replaygain.apply_track_gain(mp3)
    assert ok is True                           # it wrote the TXXX frames
    assert abs(os.stat(mp3).st_mtime - _OLD_TS) <= _TOL


# --- importer upgrade branch ------------------------------------------------

def test_importer_upgrade_inherits_old_mtime(tmp_path, monkeypatch):
    """
    The in-place upgrade branch must stamp the replacement with the OLD file's
    mtime (not the fresh download/copy mtime), so an upgraded song keeps its
    original "date added".
    """
    from app import importer

    music = tmp_path / "music"
    downloads = tmp_path / "downloads"
    music.mkdir()
    downloads.mkdir()

    # Existing library file (the OLD, worse version) with an old mtime.
    artist_dir = music / "A"
    album_dir = artist_dir / "T"
    album_dir.mkdir(parents=True)
    existing = album_dir / "T.mp3"
    _write_dummy_mp3(str(existing))
    _set_mtime(str(existing), _OLD_TS)

    # Incoming download (the NEW, better version) with a fresh mtime.
    src = downloads / "incoming.mp3"
    _write_dummy_mp3(str(src))
    fresh = time.time()
    _set_mtime(str(src), fresh)

    # Manifest marks this as a replace/upgrade request for the same song.
    monkeypatch.setattr(
        tags, "manifest_lookup",
        lambda path: {"artist": "A", "title": "T", "album": "T",
                      "replace": True, "source": "manuell"},
    )
    monkeypatch.setattr(
        tags, "build_target_path",
        lambda music_dir, path: (str(existing), ("A", "T", "T", True, False)),
    )

    # Force the "existing song found" + "new is better" decision without ffmpeg.
    monkeypatch.setattr(importer, "_song_present_for_artist",
                        lambda md, a, t: str(existing))
    monkeypatch.setattr(importer.quality, "quality_key",
                        lambda path, br: 2.0 if str(src) in path else 1.0)
    monkeypatch.setattr(importer, "_read_bitrate", lambda path: 320)

    # Skip the content-side effects (not under test here); they are covered
    # above. Keep write_tags real so the normal copy+retag path runs.
    monkeypatch.setattr(importer.cover, "embed_cover",
                        lambda *a, **k: False)
    monkeypatch.setattr(importer.replaygain, "apply_track_gain",
                        lambda *a, **k: False)
    monkeypatch.setattr(importer, "_flashback_year", lambda a, t: "")
    monkeypatch.setattr(importer, "_append_import_log", lambda *a, **k: None)

    # Point config at the temp dirs; enable copy mode + preservation; relax gates.
    monkeypatch.setattr(config, "MUSIC_DIR", str(music))
    monkeypatch.setattr(config, "DOWNLOADS_DIR", str(downloads))
    monkeypatch.setattr(config, "COPY_MODE", True)
    monkeypatch.setattr(config, "REQUIRE_MANIFEST", True)
    monkeypatch.setattr(config, "REJECT_ENABLED", False)
    monkeypatch.setattr(config, "MIN_AGE_SECONDS", 0)
    monkeypatch.setattr(config, "PRESERVE_MTIME", True)

    imported, rejected, flagged = importer.import_downloads()

    assert any(t_[0] == str(existing) for t_ in imported), (
        f"upgrade did not import to the existing path: {imported}")
    # The replacement must carry the OLD mtime, NOT the fresh copy mtime.
    assert abs(os.stat(str(existing)).st_mtime - _OLD_TS) <= _TOL, (
        "upgraded file did not inherit the old mtime")
    assert os.stat(str(existing)).st_mtime < fresh - 100
