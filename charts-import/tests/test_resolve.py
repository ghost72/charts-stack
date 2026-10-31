"""
Unit tests for the import-time metadata resolver (app/resolve.py) and the two
source adapters (deezer.resolve_release_meta / mbyear.resolve_mb_meta). No
network: the adapters are monkeypatched or driven with canned payloads.
"""

import pytest

from app import config, deezer, mbyear, resolve


@pytest.fixture(autouse=True)
def _reset_budget():
    resolve.reset_run_budget()
    yield
    resolve.reset_run_budget()


# --- is_compilation_title ---------------------------------------------------

def test_is_compilation_title_flags_strong_signals():
    for title in ("Various Artists", "Bravo Hits 2024",
                  "Now That's What I Call Music", "Greatest Hits",
                  "The Ultimate Sampler", "VA - Dance Max",
                  # Widened secondary-net markers (FR7).
                  "Messenger Of The Gods: The Singles Collection",
                  "All the Best - His Greatest Hits",
                  "The Essential Bob Dylan", "Definitive Collection",
                  "Nederpop Hits uit de 90's", "2gether 90 (Années 90)",
                  # Deliberate accepted over-rejection: a real album whose title
                  # merely contains "collection" is now flagged (empty beats
                  # wrong). Moved here from the negatives test.
                  "Legend: The Best Collection", "Grace (Collection)"):
        assert resolve.is_compilation_title(title) is True, title


def test_is_compilation_title_flags_va_token():
    # "VA" (Various Artists) must still be detected as a whole token even when
    # the rest of the title carries no other compilation marker.
    for title in ("VA - Summer Jams 2024", "va- Road Trip Mix",
                  "V.A. Festival Anthems"):
        assert resolve.is_compilation_title(title) is True, title


def test_is_compilation_title_keeps_real_albums():
    for title in ("Super Hits Vol. 2", "Random Access Memories",
                  "Joe le taxi", "",
                  # Real albums whose titles merely contain the substring "va"
                  # must NOT be flagged (regression guard for the "va" bug).
                  "Nirvana", "Supernova", "Lava", "Viva la Vida", "Avantasia"):
        assert resolve.is_compilation_title(title) is False, title


# --- resolve_metadata orchestration ----------------------------------------

def test_deezer_hit_wins(monkeypatch):
    monkeypatch.setattr(config, "IMPORT_RESOLVE_ENABLED", True)
    monkeypatch.setattr(resolve, "_deezer_meta",
                        lambda a, t: resolve.ResolvedMeta(
                            year="1987", album="Album", album_year="1988", track="3"))
    monkeypatch.setattr(resolve, "_mb_meta",
                        lambda a, t: resolve.ResolvedMeta(year="1999"))
    m = resolve.resolve_metadata("Vanessa Paradis", "Joe le taxi")
    assert m == resolve.ResolvedMeta(year="1987", album="Album",
                                     album_year="1988", track="3")


def test_mb_fills_empty_deezer_fields(monkeypatch):
    monkeypatch.setattr(config, "IMPORT_RESOLVE_ENABLED", True)
    # Deezer has only a track (no year/album); MB fills year + album, track "".
    monkeypatch.setattr(resolve, "_deezer_meta",
                        lambda a, t: resolve.ResolvedMeta(track="5"))
    monkeypatch.setattr(resolve, "_mb_meta",
                        lambda a, t: resolve.ResolvedMeta(
                            year="1990", album="MB Album", album_year="1990", track=""))
    m = resolve.resolve_metadata("A", "B")
    assert m.year == "1990"
    assert m.album == "MB Album"
    assert m.album_year == "1990"
    assert m.track == "5"  # Deezer's track preferred


def test_total_miss_returns_empty(monkeypatch):
    monkeypatch.setattr(config, "IMPORT_RESOLVE_ENABLED", True)
    monkeypatch.setattr(resolve, "_deezer_meta", lambda a, t: resolve.ResolvedMeta())
    monkeypatch.setattr(resolve, "_mb_meta", lambda a, t: resolve.ResolvedMeta())
    assert resolve.resolve_metadata("A", "B") == resolve.ResolvedMeta()


