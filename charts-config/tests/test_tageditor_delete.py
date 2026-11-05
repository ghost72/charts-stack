"""
Tests for the Manual Tag Editor's "delete this song" feature.

The delete is REVERSIBLE: it moves the loaded song into
MUSIC_DIR/.charts-trash/tageditor-delete-<YYYY-MM-DD>/ (mirroring the convert
path's trash convention, NOT a hard delete) and clears the file's manifest
entry so charts-import does not re-import it on its next run.

No network, no ffmpeg, no Docker: the engine (editor.delete_song) is pure
filesystem + JSON, so the tests drive the real HTTP route over a tmp MUSIC_DIR
and a tmp manifest and assert OUR orchestration:
  * 401 anonymous (same auth gate as every other /api/tageditor/* route);
  * path outside MUSIC_DIR (traversal + absolute) -> 400 path_outside, nothing
    moved, manifest untouched (the path-guard rejects before any mutation);
  * a missing file -> 404 not_found;
  * happy path: the file is GONE from the library and PRESENT under
    .charts-trash/tageditor-delete-<today>/..., mtime preserved, the manifest
    entry removed, the response carries deleted/trashed_to/manifest_cleared;
  * a stack-single <Artist>/<Title>/ folder is pruned when it empties, but a
    shared folder holding other files is left intact;
  * deleting a non-MP3 (FLAC) song works (delete is not MP3-only);
  * a song with no manifest entry still deletes (manifest_cleared False).
"""

import datetime
import glob
import importlib
import json
import os
import pathlib

import pytest
from fastapi.testclient import TestClient

from app import i18n


_LANG_DIR = pathlib.Path(i18n.__file__).resolve().parent.parent.parent / "lang"


def _delete_dirs(root):
    """All tageditor-delete-<timestamp> subfolders under the trash root, sorted.

    Each delete now lands in its OWN unique timestamped folder, so tests locate
    the trashed file dynamically instead of hardcoding a date-only folder name.
    """
    trash = pathlib.Path(root) / ".charts-trash"
    return sorted(p for p in glob.glob(str(trash / "tageditor-delete-*"))
                  if os.path.isdir(p))


def _only_delete_dir(root):
    """The single tageditor-delete-<timestamp> folder (assert exactly one)."""
    dirs = _delete_dirs(root)
    assert len(dirs) == 1, f"expected one delete folder, got {dirs}"
    return pathlib.Path(dirs[0])


def _reload_app(monkeypatch, music_dir, manifest_file):
    """Reload config + editor + library + manifest + main with a tmp MUSIC_DIR.

    TAGEDITOR_AUDIO_ENABLED is deliberately OFF (the default), proving delete is
    auth-gated only, not flag-gated.
    """
    monkeypatch.setenv("CONFIG_PASSWORD", "secret")
    monkeypatch.setenv("CONFIG_USERNAME", "admin")
    monkeypatch.setenv("CHARTS_LANG", "en")
    monkeypatch.setenv("MUSIC_DIR", str(music_dir))
    monkeypatch.setenv("MANIFEST_FILE", str(manifest_file))
    monkeypatch.setenv("TAGEDITOR_AUDIO_ENABLED", "0")
    import app.config as config_mod
    importlib.reload(config_mod)
    i18n.setup(lang="en", lang_dir=_LANG_DIR)
    import app.manifest as manifest_mod
    importlib.reload(manifest_mod)
    import app.editor as editor_mod
    importlib.reload(editor_mod)
    import app.library as library_mod
    importlib.reload(library_mod)
    import app.main as main_mod
    importlib.reload(main_mod)
    return main_mod


def _write_min_mp3(path: str) -> None:
    """Write a tiny but valid MP3 frame so mutagen/os treat it as a real file."""
    header = b"\xff\xfb\x90\x40"
    frame = header + b"\x00" * (417 - 4)
    with open(path, "wb") as fh:
        fh.write(frame * 30)


