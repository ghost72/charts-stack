"""
Provenance-strip tests for the charts-config copy of strip_provenance_tags
(used by the FLAC->MP3 convert path). Mirrors the charts-import tests for the
nine upload-site / shop frames: WORS, TRSN, TRSO, TIT1, TIT3, GRP1 are removed
unconditionally; TPUB, TCOP, TOPE only when the value looks like a domain/URL
stamp. Content frames and the protected TXXX must survive.

No network. Builds a tiny valid MP3 and writes raw ID3 frames onto it.
"""

import pytest

from app import config, tags


def _write_dummy_mp3(path: str) -> None:
    header = b"\xff\xfb\x90\x40"
    frame = header + b"\x00" * (417 - 4)
    with open(path, "wb") as fh:
        fh.write(frame * 30)


@pytest.fixture(autouse=True)
def _preserve_off(monkeypatch):
    monkeypatch.setattr(config, "PRESERVE_MTIME", False)


def _frame_keys(path):
    from mutagen.id3 import ID3
    return set(str(k) for k in ID3(path).keys())


_NINE = ("WORS", "GRP1", "TCOP", "TIT1", "TIT3", "TOPE", "TPUB", "TRSN", "TRSO")
_UNCONDITIONAL = ("WORS", "TRSN", "TRSO", "TIT1", "TIT3", "GRP1")
_VALUE_COND = ("TPUB", "TCOP", "TOPE")


def _add(id3, fid, value):
    from mutagen import id3 as m
    if fid == "WORS":
        id3.add(m.WORS(url=value))
    else:
        id3.add(getattr(m, fid)(encoding=3, text=[value]))


def _make(tmp_path, frames, name="x.mp3"):
    from mutagen.id3 import ID3
    p = tmp_path / name
    _write_dummy_mp3(str(p))
    id3 = ID3()
    for fid, val in frames:
        _add(id3, fid, val)
    id3.save(str(p))
    return str(p)


def test_all_nine_frames_with_domain_stamp_are_removed(tmp_path):
    p = _make(tmp_path, [(f, "fordjonly.com") for f in _NINE])
    assert tags.strip_provenance_tags(p) is True
    assert _frame_keys(p).isdisjoint(set(_NINE))


@pytest.mark.parametrize("fid", _UNCONDITIONAL)
def test_unconditional_frames_removed_even_with_harmless_text(tmp_path, fid):
    p = _make(tmp_path, [(fid, "http://x.example/a" if fid == "WORS" else "Harmless")])
    assert tags.strip_provenance_tags(p) is True
    assert fid not in _frame_keys(p)


@pytest.mark.parametrize("fid", _VALUE_COND)
@pytest.mark.parametrize("val", ["Universal Music",
                                 "\u2117 2020 Sony Music Entertainment",
                                 "Warner Records Inc."])
def test_value_conditional_frames_kept_for_real_label_text(tmp_path, fid, val):
    p = _make(tmp_path, [(fid, val)])
    assert tags.strip_provenance_tags(p) is False
    assert fid in _frame_keys(p)


@pytest.mark.parametrize("fid", _VALUE_COND)
@pytest.mark.parametrize("val", ["fordjonly.com", "www.example.net",
                                 "http://example.org/x", "t.me/channel",
                                 "something.ru", "@handle"])
def test_value_conditional_frames_removed_for_stamps(tmp_path, fid, val):
    p = _make(tmp_path, [(fid, val)])
    assert tags.strip_provenance_tags(p) is True
    assert fid not in _frame_keys(p)


def test_content_frames_survive_strip(tmp_path):
    from mutagen import id3 as m
    from mutagen.id3 import ID3
    p = tmp_path / "keep.mp3"
    _write_dummy_mp3(str(p))
    id3 = ID3()
    id3.add(m.TIT2(encoding=3, text=["Title"]))
    id3.add(m.TPE1(encoding=3, text=["Artist"]))
    id3.add(m.TPE2(encoding=3, text=["Album Artist"]))
    id3.add(m.TALB(encoding=3, text=["Album"]))
    id3.add(m.TRCK(encoding=3, text=["1/10"]))
    id3.add(m.TDRC(encoding=3, text=["2020"]))
    id3.add(m.TDOR(encoding=3, text=["2019"]))
    id3.add(m.TCON(encoding=3, text=["Pop"]))
    id3.add(m.APIC(encoding=3, mime="image/jpeg", type=3, desc="c", data=b"x"))
    id3.add(m.TXXX(encoding=3, desc="CHARTS_SOURCE", text=["youtube"]))
    id3.add(m.TXXX(encoding=3, desc="MusicBrainz Album Id", text=["abc"]))
    id3.add(m.TXXX(encoding=3, desc="REPLAYGAIN_TRACK_GAIN", text=["-6 dB"]))
    for f in _NINE:
        _add(id3, f, "fordjonly.com")
    id3.save(str(p))
    before = _frame_keys(str(p)) - set(_NINE)
    assert tags.strip_provenance_tags(str(p)) is True
    assert _frame_keys(str(p)) == before
    for prefix in ("TIT2", "TPE1", "TPE2", "TALB", "TRCK", "TDRC", "TDOR",
                   "TCON", "APIC", "TXXX:CHARTS_SOURCE", "TXXX:MusicBrainz",
                   "TXXX:REPLAYGAIN_"):
        assert any(k.startswith(prefix) for k in before), prefix