def test_adapter_raising_yields_empty(monkeypatch):
    monkeypatch.setattr(config, "IMPORT_RESOLVE_ENABLED", True)

    def _boom(a, t):
        raise RuntimeError("deezer down")

    # _deezer_meta / _mb_meta wrap the adapters; make the underlying modules raise.
    monkeypatch.setattr(deezer, "resolve_release_meta", _boom)
    monkeypatch.setattr(mbyear, "resolve_mb_meta", _boom)
    assert resolve.resolve_metadata("A", "B") == resolve.ResolvedMeta()


def test_disabled_and_unknown_inputs(monkeypatch):
    monkeypatch.setattr(config, "IMPORT_RESOLVE_ENABLED", False)
    assert resolve.resolve_metadata("A", "B") == resolve.ResolvedMeta()
    monkeypatch.setattr(config, "IMPORT_RESOLVE_ENABLED", True)
    assert resolve.resolve_metadata("", "B") == resolve.ResolvedMeta()
    assert resolve.resolve_metadata("Unknown Artist", "B") == resolve.ResolvedMeta()


# --- deezer._best_item / resolve_release_meta -------------------------------

def _search_payload():
    """A canned Deezer search with a compilation best hit + a real runner-up."""
    return {
        "data": [
            {"id": 11, "title": "Joe le taxi", "title_short": "Joe le taxi",
             "artist": {"name": "Vanessa Paradis"},
             "album": {"id": 100, "title": "Bravo Hits 42"}},
            {"id": 12, "title": "Joe le taxi", "title_short": "Joe le taxi",
             "artist": {"name": "Vanessa Paradis"},
             "album": {"id": 200, "title": "M&J"}},
        ]
    }


def test_best_item_and_canonical_refactor_safe():
    data = _search_payload()["data"]
    item = deezer._best_item(data, "Vanessa Paradis", "Joe le taxi")
    assert item is not None and item["artist"]["name"] == "Vanessa Paradis"
    # resolve_canonical still returns the same (artist, title) after the refactor.
    assert deezer._best_canonical(data, "Vanessa Paradis", "Joe le taxi") == (
        "Vanessa Paradis", "Joe le taxi")


def test_resolve_release_meta_walks_to_non_compilation(monkeypatch):
    monkeypatch.setattr(config, "DEEZER_CANONICAL_ENABLED", True)
    # No real HTTP: fake httpx.Client with canned responses.
    search = _search_payload()
    album_detail = {"id": 200, "title": "M&J", "release_date": "1988-01-01",
                    "record_type": "album",
                    "artist": {"id": 7, "name": "Vanessa Paradis"}}
    track_detail = {"id": 12, "release_date": "1987-06-01", "track_position": 3}

    class _Resp:
        def __init__(self, payload):
            self._p = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self._p

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def get(self, url, params=None):
            if url == deezer._SEARCH_URL:
                return _Resp(search)
            if url.startswith(deezer._ALBUM_URL):
                return _Resp(album_detail)
            if url.startswith(deezer._TRACK_URL):
                return _Resp(track_detail)
            raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(deezer.httpx, "Client", _Client)
    # Isolate the cache so a prior run does not short-circuit.
    monkeypatch.setattr(deezer, "_cache", {})
    monkeypatch.setattr(deezer, "_save_cache", lambda: None)

    meta = deezer.resolve_release_meta("Vanessa Paradis", "Joe le taxi")
    assert meta.album == "M&J"          # walked past "Bravo Hits 42"
    assert meta.year == "1987"          # track release year preferred
    assert meta.album_year == "1988"
    assert meta.track == "3"


# --- shared guard helpers ---------------------------------------------------

def test_is_no_resolve_artist():
    for a in ("Various Artists", "various", "VA", "V.A.", "V/A",
              "Unknown Artist", "  various artists  "):
        assert resolve.is_no_resolve_artist(a) is True, a
    for a in ("ZZ Top", "Tracy Chapman", "Nirvana", ""):
        assert resolve.is_no_resolve_artist(a) is False, a


def test_is_junk_album_title():
    for a in ("", "c", "X", "÷", "various", "Various Artists", "  "):
        assert resolve.is_junk_album_title(a) is True, a
    for a in ("Get Lucky", "Eye In The Sky", "M&J"):
        assert resolve.is_junk_album_title(a) is False, a