def _write_min_flac(path: str) -> None:
    """Write a minimal valid FLAC (fLaC marker + STREAMINFO), as in the convert
    tests, so the delete-of-non-MP3 case uses a real non-MP3 file."""
    min_block = (4096).to_bytes(2, "big")
    max_block = (4096).to_bytes(2, "big")
    min_frame = (0).to_bytes(3, "big")
    max_frame = (0).to_bytes(3, "big")
    packed = (44100 << 44) | ((2 - 1) << 41) | ((16 - 1) << 36) | 0
    streaminfo = (min_block + max_block + min_frame + max_frame
                  + packed.to_bytes(8, "big") + bytes(16))
    header = b"fLaC" + bytes([0x80]) + len(streaminfo).to_bytes(3, "big")
    with open(path, "wb") as fh:
        fh.write(header + streaminfo)


def _authed_client(main_mod):
    main_mod._sessions["tok"] = "admin"
    client = TestClient(main_mod.app)
    client.cookies.set("session", "tok")
    return client


@pytest.fixture
def library_dir(tmp_path):
    """A tmp MUSIC_DIR with a stack-single Artist/Title/Title.mp3 plus a manifest
    entry keyed by the lowercased basename. Returns (root, mp3, manifest)."""
    root = tmp_path / "Music"
    (root / "Wrong Artist" / "My Song").mkdir(parents=True)
    mp3 = root / "Wrong Artist" / "My Song" / "My Song.mp3"
    _write_min_mp3(str(mp3))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"entries": {
        "my song.mp3": {"artist": "Wrong Artist", "title": "My Song",
                        "album": "My Song", "source": "youtube"},
        "other.mp3": {"artist": "Keep", "title": "Keep", "album": "Keep"},
    }}), encoding="utf-8")
    return root, str(mp3), manifest


# --- auth + guards ----------------------------------------------------------

def test_delete_anon_401(library_dir, monkeypatch):
    root, mp3, manifest = library_dir
    main_mod = _reload_app(monkeypatch, root, manifest)
    anon = TestClient(main_mod.app)
    r = anon.post("/api/tageditor/delete", data={"path": mp3})
    assert r.status_code == 401
    assert r.json()["error"] == i18n.t("config.api.not_authed")
    # Nothing moved: the original is still present.
    assert os.path.isfile(mp3)


@pytest.mark.parametrize("bad", ["/etc/passwd", "../../../../etc/passwd"])
def test_delete_path_outside_400(library_dir, monkeypatch, bad):
    root, _mp3, manifest = library_dir
    main_mod = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    r = client.post("/api/tageditor/delete", data={"path": bad})
    assert r.status_code == 400
    assert r.json()["error"] == i18n.t("config.tageditor.path_outside")
    # The manifest is untouched by a rejected request.
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert "my song.mp3" in data["entries"]


def test_delete_traversal_into_trash_leaves_library(library_dir, monkeypatch):
    """A crafted path cannot delete an arbitrary file: anything resolving outside
    MUSIC_DIR is refused. The guard uses realpath, so '..' cannot escape."""
    root, _mp3, manifest = library_dir
    # A secret outside the library the attacker would like to remove.
    secret = root.parent / "secret.txt"
    secret.write_text("keep me", encoding="utf-8")
    main_mod = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    attack = str(root / ".." / "secret.txt")
    r = client.post("/api/tageditor/delete", data={"path": attack})
    assert r.status_code == 400
    assert r.json()["error"] == i18n.t("config.tageditor.path_outside")
    assert secret.is_file()   # never touched


def test_delete_missing_file_404(library_dir, monkeypatch):
    root, _mp3, manifest = library_dir
    gone = str(root / "Wrong Artist" / "My Song" / "gone.mp3")
    main_mod = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    r = client.post("/api/tageditor/delete", data={"path": gone})
    assert r.status_code == 404
    assert r.json()["error"] == i18n.t("config.tageditor.not_found")


# --- happy path -------------------------------------------------------------

def test_delete_happy_path(library_dir, monkeypatch):
    root, mp3, manifest = library_dir
    old = 1_000_000_000.0
    os.utime(mp3, (old, old))
    main_mod = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    r = client.post("/api/tageditor/delete", data={"path": mp3})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["deleted"] is True
    assert body["manifest_cleared"] is True

    # The original is gone from the library...
    assert not os.path.exists(mp3)
    # ...and present under .charts-trash/tageditor-delete-<timestamp>/...
    trashed = (_only_delete_dir(root)
               / "Wrong Artist" / "My Song" / "My Song.mp3")
    assert trashed.is_file()   # moved, not deleted
    # The file keeps its ORIGINAL name (no "(2)" numbered suffix).
    assert trashed.name == "My Song.mp3"
    assert body["trashed_to"].endswith("My Song.mp3")
    # mtime preserved (date added kept in the trash copy).
    assert int(os.stat(trashed).st_mtime) == int(old)

    # The manifest entry for this file is removed; the OTHER entry is kept.
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert "my song.mp3" not in data["entries"]
    assert "other.mp3" in data["entries"]


