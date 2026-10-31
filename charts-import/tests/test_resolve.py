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
                  "The Ultimate Sampler", "VA - Dance Max"):
        assert resolve.is_compilation_title(title) is True, title


def test_is_compilation_title_flags_va_token():
    # "VA" (Various Artists) must still be detected as a whole token even when
    # the rest of the title carries no other compilation marker.
    for title in ("VA - Summer Jams 2024", "va- Road Trip Mix",
                  "V.A. Festival Anthems"):
        assert resolve.is_compilation_title(title) is True, title


def test_is_compilation_title_keeps_real_albums():
    for title in ("Legend: The Best Collection", "Super Hits Vol. 2",
                  "Grace (Collection)", "Random Access Memories",
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
    album_detail = {"id": 200, "title": "M&J", "release_date": "1988-01-01"}
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
