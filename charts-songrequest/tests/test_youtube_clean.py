"""
Tests for youtube.clean_title + youtube.suggest_artist_title (suggestion-only
cleanup/derivation). Pure, no network.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import youtube


# --- clean_title ----------------------------------------------------------

def test_strip_official_video():
    assert youtube.clean_title("Golden (Official Video)") == "Golden"


def test_strip_lyrics_bracket():
    assert youtube.clean_title("Golden [Lyrics]") == "Golden"


def test_strip_official_audio_and_visualizer():
    assert youtube.clean_title("Song (Official Audio)") == "Song"
    assert youtube.clean_title("Song (Visualizer)") == "Song"


def test_strip_hd_4k_remastered():
    assert youtube.clean_title("Song (HD)") == "Song"
    assert youtube.clean_title("Song (4K)") == "Song"
    assert youtube.clean_title("Song (Remastered 2011)") == "Song"


def test_case_insensitive():
    assert youtube.clean_title("Song (OFFICIAL VIDEO)") == "Song"


def test_strip_feat_parenthetical():
    assert youtube.clean_title("Song (feat. Someone)") == "Song"
    assert youtube.clean_title("Song (ft. X)") == "Song"


def test_bare_word_not_stripped():
    # No surrounding bracket -> NOT noise; the plain words survive.
    assert youtube.clean_title("HD Radio") == "HD Radio"


def test_collapse_whitespace():
    assert youtube.clean_title("  Golden   Hour  ") == "Golden Hour"


# --- suggest_artist_title -------------------------------------------------

def test_prefer_youtube_music_track_artist():
    info = {"title": "whatever (Official Video)", "track": "Golden",
            "artist": "HUNTR/X", "uploader": "Some Channel"}
    assert youtube.suggest_artist_title(info) == ("HUNTR/X", "Golden")


def test_artist_title_split_on_dash():
    info = {"title": "Dermot Kennedy - Kiss Me (Official Video)"}
    assert youtube.suggest_artist_title(info) == ("Dermot Kennedy", "Kiss Me")


def test_topic_channel_stripped_as_artist():
    info = {"title": "Kiss Me", "uploader": "Dermot Kennedy - Topic"}
    assert youtube.suggest_artist_title(info) == ("Dermot Kennedy", "Kiss Me")


def test_empty_artist_when_no_signal():
    info = {"title": "JustATitle"}
    artist, title = youtube.suggest_artist_title(info)
    assert artist == ""
    assert title == "JustATitle"


def test_channel_fallback_as_artist():
    info = {"title": "Some Song", "channel": "CoolChannel"}
    artist, title = youtube.suggest_artist_title(info)
    assert artist == "CoolChannel"
    assert title == "Some Song"
