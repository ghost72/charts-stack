"""
Unit tests for import-time year precedence in importer.import_downloads.

No network. Drives the real import path with a tiny valid MP3 and a stubbed
manifest/build_target_path, asserting which free year is written at import and
when a pending marker is created (Decision 3). MusicBrainz is never called at
import.
"""

import json
import os

import pytest

from app import config, importer, tags, yearmap


def _write_dummy_mp3(path: str) -> None:
    header = b"\xff\xfb\x90\x40"
    frame = header + b"\x00" * (417 - 4)
    with open(path, "wb") as fh:
        fh.write(frame * 30)


def _read_date(path: str) -> str:
    from mutagen import File as MutagenFile
    audio = MutagenFile(path, easy=True)
    return (audio.get("date") or [""])[0]


@pytest.fixture
def env(tmp_path, monkeypatch):
    music = tmp_path / "music"
    downloads = tmp_path / "downloads"
    charts = tmp_path / "charts"
    music.mkdir()
    downloads.mkdir()
    charts.mkdir()
    year_map = charts / ".charts-flashback-years.json"

    monkeypatch.setattr(config, "MUSIC_DIR", str(music))
    monkeypatch.setattr(config, "DOWNLOADS_DIR", str(downloads))
    monkeypatch.setattr(config, "COPY_MODE", True)
    monkeypatch.setattr(config, "REQUIRE_MANIFEST", True)
    monkeypatch.setattr(config, "REJECT_ENABLED", False)
    monkeypatch.setattr(config, "MIN_AGE_SECONDS", 0)
    monkeypatch.setattr(config, "PRESERVE_MTIME", True)
    monkeypatch.setattr(yearmap, "YEAR_MAP_FILE", str(year_map))
    # Silence the content side effects not under test.
    monkeypatch.setattr(importer.cover, "embed_cover", lambda *a, **k: False)
    monkeypatch.setattr(importer.replaygain, "apply_track_gain",
                        lambda *a, **k: False)
    monkeypatch.setattr(importer, "_append_import_log", lambda *a, **k: None)
    # No cached miss unless a test says so.
    monkeypatch.setattr(importer.mbyear, "is_cached_miss", lambda a, t: False)
    # By default the import-time resolver MISSES (empty meta) so the free-year
    # precedence is exercised in isolation and no network is touched. Individual
    # tests override this to assert the resolver tier.
    monkeypatch.setattr(importer.resolve, "resolve_metadata",
                        lambda a, t: importer.resolve.ResolvedMeta())

    return {"music": music, "downloads": downloads, "year_map": year_map,
            "monkeypatch": monkeypatch}


def _stub_target(env, artist, title, album, target_path):
    env["monkeypatch"].setattr(
        tags, "build_target_path",
        lambda music_dir, path: (target_path, (artist, title, album, True, False)))
    env["monkeypatch"].setattr(
        tags, "manifest_lookup",
        lambda path: {"artist": artist, "title": title, "album": album})


def _make_download(env, name="incoming.mp3", year_in_tag=None):
    src = env["downloads"] / name
    _write_dummy_mp3(str(src))
    if year_in_tag:
        from mutagen import File as MutagenFile
        a = MutagenFile(str(src), easy=True)
        a["date"] = year_in_tag
        a.save()
    return str(src)