def test_delete_writes_restore_sidecar(library_dir, monkeypatch):
    """Delete writes a restore sidecar next to the trashed file capturing the
    original library-relative path AND the removed manifest entry, so a later
    restore can put the file back and re-create the manifest entry."""
    root, mp3, manifest = library_dir
    main_mod = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    r = client.post("/api/tageditor/delete", data={"path": mp3})
    assert r.status_code == 200, r.text
    trashed = (_only_delete_dir(root)
               / "Wrong Artist" / "My Song" / "My Song.mp3")
    sidecar = pathlib.Path(str(trashed) + ".trashinfo.json")
    assert sidecar.is_file()
    info = json.loads(sidecar.read_text(encoding="utf-8"))
    assert info["original_rel_path"] == "Wrong Artist/My Song/My Song.mp3"
    assert [e["key"] for e in info["manifest_entries"]] == ["my song.mp3"]
    # The FULL removed manifest value is preserved for re-insertion.
    assert info["manifest_entries"][0]["entry"]["artist"] == "Wrong Artist"
    assert info["manifest_entries"][0]["entry"]["source"] == "youtube"
    assert info["source_files"] == []
    assert info["deleted_utc"]


def test_delete_prunes_empty_single_folder(library_dir, monkeypatch):
    """The now-empty stack-single <Title> album folder is removed, but its parent
    <Artist> folder is left (it may hold other titles)."""
    root, mp3, manifest = library_dir
    album_dir = root / "Wrong Artist" / "My Song"
    main_mod = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    r = client.post("/api/tageditor/delete", data={"path": mp3})
    assert r.status_code == 200, r.text
    assert not album_dir.exists()                 # emptied -> pruned
    assert (root / "Wrong Artist").exists()       # artist dir kept


def test_delete_keeps_shared_folder(tmp_path, monkeypatch):
    """A folder holding OTHER audio files must never be removed by the delete."""
    root = tmp_path / "Music"
    (root / "Band" / "Album").mkdir(parents=True)
    a = root / "Band" / "Album" / "01 - Song A.mp3"
    b = root / "Band" / "Album" / "02 - Song B.mp3"
    _write_min_mp3(str(a))
    _write_min_mp3(str(b))
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    main_mod = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    r = client.post("/api/tageditor/delete", data={"path": str(a)})
    assert r.status_code == 200, r.text
    assert not a.exists()                                   # deleted (trashed)
    assert b.exists()                                       # sibling kept
    assert (root / "Band" / "Album").is_dir()               # folder kept


def test_delete_non_mp3_flac(tmp_path, monkeypatch):
    """Delete is not MP3-only: a FLAC the editor can load is deletable too."""
    root = tmp_path / "Music"
    (root / "Artist" / "Tune").mkdir(parents=True)
    flac = root / "Artist" / "Tune" / "Tune.flac"
    _write_min_flac(str(flac))
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    main_mod = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    r = client.post("/api/tageditor/delete", data={"path": str(flac)})
    assert r.status_code == 200, r.text
    assert not flac.exists()
    trashed = (_only_delete_dir(root)
               / "Artist" / "Tune" / "Tune.flac")
    assert trashed.is_file()


def test_delete_without_manifest_entry(tmp_path, monkeypatch):
    """A song with no manifest entry still deletes; manifest_cleared is False."""
    root = tmp_path / "Music"
    (root / "Artist" / "Solo").mkdir(parents=True)
    mp3 = root / "Artist" / "Solo" / "Solo.mp3"
    _write_min_mp3(str(mp3))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"entries": {}}), encoding="utf-8")
    main_mod = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    r = client.post("/api/tageditor/delete", data={"path": str(mp3)})
    assert r.status_code == 200, r.text
    assert r.json()["manifest_cleared"] is False
    assert not mp3.exists()