def test_is_va_album():
    assert resolve.is_va_album(5080, "Something") is True   # id wins
    assert resolve.is_va_album(5080, None) is True
    assert resolve.is_va_album(None, "Various Artists") is True
    assert resolve.is_va_album("abc", "VA") is True          # non-numeric id -> name
    assert resolve.is_va_album(2703, "ZZ Top") is False
    assert resolve.is_va_album(None, None) is False


def test_album_detail_acceptable():
    # Good studio album.
    assert resolve.album_detail_acceptable("Eye In The Sky", "album", 3702,
                                           "The Alan Parsons Project") is True
    # EP is acceptable.
    assert resolve.album_detail_acceptable("Espresso EP", "ep", 42, "Sabrina") is True
    # VA album rejected even with record_type album.
    assert resolve.album_detail_acceptable("2gether 90 (Années 90)", "album",
                                           5080, "Various Artists") is False
    # Junk title rejected.
    assert resolve.album_detail_acceptable("c", "album", 1, "Lucy") is False
    # Compilation-marker title rejected.
    assert resolve.album_detail_acceptable(
        "Messenger Of The Gods: The Singles Collection", "album", 7870,
        "Freddie Mercury") is False
    # record_type single rejected (positive requirement).
    assert resolve.album_detail_acceptable("Espresso", "single", 42, "Sabrina") is False
    # Empty/unknown record_type fails safe.
    assert resolve.album_detail_acceptable("Clean Album", "", 1, "Artist") is False
    assert resolve.album_detail_acceptable("Clean Album", None, 1, "Artist") is False


# --- resolve_release_meta: canned-payload driver ---------------------------

def _meta_client(monkeypatch, search, albums, tracks, *, counter=None):
    """
    Install a fake httpx.Client on deezer returning canned JSON:
      * search: the /search payload (dict with "data")
      * albums: {album_id: album_detail_dict}
      * tracks: {track_id: track_detail_dict}
    Optionally bump counter["albums"] on each /album/<id> GET. Isolates the
    cache so a prior run does not short-circuit.
    """
    class _Resp:
        def __init__(self, payload):
            self._p = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self._p

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def get(self, url, params=None):
            if url == deezer._SEARCH_URL:
                return _Resp(search)
            if url.startswith(deezer._ALBUM_URL):
                aid = url.rsplit("/", 1)[-1]
                if counter is not None:
                    counter["albums"] = counter.get("albums", 0) + 1
                return _Resp(albums.get(aid, albums.get(int(aid)) if aid.isdigit() else None))
            if url.startswith(deezer._TRACK_URL):
                tid = url.rsplit("/", 1)[-1]
                return _Resp(tracks.get(tid, tracks.get(int(tid)) if tid.isdigit() else None))
            raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(config, "DEEZER_CANONICAL_ENABLED", True)
    monkeypatch.setattr(deezer.httpx, "Client", _Client)
    monkeypatch.setattr(deezer, "_cache", {})
    monkeypatch.setattr(deezer, "_save_cache", lambda: None)


def _hit(track_id, album_id, album_title, artist="A", title="Song"):
    return {"id": track_id, "title": title, "title_short": title,
            "artist": {"name": artist},
            "album": {"id": album_id, "title": album_title}}


# BAD cases -> album left empty.

def test_meta_rejects_va_album_by_id(monkeypatch):
    search = {"data": [_hit(1, 10, "2gether 90 (Années 90)",
                            artist="Robert Miles", title="Children")]}
    albums = {10: {"id": 10, "title": "2gether 90 (Années 90)",
                   "record_type": "album", "release_date": "1996-01-01",
                   "artist": {"id": 5080, "name": "Various Artists"}}}
    tracks = {1: {"id": 1, "release_date": "1996-03-01", "track_position": 2}}
    _meta_client(monkeypatch, search, albums, tracks)
    meta = deezer.resolve_release_meta("Robert Miles", "Children")
    assert meta.album == ""              # VA album rejected (artist.id 5080)
    assert meta.year == "1996"           # year/track still kept
    assert meta.track == "2"


