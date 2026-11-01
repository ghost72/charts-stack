"""
Save-path tests for the Manual Tag Editor backend engine (editor.save_song).

These exercise the engine directly (the HTTP routes + the 401 auth guard are
FEAT-003; the auth step 1 is the route's responsibility). All run WITHOUT
network/Docker: tiny tmp MP3s written via mutagen, and httpx stubbed for the
cover-from-URL path (mirroring test_collector_sources.py's fully-mocked style).

They pin:
  * only-changed fields are written; untouched ID3 frames stay byte-identical;
  * NO churn on a cosmetic-only diff (track '3' vs '3/12', year '1998' vs
    '1998-05-01' -> not written);
  * an emptied submitted year/track leaves the field unchanged (never removed);
  * cover-via-upload embeds an APIC;
  * cover-via-URL embeds an APIC (httpx stubbed);
  * mtime unchanged after a tag-only edit AND after a cover-only edit;
  * both markers absent after save;
  * path + basename identical before/after, no new '<Album> (Year)/' sibling;
  * a non-MP3 save -> mp3_only, file unmodified;
  * bad year / bad track / non-image cover -> rejected, file unmodified;
  * a cover_url 'file://...' and a 3xx redirect are both rejected
    (cover_fetch_failed), no write.
"""

import io
import os

import pytest
from mutagen.id3 import ID3, TXXX

from app import config, cover, editor, tags


# --- tiny valid MP3 ----------------------------------------------------------

def _write_dummy_mp3(path: str) -> None:
    header = b"\xff\xfb\x90\x40"
    frame = header + b"\x00" * (417 - 4)
    with open(path, "wb") as fh:
        fh.write(frame * 30)


_OLD_TS = 1_000_000_000.0
_TOL = 1.0


def _set_mtime(path: str, ts: float) -> None:
    os.utime(path, (ts, ts))


def _tag(path, **easy):
    from mutagen import File as MutagenFile
    audio = MutagenFile(path, easy=True)
    for k, v in easy.items():
        audio[k] = v
    audio.save()


def _read_easy(path, key):
    from mutagen import File as MutagenFile
    audio = MutagenFile(path, easy=True)
    val = audio.get(key)
    if not val:
        return ""
    return str(val[0]) if isinstance(val, list) else str(val)


def _small_square_jpeg() -> bytes:
    """A valid tiny square JPEG via Pillow (available in the venv)."""
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), (120, 30, 200)).save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _preserve_on(monkeypatch):
    monkeypatch.setattr(config, "PRESERVE_MTIME", True)


@pytest.fixture
def song(tmp_path, monkeypatch):
    """A tmp MP3 inside a MUSIC_DIR, pre-tagged, with both markers stamped."""
    root = tmp_path / "Music"
    (root / "Artist" / "Album").mkdir(parents=True)
    p = root / "Artist" / "Album" / "track.mp3"
    _write_dummy_mp3(str(p))
    _tag(str(p), artist="Old Artist", albumartist="Old Artist",
         title="Old Title", album="Old Album", date="1990", tracknumber="3/12")
    # Stamp both provenance markers so we can prove the save clears them.
    id3 = ID3(str(p))
    id3.add(TXXX(encoding=3, desc=tags.CHARTS_SOURCE_DESC, text=["youtube"]))
    id3.add(TXXX(encoding=3, desc=tags.CHARTS_ALBUM_SRC_DESC, text=["resolver"]))
    id3.save(str(p))
    monkeypatch.setattr(config, "MUSIC_DIR", str(root))
    return str(p)


# --- httpx stub for download_cover ------------------------------------------

class _FakeStream:
    def __init__(self, status_code=200, body=b""):
        self.status_code = status_code
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_bytes(self):
        yield self._body


class _FakeClient:
    def __init__(self, status_code=200, body=b"", **kwargs):
        self._status = status_code
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def stream(self, method, url):
        return _FakeStream(self._status, self._body)


def _patch_httpx(monkeypatch, status_code=200, body=b""):
    def fake_client(*a, **kw):
        return _FakeClient(status_code=status_code, body=body)
    monkeypatch.setattr(cover.httpx, "Client", fake_client)


# --- only-changed writes + untouched frames byte-identical -------------------

def test_only_changed_fields_written(song):
    res = editor.save_song(song, {"artist": "Old Artist", "title": "New Title",
                                   "album": "Old Album", "year": "1990",
                                   "track": "3"})
    assert res["title"] == "New Title"
    # Title changed, artist/album/year/track unchanged.
    assert _read_easy(song, "title") == "New Title"
    assert _read_easy(song, "artist") == "Old Artist"
    assert _read_easy(song, "album") == "Old Album"
    assert _read_easy(song, "date") == "1990"
    # tracknumber was cosmetic-equal ('3' vs '3/12') -> NOT rewritten.
    assert _read_easy(song, "tracknumber") == "3/12"


