"""
Provenance-strip tests for the extended strip_provenance_tags (Decision 9):
TMED and WOAR are removed; a TOAL with a "(CD-Rip)" marker is removed while a
genuine original-album TOAL survives; a non-MusicBrainz UFID is removed while a
MusicBrainz UFID is kept.

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
    # Not under test here; keep real file writes simple.
    monkeypatch.setattr(config, "PRESERVE_MTIME", False)


@pytest.fixture
def mp3(tmp_path):
    from mutagen.id3 import ID3, TMED, WOAR, TOAL, UFID
    p = tmp_path / "s.mp3"
    _write_dummy_mp3(str(p))
    id3 = ID3()
    id3.add(TMED(encoding=3, text=["CD"]))
    id3.add(WOAR(url="http://uploader.example/artist"))
    id3.add(TOAL(encoding=3, text=["Genuine Original Album"]))
    # A second TOAL carrying a CD-Rip provenance marker. ID3 stores TOAL once,
    # so put the CD-Rip marker in its own file variant below; here keep the
    # genuine one and add a separate rip-marked frame via a desc-less approach
    # is not possible (TOAL is unique). Instead this fixture holds the genuine
    # TOAL + MB/non-MB UFIDs; the CD-Rip case is a separate test file.
    id3.add(UFID(owner="http://musicbrainz.org", data=b"mbid-123"))
    id3.add(UFID(owner="HoerspielWizard", data=b"riptool"))
    id3.save(str(p))
    return str(p)


def _frame_keys(path):
    from mutagen.id3 import ID3
    return set(str(k) for k in ID3(path).keys())


def test_strip_removes_tmed_woar_and_nonmb_ufid_keeps_genuine_toal_and_mb(mp3):
    changed = tags.strip_provenance_tags(mp3)
    assert changed is True
    keys = _frame_keys(mp3)
    # Removed.
    assert not any(k.startswith("TMED") for k in keys)
    assert not any(k.startswith("WOAR") for k in keys)
    assert "UFID:HoerspielWizard" not in keys
    # Kept.
    assert any(k.startswith("TOAL") for k in keys)  # genuine original album
    assert "UFID:http://musicbrainz.org" in keys


def test_strip_removes_cdrip_toal(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PRESERVE_MTIME", False)
    from mutagen.id3 import ID3, TOAL
    p = tmp_path / "rip.mp3"
    _write_dummy_mp3(str(p))
    id3 = ID3()
    id3.add(TOAL(encoding=3, text=["Some Album (CD-Rip)"]))
    id3.save(str(p))
    changed = tags.strip_provenance_tags(str(p))
    assert changed is True
    assert not any(str(k).startswith("TOAL") for k in ID3(str(p)).keys())
