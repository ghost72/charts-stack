"""
charts-upgrade re-correction tests: the replace branch must capture the old
file's year/album BEFORE trashing and apply the "never worse" floor (Decision 5).

No network/ffmpeg. Drives importer.import_downloads with a tiny valid MP3 and a
stubbed manifest/build_target_path, like test_mtime_preserve.py.
"""

import os
import time

import pytest

from app import config, importer, tags


def _write_dummy_mp3(path: str) -> None:
    header = b"\xff\xfb\x90\x40"
    frame = header + b"\x00" * (417 - 4)
    with open(path, "wb") as fh:
        fh.write(frame * 30)


def _read_tag(path, key):
    from mutagen import File as MutagenFile
    audio = MutagenFile(path, easy=True)
    return (audio.get(key) or [""])[0]


def _setup(tmp_path, monkeypatch, *, old_year, old_album, resolver_meta):
    music = tmp_path / "music"
    downloads = tmp_path / "downloads"
    music.mkdir()
    downloads.mkdir()

    album_dir = music / "A" / "Old Album"
    album_dir.mkdir(parents=True)
    existing = album_dir / "T.mp3"
    _write_dummy_mp3(str(existing))
    # Pre-tag the OLD library file with a year/album in its tags.
    from mutagen import File as MutagenFile
    au = MutagenFile(str(existing), easy=True)
    if old_year:
        au["date"] = old_year
    if old_album:
        au["album"] = old_album
    au["title"] = "T"
    au.save()

    src = downloads / "incoming.mp3"
    _write_dummy_mp3(str(src))

    monkeypatch.setattr(
        tags, "manifest_lookup",
        lambda path: {"artist": "A", "title": "T", "album": "T",
                      "replace": True, "source": "manuell"})
    monkeypatch.setattr(
        tags, "build_target_path",
        lambda music_dir, path: (str(existing), ("A", "T", "T", True, False)))
    monkeypatch.setattr(importer, "_song_present_for_artist",
                        lambda md, a, t: str(existing))
    monkeypatch.setattr(importer.quality, "quality_key",
                        lambda path, br: 2.0 if "incoming" in path else 1.0)
    monkeypatch.setattr(importer, "_read_bitrate", lambda path: 320)
    monkeypatch.setattr(importer.cover, "embed_cover", lambda *a, **k: False)
    monkeypatch.setattr(importer.replaygain, "apply_track_gain", lambda *a, **k: False)
    monkeypatch.setattr(importer, "_hint_year", lambda a, t: ("", False))
    monkeypatch.setattr(importer, "_append_import_log", lambda *a, **k: None)
    monkeypatch.setattr(importer.resolve, "resolve_metadata",
                        lambda a, t: resolver_meta)

    monkeypatch.setattr(config, "MUSIC_DIR", str(music))
    monkeypatch.setattr(config, "DOWNLOADS_DIR", str(downloads))
    monkeypatch.setattr(config, "COPY_MODE", True)
    monkeypatch.setattr(config, "REQUIRE_MANIFEST", True)
    monkeypatch.setattr(config, "REJECT_ENABLED", False)
    monkeypatch.setattr(config, "MIN_AGE_SECONDS", 0)
    monkeypatch.setattr(config, "PRESERVE_MTIME", True)
    return str(existing)


def test_upgrade_resolver_miss_retains_old_year_album(tmp_path, monkeypatch):
    existing = _setup(tmp_path, monkeypatch, old_year="1984",
                      old_album="The Works",
                      resolver_meta=importer.resolve.ResolvedMeta())
    importer.import_downloads()
    # Floor carried the old metadata forward.
    assert _read_tag(existing, "date") == "1984"
    assert _read_tag(existing, "album") == "The Works"


def test_upgrade_resolver_hit_uses_fresh_metadata(tmp_path, monkeypatch):
    existing = _setup(
        tmp_path, monkeypatch, old_year="1984", old_album="Old Compilation",
        resolver_meta=importer.resolve.ResolvedMeta(
            year="1985", album="Fresh Album", album_year="1985", track="2"))
    importer.import_downloads()
    assert _read_tag(existing, "date") == "1985"
    assert _read_tag(existing, "album") == "Fresh Album"
    assert _read_tag(existing, "tracknumber") == "2"


def test_upgrade_floor_ignores_compilation_old_album(tmp_path, monkeypatch):
    # Old album is a compilation -> the floor must NOT carry it; album stays
    # the title.
    existing = _setup(tmp_path, monkeypatch, old_year="", old_album="Bravo Hits 7",
                      resolver_meta=importer.resolve.ResolvedMeta())
    importer.import_downloads()
    assert _read_tag(existing, "album") == "T"  # not the compilation


def test_upgrade_inherits_old_mtime_with_recorrection(tmp_path, monkeypatch):
    existing = _setup(tmp_path, monkeypatch, old_year="1984", old_album="Works",
                      resolver_meta=importer.resolve.ResolvedMeta(
                          year="1985", album="Fresh", album_year="1985", track="1"))
    old_ts = 1_000_000_000.0
    os.utime(existing, (old_ts, old_ts))
    importer.import_downloads()
    assert abs(os.stat(existing).st_mtime - old_ts) <= 1.0