def test_meta_rejects_va_album_by_name(monkeypatch):
    search = {"data": [_hit(1, 10, "Some Comp")]}
    albums = {10: {"id": 10, "title": "Some Comp", "record_type": "album",
                   "release_date": "2000-01-01",
                   "artist": {"name": "Various Artists"}}}  # no numeric id
    tracks = {1: {"id": 1, "release_date": "2000-01-01", "track_position": 1}}
    _meta_client(monkeypatch, search, albums, tracks)
    meta = deezer.resolve_release_meta("A", "Song")
    assert meta.album == ""


def test_meta_rejects_singles_collection_title(monkeypatch):
    search = {"data": [_hit(1, 10, "Messenger Of The Gods: The Singles Collection",
                            artist="Freddie Mercury", title="Living on My Own")]}
    albums = {10: {"id": 10, "title": "Messenger Of The Gods: The Singles Collection",
                   "record_type": "album", "release_date": "2016-01-01",
                   "artist": {"id": 7870, "name": "Freddie Mercury"}}}
    tracks = {1: {"id": 1, "release_date": "1985-01-01", "track_position": 3}}
    _meta_client(monkeypatch, search, albums, tracks)
    meta = deezer.resolve_release_meta("Freddie Mercury", "Living on My Own")
    assert meta.album == ""              # widened title markers
    assert meta.year == "1985"


def test_meta_rejects_single_char_junk_album(monkeypatch):
    search = {"data": [_hit(1, 10, "c", artist="Lucy Bedroque")]}
    albums = {10: {"id": 10, "title": "c", "record_type": "album",
                   "release_date": "2021-01-01",
                   "artist": {"id": 99, "name": "Lucy Bedroque"}}}
    tracks = {1: {"id": 1, "release_date": "2021-01-01", "track_position": 1}}
    _meta_client(monkeypatch, search, albums, tracks)
    meta = deezer.resolve_release_meta("Lucy Bedroque", "Song")
    assert meta.album == ""              # junk album


def test_meta_rejects_record_type_single(monkeypatch):
    search = {"data": [_hit(1, 10, "Espresso", artist="Sabrina Carpenter",
                            title="Espresso")]}
    albums = {10: {"id": 10, "title": "Espresso", "record_type": "single",
                   "release_date": "2024-04-01",
                   "artist": {"id": 42, "name": "Sabrina Carpenter"}}}
    tracks = {1: {"id": 1, "release_date": "2024-04-01", "track_position": 1}}
    _meta_client(monkeypatch, search, albums, tracks)
    meta = deezer.resolve_release_meta("Sabrina Carpenter", "Espresso")
    assert meta.album == ""              # record_type single rejected as album
    assert meta.year == "2024"           # year kept


def test_meta_va_file_artist_no_network(monkeypatch):
    # A Various-Artists FILE artist never resolves and never touches the client.
    class _Boom:
        def __init__(self, *a, **k):
            raise AssertionError("no network for a VA file artist")

    monkeypatch.setattr(config, "DEEZER_CANONICAL_ENABLED", True)
    monkeypatch.setattr(deezer.httpx, "Client", _Boom)
    monkeypatch.setattr(deezer, "_cache", {})
    for a in ("Various Artists", "VA", "V.A.", "V/A", "Various", "Unknown Artist"):
        assert deezer.resolve_release_meta(a, "What Is Love") == resolve.ResolvedMeta()


def test_resolve_metadata_va_skips_both_sources(monkeypatch):
    # resolve.resolve_metadata short-circuits BEFORE Deezer and MusicBrainz for
    # a VA/unknown file artist (AC1 / FR1: MB must never be called).
    monkeypatch.setattr(config, "IMPORT_RESOLVE_ENABLED", True)
    calls = {"deezer": 0, "mb": 0}

    def _spy_deezer(a, t):
        calls["deezer"] += 1
        return resolve.ResolvedMeta()

    def _spy_mb(a, t):
        calls["mb"] += 1
        return resolve.ResolvedMeta()

    monkeypatch.setattr(resolve, "_deezer_meta", _spy_deezer)
    monkeypatch.setattr(resolve, "_mb_meta", _spy_mb)
    for a in ("Various Artists", "VA", "V.A.", "V/A", "Various", "Unknown Artist"):
        assert resolve.resolve_metadata(a, "What Is Love") == resolve.ResolvedMeta()
    assert calls == {"deezer": 0, "mb": 0}


