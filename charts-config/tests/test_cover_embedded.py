"""
Tests for cover._embedded_cover_bytes - the format-agnostic embedded-cover read
behind the Manual Tag Editor's cover PREVIEW.

Regression target: the editor used to show the empty music-note placeholder for
a non-MP3 file (.m4a / .flac) that DID carry embedded art, because the read went
only through ID3()/APIC. _embedded_cover_bytes is now format-agnostic (MP3 APIC,
MP4 `covr`, FLAC `.pictures`, OGG `metadata_block_picture`) while keeping the
same contract (first cover's raw bytes, or None) and staying fail-open.

Real tiny fixtures are used where feasible (MP3 with an APIC frame, FLAC with a
Picture block). The MP4 `covr` and OGG `metadata_block_picture` shapes - which
cannot be synthesised into a valid container from scratch without a real encoder
- are exercised by stubbing mutagen.File to return objects exposing exactly the
tag/picture shapes mutagen produces, asserting the extractor picks the bytes
from each. No network, no Docker, no ffmpeg.
"""

import pathlib

import pytest

from app import cover


_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"\xff\xd9"
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


# --- real fixtures ----------------------------------------------------------

def _write_min_flac(path: str) -> None:
    """Write a minimal valid FLAC file (fLaC marker + STREAMINFO).

    Mirrors the known-good header builder from test_tageditor_convert so mutagen
    will open the file and accept added Picture blocks.
    """
    min_block = (4096).to_bytes(2, "big")
    max_block = (4096).to_bytes(2, "big")
    min_frame = (0).to_bytes(3, "big")
    max_frame = (0).to_bytes(3, "big")
    sample_rate = 44100
    channels = 2
    bps = 16
    total = 0
    packed = (sample_rate << 44) | ((channels - 1) << 41) | ((bps - 1) << 36) | total
    packed_bytes = packed.to_bytes(8, "big")
    md5 = bytes(16)
    streaminfo = min_block + max_block + min_frame + max_frame + packed_bytes + md5
    assert len(streaminfo) == 34
    header = b"fLaC" + bytes([0x80]) + len(streaminfo).to_bytes(3, "big")
    with open(path, "wb") as fh:
        fh.write(header + streaminfo)


def _write_min_mp3(path: str) -> None:
    """Write a minimal valid MP3 (repeated silent MPEG-1 Layer III frames).

    mutagen.File sniffs real MPEG audio frames to recognise a file as MP3, so a
    tag-only stub is not enough. This reuses the known-good frame builder from
    the sibling tageditor tests (0xFFFB9040 header + zero body, 30 frames) so
    mutagen opens it as MP3 and attaches the ID3 tag.
    """
    header = b"\xff\xfb\x90\x40"
    frame = header + b"\x00" * (417 - 4)
    with open(path, "wb") as fh:
        fh.write(frame * 30)


def _write_mp3_with_apic(path: str, data: bytes) -> None:
    """Write a tiny valid MP3 carrying a single APIC frame."""
    from mutagen.id3 import ID3, APIC
    _write_min_mp3(path)
    tags = ID3()
    tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=data))
    tags.save(path)


def _write_flac_with_picture(path: str, data: bytes) -> None:
    from mutagen.flac import FLAC, Picture
    _write_min_flac(path)
    fl = FLAC(path)
    pic = Picture()
    pic.type = 3
    pic.mime = "image/jpeg"
    pic.data = data
    fl.add_picture(pic)
    fl.save()


def test_mp3_apic_returns_bytes(tmp_path):
    p = str(tmp_path / "song.mp3")
    _write_mp3_with_apic(p, _JPEG)
    assert cover._embedded_cover_bytes(p) == _JPEG


def test_flac_picture_returns_bytes(tmp_path):
    p = str(tmp_path / "song.flac")
    _write_flac_with_picture(p, _JPEG)
    assert cover._embedded_cover_bytes(p) == _JPEG


def test_flac_without_cover_returns_none(tmp_path):
    p = str(tmp_path / "bare.flac")
    _write_min_flac(p)
    assert cover._embedded_cover_bytes(p) is None


def test_mp3_without_apic_returns_none(tmp_path):
    """A readable file with tags but no APIC frame -> None, not a raise."""
    from mutagen.id3 import ID3, TIT2
    p = str(tmp_path / "notag.mp3")
    _write_min_mp3(p)
    tags = ID3()
    tags.add(TIT2(encoding=3, text="x"))
    tags.save(p)
    assert cover._embedded_cover_bytes(p) is None


# --- fail-open --------------------------------------------------------------

def test_garbage_file_returns_none(tmp_path):
    p = str(tmp_path / "garbage.m4a")
    pathlib.Path(p).write_bytes(b"not a real audio container" * 4)
    assert cover._embedded_cover_bytes(p) is None


def test_missing_file_returns_none(tmp_path):
    assert cover._embedded_cover_bytes(str(tmp_path / "nope.flac")) is None


def test_mutagenfile_returns_none(monkeypatch, tmp_path):
    """mutagen.File can return None for an unrecognised stream -> None."""
    monkeypatch.setattr(cover, "MutagenFile", lambda *a, **k: None)
    assert cover._embedded_cover_bytes(str(tmp_path / "x")) is None


def test_mutagenfile_raises_returns_none(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise OSError("read error")
    monkeypatch.setattr(cover, "MutagenFile", boom)
    assert cover._embedded_cover_bytes(str(tmp_path / "x")) is None


# --- stubbed container shapes (MP4 covr, OGG metadata_block_picture) --------

class _FakeTags(dict):
    """A dict-like tags object (keys() + get() are what the extractor uses)."""


class _FakeAudio:
    def __init__(self, tags=None, pictures=None):
        self.tags = tags
        if pictures is not None:
            self.pictures = pictures


def test_mp4_covr_atom(monkeypatch, tmp_path):
    """MP4 cover: tags['covr'] is a list; first entry's bytes are returned."""
    tags = _FakeTags()
    tags["covr"] = [bytearray(_JPEG)]  # MP4Cover is a bytes subclass
    monkeypatch.setattr(cover, "MutagenFile", lambda *a, **k: _FakeAudio(tags=tags))
    assert cover._embedded_cover_bytes(str(tmp_path / "s.m4a")) == _JPEG


def test_flac_pictures_shape(monkeypatch, tmp_path):
    """FLAC path via the file object's .pictures list (first .data)."""
    class _Pic:
        data = _PNG
    audio = _FakeAudio(tags=_FakeTags(), pictures=[_Pic()])
    monkeypatch.setattr(cover, "MutagenFile", lambda *a, **k: audio)
    assert cover._embedded_cover_bytes(str(tmp_path / "s.flac")) == _PNG


def test_ogg_metadata_block_picture(monkeypatch, tmp_path):
    """OGG/Opus: base64 metadata_block_picture Vorbis comment is decoded."""
    import base64
    from mutagen.flac import Picture
    pic = Picture()
    pic.type = 3
    pic.mime = "image/jpeg"
    pic.data = _JPEG
    encoded = base64.b64encode(pic.write()).decode("ascii")
    tags = _FakeTags()
    tags["metadata_block_picture"] = [encoded]
    monkeypatch.setattr(cover, "MutagenFile", lambda *a, **k: _FakeAudio(tags=tags))
    assert cover._embedded_cover_bytes(str(tmp_path / "s.ogg")) == _JPEG