# --- BUG 1: trash listing reflects the Tag-Editor delete immediately --------

def test_delete_shows_in_trash_listing_immediately(library_dir, monkeypatch):
    """A song deleted via the Tag-Editor delete route appears in the trash
    listing right away - the delete route invalidates the TTL-cached trash scan,
    so the freshly trashed row (nested tageditor-delete-<date>/Artist/Title/file
    path) is listed with its full rel_path and flagged restorable, without
    waiting for the TTL. Regression test for the stale-cache bug where the new
    row never appeared (not even after a page refresh)."""
    root, mp3, manifest = library_dir
    main_mod = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)

    # Prime the trash cache while the trash is still empty (as a user who opened
    # the Papierkorb tab before deleting would).
    first = client.get("/api/tageditor/trash").json()
    assert first["count"] == 0

    # Delete the song via the Tag-Editor route (NOT trash.delete_file).
    r = client.post("/api/tageditor/delete", data={"path": mp3})
    assert r.status_code == 200, r.text

    # The very next listing shows the nested tageditor-delete entry, restorable.
    second = client.get("/api/tageditor/trash").json()
    delete_folder = _only_delete_dir(root).name
    expected_rel = (f"{delete_folder}/Wrong Artist/My Song/My Song.mp3")
    by_rel = {e["rel_path"]: e for e in second["entries"]}
    assert expected_rel in by_rel
    row = by_rel[expected_rel]
    assert row["restorable"] is True
    assert row["rel_path"] == expected_rel
    # The sidecar is hidden (not a row), count reflects just the one audio file.
    assert not any(rel.endswith(".trashinfo.json") for rel in by_rel)
    assert second["count"] == 1
    assert second["total_bytes"] == row["bytes"] > 0


# --- BUG 5b: unique timestamped delete folder, no "(2)" numbered duplicates ---

def _recreate_song(mp3_path: str) -> None:
    """Re-create the same library song at its original path (as a re-download
    would), so it can be deleted a second time."""
    os.makedirs(os.path.dirname(mp3_path), exist_ok=True)
    _write_min_mp3(mp3_path)


def test_second_delete_same_song_goes_to_distinct_folder(library_dir, monkeypatch):
    """A second deletion of the SAME song (re-created at its original path) must
    land in a DISTINCT timestamped folder and keep its ORIGINAL basename - never
    a "<name> (2).mp3" numbered duplicate in a shared date folder."""
    root, mp3, manifest = library_dir
    main_mod = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)

    # First delete.
    r1 = client.post("/api/tageditor/delete", data={"path": mp3})
    assert r1.status_code == 200, r1.text
    dirs1 = _delete_dirs(root)
    assert len(dirs1) == 1

    # Re-create the song and delete it again. Force a distinct timestamp-folder
    # name even if both deletes land in the same wall-clock second: a leftover
    # folder with the base-second name makes _unique_delete_dir append "-2".
    _recreate_song(mp3)
    r2 = client.post("/api/tageditor/delete", data={"path": mp3})
    assert r2.status_code == 200, r2.text
    dirs2 = _delete_dirs(root)
    assert len(dirs2) == 2, f"expected two distinct delete folders, got {dirs2}"

    # Each folder holds the file under its ORIGINAL name - no "(2)" anywhere.
    for d in dirs2:
        f = pathlib.Path(d) / "Wrong Artist" / "My Song" / "My Song.mp3"
        assert f.is_file(), f"missing original-named file in {d}"
    assert not any("(2)" in p.name
                   for p in pathlib.Path(root / ".charts-trash").rglob("*.mp3"))


