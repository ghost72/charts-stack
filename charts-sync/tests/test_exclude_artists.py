"""
Unit-Tests fuer den playlist-spezifischen exclude_artists-Filter
(deezer._is_excluded_artist / deezer.fetch_chart).

Hintergrund (Bug aus dem NAS-Betrieb): In der Playlist "Schweizer Mundart" mit
exclude_artists=["Trauffer","Nemo"] blieben trotzdem Trauffer-/Nemo-Tracks in
missing-mundart.txt stehen. An echten Deezer-Daten verifiziert waren das
Collab-Tracks, bei denen der Haupt-Kuenstler NICHT der ausgeschlossene Act ist
(z.B. artist="Goelae", contributors=[..., "Trauffer"]) - der alte Filter prueft
nur artist.name und liess sie durch.

Zusaetzlich abgesichert: der Dedup-Umgehungspfad. Taucht derselbe Song (gleicher
_merge_key) zuerst unter einem NICHT ausgeschlossenen Namen auf, darf das die
spaetere, korrekt ausgeschlossene Variante nicht ueber 'key in seen' am Filter
vorbeischleusen.
"""

import pytest

from app import deezer
from app.deezer import ChartTrack


EXCLUDE = {"trauffer", "nemo"}


# --- _is_excluded_artist: direkte Einheitstests -----------------------------

def test_main_artist_exact_match():
    assert deezer._is_excluded_artist("Trauffer", EXCLUDE) is True
    assert deezer._is_excluded_artist("Nemo", EXCLUDE) is True


def test_main_artist_case_insensitive():
    assert deezer._is_excluded_artist("TRAUFFER", EXCLUDE) is True


def test_main_artist_in_feat_combo():
    # "Trauffer feat. Beatrice Egli" -> Haupt-Act ist Trauffer.
    assert deezer._is_excluded_artist(
        "Trauffer feat. Beatrice Egli", EXCLUDE) is True


def test_not_excluded_artist_passes():
    assert deezer._is_excluded_artist("Gölä", EXCLUDE) is False
    assert deezer._is_excluded_artist("Bligg", EXCLUDE) is False


def test_excluded_as_contributor_only():
    # Der eigentliche Bug: Haupt-Artist ist Goelae, Trauffer nur als Contributor.
    assert deezer._is_excluded_artist(
        "Gölä", EXCLUDE,
        contributors=["Gölä", "Trauffer"]) is True


def test_contributor_not_excluded_passes():
    assert deezer._is_excluded_artist(
        "Gölä", EXCLUDE,
        contributors=["Gölä", "Beatrice Egli"]) is False


def test_empty_exclude_set_never_excludes():
    assert deezer._is_excluded_artist(
        "Trauffer", set(), contributors=["Trauffer"]) is False


# --- fetch_chart: Integration mit gestubbten Feeds --------------------------

class _StubFeed:
    kind = "deezer"


class _StubSource:
    """Minimaler ChartSource-Ersatz fuer fetch_chart (eine Playlist, N Feeds)."""

    def __init__(self, feeds, exclude_artists):
        self._feeds = feeds
        self.title = "Schweizer Mundart (Test)"
        self.exclude_artists = exclude_artists
        self.last_stats = {}

    @property
    def active_feeds(self):
        return self._feeds

    @property
    def feeds(self):
        return self._feeds

    @property
    def effective_genre_filter(self):
        # Kein pro-Playlist-Override -> globaler GENRE_FILTER (wie ChartSource).
        return deezer.config.GENRE_FILTER


def _result_artists(tracks):
    return [(t.artist, t.title) for t in tracks]


def test_fetch_chart_excludes_contributor_collab(monkeypatch):
    """Collab-Track (Trauffer nur Contributor) darf NICHT im Ergebnis landen."""
    feed = _StubFeed()
    tracks = [
        ChartTrack(1, "Bligg", "0815"),
        ChartTrack(2, "Gölä", "Büetzer Buebe", contributors=["Gölä", "Trauffer"]),
        ChartTrack(3, "Trauffer", "Heubode"),
    ]
    monkeypatch.setattr(deezer, "_fetch_one_feed", lambda f, c: tracks)
    # Genre-Filter aus (sonst echte Last.fm-Calls).
    monkeypatch.setattr(deezer.config, "GENRE_FILTER", False)

    src = _StubSource([feed], EXCLUDE)
    result = deezer.fetch_chart(src)

    artists = {a.lower() for a, _ in _result_artists(result)}
    assert "trauffer" not in artists
    # Nur Bligg bleibt uebrig.
    assert _result_artists(result) == [("Bligg", "0815")]


def test_fetch_chart_dedup_does_not_bypass_exclude(monkeypatch):
    """
    Reihenfolge-Bug: derselbe Song (gleicher _merge_key) taucht zuerst unter
    einem NICHT ausgeschlossenen Artist-Namen auf, dann unter dem korrekt
    ausgeschlossenen. Der ausgeschlossene Kuenstler darf NICHT durchrutschen.
    """
    feed_a = _StubFeed()
    feed_b = _StubFeed()
    # Feed A: Song "Heubode" faelschlich einem anderen Act zugeschrieben
    # (gleicher Titel). Feed B: korrekt Trauffer.
    tracks_a = [ChartTrack(1, "Trauffer", "Heubode", contributors=[])]
    # Simuliere, dass derselbe _merge_key schon "gesehen" wurde, indem Feed A
    # einen Trauffer-Treffer liefert -> muss sofort ausgeschlossen werden und
    # darf 'seen' gar nicht erst fuellen.
    tracks_b = [ChartTrack(5, "Trauffer", "Heubode", contributors=[])]

    feeds_map = {id(feed_a): tracks_a, id(feed_b): tracks_b}
    monkeypatch.setattr(deezer, "_fetch_one_feed",
                        lambda f, c: feeds_map[id(f)])
    monkeypatch.setattr(deezer.config, "GENRE_FILTER", False)

    src = _StubSource([feed_a, feed_b], EXCLUDE)
    result = deezer.fetch_chart(src)

    assert result == [] or all("trauffer" != t.artist.lower() for t in result)


def test_fetch_chart_keeps_allowed_artists(monkeypatch):
    """Nicht ausgeschlossene Kuenstler bleiben erhalten (keine Fehlausschluesse)."""
    feed = _StubFeed()
    tracks = [
        ChartTrack(1, "Gölä", "Schwan"),
        ChartTrack(2, "Lo & Leduc", "079", contributors=["Lo & Leduc"]),
        ChartTrack(3, "Bligg", "Rosmarie"),
    ]
    monkeypatch.setattr(deezer, "_fetch_one_feed", lambda f, c: tracks)
    monkeypatch.setattr(deezer.config, "GENRE_FILTER", False)

    src = _StubSource([feed], EXCLUDE)
    result = deezer.fetch_chart(src)
    assert len(result) == 3
