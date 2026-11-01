"""
Search + origin-classification tests for the Manual Tag Editor backend.

All run WITHOUT network/Docker: a tiny tmp MUSIC_DIR tree of real (silent) MP3s
written via mutagen + a fixture manifest. They pin:
  * a substring / accent-folded query returns the expected matches;
  * a file carrying TXXX:CHARTS_SOURCE classifies origin 'stack' (authoritative);
  * a manifest-basename-only match (no marker) also classifies 'stack' (advisory);
  * a plain hand-added file (no marker, no manifest entry) classifies 'curated';
  * a < 2-char query returns [];
  * limit is clamped to [1, 50].
"""

import json
import os

import pytest

from app import config, editor, library, tags


# --- tiny valid MP3 (hand-built silent frame, like charts-import tests) ------

def _write_dummy_mp3(path: str) -> None:
    header = b"\xff\xfb\x90\x40"
    frame = header + b"\x00" * (417 - 4)
    with open(path, "wb") as fh:
        fh.write(frame * 30)


def _mk_song(root, artist, album, filename):
    d = os.path.join(root, artist, album)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, filename)
    _write_dummy_mp3(p)
    return p


@pytest.fixture(autouse=True)
def _preserve_on(monkeypatch):
    monkeypatch.setattr(config, "PRESERVE_MTIME", True)


@pytest.fixture(autouse=True)
def _reset_index():
    # The index is a module-level TTL cache; reset it so each test rebuilds it
    # against its own tmp MUSIC_DIR.
    library._index = None
    library._index_dir = None
    library._index_ts = 0.0
    yield
    library._index = None
    library._index_dir = None
    library._index_ts = 0.0


@pytest.fixture
def music_tree(tmp_path, monkeypatch):
    """Build a small MUSIC_DIR + point config.MUSIC_DIR/MANIFEST_FILE at it."""
    root = tmp_path / "Music"
    root.mkdir()
    # An accented artist to prove accent-folded search.
    p_bjork = _mk_song(str(root), "Björk", "Debut", "01 Human Behaviour.mp3")
    # A stack song carrying the authoritative TXXX:CHARTS_SOURCE marker.
    p_stack = _mk_song(str(root), "Oli.P", "Flugzeuge im Bauch (1998)",
                       "01 Flugzeuge im Bauch.mp3")
    from mutagen.id3 import ID3, TXXX
    id3 = ID3()
    id3.add(TXXX(encoding=3, desc=tags.CHARTS_SOURCE_DESC, text=["youtube"]))
    id3.save(p_stack)
    # A song whose basename matches a manifest entry (advisory stack signal).
    p_manifest = _mk_song(str(root), "Corey Hart", "Sunglasses at Night",
                          "sunglasses.mp3")
    # A plain hand-added song: no marker, no manifest entry -> curated.
    p_curated = _mk_song(str(root), "Various", "Mixtape", "random tune.mp3")

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({"entries": {
        "sunglasses.mp3": {"artist": "Corey Hart", "title": "Sunglasses at Night"},
    }}), encoding="utf-8")

    monkeypatch.setattr(config, "MUSIC_DIR", str(root))
    monkeypatch.setattr(config, "MANIFEST_FILE", str(manifest_path))
    monkeypatch.setattr(config, "TAGEDITOR_INDEX_TTL", 300)
    return {
        "root": str(root), "bjork": p_bjork, "stack": p_stack,
        "manifest": p_manifest, "curated": p_curated,
    }


# --- substring / accent-folded search ---------------------------------------

def test_substring_match(music_tree):
    out = editor.search("flugzeuge", limit=20)
    paths = {r["path"] for r in out}
    assert music_tree["stack"] in paths


def test_accent_folded_match(music_tree):
    # 'bjork' (no diacritic) must match the 'Björk' artist folder.
    out = editor.search("bjork", limit=20)
    paths = {r["path"] for r in out}
    assert music_tree["bjork"] in paths


def test_combined_artist_title_match(music_tree):
    # A query that spans the artist/title boundary contiguously matches the
    # folded 'artist title' combined key ('coreyhart' + 'sunglassesatnight').
    out = editor.search("hart sunglasses", limit=20)
    paths = {r["path"] for r in out}
    assert music_tree["manifest"] in paths


# --- origin classification --------------------------------------------------

def _origin_for(out, path):
    for r in out:
        if r["path"] == path:
            return r["origin"]
    return None


def test_marker_file_is_stack(music_tree):
    out = editor.search("flugzeuge", limit=20)
    assert _origin_for(out, music_tree["stack"]) == "stack"


def test_manifest_only_is_stack(music_tree):
    out = editor.search("sunglasses", limit=20)
    assert _origin_for(out, music_tree["manifest"]) == "stack"


def test_plain_file_is_curated(music_tree):
    out = editor.search("random tune", limit=20)
    assert _origin_for(out, music_tree["curated"]) == "curated"


# --- query/limit guards ------------------------------------------------------

def test_short_query_returns_empty(music_tree):
    assert editor.search("a", limit=20) == []
    assert editor.search(" ", limit=20) == []