def test_delete_restore_delete_cycle_keeps_same_name(tmp_path, monkeypatch):
    """The normal cycle delete -> restore -> delete the same song must always
    yield the same filename twice, with NO numbered suffix. Each delete gets its
    own timestamped folder, so the second trashed file is "My Song.mp3", not
    "My Song (2).mp3"."""
    root = tmp_path / "Music"
    (root / "Wrong Artist" / "My Song").mkdir(parents=True)
    mp3 = root / "Wrong Artist" / "My Song" / "My Song.mp3"
    _write_min_mp3(str(mp3))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"entries": {
        "my song.mp3": {"artist": "Wrong Artist", "title": "My Song",
                        "album": "My Song", "source": "youtube"},
    }}), encoding="utf-8")
    main_mod = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)

    # Delete #1.
    assert client.post("/api/tageditor/delete",
                       data={"path": str(mp3)}).status_code == 200
    first = _only_delete_dir(root)
    first_file = first / "Wrong Artist" / "My Song" / "My Song.mp3"
    assert first_file.is_file() and first_file.name == "My Song.mp3"

    # Restore it (via the listing) - back to the original path, folder pruned.
    lr = client.get("/api/tageditor/trash").json()
    trashed_abs = [e for e in lr["entries"] if e["restorable"]][0]["path"]
    assert client.post("/api/tageditor/trash/restore",
                       data={"path": trashed_abs}).status_code == 200
    assert mp3.is_file()
    assert not first.exists()    # emptied timestamped folder pruned by restore

    # Delete #2 of the SAME song: a fresh timestamped folder, SAME filename.
    assert client.post("/api/tageditor/delete",
                       data={"path": str(mp3)}).status_code == 200
    second = _only_delete_dir(root)
    second_file = second / "Wrong Artist" / "My Song" / "My Song.mp3"
    assert second_file.is_file() and second_file.name == "My Song.mp3"
    # No numbered duplicate was ever created in the whole trash tree.
    assert not any("(2)" in p.name
                   for p in (root / ".charts-trash").rglob("*.mp3"))


def test_two_deletes_same_second_use_counter_suffix(tmp_path, monkeypatch):
    """Two deletes within the same wall-clock second must still get DISTINCT
    folders: the second reuses the base timestamp name, so _unique_delete_dir
    appends a "-<n>" counter. Simulated by pre-creating the base-second folder so
    the real delete is forced onto the "-2" variant."""
    root = tmp_path / "Music"
    (root / "A" / "S").mkdir(parents=True)
    mp3 = root / "A" / "S" / "S.mp3"
    _write_min_mp3(str(mp3))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"entries": {}}), encoding="utf-8")
    main_mod = _reload_app(monkeypatch, root, manifest)
    import app.editor as editor_mod

    # Pre-create the folder _unique_delete_dir would pick for "now" so the
    # helper must fall through to a counter-suffixed name.
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    base = root / ".charts-trash" / f"tageditor-delete-{ts}"
    base.mkdir(parents=True)

    client = _authed_client(main_mod)
    r = client.post("/api/tageditor/delete", data={"path": str(mp3)})
    assert r.status_code == 200, r.text
    # The delete landed in a distinct counter-suffixed folder, original name kept.
    counter_dir = root / ".charts-trash" / f"tageditor-delete-{ts}-2"
    assert (counter_dir / "A" / "S" / "S.mp3").is_file()
    assert not base.joinpath("A").exists()   # the pre-created base stayed empty


def test_delete_drops_song_from_search_index_immediately(library_dir, monkeypatch):
    """After a delete the song no longer surfaces in a Tag-Editor search right
    away (the delete busts the cached path-based search index), not only after
    the index TTL expires."""
    root, mp3, manifest = library_dir
    main_mod = _reload_app(monkeypatch, root, manifest)
    import app.library as library_mod
    # Prime the search index; the song is findable.
    hits = library_mod.search("My Song", limit=20)
    assert any(h["path"] == mp3 for h in hits)

    client = _authed_client(main_mod)
    r = client.post("/api/tageditor/delete", data={"path": mp3})
    assert r.status_code == 200, r.text

    # Immediately gone from search (no TTL wait).
    hits2 = library_mod.search("My Song", limit=20)
    assert not any(h["path"] == mp3 for h in hits2)


# --- re-import bug: YouTube/upload manifest keys + download-dir sources ------
# The manifest key of a YouTube/upload song is the DOWNLOAD basename
# "<artist> - <title>.mp3", NOT the library basename "<Title>.mp3". Delete must
# find the entry by artist+title and move the source file out of the download
# dir, otherwise charts-import re-imports the song.

_YT_ARTIST = "Güschä Güsche & Knackeboul"
_YT_TITLE = "Geil Wids Machsch"
_YT_KEY = "güschä güsche & knackeboul - geil wids machsch.mp3"
_YT_KEY2 = "güschä güsche & knackeboul - geil wids machsch (2).mp3"
_WRONG_KEY = "güsche & knackeboul - geil wids machsch.mp3"
_OTHER_KEY = "someone else - geil wids machsch.mp3"


