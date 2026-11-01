"""
Move-path tests for the Manual Tag Editor "folder follows the artist tag"
feature (editor.save_song + editor.move_after_decision + library move
primitives), covering the authoritative 6-rule precedence.

All run WITHOUT network/Docker: tiny tmp MP3s written via mutagen, config.MUSIC_DIR
pointed at a tmp root. They never touch a real library path.

6-rule precedence (first match wins):
  1. exact existing folder-name match          -> AUTO move
  2. strict _artist_key match (diff spelling)   -> AUTO move
  3. no strict/exact match AND old folder empties out -> RENAME old folder
  4. _loose_key match to a folder with OTHER songs    -> decision dialog
  5. exact target file / same _title_key in dest album -> collision dialog
  6. otherwise (new artist, old folder not emptied)    -> AUTO create + move

They pin:
  * rule 1 exact folder-name merge;
  * rule 2 strict-key merge (casing/apostrophe/accent variants, one folder);
  * rule 3 rename-when-old-folder-empties;
  * rule 4 loose-match-with-other-songs -> dialog, NO move until a choice;
  * rule 5 collision (exact + same-title-different-name) -> dialog, NO move;
  * rule 6 truly new artist -> auto create + cleanup old empty folder;
  * second step: into_existing + create_new both move; cancel keeps the saved
    tags at the OLD path (both dialog variants);
  * scope guard skips Lidarr (Year)/NN - Title + curated compilations;
  * cleanup skips @eaDir, keeps real-audio siblings;
  * mtime preserved across os.rename;
  * EXDEV hard refuse (no copy fallback, tags kept);
  * the search index follows the move (new path found, old path gone);
  * a title-only change on a single does not trigger an artist-folder move.
"""

import errno
import os

import pytest

from app import config, editor, library


# --- tiny valid MP3 ----------------------------------------------------------

def _write_dummy_mp3(path: str) -> None:
    header = b"\xff\xfb\x90\x40"
    frame = header + b"\x00" * (417 - 4)
    with open(path, "wb") as fh:
        fh.write(frame * 30)


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


@pytest.fixture(autouse=True)
def _preserve_on(monkeypatch):
    monkeypatch.setattr(config, "PRESERVE_MTIME", True)


def _make_single(root, artist, title):
    """Create a stack-single <artist>/<title>/<title>.mp3 under root (tagged)."""
    album_dir = root / artist / title
    album_dir.mkdir(parents=True, exist_ok=True)
    p = album_dir / f"{title}.mp3"
    _write_dummy_mp3(str(p))
    _tag(str(p), artist=artist, albumartist=artist, title=title, album=title)
    return str(p)


@pytest.fixture
def music(tmp_path, monkeypatch):
    root = tmp_path / "Music"
    root.mkdir()
    monkeypatch.setattr(config, "MUSIC_DIR", str(root))
    return root


# --- rule 1: exact existing folder-name match -> AUTO move -------------------

def test_rule1_exact_folder_name_merge(music):
    # An existing 'Beyonce/' folder with another song; the save's cleaned artist
    # equals that folder name exactly -> move into it, no dialog.
    _make_single(music, "Beyonce", "Halo")
    src = _make_single(music, "Beyoncé Knowles", "Single Ladies")

    res = editor.save_song(src, {"artist": "Beyonce", "title": "Single Ladies",
                                 "album": "Single Ladies"})

    assert res.get("decision") is None
    assert res["moved"] is True
    new_path = os.path.join(str(music), "Beyonce", "Single Ladies",
                            "Single Ladies.mp3")
    assert os.path.isfile(new_path)
    assert res["moved_to_artist"] == "Beyonce"


# --- rule 2: strict _artist_key match (different spelling) -> AUTO move -------