def _read_map(env):
    try:
        return json.loads(env["year_map"].read_text("utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


# --- (a) year-map hint wins and is tagged, no marker ------------------------

def test_hint_bare_string_wins_no_marker(env):
    _make_download(env)
    target = str(env["music"] / "Queen" / "Under Pressure" / "Under Pressure.mp3")
    _stub_target(env, "Queen", "Under Pressure", "Under Pressure", target)
    env["year_map"].write_text(
        json.dumps({"queen|under pressure": "1981"}), encoding="utf-8")
    env["monkeypatch"].setattr(importer, "_song_present_for_artist",
                               lambda md, a, t: None)
    importer.import_downloads()
    assert _read_date(target) == "1981"
    # Still a bare string, no pending marker added.
    assert _read_map(env)["queen|under pressure"] == "1981"


# --- conflict: pending midpoint hint + existing (YYYY) folder -> folder wins -

def test_pending_midpoint_vs_lidarr_folder_folder_wins(env):
    _make_download(env)
    # An existing Lidarr (1986) album track for the same artist/title.
    lidarr_dir = env["music"] / "A-ha" / "Take On Me (1986)"
    lidarr_dir.mkdir(parents=True)
    existing = lidarr_dir / "01 - Take On Me.mp3"
    _write_dummy_mp3(str(existing))
    target = str(env["music"] / "A-ha" / "Take On Me" / "Take On Me.mp3")
    _stub_target(env, "A-ha", "Take On Me", "Take On Me", target)
    # charts-energy midpoint hint 1985 (pending object).
    env["year_map"].write_text(json.dumps({"a-ha|take on me": {
        "year": "1985", "pending": True, "decade": "1980",
        "src": "energy-library", "tries": 0}}), encoding="utf-8")
    # The existing library match sits in the (1986) folder.
    env["monkeypatch"].setattr(importer, "_song_present_for_artist",
                               lambda md, a, t: str(existing))
    # Make the "existing file" branch NOT short-circuit: treat the new as a
    # strict upgrade so it proceeds to write the target.
    env["monkeypatch"].setattr(importer.quality, "quality_key",
                               lambda path, br: 2.0 if "incoming" in path or "Take On Me/Take On Me" in path else 1.0)
    env["monkeypatch"].setattr(importer, "_read_bitrate", lambda path: 320)
    env["monkeypatch"].setattr(
        tags, "manifest_lookup",
        lambda path: {"artist": "A-ha", "title": "Take On Me",
                      "album": "Take On Me", "replace": True})
    importer.import_downloads()
    # The written target carries the Lidarr folder year 1986, not the midpoint.
    assert _read_date(str(existing)) == "1986"
    # Confirmed to the folder year, no pending marker.
    assert _read_map(env)["a-ha|take on me"] == "1986"


# --- (c') existing (YYYY) folder adopted, no hint, no marker ----------------

def test_existing_year_folder_adopted_no_marker(env):
    _make_download(env)
    lidarr_dir = env["music"] / "Madonna" / "Vogue (1990)"
    lidarr_dir.mkdir(parents=True)
    existing = lidarr_dir / "01 - Vogue.mp3"
    _write_dummy_mp3(str(existing))
    target = str(env["music"] / "Madonna" / "Vogue" / "Vogue.mp3")
    _stub_target(env, "Madonna", "Vogue", "Vogue", target)
    env["year_map"].write_text(json.dumps({}), encoding="utf-8")
    env["monkeypatch"].setattr(importer, "_song_present_for_artist",
                               lambda md, a, t: str(existing))
    env["monkeypatch"].setattr(importer.quality, "quality_key",
                               lambda path, br: 2.0 if "incoming" in path or "Vogue/Vogue" in path else 1.0)
    env["monkeypatch"].setattr(importer, "_read_bitrate", lambda path: 320)
    env["monkeypatch"].setattr(
        tags, "manifest_lookup",
        lambda path: {"artist": "Madonna", "title": "Vogue",
                      "album": "Vogue", "replace": True})
    importer.import_downloads()
    assert _read_date(str(existing)) == "1990"
    assert "madonna|vogue" not in _read_map(env)  # no pending marker


# --- first-ever import (no match) -> pending generic marker -----------------

def test_first_import_no_match_goes_pending(env):
    _make_download(env)
    target = str(env["music"] / "New Act" / "New Song" / "New Song.mp3")
    _stub_target(env, "New Act", "New Song", "New Song", target)
    env["year_map"].write_text(json.dumps({}), encoding="utf-8")
    env["monkeypatch"].setattr(importer, "_song_present_for_artist",
                               lambda md, a, t: None)
    importer.import_downloads()
    assert _read_date(target) == ""     # no year written
    m = _read_map(env)["new act|new song"]
    assert m == {"year": "", "pending": True, "src": "import-generic",
                 "tries": 0}


# --- not trusting a download's embedded date --------------------------------

def test_download_embedded_date_not_trusted(env):
    # The fresh download carries an (untrusted) embedded date; import must still
    # go generic-pending, ACTIVELY CLEAR that date, and NOT adopt it.
    _make_download(env, year_in_tag="2008")
    target = str(env["music"] / "Rando" / "Mystery" / "Mystery.mp3")
    _stub_target(env, "Rando", "Mystery", "Mystery", target)
    env["year_map"].write_text(json.dumps({}), encoding="utf-8")
    env["monkeypatch"].setattr(importer, "_song_present_for_artist",
                               lambda md, a, t: None)
    importer.import_downloads()
    assert _read_date(target) == ""  # the stale 2008 date was actively removed
    m = _read_map(env)["rando|mystery"]
    assert m["pending"] is True and m["year"] == ""  # date NOT adopted


def test_no_year_clear_is_noop_without_date(env):
    # A no-year import of a file with no date must not throw and leaves no date.
    _make_download(env)  # no embedded date
    target = str(env["music"] / "Plain" / "Track" / "Track.mp3")
    _stub_target(env, "Plain", "Track", "Track", target)
    env["year_map"].write_text(json.dumps({}), encoding="utf-8")
    env["monkeypatch"].setattr(importer, "_song_present_for_artist",
                               lambda md, a, t: None)
    importer.import_downloads()
    assert _read_date(target) == ""


# --- (d) resolver tier writes year + album + track when map/folder empty ----

def _read_tag(path, key):
    from mutagen import File as MutagenFile
    audio = MutagenFile(path, easy=True)
    return (audio.get(key) or [""])[0]


def test_resolver_tier_writes_year_album_track(env):
    _make_download(env)
    target = str(env["music"] / "Vanessa Paradis" / "Joe le taxi" / "Joe le taxi.mp3")
    _stub_target(env, "Vanessa Paradis", "Joe le taxi", "Joe le taxi", target)
    env["year_map"].write_text(json.dumps({}), encoding="utf-8")
    env["monkeypatch"].setattr(importer, "_song_present_for_artist",
                               lambda md, a, t: None)
    env["monkeypatch"].setattr(
        importer.resolve, "resolve_metadata",
        lambda a, t: importer.resolve.ResolvedMeta(
            year="1987", album="M&J", album_year="1988", track="3"))
    importer.import_downloads()
    assert _read_date(target) == "1987"
    assert _read_tag(target, "album") == "M&J"
    assert _read_tag(target, "tracknumber") == "3"
    # Resolved -> no pending marker.
    assert "vanessa paradis|joe le taxi" not in _read_map(env)


def test_map_year_wins_but_resolver_album_track_applied(env):
    _make_download(env)
    target = str(env["music"] / "Queen" / "Radio Ga Ga" / "Radio Ga Ga.mp3")
    _stub_target(env, "Queen", "Radio Ga Ga", "Radio Ga Ga", target)
    env["year_map"].write_text(
        json.dumps({"queen|radio ga ga": "1984"}), encoding="utf-8")
    env["monkeypatch"].setattr(importer, "_song_present_for_artist",
                               lambda md, a, t: None)
    env["monkeypatch"].setattr(
        importer.resolve, "resolve_metadata",
        lambda a, t: importer.resolve.ResolvedMeta(
            year="1999", album="The Works", album_year="1984", track="2"))
    importer.import_downloads()
    # Map year wins over the resolver year ...
    assert _read_date(target) == "1984"
    # ... while the resolver album + track are still applied.
    assert _read_tag(target, "album") == "The Works"
    assert _read_tag(target, "tracknumber") == "2"


def test_resolver_compilation_album_not_applied(env):
    _make_download(env)
    target = str(env["music"] / "Some Act" / "Hit" / "Hit.mp3")
    _stub_target(env, "Some Act", "Hit", "Hit", target)
    env["year_map"].write_text(json.dumps({}), encoding="utf-8")
    env["monkeypatch"].setattr(importer, "_song_present_for_artist",
                               lambda md, a, t: None)
    env["monkeypatch"].setattr(
        importer.resolve, "resolve_metadata",
        lambda a, t: importer.resolve.ResolvedMeta(
            year="2001", album="Bravo Hits 42", album_year="2001", track=""))
    importer.import_downloads()
    assert _read_date(target) == "2001"
    # A compilation album is rejected -> album stays the title.
    assert _read_tag(target, "album") == "Hit"


def test_resolver_album_year_fallback(env):
    # No year/map/folder, resolver has no track year but an album_year -> used
    # as the date (Decision 6 fallback).
    _make_download(env)
    target = str(env["music"] / "Act2" / "Song2" / "Song2.mp3")
    _stub_target(env, "Act2", "Song2", "Song2", target)
    env["year_map"].write_text(json.dumps({}), encoding="utf-8")
    env["monkeypatch"].setattr(importer, "_song_present_for_artist",
                               lambda md, a, t: None)
    env["monkeypatch"].setattr(
        importer.resolve, "resolve_metadata",
        lambda a, t: importer.resolve.ResolvedMeta(
            year="", album="Real Album", album_year="1995", track=""))
    importer.import_downloads()
    assert _read_date(target) == "1995"
    assert _read_tag(target, "album") == "Real Album"
    assert "act2|song2" not in _read_map(env)  # resolved -> no pending


def test_resolver_error_never_throws_goes_pending(env):
    # A resolver that raises is wrapped elsewhere, but even if resolve_metadata
    # returns empty the import completes and the song goes pending.
    _make_download(env)
    target = str(env["music"] / "Act3" / "Song3" / "Song3.mp3")
    _stub_target(env, "Act3", "Song3", "Song3", target)
    env["year_map"].write_text(json.dumps({}), encoding="utf-8")
    env["monkeypatch"].setattr(importer, "_song_present_for_artist",
                               lambda md, a, t: None)
    env["monkeypatch"].setattr(importer.resolve, "resolve_metadata",
                               lambda a, t: importer.resolve.ResolvedMeta())
    importer.import_downloads()
    assert _read_date(target) == ""
    assert _read_map(env)["act3|song3"]["pending"] is True


def test_uniform_tagging_across_sources(env):
    # Two songs with DIFFERENT manifest source values get identically clean
    # tagging via the one shared block.
    env["year_map"].write_text(json.dumps({}), encoding="utf-8")
    env["monkeypatch"].setattr(importer, "_song_present_for_artist",
                               lambda md, a, t: None)
    env["monkeypatch"].setattr(
        importer.resolve, "resolve_metadata",
        lambda a, t: importer.resolve.ResolvedMeta(
            year="2010", album=f"Alb {a}", album_year="2010", track="1"))
    for name, src_label, artist in (("a.mp3", "charts", "Aaa"),
                                     ("b.mp3", "youtube", "Bbb")):
        _make_download(env, name=name)
    results = {}
    # Rebuild stubs per song via a dispatch on path basename.
    import os as _os

    def _bt(music_dir, path):
        base = _os.path.basename(path)
        if base == "a.mp3":
            tgt = str(env["music"] / "Aaa" / "S" / "S.mp3")
            return tgt, ("Aaa", "S", "S", True, False)
        tgt = str(env["music"] / "Bbb" / "S" / "S.mp3")
        return tgt, ("Bbb", "S", "S", True, False)

    def _ml(path):
        base = _os.path.basename(path)
        if base == "a.mp3":
            return {"artist": "Aaa", "title": "S", "album": "S", "source": "charts"}
        return {"artist": "Bbb", "title": "S", "album": "S", "source": "youtube"}

    env["monkeypatch"].setattr(tags, "build_target_path", _bt)
    env["monkeypatch"].setattr(tags, "manifest_lookup", _ml)
    importer.import_downloads()
    for artist in ("Aaa", "Bbb"):
        tgt = str(env["music"] / artist / "S" / "S.mp3")
        assert _read_date(tgt) == "2010"
        assert _read_tag(tgt, "album") == f"Alb {artist}"
        assert _read_tag(tgt, "tracknumber") == "1"
        results[artist] = True
    assert results == {"Aaa": True, "Bbb": True}


# --- cached-miss guard suppresses the marker --------------------------------

def test_cached_miss_guard_suppresses_marker(env):
    _make_download(env)
    target = str(env["music"] / "Gone" / "Band" / "Band.mp3")
    _stub_target(env, "Gone", "Band", "Band", target)
    env["year_map"].write_text(json.dumps({}), encoding="utf-8")
    env["monkeypatch"].setattr(importer, "_song_present_for_artist",
                               lambda md, a, t: None)
    env["monkeypatch"].setattr(importer.mbyear, "is_cached_miss",
                               lambda a, t: (a.lower(), t.lower()) == ("gone", "band"))
    importer.import_downloads()
    assert "gone|band" not in _read_map(env)  # guard suppressed the marker