def _yt_entry(artist, title, **extra):
    d = {"artist": artist, "title": title, "album": title, "source": "youtube",
         "accept_anyway": True}
    d.update(extra)
    return d


@pytest.fixture
def yt_env(tmp_path):
    """Library song + YouTube-style manifest keys + source files in a download
    dir that also holds the manifest. Returns a dict of paths."""
    root = tmp_path / "Music"
    lib_dir = root / _YT_ARTIST / _YT_TITLE
    lib_dir.mkdir(parents=True)
    mp3 = lib_dir / f"{_YT_TITLE}.mp3"
    _write_min_mp3(str(mp3))
    charts = tmp_path / "Charts"
    charts.mkdir()
    manifest = charts / ".charts-manifest.json"
    manifest.write_text(json.dumps({"entries": {
        _YT_KEY: _yt_entry(_YT_ARTIST, _YT_TITLE),
        _WRONG_KEY: _yt_entry("Güsche & Knackeboul", _YT_TITLE),
        _OTHER_KEY: _yt_entry("Someone Else", _YT_TITLE),
        "other.mp3": {"artist": "Keep", "title": "Keep", "album": "Keep"},
    }}, ensure_ascii=False), encoding="utf-8")
    # Source files keep their ORIGINAL (mixed) case; the keys are lowercased.
    names = {
        "src": f"{_YT_ARTIST} - {_YT_TITLE}.mp3",
        "wrong": f"Güsche & Knackeboul - {_YT_TITLE}.mp3",
        "other": f"Someone Else - {_YT_TITLE}.mp3",
    }
    for n in names.values():
        _write_min_mp3(str(charts / n))
    return {"root": root, "mp3": str(mp3), "charts": charts,
            "manifest": manifest, "names": names}


def _entries(manifest):
    return json.loads(manifest.read_text(encoding="utf-8"))["entries"]


def _restore_all(client):
    rows = [e for e in client.get("/api/tageditor/trash").json()["entries"]
            if e["restorable"]]
    assert len(rows) == 1
    return client.post("/api/tageditor/trash/restore", data={"path": rows[0]["path"]})


def test_delete_youtube_clears_entry_and_moves_source(yt_env, monkeypatch):
    e = yt_env
    main_mod = _reload_app(monkeypatch, e["root"], e["manifest"])
    client = _authed_client(main_mod)
    old = 1_000_000_000.0
    os.utime(e["charts"] / e["names"]["src"], (old, old))

    r = client.post("/api/tageditor/delete", data={"path": e["mp3"]})
    assert r.status_code == 200, r.text
    assert r.json()["manifest_cleared"] is True

    entries = _entries(e["manifest"])
    assert _YT_KEY not in entries
    # Wrong-artist copy, different artist, unrelated entry: untouched.
    assert _WRONG_KEY in entries and _OTHER_KEY in entries and "other.mp3" in entries

    # Source moved (not copied) into <delete-folder>/.charts-sources/, mtime kept.
    assert not (e["charts"] / e["names"]["src"]).exists()
    moved = _only_delete_dir(e["root"]) / ".charts-sources" / e["names"]["src"]
    assert moved.is_file() and int(os.stat(moved).st_mtime) == int(old)
    # The other songs' sources stay where they are.
    assert (e["charts"] / e["names"]["wrong"]).is_file()
    assert (e["charts"] / e["names"]["other"]).is_file()

    lib_trashed = (_only_delete_dir(e["root"]) / _YT_ARTIST / _YT_TITLE
                   / f"{_YT_TITLE}.mp3")
    info = json.loads(pathlib.Path(str(lib_trashed) + ".trashinfo.json")
                      .read_text(encoding="utf-8"))
    assert [x["key"] for x in info["manifest_entries"]] == [_YT_KEY]
    assert info["manifest_entries"][0]["entry"]["source"] == "youtube"
    assert info["source_files"] == [{
        "original_name": e["names"]["src"],
        "trash_rel": f".charts-sources/{e['names']['src']}"}]


