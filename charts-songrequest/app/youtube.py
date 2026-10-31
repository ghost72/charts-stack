"""
YouTube URL import for charts-songrequest (third tab).

All yt-dlp usage is encapsulated here so main.py stays thin and the network
surface is unit-testable by monkeypatching. Two pure helpers (is_valid_url,
clean_title) gate/clean the input; two network functions (fetch_metadata,
download_audio) wrap yt-dlp. yt_dlp is imported lazily inside the two network
functions so unit tests that monkeypatch them never require yt-dlp installed.

The downloaded MP3 is dropped, together with its manifest entry, into the
shared /charts folder through the existing _promote_upload path with
source="youtube" and accept_anyway=True (so charts-import skips ONLY its
upsampling rejection for it - all other rejections still apply).
"""

import os
import re
import secrets
import urllib.parse

from . import config

# Hosts yt-dlp is allowed to be handed (lowercased, after stripping a leading
# www./m./music. label). This allowlist is the SSRF / arbitrary-URL guard: a
# non-YouTube URL never reaches the downloader.
_ALLOWED_HOSTS = {"youtube.com", "youtu.be", "youtube-nocookie.com"}

# Bracketed "noise" commonly appended to YouTube video titles. Anchored to a
# bracket so a bare word (e.g. "HD Radio") is NOT stripped - this is a
# best-effort, suggestion-only cleanup the user can overwrite.
_NOISE_RE = re.compile(
    r"[(\[](?:official|lyric|lyrics|audio|visualizer|hd|4k|"
    r"remaster\w*|mv|m/v)[^)\]]*[)\]]",
    re.IGNORECASE,
)

# "feat."/"ft."/"featuring" parentheticals, dropped from the TITLE suggestion
# (readable text, not a slug - do not reuse library._title_key here).
_FEAT_RE = re.compile(r"\s*[(\[](?:feat|ft|featuring)\.?[^)\]]*[)\]]",
                      re.IGNORECASE)


def is_valid_url(url: str) -> bool:
    """
    True only for a single-video YouTube URL. Gate for BOTH endpoints.

    Rules:
      - scheme must be http/https;
      - host (lowercased, leading www./m./music. stripped) must be in the
        allowlist;
      - youtube.com must carry a watch?v=<id> query OR a /shorts/<id> or
        /embed/<id> path; youtu.be must carry a non-empty first path segment;
      - everything else (channel/user pages, playlist-only list= without v=,
        non-http schemes, empty) is rejected.
    """
    if not url:
        return False
    try:
        parts = urllib.parse.urlparse(url.strip())
    except ValueError:
        return False
    if parts.scheme not in ("http", "https"):
        return False
    host = (parts.hostname or "").lower()
    for prefix in ("www.", "m.", "music."):
        if host.startswith(prefix):
            host = host[len(prefix):]
            break
    if host not in _ALLOWED_HOSTS:
        return False
    path = parts.path or ""
    if host == "youtu.be":
        # The id is the first path segment (e.g. youtu.be/<id>).
        return bool(path.strip("/").split("/")[0])
    # youtube.com / youtube-nocookie.com: a single video only.
    query = urllib.parse.parse_qs(parts.query)
    if query.get("v", [""])[0]:
        return True
    for prefix in ("/shorts/", "/embed/"):
        if path.startswith(prefix) and path[len(prefix):].strip("/"):
            return True
    return False


def clean_title(raw_title: str) -> str:
    """
    Suggestion-only title cleanup: strip bracketed noise
    ((Official Video)/[Lyrics]/(Audio)/...), drop feat. parentheticals, collapse
    whitespace. Applied ONLY to the fetched suggestion, never to what the user
    submits.
    """
    t = raw_title or ""
    t = _NOISE_RE.sub(" ", t)
    t = _FEAT_RE.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip()
    # Trim a dangling separator left behind by a stripped trailing group.
    t = t.strip(" -–—·|")
    return t.strip()