def test_limit_clamped_low(music_tree):
    # limit 0 -> clamped to 1 (at most one result even though several match 'a...').
    out = library.search("the", limit=0)
    assert len(out) <= 1


def test_limit_clamped_high(music_tree):
    # A huge limit is clamped to 50 (can't exceed it); the tiny tree has < 50
    # files so this just proves no crash and a bounded return.
    out = library.search("0", limit=10_000)
    assert len(out) <= 50


# --- walk pruning: trash / sidecar / hidden folders are excluded ------------

def test_build_records_excludes_trash_sidecar_hidden(tmp_path, monkeypatch):
    """_build_records must not descend into .charts-trash (the deleted-file bin),
    the @eaDir/#recycle sidecar trees, or any dot-folder. Only the normal
    artist/album/song is indexed; the trash/sidecar/hidden ones are skipped."""
    root = tmp_path / "Music"
    root.mkdir()
    # A normal, real artist/album/song that MUST be indexed.
    p_normal = _mk_song(str(root), "Chappell Roan",
                        "The Rise and Fall of a Midwest Princess",
                        "Pink Pony Club.mp3")
    # A file inside the stack's own deleted-file bin -> must be excluded.
    p_trash = _mk_song(str(root), ".charts-trash/2026-01-01/Chappell Roan",
                       "Album", "Pink Pony Club.mp3")
    # A Synology sidecar subtree -> must be excluded.
    p_sidecar = _mk_song(str(root), "@eaDir", "thumbs", "Pink Pony Club.mp3")
    # Any other dot-folder -> must be excluded.
    p_hidden = _mk_song(str(root), ".hidden", "Album", "Pink Pony Club.mp3")

    monkeypatch.setattr(config, "MUSIC_DIR", str(root))

    paths = {r["path"] for r in library._build_records(str(root))}
    assert p_normal in paths
    assert p_trash not in paths
    assert p_sidecar not in paths
    assert p_hidden not in paths


# --- search index refreshes on save (stale-after-save fix) ------------------

def test_search_after_save_reflects_new_artist(tmp_path, monkeypatch):
    """A save must refresh the cached index so the next search finds the file
    under its NEW artist/title, not the old path-derived name (the TTL cache
    otherwise keeps surfacing the old name until it expires)."""
    root = tmp_path / "Music"
    root.mkdir()
    # Folder is named after the OLD artist ('Pink'); the index is path-derived.
    p = _mk_song(str(root), "Pink", "Album", "So What.mp3")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({"entries": {}}), encoding="utf-8")
    monkeypatch.setattr(config, "MUSIC_DIR", str(root))
    monkeypatch.setattr(config, "MANIFEST_FILE", str(manifest_path))
    monkeypatch.setattr(config, "TAGEDITOR_INDEX_TTL", 300)
    monkeypatch.setattr(config, "PRESERVE_MTIME", True)

    # Build the index under the OLD name: 'pink' matches, 'pnk' (P!nk) does not.
    before = {r["path"] for r in editor.search("pink", limit=20)}
    assert p in before
    assert editor.search("pnk", limit=20) == []

    # Save a new artist tag (Pink -> P!nk). The lone source folder empties out,
    # so rule 3 RENAMES Pink/ -> P!nk/ and the file follows the move; the index
    # is refreshed to the NEW path (and the old path is dropped). Without the
    # on-save index refresh the stale cache would still only match 'pink'.
    res = editor.save_song(p, {"artist": "P!nk", "title": "So What"})
    assert res["moved"] is True
    new_path = res["path"]
    assert new_path != p

    # The same cached index now reflects the saved tag at the new path: 'pnk'
    # finds it, and the stale old path is gone.
    after = {r["path"] for r in editor.search("pnk", limit=20)}
    assert new_path in after
    assert p not in after
    # And the refreshed record carries the new display artist.
    rec = next(r for r in editor.search("pnk", limit=20) if r["path"] == new_path)
    assert rec["artist"] == "P!nk"


def test_save_does_not_reset_index_ttl(tmp_path, monkeypatch):
    """The on-save refresh updates ONE record in place; it must not rebuild the
    whole index or reset the TTL timestamp (normal lazy-cache behavior intact)."""
    root = tmp_path / "Music"
    root.mkdir()
    p = _mk_song(str(root), "Pink", "Album", "So What.mp3")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({"entries": {}}), encoding="utf-8")
    monkeypatch.setattr(config, "MUSIC_DIR", str(root))
    monkeypatch.setattr(config, "MANIFEST_FILE", str(manifest_path))
    monkeypatch.setattr(config, "TAGEDITOR_INDEX_TTL", 300)
    monkeypatch.setattr(config, "PRESERVE_MTIME", True)

    editor.search("pink", limit=20)           # build the index
    ts_before = library._index_ts
    editor.save_song(p, {"artist": "P!nk"})
    # The TTL anchor is unchanged -> no full rebuild was forced by the save.
    assert library._index_ts == ts_before