def test_deleted_source_basename_no_longer_in_manifest(yt_env, monkeypatch):
    """Original bug at unit level: charts-import looks the manifest up by the
    SOURCE basename. After the delete that lookup finds nothing (so
    REQUIRE_MANIFEST skips it) and the source file is gone from the charts dir."""
    e = yt_env
    main_mod = _reload_app(monkeypatch, e["root"], e["manifest"])
    client = _authed_client(main_mod)
    assert client.post("/api/tageditor/delete",
                       data={"path": e["mp3"]}).status_code == 200
    import app.manifest as manifest_mod
    entries = manifest_mod.load_entries(str(e["manifest"]))
    assert entries.get(e["names"]["src"].lower()) is None
    assert not (e["charts"] / e["names"]["src"]).exists()


def test_restore_puts_library_source_and_entry_back(yt_env, monkeypatch):
    e = yt_env
    main_mod = _reload_app(monkeypatch, e["root"], e["manifest"])
    client = _authed_client(main_mod)
    before = _entries(e["manifest"])[_YT_KEY]
    assert client.post("/api/tageditor/delete",
                       data={"path": e["mp3"]}).status_code == 200
    r = _restore_all(client)
    assert r.status_code == 200, r.text
    assert os.path.isfile(e["mp3"])
    assert (e["charts"] / e["names"]["src"]).is_file()
    assert _entries(e["manifest"])[_YT_KEY] == before
    # Emptied timestamped folder (incl. .charts-sources) is cleaned up.
    assert _delete_dirs(e["root"]) == []


def test_restore_source_collision_does_not_overwrite(yt_env, monkeypatch):
    e = yt_env
    main_mod = _reload_app(monkeypatch, e["root"], e["manifest"])
    client = _authed_client(main_mod)
    assert client.post("/api/tageditor/delete",
                       data={"path": e["mp3"]}).status_code == 200
    blocker = e["charts"] / e["names"]["src"]
    blocker.write_bytes(b"newer download")
    r = _restore_all(client)
    assert r.status_code == 200, r.text            # library restore still succeeds
    assert os.path.isfile(e["mp3"])
    assert blocker.read_bytes() == b"newer download"   # never overwritten
    assert _YT_KEY in _entries(e["manifest"])


def test_delete_removes_all_matching_entries_only(yt_env, monkeypatch):
    e = yt_env
    # Extra "(2)" collision key + a re-download under a differently decorated
    # title, both for the SAME artist+title.
    data = json.loads(e["manifest"].read_text(encoding="utf-8"))
    data["entries"][_YT_KEY2] = _yt_entry(_YT_ARTIST, _YT_TITLE)
    data["entries"]["x - y.mp3"] = _yt_entry(_YT_ARTIST.upper(),
                                             _YT_TITLE + " (320 Kbps)")
    e["manifest"].write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    _write_min_mp3(str(e["charts"] / f"{_YT_ARTIST} - {_YT_TITLE} (2).mp3"))
    main_mod = _reload_app(monkeypatch, e["root"], e["manifest"])
    client = _authed_client(main_mod)

    assert client.post("/api/tageditor/delete",
                       data={"path": e["mp3"]}).status_code == 200
    entries = _entries(e["manifest"])
    assert set(entries) == {_WRONG_KEY, _OTHER_KEY, "other.mp3"}
    srcs = _only_delete_dir(e["root"]) / ".charts-sources"
    assert sorted(p.name for p in srcs.iterdir()) == sorted([
        e["names"]["src"], f"{_YT_ARTIST} - {_YT_TITLE} (2).mp3"])
    # Wrong-artist + different-artist sources untouched.
    assert (e["charts"] / e["names"]["wrong"]).is_file()
    assert (e["charts"] / e["names"]["other"]).is_file()

    # Restore brings every removed entry back.
    assert _restore_all(client).status_code == 200
    assert {_YT_KEY, _YT_KEY2, "x - y.mp3"} <= set(_entries(e["manifest"]))