def suggest_artist_title(info: dict) -> tuple[str, str]:
    """
    Derive a suggested (artist, title) from yt-dlp metadata.

    Preference order:
      - YouTube Music track/artist fields when present (most reliable);
      - otherwise an "Artist - Title" split of the video title;
      - artist fallback is the uploader/channel with a trailing " - Topic"
        removed, then "".
    The artist is NOT floored to "Unknown Artist" here - that happens at record
    time so the UI can show an empty field and let the user fill it in.
    """
    raw_title = (info.get("title") or "").strip()
    yt_track = (info.get("track") or "").strip()
    yt_artist = (info.get("artist") or "").strip()

    if yt_track:
        # YouTube Music: trust the structured fields.
        artist = yt_artist
        title = clean_title(yt_track)
    else:
        title = clean_title(raw_title)
        artist = yt_artist
        if not artist and " - " in raw_title:
            # Common "Artist - Title" video convention: split on the FIRST " - ".
            left, right = raw_title.split(" - ", 1)
            artist = left.strip()
            title = clean_title(right)

    if not artist:
        channel = (info.get("uploader") or info.get("channel") or "").strip()
        # YouTube auto-generated "<Artist> - Topic" channels.
        if channel.endswith(" - Topic"):
            channel = channel[: -len(" - Topic")].strip()
        artist = channel

    return artist.strip(), title.strip()


def fetch_metadata(url: str) -> dict:
    """
    Resolve a YouTube video's metadata WITHOUT downloading. Returns the yt-dlp
    info dict (title, uploader/channel, YouTube-Music artist/track, duration,
    ...). Raises on failure (network/extractor change) - the caller maps that to
    a localized "could not read details" error.
    """
    import yt_dlp  # lazy import so monkeypatched unit tests need no yt-dlp

    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": 20,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return info or {}


def _match_filter(info_dict, *, incomplete=False):
    """
    yt-dlp match_filter: reject live streams and videos longer than the
    configured ceiling BEFORE any download starts. Returns None to accept, or a
    reason string to skip (yt-dlp raises it as a DownloadError).
    """
    if info_dict.get("is_live"):
        return "live stream rejected"
    duration = info_dict.get("duration")
    ceiling = config.YOUTUBE_MAX_DURATION_SECONDS
    if duration and ceiling and duration > ceiling:
        return f"video too long ({duration}s > {ceiling}s)"
    return None


def download_audio(url: str, dest_dir: str) -> str:
    """
    Download bestaudio and extract to MP3 with the YouTube thumbnail embedded as
    the cover (APIC). Writes into dest_dir (container-local staging, NOT
    /charts) under a random token stem and returns the path of the produced
    .mp3. Raises yt_dlp.utils.DownloadError (unavailable/too long/live/no audio)
    or OSError (unexpected).

    The postprocessor ORDER matters: FFmpegExtractAudio runs first (bestaudio ->
    mp3), then EmbedThumbnail embeds the fetched thumbnail into the mp3 as the
    cover. No FFmpegMetadata step is used: yt-dlp would otherwise write a wrong
    `date` (the upload/encode year, not the release year) into the mp3, and
    charts-import re-tags artist/title/album/year from the manifest + its
    metadata resolver anyway. EmbedThumbnail does not depend on FFmpegMetadata
    (for mp3 it creates the ID3 header it needs itself).
    """
    import yt_dlp  # lazy import so monkeypatched unit tests need no yt-dlp

    os.makedirs(dest_dir, exist_ok=True)
    token = secrets.token_urlsafe(16)
    outtmpl = os.path.join(dest_dir, f"{token}.%(ext)s")
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "writethumbnail": True,
        "socket_timeout": 20,
        "match_filter": _match_filter,
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3",
             "preferredquality": "0"},
            {"key": "EmbedThumbnail"},
        ],
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    # The extraction produced "<token>.mp3" in dest_dir; locate it.
    final = os.path.join(dest_dir, f"{token}.mp3")
    if os.path.exists(final):
        return final
    # Fallback: pick any file in dest_dir sharing the token stem (defensive;
    # the extractor normally yields exactly "<token>.mp3").
    for name in os.listdir(dest_dir):
        if name.startswith(token) and name.lower().endswith(".mp3"):
            return os.path.join(dest_dir, name)
    raise OSError("yt-dlp produced no mp3 output")
