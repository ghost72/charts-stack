"""
Unit tests for backfill._year_pass - the MusicBrainz year backfill.

MusicBrainz is ALWAYS mocked (never a real network call): mbyear.resolve_mb_year
/ is_cached / is_cached_miss / cache_miss are monkeypatched. A tiny valid MP3
(from the test_mtime_preserve fixture style) lets mutagen read/write date tags
without ffmpeg. Each numbered case binds to the design's test plan.
"""

import json
import os

import pytest

from app import backfill, config, tags, yearmap


# --- tiny valid MP3 (no network, no ffmpeg) --------------------------------

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
    """A music dir + manifest + year map wired into config/yearmap."""
    music = tmp_path / "music"
    charts = tmp_path / "charts"
    music.mkdir()
    charts.mkdir()
    manifest = charts / ".charts-manifest.json"
    year_map = charts / ".charts-flashback-years.json"

    monkeypatch.setattr(config, "MUSIC_DIR", str(music))
    monkeypatch.setattr(config, "MANIFEST_FILE", str(manifest))
    monkeypatch.setattr(config, "PRESERVE_MTIME", True)
    monkeypatch.setattr(config, "YEAR_BACKFILL_ENABLED", True)
    monkeypatch.setattr(config, "YEAR_BACKFILL_LIMIT", 50)
    monkeypatch.setattr(config, "YEAR_BACKFILL_MAX_TRIES", 6)
    monkeypatch.setattr(yearmap, "YEAR_MAP_FILE", str(year_map))

    # Default MB mocks: cache empty, no cached miss, cache_miss records calls.
    monkeypatch.setattr(backfill.mbyear, "is_cached", lambda a, t: False)
    monkeypatch.setattr(backfill.mbyear, "is_cached_miss", lambda a, t: False)

    return {
        "music": music, "charts": charts,
        "manifest": manifest, "year_map": year_map,
        "monkeypatch": monkeypatch,
    }


