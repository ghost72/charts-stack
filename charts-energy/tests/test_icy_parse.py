"""
Unit tests for the ICY metadata parser and byte-level reader (app/icy.py).

All network is stubbed: parse_metadata / spin_key take plain strings, and
_read_exact is driven by an in-memory io.BytesIO framed exactly like an ICY
block. No socket is opened.
"""

import io

import pytest

from app import icy


# A real sample StreamTitle block (feasibility report section 2). The separator
# is U+02D7, NOT an ASCII hyphen.
_SAMPLE = (
    "StreamTitle='Lo & Leduc \u02d7 LOYALTY x MANY MEN';"
    "StreamUrl='https://storage.energy.ch/broadcast/covers/songs/1_2.jpg?"
    "ref=x&type=music&plId=12345&startedAt=2026-10-22T09:16:22+0200&"
    "duration=268&adbreak=true&songUrlTitle=loyalty-x-many-men&"
    "artistUrlTitle=lo-1';"
)


# --- parse_metadata --------------------------------------------------------

def test_parse_splits_on_u02d7():
    track = icy.parse_metadata(_SAMPLE)
    assert track is not None
    assert track.artist == "Lo & Leduc"
    assert track.title == "LOYALTY x MANY MEN"
    assert track.started_at == "2026-10-22T09:16:22+0200"
    assert track.pl_id == "12345"
    assert track.duration == "268"
    assert track.song_slug == "loyalty-x-many-men"
    assert track.artist_slug == "lo-1"


def test_parse_trims_whitespace():
    block = (
        "StreamTitle='  Artist Name  \u02d7   Song Title  ';"
        "StreamUrl='https://x/?type=music&plId=9&startedAt=t';"
    )
    track = icy.parse_metadata(block)
    assert track is not None
    assert track.artist == "Artist Name"
    assert track.title == "Song Title"


def test_parse_ascii_hyphen_not_split():
    """An ASCII '-' title must NOT be split (only U+02D7 splits)."""
    block = (
        "StreamTitle='AC-DC - Thunder';"
        "StreamUrl='https://x/?type=music&plId=1&startedAt=t';"
    )
    assert icy.parse_metadata(block) is None


def test_parse_keeps_type_music():
    track = icy.parse_metadata(_SAMPLE)
    assert track is not None


def test_parse_drops_non_music():
    block = (
        "StreamTitle='Some \u02d7 Advert';"
        "StreamUrl='https://x/?type=ad&plId=1&startedAt=t';"
    )
    assert icy.parse_metadata(block) is None


def test_parse_no_separator_returns_none(caplog):
    block = (
        "StreamTitle='No Separator Here';"
        "StreamUrl='https://x/?type=music&plId=1&startedAt=t';"
    )
    with caplog.at_level("DEBUG"):
        assert icy.parse_metadata(block) is None


def test_parse_empty_block_returns_none():
    assert icy.parse_metadata("") is None
    assert icy.parse_metadata("garbage with no streamtitle") is None


# --- spin_key --------------------------------------------------------------

def test_spin_key_from_started_and_plid():
    track = icy.parse_metadata(_SAMPLE)
    assert icy.spin_key(track) == "2026-10-22T09:16:22+0200|12345"


def test_spin_key_fallback_on_missing_fields():
    # No startedAt/plId -> fall back to normalized artist|title.
    track = icy.IcyTrack(artist="The Artist", title="A Song")
    assert icy.spin_key(track) == "the artist|a song"


# --- _read_exact against a framed ICY block --------------------------------

def _frame_icy_block(metaint: int, meta: bytes) -> bytes:
    """Build metaint audio bytes + 1 length byte + padded metadata."""
    audio = b"\x00" * metaint
    pad = (-len(meta)) % 16
    padded = meta + b"\x00" * pad
    length_byte = bytes([len(padded) // 16])
    return audio + length_byte + padded


def test_read_exact_reads_full_length():
    data = b"abcdefghij"
    fp = io.BytesIO(data)
    assert icy._read_exact(fp, 10) == data


def test_read_exact_premature_eof_raises():
    fp = io.BytesIO(b"abc")
    with pytest.raises(icy.IcyReadError):
        icy._read_exact(fp, 10)


def test_read_exact_walks_icy_frame():
    """Parse a framed ICY block manually like read_now_playing does."""
    metaint = 32
    meta = _SAMPLE.encode("utf-8")
    framed = _frame_icy_block(metaint, meta)
    fp = io.BytesIO(framed)

    icy._read_exact(fp, metaint)                 # discard audio
    length_byte = icy._read_exact(fp, 1)
    meta_len = length_byte[0] * 16
    block = icy._read_exact(fp, meta_len).rstrip(b"\x00").decode("utf-8")

    track = icy.parse_metadata(block)
    assert track is not None
    assert track.artist == "Lo & Leduc"
