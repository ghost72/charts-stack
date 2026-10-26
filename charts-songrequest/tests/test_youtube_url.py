"""
Tests for youtube.is_valid_url (the SSRF / single-video gate).

Pure, no network. A valid URL is a single YouTube video (watch?v=, youtu.be,
/shorts/, /embed/) on an allowlisted host; everything else (non-YouTube hosts,
channel/user pages, playlist-only list= without v=, non-http schemes, empty)
is rejected.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import youtube


# --- Accepted -------------------------------------------------------------

def test_accept_watch_v():
    assert youtube.is_valid_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")


def test_accept_youtu_be_short():
    assert youtube.is_valid_url("https://youtu.be/dQw4w9WgXcQ")


def test_accept_shorts():
    assert youtube.is_valid_url("https://www.youtube.com/shorts/abc123")


def test_accept_embed():
    assert youtube.is_valid_url("https://www.youtube.com/embed/abc123")


def test_accept_mobile_and_music_prefixes():
    assert youtube.is_valid_url("https://m.youtube.com/watch?v=abc")
    assert youtube.is_valid_url("https://music.youtube.com/watch?v=abc")


def test_accept_nocookie_host():
    assert youtube.is_valid_url("https://www.youtube-nocookie.com/embed/abc")


def test_accept_watch_with_list_when_v_present():
    # A playlist context is fine as long as a single video (v=) is named;
    # noplaylist=True in the downloader guarantees only that video is fetched.
    assert youtube.is_valid_url(
        "https://www.youtube.com/watch?v=abc&list=PL123")


# --- Rejected -------------------------------------------------------------

def test_reject_non_youtube_host():
    assert not youtube.is_valid_url("https://example.com/watch?v=abc")
    assert not youtube.is_valid_url("https://vimeo.com/123456")


def test_reject_channel_and_user_pages():
    assert not youtube.is_valid_url("https://www.youtube.com/@somechannel")
    assert not youtube.is_valid_url("https://www.youtube.com/user/someone")
    assert not youtube.is_valid_url("https://www.youtube.com/channel/UC123")


def test_reject_playlist_only_without_v():
    assert not youtube.is_valid_url("https://www.youtube.com/playlist?list=PL123")


def test_reject_non_http_scheme():
    assert not youtube.is_valid_url("ftp://youtube.com/watch?v=abc")
    assert not youtube.is_valid_url("file:///etc/passwd")


def test_reject_empty_and_garbage():
    assert not youtube.is_valid_url("")
    assert not youtube.is_valid_url("not a url")
    assert not youtube.is_valid_url("youtube.com/watch?v=abc")  # no scheme