def test_rule2_strict_key_casing_variant_merge(music):
    # Existing 'Rod Stewart/' with another song; the user types 'rod stewart'
    # (casing variant). strict key folds both to 'rodstewart' -> auto merge.
    _make_single(music, "Rod Stewart", "Sailing")
    src = _make_single(music, "ROD STEWART TEMP", "Maggie May")

    res = editor.save_song(src, {"artist": "rod stewart", "title": "Maggie May",
                                 "album": "Maggie May"})

    assert res.get("decision") is None
    assert res["moved"] is True
    new_path = os.path.join(str(music), "Rod Stewart", "Maggie May",
                            "Maggie May.mp3")
    assert os.path.isfile(new_path)
    # Moved into the established spelling; the temp source folder is gone.
    assert not os.path.isdir(os.path.join(str(music), "ROD STEWART TEMP"))
    # Case-insensitive compare: on a case-INsensitive test FS the exact-path
    # fast path in _find_artist_dir can echo the typed casing; on the real
    # case-sensitive ext4 library the established 'Rod Stewart' spelling wins.
    assert res["moved_to_artist"].lower() == "rod stewart"


def test_rule2_strict_key_accent_variant_merge(music):
    # Motörhead/Motorhead: NFKD accent-fold makes the strict keys equal.
    _make_single(music, "Motörhead", "Ace of Spades")
    src = _make_single(music, "Motorhead Temp", "Overkill")

    res = editor.save_song(src, {"artist": "Motorhead", "title": "Overkill",
                                 "album": "Overkill"})

    assert res["moved"] is True
    assert os.path.isfile(os.path.join(str(music), "Motörhead", "Overkill",
                                       "Overkill.mp3"))
    assert res["moved_to_artist"] == "Motörhead"


# --- rule 3: rename old folder when it empties out ---------------------------

def test_rule3_rename_old_folder_when_empty(music):
    # Lone 'Pink/' folder; save artist 'P!nk'. No exact/strict match (pnk!=pink),
    # no other loose folder, and the old folder empties out -> rename Pink/ to
    # the new cleaned artist 'P!nk/', carrying the file along.
    src = _make_single(music, "Pink", "Stupid Girls")

    res = editor.save_song(src, {"artist": "P!nk", "title": "Stupid Girls",
                                 "album": "Stupid Girls"})

    assert res.get("decision") is None
    assert res["moved"] is True
    assert not os.path.isdir(os.path.join(str(music), "Pink"))
    new_path = os.path.join(str(music), "P!nk", "Stupid Girls",
                            "Stupid Girls.mp3")
    assert os.path.isfile(new_path)
    assert res["path"] == new_path
    assert res["moved_to_artist"] == "P!nk"


def test_rule3_rename_old_folder_artist_and_title_change(music):
    # Lone 'Pink/' folder; the save changes BOTH the artist ('P!nk') and the
    # title ('Smart Girls'). Rule 3 renames the artist folder, and because the
    # title also changed the inner album folder + filename must follow too. The
    # returned path must EXIST on disk and be findable (regression: the folder
    # rename alone used to leave the file under its old name while the response
    # pointed at the new-title path that was not on disk).
    src = _make_single(music, "Pink", "Stupid Girls")
    library.build_search_index(str(music))

    res = editor.save_song(src, {"artist": "P!nk", "title": "Smart Girls",
                                 "album": "Smart Girls"})

    assert res.get("decision") is None
    assert res["moved"] is True
    assert res["moved_to_artist"] == "P!nk"
    # Old artist folder gone, new-title path present on disk.
    assert not os.path.isdir(os.path.join(str(music), "Pink"))
    new_path = os.path.join(str(music), "P!nk", "Smart Girls", "Smart Girls.mp3")
    assert res["path"] == new_path
    assert os.path.isfile(new_path)
    # No stale old-title folder/file left behind inside the renamed folder.
    assert not os.path.isdir(os.path.join(str(music), "P!nk", "Stupid Girls"))
    # The saved tags live at the returned path and it is findable by search.
    assert _read_easy(new_path, "artist") == "P!nk"
    assert _read_easy(new_path, "title") == "Smart Girls"
    hits = library.search("smart girls", 20)
    paths = [h["path"] for h in hits]
    assert new_path in paths
    assert src not in paths


# --- rule 4: loose match to folder with OTHER songs -> dialog, no move -------