def test_delete_soulseek_style_basename_key_still_works(tmp_path, monkeypatch):
    """Old behaviour: the entry keyed by the library basename is removed; no
    source file in the charts dir is fine."""
    root = tmp_path / "Music"
    (root / "Band" / "Tune").mkdir(parents=True)
    mp3 = root / "Band" / "Tune" / "Tune.mp3"
    _write_min_mp3(str(mp3))
    charts = tmp_path / "Charts"
    charts.mkdir()
    manifest = charts / ".charts-manifest.json"
    manifest.write_text(json.dumps({"entries": {
        "tune.mp3": {"artist": "Somebody Else", "title": "Other"}}}),
        encoding="utf-8")
    main_mod = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    r = client.post("/api/tageditor/delete", data={"path": str(mp3)})
    assert r.status_code == 200, r.text
    assert r.json()["manifest_cleared"] is True
    assert _entries(manifest) == {}
    assert not (_only_delete_dir(root) / ".charts-sources").exists()


def test_restore_old_format_sidecar(tmp_path, monkeypatch):
    """A sidecar written by the first shipped version (single manifest_key /
    manifest_entry, no source list) still restores and re-inserts the entry."""
    root = tmp_path / "Music"
    root.mkdir()
    folder = root / ".charts-trash" / "tageditor-delete-2026-11-05_16-26-44"
    trashed = folder / "Band" / "Tune" / "Tune.mp3"
    trashed.parent.mkdir(parents=True)
    _write_min_mp3(str(trashed))
    pathlib.Path(str(trashed) + ".trashinfo.json").write_text(json.dumps({
        "original_rel_path": "Band/Tune/Tune.mp3",
        "manifest_key": "tune.mp3",
        "manifest_entry": {"artist": "Band", "title": "Tune"},
        "deleted_utc": "2026-11-05T16:26:44+00:00"}), encoding="utf-8")
    charts = tmp_path / "Charts"
    charts.mkdir()
    manifest = charts / ".charts-manifest.json"
    manifest.write_text(json.dumps({"entries": {}}), encoding="utf-8")
    main_mod = _reload_app(monkeypatch, root, manifest)
    client = _authed_client(main_mod)
    r = _restore_all(client)
    assert r.status_code == 200, r.text
    assert (root / "Band" / "Tune" / "Tune.mp3").is_file()
    assert _entries(manifest)["tune.mp3"]["artist"] == "Band"


def test_sources_not_listed_and_removed_with_trash_entry(yt_env, monkeypatch):
    e = yt_env
    main_mod = _reload_app(monkeypatch, e["root"], e["manifest"])
    client = _authed_client(main_mod)
    assert client.post("/api/tageditor/delete",
                       data={"path": e["mp3"]}).status_code == 200
    listing = client.get("/api/tageditor/trash").json()
    assert [x["rel_path"] for x in listing["entries"]] == [
        f"{_only_delete_dir(e['root']).name}/{_YT_ARTIST}/{_YT_TITLE}/{_YT_TITLE}.mp3"]
    assert listing["count"] == 1
    assert listing["entries"][0]["restorable"] is True
    assert listing["total_bytes"] == listing["entries"][0]["bytes"]
    folder = _only_delete_dir(e["root"])
    assert (folder / ".charts-sources" / e["names"]["src"]).is_file()

    # Permanent delete of the song's trash row removes the sources, too.
    r = client.post("/api/tageditor/trash/delete",
                    data={"path": listing["entries"][0]["path"]})
    assert r.status_code == 200, r.text
    assert not folder.exists()


def test_empty_trash_removes_sources(yt_env, monkeypatch):
    e = yt_env
    main_mod = _reload_app(monkeypatch, e["root"], e["manifest"])
    client = _authed_client(main_mod)
    assert client.post("/api/tageditor/delete",
                       data={"path": e["mp3"]}).status_code == 200
    r = client.post("/api/tageditor/trash/empty", data={"confirm": "empty"})
    assert r.status_code == 200, r.text
    assert list((e["root"] / ".charts-trash").iterdir()) == []


def test_delete_succeeds_when_manifest_missing(yt_env, monkeypatch):
    e = yt_env
    e["manifest"].unlink()
    main_mod = _reload_app(monkeypatch, e["root"], e["manifest"])
    client = _authed_client(main_mod)
    r = client.post("/api/tageditor/delete", data={"path": e["mp3"]})
    assert r.status_code == 200, r.text
    assert r.json()["manifest_cleared"] is False
    assert not os.path.exists(e["mp3"])


# Restore import-time i18n state so test ordering never leaks a language.
@pytest.fixture(autouse=True)
def _restore_setup():
    yield
    i18n.setup()