# Preference scan + bounded GET budget.

def test_meta_prefers_studio_album_over_single(monkeypatch):
    # Ranked hits: a single first, then the real studio album. The scan must
    # prefer the album, inspecting at most _PREFERENCE_SCAN_MAX album GETs.
    search = {"data": [
        _hit(1, 10, "Espresso", artist="Sabrina Carpenter", title="Espresso"),
        _hit(2, 20, "Short n' Sweet", artist="Sabrina Carpenter", title="Espresso"),
    ]}
    albums = {
        10: {"id": 10, "title": "Espresso", "record_type": "single",
             "release_date": "2024-04-01",
             "artist": {"id": 42, "name": "Sabrina Carpenter"}},
        20: {"id": 20, "title": "Short n' Sweet", "record_type": "album",
             "release_date": "2024-08-23",
             "artist": {"id": 42, "name": "Sabrina Carpenter"}},
    }
    tracks = {1: {"id": 1, "release_date": "2024-04-01", "track_position": 1},
              2: {"id": 2, "release_date": "2024-08-23", "track_position": 1}}
    counter = {}
    _meta_client(monkeypatch, search, albums, tracks, counter=counter)
    meta = deezer.resolve_release_meta("Sabrina Carpenter", "Espresso")
    assert meta.album == "Short n' Sweet"   # studio album chosen over single
    assert counter["albums"] <= deezer._PREFERENCE_SCAN_MAX


def test_meta_scan_bounded_to_max_album_gets(monkeypatch):
    # Six rejectable candidates -> the scan must stop after _PREFERENCE_SCAN_MAX
    # album GETs (plus possibly one best-hit year-fallback GET), never all six.
    search = {"data": [_hit(i, 100 + i, f"Comp {i}") for i in range(1, 7)]}
    albums = {100 + i: {"id": 100 + i, "title": f"Comp {i}",
                        "record_type": "single", "release_date": "2000-01-01",
                        "artist": {"id": 1, "name": "A"}} for i in range(1, 7)}
    tracks = {i: {"id": i, "release_date": "2000-01-01", "track_position": 1}
              for i in range(1, 7)}
    counter = {}
    _meta_client(monkeypatch, search, albums, tracks, counter=counter)
    meta = deezer.resolve_release_meta("A", "Song")
    assert meta.album == ""
    # The scan itself inspects at most _PREFERENCE_SCAN_MAX candidates; the
    # all-rejected path then does ONE extra best-hit /album/<id> GET for the
    # album_year fallback (FR6). So the hard ceiling is MAX + 1 - never all six.
    assert counter["albums"] <= deezer._PREFERENCE_SCAN_MAX + 1


def test_meta_all_rejected_keeps_year_from_album_when_track_has_none(monkeypatch):
    # AC7 / Finding 2: all candidates rejected AND the track detail carries no
    # year -> the returned year falls back to the best hit's album release_date.
    search = {"data": [_hit(1, 10, "c", artist="Lucy Bedroque")]}
    albums = {10: {"id": 10, "title": "c", "record_type": "album",
                   "release_date": "2021-05-05",
                   "artist": {"id": 99, "name": "Lucy Bedroque"}}}
    tracks = {1: {"id": 1, "release_date": "", "track_position": 4}}  # no year
    _meta_client(monkeypatch, search, albums, tracks)
    meta = deezer.resolve_release_meta("Lucy Bedroque", "Song")
    assert meta.album == ""
    assert meta.year == "2021"           # album_year -> year fallback survived
    assert meta.album_year == "2021"
    assert meta.track == "4"


# GOOD cases -> real album kept.

def test_meta_keeps_tracy_chapman(monkeypatch):
    search = {"data": [_hit(1, 10, "Tracy Chapman", artist="Tracy Chapman",
                            title="Fast Car")]}
    albums = {10: {"id": 10, "title": "Tracy Chapman", "record_type": "album",
                   "release_date": "1988-04-05",
                   "artist": {"id": 971, "name": "Tracy Chapman"}}}
    tracks = {1: {"id": 1, "release_date": "1988-04-05", "track_position": 1}}
    _meta_client(monkeypatch, search, albums, tracks)
    meta = deezer.resolve_release_meta("Tracy Chapman", "Fast Car")
    assert meta.album == "Tracy Chapman"
    assert meta.year == "1988"