def test_rule4_loose_match_with_other_songs_dialog(music):
    # Existing 'Pink/' that keeps OTHER songs; the user types 'P!nk' on a file
    # currently under a DIFFERENT source folder. Loose key matches (pink==pink
    # after ! -> i), strict does not -> decision dialog, NO move.
    _make_single(music, "Pink", "Get the Party Started")
    src = _make_single(music, "Pink Temp", "So What")

    res = editor.save_song(src, {"artist": "P!nk", "title": "So What",
                                 "album": "So What"})

    assert res["moved"] is False
    dec = res["decision"]
    assert dec["variant"] == "loose"
    assert dec["existing_folder"] == "Pink"
    assert dec["other_count"] == 1
    assert dec["new_folder"] == "P!nk"
    # No move happened: the file is still at the old path with the saved tag.
    assert os.path.isfile(src)
    assert _read_easy(src, "artist") == "P!nk"


def test_rule4_dialog_choice_into_existing_moves(music):
    _make_single(music, "Pink", "Get the Party Started")
    src = _make_single(music, "Pink Temp", "So What")
    res = editor.save_song(src, {"artist": "P!nk", "title": "So What",
                                 "album": "So What"})
    dec = res["decision"]

    out = editor.move_after_decision(src, "into_existing", dec["artist"],
                                     dec["title"], dec["album"])

    assert out["moved"] is True
    new_path = os.path.join(str(music), "Pink", "So What", "So What.mp3")
    assert os.path.isfile(new_path)
    assert out["path"] == new_path
    assert out["moved_to_artist"] == "Pink"
    assert not os.path.isfile(src)


def test_rule4_dialog_choice_create_new_moves(music):
    _make_single(music, "Pink", "Get the Party Started")
    src = _make_single(music, "Pink Temp", "So What")
    res = editor.save_song(src, {"artist": "P!nk", "title": "So What",
                                 "album": "So What"})
    dec = res["decision"]

    out = editor.move_after_decision(src, "create_new", dec["artist"],
                                     dec["title"], dec["album"])

    assert out["moved"] is True
    new_path = os.path.join(str(music), "P!nk", "So What", "So What.mp3")
    assert os.path.isfile(new_path)
    assert out["moved_to_artist"] == "P!nk"
    # The established 'Pink/' folder was NOT touched.
    assert os.path.isfile(os.path.join(str(music), "Pink",
                                       "Get the Party Started",
                                       "Get the Party Started.mp3"))


def test_rule4_dialog_cancel_keeps_saved_tags_at_old_path(music):
    _make_single(music, "Pink", "Get the Party Started")
    src = _make_single(music, "Pink Temp", "So What")
    res = editor.save_song(src, {"artist": "P!nk", "title": "So What",
                                 "album": "So What"})
    assert res["decision"]["variant"] == "loose"

    out = editor.move_after_decision(src, "cancel", "P!nk", "So What", "So What")

    assert out["moved"] is False
    assert out["move_reason"] == "kept_in_place"
    # Saved tags remain at the ORIGINAL path; no new P!nk/ folder was made.
    assert os.path.isfile(src)
    assert _read_easy(src, "artist") == "P!nk"
    assert not os.path.isdir(os.path.join(str(music), "P!nk"))


# --- rule 5: collision -> dialog, no move ------------------------------------

def test_rule5_collision_exact_target_dialog(music):
    # Destination 'Rod Stewart/' already holds the exact target file (strict
    # match path), and another song so it is not a rename candidate.
    _make_single(music, "Rod Stewart", "Other")
    _make_single(music, "Rod Stewart", "Sailing")
    src = _make_single(music, "rod stewart temp", "Sailing")

    res = editor.save_song(src, {"artist": "Rod Stewart", "title": "Sailing",
                                 "album": "Sailing"})

    assert res["moved"] is False
    dec = res["decision"]
    assert dec["variant"] == "collision"
    assert dec["existing_folder"] == "Rod Stewart"
    # No move, no '(2)', old path intact with the saved tags written in place.
    assert os.path.isfile(src)
    assert _read_easy(src, "artist") == "Rod Stewart"
    dest_album = os.path.join(str(music), "Rod Stewart", "Sailing")
    assert set(os.listdir(dest_album)) == {"Sailing.mp3"}