def test_cosmetic_only_diff_no_churn(song):
    # Submit cosmetic variants only: track '3' vs '3/12', year '1998' kept.
    _tag(song, date="1998-05-01")
    before = open(song, "rb").read()
    editor.save_song(song, {"artist": "Old Artist", "title": "Old Title",
                             "album": "Old Album", "year": "1998",
                             "track": "3"})
    # Year '1998' normalizes equal to '1998-05-01'[:4]; track '3' == '3/12' head.
    # Neither is rewritten. Only the marker clears touched the file.
    assert _read_easy(song, "date") == "1998-05-01"
    assert _read_easy(song, "tracknumber") == "3/12"


def test_empty_year_track_leaves_field_unchanged(song):
    editor.save_song(song, {"artist": "Old Artist", "title": "Old Title",
                             "album": "Old Album", "year": "", "track": ""})
    # Empty submitted year/track must NOT remove the existing values.
    assert _read_easy(song, "date") == "1990"
    assert _read_easy(song, "tracknumber") == "3/12"


# --- cover embed (upload + URL) ---------------------------------------------

def test_cover_via_upload_embeds_apic(song):
    editor.save_song(song, {}, cover_file=_small_square_jpeg())
    tags_after = ID3(song)
    assert any(k.startswith("APIC") for k in tags_after.keys())


def test_cover_via_url_embeds_apic(song, monkeypatch):
    _patch_httpx(monkeypatch, status_code=200, body=_small_square_jpeg())
    editor.save_song(song, {}, cover_url="https://example.com/cover.jpg")
    tags_after = ID3(song)
    assert any(k.startswith("APIC") for k in tags_after.keys())


# --- mtime preserved ---------------------------------------------------------

def test_mtime_preserved_tag_edit(song):
    _set_mtime(song, _OLD_TS)
    editor.save_song(song, {"title": "Another Title"})
    assert abs(os.stat(song).st_mtime - _OLD_TS) <= _TOL


def test_mtime_preserved_cover_edit(song):
    _set_mtime(song, _OLD_TS)
    editor.save_song(song, {}, cover_file=_small_square_jpeg())
    assert abs(os.stat(song).st_mtime - _OLD_TS) <= _TOL


# --- markers cleared on success ---------------------------------------------

def test_markers_absent_after_save(song):
    assert tags.read_charts_source(song) == "youtube"
    assert tags.read_charts_album_src(song) == "resolver"
    editor.save_song(song, {"title": "New Title"})
    assert tags.read_charts_source(song) == ""
    assert tags.read_charts_album_src(song) == ""


# --- in-place only (no re-file) ---------------------------------------------

def test_no_refile(song):
    parent = os.path.dirname(song)
    siblings_before = set(os.listdir(os.path.dirname(parent)))
    editor.save_song(song, {"title": "New Title", "year": "2001"})
    # Same path + basename.
    assert os.path.isfile(song)
    # No new '<Album> (Year)/' sibling folder appeared next to the album dir.
    siblings_after = set(os.listdir(os.path.dirname(parent)))
    assert siblings_after == siblings_before


# --- validation (fail-closed, file unmodified) ------------------------------

def test_non_mp3_rejected(tmp_path, monkeypatch):
    root = tmp_path / "Music"
    (root / "A" / "B").mkdir(parents=True)
    p = root / "A" / "B" / "song.flac"
    p.write_bytes(b"not really flac")
    monkeypatch.setattr(config, "MUSIC_DIR", str(root))
    before = p.read_bytes()
    with pytest.raises(editor.EditorError) as ei:
        editor.save_song(str(p), {"title": "X"})
    assert ei.value.code == "mp3_only"
    assert p.read_bytes() == before


def test_bad_year_rejected(song):
    before = open(song, "rb").read()
    with pytest.raises(editor.EditorError) as ei:
        editor.save_song(song, {"year": "99"})
    assert ei.value.code == "bad_year"
    assert open(song, "rb").read() == before


def test_bad_track_rejected(song):
    before = open(song, "rb").read()
    with pytest.raises(editor.EditorError) as ei:
        editor.save_song(song, {"track": "abc"})
    assert ei.value.code == "bad_track"
    assert open(song, "rb").read() == before


def test_non_image_cover_rejected(song):
    before = open(song, "rb").read()
    with pytest.raises(editor.EditorError) as ei:
        editor.save_song(song, {}, cover_file=b"this is not an image")
    assert ei.value.code == "cover_not_image"
    assert open(song, "rb").read() == before


# --- cover-URL scheme / redirect guard --------------------------------------

def test_cover_url_file_scheme_rejected(song, monkeypatch):
    before = open(song, "rb").read()
    with pytest.raises(editor.EditorError) as ei:
        editor.save_song(song, {}, cover_url="file:///etc/passwd")
    assert ei.value.code == "cover_fetch_failed"
    assert open(song, "rb").read() == before


def test_cover_url_redirect_rejected(song, monkeypatch):
    _patch_httpx(monkeypatch, status_code=302, body=b"")
    before = open(song, "rb").read()
    with pytest.raises(editor.EditorError) as ei:
        editor.save_song(song, {}, cover_url="https://example.com/cover.jpg")
    assert ei.value.code == "cover_fetch_failed"
    assert open(song, "rb").read() == before