def test_meta_keeps_eye_in_the_sky(monkeypatch):
    search = {"data": [_hit(1, 10, "Eye In The Sky",
                            artist="The Alan Parsons Project", title="Eye in the Sky")]}
    albums = {10: {"id": 10, "title": "Eye In The Sky", "record_type": "album",
                   "release_date": "1982-05-01",
                   "artist": {"id": 3702, "name": "The Alan Parsons Project"}}}
    tracks = {1: {"id": 1, "release_date": "1982-05-01", "track_position": 2}}
    _meta_client(monkeypatch, search, albums, tracks)
    meta = deezer.resolve_release_meta("The Alan Parsons Project", "Eye in the Sky")
    assert meta.album == "Eye In The Sky"


def test_meta_keeps_get_lucky(monkeypatch):
    search = {"data": [_hit(1, 10, "Get Lucky", artist="Loverboy",
                            title="Working for the Weekend")]}
    albums = {10: {"id": 10, "title": "Get Lucky", "record_type": "album",
                   "release_date": "1981-10-01",
                   "artist": {"id": 7521, "name": "Loverboy"}}}
    tracks = {1: {"id": 1, "release_date": "1981-10-01", "track_position": 1}}
    _meta_client(monkeypatch, search, albums, tracks)
    meta = deezer.resolve_release_meta("Loverboy", "Working for the Weekend")
    assert meta.album == "Get Lucky"


def test_meta_keeps_ep_only_release(monkeypatch):
    # A legitimate EP-only release: record_type ep must be accepted (FR4).
    search = {"data": [_hit(1, 10, "Espresso EP", artist="Sabrina Carpenter",
                            title="Espresso")]}
    albums = {10: {"id": 10, "title": "Espresso EP", "record_type": "ep",
                   "release_date": "2024-04-01",
                   "artist": {"id": 42, "name": "Sabrina Carpenter"}}}
    tracks = {1: {"id": 1, "release_date": "2024-04-01", "track_position": 1}}
    _meta_client(monkeypatch, search, albums, tracks)
    meta = deezer.resolve_release_meta("Sabrina Carpenter", "Espresso")
    assert meta.album == "Espresso EP"


# --- mbyear.resolve_mb_meta -------------------------------------------------

def _mb_recording_data():
    return {
        "recordings": [
            {"score": 100, "disambiguation": "",
             "first-release-date": "1990-01-01",
             "releases": [
                 {"title": "Real Studio Album", "date": "1990-05-01",
                  "release-group": {"primary-type": "Album",
                                    "first-release-date": "1990-01-01"}},
             ]},
        ]
    }


def test_resolve_mb_meta_year_album_no_track(monkeypatch):
    monkeypatch.setattr(config, "MB_ENABLED", True)
    monkeypatch.setattr(mbyear, "_cache", {})
    monkeypatch.setattr(mbyear, "_save_cache", lambda: None)
    monkeypatch.setattr(mbyear, "_mb_fetch",
                        lambda a, t: (_mb_recording_data(), True))
    meta = mbyear.resolve_mb_meta("Artist", "Song")
    assert meta.year == "1990"
    assert meta.album == "Real Studio Album"
    assert meta.album_year == "1990"
    assert meta.track == ""  # MB never supplies a track number


def test_resolve_mb_meta_drops_compilation_album(monkeypatch):
    monkeypatch.setattr(config, "MB_ENABLED", True)
    monkeypatch.setattr(mbyear, "_cache", {})
    monkeypatch.setattr(mbyear, "_save_cache", lambda: None)
    data = {
        "recordings": [
            {"score": 100, "disambiguation": "",
             "first-release-date": "2001-01-01",
             "releases": [
                 {"title": "Bravo Hits 2001", "date": "2001-05-01",
                  "release-group": {"primary-type": "Album"}},
             ]},
        ]
    }
    monkeypatch.setattr(mbyear, "_mb_fetch", lambda a, t: (data, True))
    meta = mbyear.resolve_mb_meta("Artist", "Song")
    assert meta.year == "2001"
    assert meta.album == ""  # compilation rejected