def test_rule5_collision_same_title_diff_name_dialog(music):
    # Destination album has a Lidarr-style 'NN - Title.mp3' for the same song.
    _make_single(music, "Rod Stewart", "Other")
    dest_album = music / "Rod Stewart" / "Sailing"
    dest_album.mkdir(parents=True)
    lidarr = dest_album / "06 - Sailing.mp3"
    _write_dummy_mp3(str(lidarr))
    src = _make_single(music, "rod stewart temp", "Sailing")

    res = editor.save_song(src, {"artist": "Rod Stewart", "title": "Sailing",
                                 "album": "Sailing"})

    assert res["moved"] is False
    assert res["decision"]["variant"] == "collision"
    assert os.path.isfile(src)


def test_rule5_collision_cancel_keeps_saved_tags(music):
    _make_single(music, "Rod Stewart", "Other")
    _make_single(music, "Rod Stewart", "Sailing")
    src = _make_single(music, "rod stewart temp", "Sailing")
    res = editor.save_song(src, {"artist": "Rod Stewart", "title": "Sailing",
                                 "album": "Sailing"})
    assert res["decision"]["variant"] == "collision"

    out = editor.move_after_decision(src, "cancel", "Rod Stewart", "Sailing",
                                     "Sailing")

    assert out["moved"] is False
    assert out["move_reason"] == "kept_in_place"
    assert os.path.isfile(src)
    assert _read_easy(src, "artist") == "Rod Stewart"
    # The existing destination song was never overwritten.
    assert os.path.isfile(os.path.join(str(music), "Rod Stewart", "Sailing",
                                       "Sailing.mp3"))


# --- rule 6: truly new artist -> AUTO create + cleanup old empty folder ------

def test_rule6_new_artist_folder_and_cleanup_old(music):
    # 'Pink/' also keeps another song (so rule 3 rename does not apply) and the
    # new artist 'Beyonce' has no existing/loose folder -> auto create + move.
    _make_single(music, "Pink", "Get the Party Started")
    src = _make_single(music, "Pink", "Stupid Girls")

    res = editor.save_song(src, {"artist": "Beyonce", "title": "Stupid Girls",
                                 "album": "Stupid Girls"})

    assert res.get("decision") is None
    assert res["moved"] is True
    new_path = os.path.join(str(music), "Beyonce", "Stupid Girls",
                            "Stupid Girls.mp3")
    assert os.path.isfile(new_path)
    # Pink/ still exists (the other song) but its empty album folder is gone.
    assert os.path.isdir(os.path.join(str(music), "Pink"))
    assert not os.path.isdir(os.path.join(str(music), "Pink", "Stupid Girls"))


# --- scope guard: Lidarr '<Album> (Year)/' folder ---------------------------

def test_scope_guard_skips_lidarr_year_folder(music):
    album_dir = music / "Pink" / "I'm Not Dead (2006)"
    album_dir.mkdir(parents=True)
    p = album_dir / "Stupid Girls.mp3"
    _write_dummy_mp3(str(p))
    _tag(str(p), artist="Pink", albumartist="Pink", title="Stupid Girls",
         album="I'm Not Dead")

    res = editor.save_song(str(p), {"artist": "P!nk"})

    assert res["moved"] is False
    assert res["move_reason"] == "album_managed"
    assert os.path.isfile(str(p))  # kept in place


def test_scope_guard_skips_nn_title_trackname(music):
    album_dir = music / "Pink" / "Stupid Girls"
    album_dir.mkdir(parents=True)
    p = album_dir / "06 - Stupid Girls.mp3"
    _write_dummy_mp3(str(p))
    _tag(str(p), artist="Pink", albumartist="Pink", title="Stupid Girls",
         album="Stupid Girls")

    res = editor.save_song(str(p), {"artist": "P!nk"})

    assert res["moved"] is False
    assert res["move_reason"] == "album_managed"
    assert os.path.isfile(str(p))


def test_scope_guard_skips_curated_compilation(music):
    album_dir = music / "Various Artists" / "Bravo Hits 42"
    album_dir.mkdir(parents=True)
    p = album_dir / "Stupid Girls.mp3"
    _write_dummy_mp3(str(p))
    _tag(str(p), artist="Various Artists", albumartist="Various Artists",
         title="Stupid Girls", album="Bravo Hits 42")

    res = editor.save_song(str(p), {"artist": "P!nk"})

    assert res["moved"] is False
    assert res["move_reason"] == "album_managed"
    assert os.path.isfile(str(p))