def _add_song(env, artist, title, album_folder, year_in_tag=None):
    """Create Music/<artist>/<album_folder>/<title>.mp3 + a manifest entry."""
    adir = env["music"] / artist / album_folder
    adir.mkdir(parents=True, exist_ok=True)
    f = adir / f"{title}.mp3"
    _write_dummy_mp3(str(f))
    if year_in_tag:
        tags.write_tags(str(f), artist, title, album_folder, year=year_in_tag)
    # Manifest entry (basename keyed).
    try:
        data = json.loads(env["manifest"].read_text("utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        data = {"entries": {}}
    data.setdefault("entries", {})[f"{title}.mp3".lower()] = {
        "artist": artist, "title": title}
    env["manifest"].write_text(json.dumps(data), encoding="utf-8")
    return str(f)


def _write_map(env, data):
    env["year_map"].write_text(json.dumps(data), encoding="utf-8")


def _read_map(env):
    return json.loads(env["year_map"].read_text("utf-8"))


def _stub_mb(env, value, ok=True, was_cached=False, calls=None):
    def _fn(a, t):
        if calls is not None:
            calls.append((a, t))
        return value, ok, was_cached
    env["monkeypatch"].setattr(backfill.mbyear, "resolve_mb_year", _fn)


# --- case 1: decade fallback corrected to in-decade MB year -----------------

def test_1_decade_fallback_corrected(env):
    f = _add_song(env, "A-ha", "Take On Me", "Take On Me", year_in_tag="1985")
    _write_map(env, {"a-ha|take on me": {
        "year": "1985", "pending": True, "decade": "1980",
        "src": "energy-library", "tries": 0}})
    _stub_mb(env, "1984")
    res = backfill._year_pass(str(env["music"]))
    assert res["corrected"] == 1
    assert _read_date(f) == "1984"
    assert _read_map(env) == {"a-ha|take on me": "1984"}


# --- case 1b: MB == stored midpoint -> promote, no rewrite ------------------

def test_1b_promote_no_rewrite(env):
    f = _add_song(env, "A-ha", "Take On Me", "Take On Me", year_in_tag="1985")
    _write_map(env, {"a-ha|take on me": {
        "year": "1985", "pending": True, "decade": "1980",
        "src": "energy-library", "tries": 0}})
    calls = []
    _stub_mb(env, "1985", calls=calls)
    mtime_before = os.stat(f).st_mtime
    res = backfill._year_pass(str(env["music"]))
    assert len(calls) == 1              # resolve called exactly once
    assert _read_date(f) == "1985"      # tag unchanged (already the midpoint)
    assert os.stat(f).st_mtime == mtime_before  # no file write
    assert _read_map(env) == {"a-ha|take on me": "1985"}  # object -> bare string


# --- case 2: out-of-decade MB keeps midpoint, tries -> 1 --------------------

def test_2_out_of_decade_keeps_midpoint(env):
    f = _add_song(env, "A-ha", "Take On Me", "Take On Me", year_in_tag="1985")
    _write_map(env, {"a-ha|take on me": {
        "year": "1985", "pending": True, "decade": "1980",
        "src": "energy-library", "tries": 0}})
    _stub_mb(env, "1975")  # out of the 1980s
    res = backfill._year_pass(str(env["music"]))
    assert _read_date(f) == "1985"
    m = _read_map(env)["a-ha|take on me"]
    assert m["pending"] is True and m["tries"] == 1


# --- case 3: generic pending + MB year -> tagged + confirmed ----------------

def test_3_generic_pending_resolved(env):
    f = _add_song(env, "Dua Lipa", "Levitating", "Levitating")
    _write_map(env, {"dua lipa|levitating": {
        "year": "", "pending": True, "src": "import-generic", "tries": 0}})
    _stub_mb(env, "2020")
    res = backfill._year_pass(str(env["music"]))
    assert res["corrected"] == 1
    assert _read_date(f) == "2020"
    assert _read_map(env) == {"dua lipa|levitating": "2020"}


# --- case 4: generic give-up on the 6th miss, delete + cache ----------------

def test_4_generic_give_up_at_max_tries(env):
    _add_song(env, "No Body", "Lost Song", "Lost Song")
    _write_map(env, {"no body|lost song": {
        "year": "", "pending": True, "src": "import-generic", "tries": 5}})
    _stub_mb(env, "")  # MB empty
    cached = []
    env["monkeypatch"].setattr(backfill.mbyear, "cache_miss",
                               lambda a, t: cached.append((a, t)))
    res = backfill._year_pass(str(env["music"]))
    assert res["gave_up"] == 1
    assert "no body|lost song" not in _read_map(env)   # marker deleted
    assert cached == [("no body", "lost song")]         # miss cached


def test_4_generic_give_up_fires_at_six_not_seven(env):
    # tries starts at 4 -> after this pass tries=5 (<6) -> kept, NOT gave_up.
    _add_song(env, "No Body", "Lost Song", "Lost Song")
    _write_map(env, {"no body|lost song": {
        "year": "", "pending": True, "src": "import-generic", "tries": 4}})
    _stub_mb(env, "")
    env["monkeypatch"].setattr(backfill.mbyear, "cache_miss", lambda a, t: None)
    res = backfill._year_pass(str(env["music"]))
    assert res["kept"] == 1 and res["gave_up"] == 0
    assert _read_map(env)["no body|lost song"]["tries"] == 5


# --- case 4b: after give-up, a later import does not re-mark ----------------

def test_4b_import_does_not_remark_after_give_up(env, monkeypatch):
    from app import importer
    # Simulate the cached miss (what cache_miss persisted) via is_cached_miss.
    monkeypatch.setattr(importer.mbyear, "is_cached_miss",
                        lambda a, t: (a.lower(), t.lower()) == ("no body", "lost song"))
    # Empty map (the generic give-up deleted the marker).
    _write_map(env, {})
    importer._ensure_pending_marker("No Body", "Lost Song", src="import-generic")
    assert _read_map(env) == {}  # not re-created


# --- case 5: confirmed bare string -> no MB, no write -----------------------

def test_5_confirmed_bare_string_skipped(env):
    f = _add_song(env, "Queen", "Under Pressure", "Under Pressure",
                  year_in_tag="1981")
    _write_map(env, {"queen|under pressure": "1981"})
    calls = []
    _stub_mb(env, "1999", calls=calls)
    res = backfill._year_pass(str(env["music"]))
    assert calls == []                  # a bare string is never a candidate
    assert _read_date(f) == "1981"
    assert _read_map(env) == {"queen|under pressure": "1981"}


# --- case 6: scope guard (unknown src) --------------------------------------

def test_6_scope_guard_unknown_src(env):
    f = _add_song(env, "Someone", "Some Song", "Some Song")
    _write_map(env, {"someone|some song": {
        "year": "", "pending": True, "src": "somethingelse", "tries": 0}})
    calls = []
    _stub_mb(env, "2001", calls=calls)
    res = backfill._year_pass(str(env["music"]))
    assert calls == []
    assert _read_date(f) == ""
    # Marker untouched.
    assert _read_map(env)["someone|some song"]["src"] == "somethingelse"


# --- case 7: marker cleared after success -> second pass no MB --------------

def test_7_marker_cleared_after_success(env):
    f = _add_song(env, "Dua Lipa", "Levitating", "Levitating")
    _write_map(env, {"dua lipa|levitating": {
        "year": "", "pending": True, "src": "import-generic", "tries": 0}})
    _stub_mb(env, "2020")
    backfill._year_pass(str(env["music"]))
    # Second pass: no pending objects left -> no MB call, file unchanged.
    calls = []
    _stub_mb(env, "1999", calls=calls)
    mtime_before = os.stat(f).st_mtime
    backfill._year_pass(str(env["music"]))
    assert calls == []
    assert os.stat(f).st_mtime == mtime_before
    assert _read_date(f) == "2020"


# --- case 8: transient MB error -> marker unchanged, tries not bumped -------

def test_8_transient_error_leaves_marker(env):
    f = _add_song(env, "Dua Lipa", "Levitating", "Levitating")
    _write_map(env, {"dua lipa|levitating": {
        "year": "", "pending": True, "src": "import-generic", "tries": 0}})
    _stub_mb(env, "", ok=False)         # transient network error
    res = backfill._year_pass(str(env["music"]))
    assert _read_date(f) == ""
    m = _read_map(env)["dua lipa|levitating"]
    assert m["pending"] is True and m["tries"] == 0  # NOT bumped


# --- case 9: exact per-pass cap ---------------------------------------------

def test_9_exact_per_pass_cap(env, monkeypatch):
    monkeypatch.setattr(config, "YEAR_BACKFILL_LIMIT", 2)
    songs = [("Artist%d" % i, "Song%d" % i) for i in range(5)]
    m = {}
    for a, t in songs:
        _add_song(env, a, t, t)
        m[f"{a.lower()}|{t.lower()}"] = {
            "year": "", "pending": True, "src": "import-generic", "tries": 0}
    _write_map(env, m)
    calls = []
    _stub_mb(env, "2000", calls=calls)   # all uncached (is_cached -> False)
    res = backfill._year_pass(str(env["music"]))
    assert len(calls) == 2               # exactly LIMIT live queries, not 3
    # 2 corrected, 3 markers remain for the next pass.
    remaining = [v for v in _read_map(env).values() if yearmap.is_pending(v)]
    assert len(remaining) == 3


def test_9b_cached_song_does_not_consume_budget(env, monkeypatch):
    monkeypatch.setattr(config, "YEAR_BACKFILL_LIMIT", 2)
    # One cached song + two uncached: the cached one resolves from cache and
    # does not consume budget, so both uncached songs still get live queries.
    _add_song(env, "Cached", "Hit", "Hit")
    _add_song(env, "Fresh1", "A", "A")
    _add_song(env, "Fresh2", "B", "B")
    _write_map(env, {
        "cached|hit": {"year": "", "pending": True, "src": "import-generic",
                       "tries": 0},
        "fresh1|a": {"year": "", "pending": True, "src": "import-generic",
                     "tries": 0},
        "fresh2|b": {"year": "", "pending": True, "src": "import-generic",
                     "tries": 0},
    })
    monkeypatch.setattr(backfill.mbyear, "is_cached",
                        lambda a, t: a == "cached")
    live = []

    def _fn(a, t):
        cached_hit = (a == "cached")
        if not cached_hit:
            live.append((a, t))
        return "2005", True, cached_hit
    monkeypatch.setattr(backfill.mbyear, "resolve_mb_year", _fn)
    res = backfill._year_pass(str(env["music"]))
    # Two live queries used (the cap), plus the cached one resolved for free.
    assert len(live) == 2
    assert res["corrected"] == 3


# --- case 10: mtime unchanged across retag ----------------------------------

def test_10_mtime_unchanged_across_retag(env):
    f = _add_song(env, "Dua Lipa", "Levitating", "Levitating")
    old_ts = 1_000_000_000.0
    os.utime(f, (old_ts, old_ts))
    _write_map(env, {"dua lipa|levitating": {
        "year": "", "pending": True, "src": "import-generic", "tries": 0}})
    _stub_mb(env, "2020")
    backfill._year_pass(str(env["music"]))
    assert abs(os.stat(f).st_mtime - old_ts) <= 1.0
    assert _read_date(f) == "2020"


# --- case 11: locator normalisation + deterministic multi-path selection ----

def test_11_locator_normalisation_feat_title(env):
    # The year-map key carries a "(feat. X)" title; the on-disk file is the
    # clean title. _title_key normalisation must still resolve it.
    f = _add_song(env, "Artist X", "Dance", "Dance")
    _write_map(env, {"artist x|dance (feat. someone)": {
        "year": "", "pending": True, "src": "import-generic", "tries": 0}})
    _stub_mb(env, "2019")
    res = backfill._year_pass(str(env["music"]))
    assert res["corrected"] == 1
    assert _read_date(f) == "2019"


def test_11b_deterministic_multipath_prefers_year_folder(env):
    # Same artist|title resolves to two files: a single folder AND a Lidarr
    # "(YYYY)" folder. The year pass must pick the (YYYY) copy -> its folder
    # year is adopted (no MB call).
    single = _add_song(env, "A-ha", "Take On Me", "Take On Me")
    lidarr = _add_song(env, "A-ha", "Take On Me", "Take On Me (1985)")
    _write_map(env, {"a-ha|take on me": {
        "year": "", "pending": True, "src": "import-generic", "tries": 0}})
    calls = []
    _stub_mb(env, "1999", calls=calls)
    res = backfill._year_pass(str(env["music"]))
    assert calls == []                  # folder year adopted, no MB
    assert res["adopted"] == 1
    assert _read_map(env) == {"a-ha|take on me": "1985"}