# --- cleanup skips @eaDir, keeps real-audio siblings -------------------------

def test_cleanup_skips_eaDir(music):
    # Rule 6 path (another song keeps the folder from being a rename candidate).
    _make_single(music, "Pink", "Keep Me")
    src = _make_single(music, "Pink", "Stupid Girls")
    eadir = music / "Pink" / "Stupid Girls" / "@eaDir"
    eadir.mkdir()
    (eadir / "thumb.jpg").write_bytes(b"x")

    res = editor.save_song(src, {"artist": "Beyonce", "title": "Stupid Girls",
                                 "album": "Stupid Girls"})

    assert res["moved"] is True
    # The @eaDir-only source album folder is removed even though @eaDir remained.
    assert not os.path.isdir(os.path.join(str(music), "Pink", "Stupid Girls"))


def test_cleanup_keeps_sibling_audio(music):
    _make_single(music, "Pink", "Other Song")
    src = _make_single(music, "Pink", "Stupid Girls")

    res = editor.save_song(src, {"artist": "Beyonce", "title": "Stupid Girls",
                                 "album": "Stupid Girls"})

    assert res["moved"] is True
    assert os.path.isdir(os.path.join(str(music), "Pink"))
    assert os.path.isfile(
        os.path.join(str(music), "Pink", "Other Song", "Other Song.mp3"))
    assert not os.path.isdir(os.path.join(str(music), "Pink", "Stupid Girls"))


# --- mtime preserved across os.rename (rule 3 rename path) -------------------

def test_mtime_preserved_after_move(music):
    # Rule 6 move path (keep a sibling so the folder is not a rename candidate).
    _make_single(music, "Pink", "Keep Me")
    src = _make_single(music, "Pink", "Stupid Girls")
    old_ts = 1_000_000_000.0
    os.utime(src, (old_ts, old_ts))

    res = editor.save_song(src, {"artist": "Beyonce", "title": "Stupid Girls",
                                 "album": "Stupid Girls"})

    assert res["moved"] is True
    assert abs(os.stat(res["path"]).st_mtime - old_ts) <= 1.0


# --- EXDEV hard refuse (no copy fallback) ------------------------------------

def test_exdev_hard_refuse(music, monkeypatch):
    _make_single(music, "Pink", "Keep Me")
    src = _make_single(music, "Pink", "Stupid Girls")

    def fake_rename(a, b):
        raise OSError(errno.EXDEV, "cross-device link not permitted")

    monkeypatch.setattr(os, "rename", fake_rename)

    res = editor.save_song(src, {"artist": "Beyonce", "title": "Stupid Girls",
                                 "album": "Stupid Girls"})

    assert res["moved"] is False
    assert res["move_reason"] == "cross_device"
    # File NOT copied to the destination; original stays, tags still written.
    assert os.path.isfile(src)
    assert _read_easy(src, "artist") == "Beyonce"
    assert not os.path.isfile(
        os.path.join(str(music), "Beyonce", "Stupid Girls", "Stupid Girls.mp3"))


# --- search index follows the move -------------------------------------------

def test_index_follows_move(music):
    _make_single(music, "Pink", "Keep Me")
    src = _make_single(music, "Pink", "Stupid Girls")
    library.build_search_index(str(music))

    editor.save_song(src, {"artist": "Beyonce", "title": "Stupid Girls",
                           "album": "Stupid Girls"})

    hits = library.search("beyonce", 20)
    paths = [h["path"] for h in hits]
    assert any(p.endswith(os.path.join("Beyonce", "Stupid Girls",
                                       "Stupid Girls.mp3")) for p in paths)
    assert src not in paths


# --- title-only change on a single -> no artist-folder move ------------------

def test_title_only_change_no_artist_move(music):
    src = _make_single(music, "Pink", "Stupid Girls")

    res = editor.save_song(src, {"artist": "Pink", "title": "Smart Girls",
                                 "album": "Stupid Girls"})

    # Artist did not change -> the move branch is not entered; in-place behavior.
    assert res["moved"] is False
    assert res["move_reason"] == ""
    assert os.path.isfile(src)
    assert _read_easy(src, "title") == "Smart Girls"
