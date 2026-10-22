"""
ICY in-stream metadata reader and StreamTitle parser (charts-energy core).

The Energy (energy.ch) channels expose their now-playing track only as Icecast
ICY in-stream metadata on the Infomaniak stream hosts - there is no usable JSON
endpoint (see .agents/tasks/energy-api-feasibility.md). This module opens the
stream with the Icy-MetaData:1 header, reads exactly ONE metadata block, closes
the connection immediately, and parses the StreamTitle.

Testability: the socket I/O (_read_exact, read_now_playing) is kept thin; the
pure parser (parse_metadata) and spin_key take plain strings so tests feed
canned metadata with no network.

The transport uses stdlib http.client + ssl (no dependency, byte-exact control).
TLS always uses ssl.create_default_context() - normal verification, never
disabled.
"""

import http.client
import logging
import re
import ssl
import urllib.parse

from . import config
from .i18n import t

logger = logging.getLogger(__name__)

# Artist/title separator in StreamTitle: U+02D7 MODIFIER LETTER MINUS SIGN,
# NOT an ASCII hyphen. Hard requirement (feasibility report section 6).
_SEPARATOR = "\u02d7"

_STREAM_TITLE_RE = re.compile(r"StreamTitle='(?P<title>.*?)';")
# StreamUrl may be the last field without a trailing ';'.
_STREAM_URL_RE = re.compile(r"StreamUrl='(?P<url>.*?)';?")


class IcyReadError(Exception):
    """A transient ICY read failure (connect/TLS/timeout/premature EOF)."""


class IcyTrack:
    """A parsed now-playing track from an ICY metadata block."""

    def __init__(self, artist: str, title: str, started_at: str = "",
                 pl_id: str = "", duration: str = "", song_slug: str = "",
                 artist_slug: str = "", raw_title: str = ""):
        self.artist = artist
        self.title = title
        self.started_at = started_at
        self.pl_id = pl_id
        self.duration = duration
        self.song_slug = song_slug
        self.artist_slug = artist_slug
        self.raw_title = raw_title

    def __repr__(self) -> str:  # pragma: no cover - debug aid only
        return f"IcyTrack(artist={self.artist!r}, title={self.title!r})"


def _normalize(text: str) -> str:
    """Lowercase + collapse whitespace for the fallback dedupe key."""
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _parse_query_keep_plus(query: str) -> dict:
    """
    Parse a URL query string into a {key: value} dict WITHOUT turning '+' into a
    space (startedAt carries a literal '+HHMM' timezone offset). Percent-escapes
    are decoded with unquote. Last value wins on a repeated key.
    """
    out: dict[str, str] = {}
    for pair in query.split("&"):
        if not pair:
            continue
        key, sep, value = pair.partition("=")
        out[urllib.parse.unquote(key)] = urllib.parse.unquote(value) if sep else ""
    return out


def spin_key(track: IcyTrack) -> str:
    """
    The dedupe identity of a spin: startedAt|plId (both from the StreamUrl
    query). When either is missing (defensive), fall back to a normalized
    artist|title so re-reading the same song still dedupes. Pure function.
    """
    if track.started_at and track.pl_id:
        return f"{track.started_at}|{track.pl_id}"
    return f"{_normalize(track.artist)}|{_normalize(track.title)}"


def parse_metadata(block: str) -> IcyTrack | None:
    """
    Parse an ICY metadata string into an IcyTrack, or None.

    Returns None (DEBUG-logged) when there is no StreamTitle, no U+02D7
    separator, or the record is not type=music (ads/jingles). Pure function.
    """
    if not block:
        return None

    title_match = _STREAM_TITLE_RE.search(block)
    if not title_match:
        return None
    raw_title = title_match.group("title")

    # Split artist/title on U+02D7 (NOT ASCII '-'). An absent separator means we
    # cannot reliably split - do not guess on ASCII '-'.
    if _SEPARATOR not in raw_title:
        logger.debug(t("energy.icy.no_separator"))
        return None
    artist, _, title = raw_title.partition(_SEPARATOR)
    artist = artist.strip()
    title = title.strip()

    # Parse the StreamUrl query for the machine fields.
    started_at = pl_id = duration = song_slug = artist_slug = ""
    media_type = ""
    url_match = _STREAM_URL_RE.search(block)
    if url_match:
        query = urllib.parse.urlsplit(url_match.group("url")).query
        # NOTE: parse the query WITHOUT '+'->space decoding. The StreamUrl is
        # not form-encoded; startedAt carries a literal '+HHMM' timezone offset
        # (e.g. "...+0200"), which parse_qs would wrongly turn into a space.
        # Percent-escapes are still decoded via unquote.
        params = _parse_query_keep_plus(query)
        media_type = params.get("type", "")
        started_at = params.get("startedAt", "")
        pl_id = params.get("plId", "")
        duration = params.get("duration", "")
        song_slug = params.get("songUrlTitle", "")
        artist_slug = params.get("artistUrlTitle", "")

    # Keep only music records; filter out ads/jingles. Do NOT use adbreak
    # (unreliable per the feasibility report).
    if media_type != "music":
        return None

    return IcyTrack(
        artist=artist, title=title, started_at=started_at, pl_id=pl_id,
        duration=duration, song_slug=song_slug, artist_slug=artist_slug,
        raw_title=raw_title,
    )


def _read_exact(fp, n: int) -> bytes:
    """
    Read exactly n bytes from fp, looping on short reads. Raises IcyReadError on
    premature EOF (so the caller treats it as a transient miss).
    """
    chunks = []
    remaining = n
    while remaining > 0:
        chunk = fp.read(remaining)
        if not chunk:
            raise IcyReadError(f"premature EOF: wanted {n} bytes, missing {remaining}")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_now_playing(slug: str) -> IcyTrack | None:
    """
    Open the Infomaniak ICY stream for `slug`, read exactly one metadata block,
    close the connection immediately, and return the parsed IcyTrack or None.

    None means "nothing to record this cycle" (no metadata host / between songs
    / not type=music / no separator) - a quiet, expected miss.
    Raises IcyReadError on connect/TLS/timeout/premature EOF - a transient
    per-channel miss the caller logs once at WARNING and retries next cycle.
    """
    url = config.ENERGY_STREAM_HOST_TMPL.format(slug=slug)
    split = urllib.parse.urlsplit(url)
    host = split.netloc
    path = split.path or "/"
    if split.query:
        path = f"{path}?{split.query}"

    conn = http.client.HTTPSConnection(
        host, timeout=config.ENERGY_READ_TIMEOUT,
        context=ssl.create_default_context(),
    )
    try:
        conn.request("GET", path, headers={
            "Icy-MetaData": "1",
            "User-Agent": config.ENERGY_USER_AGENT,
            "Connection": "close",
        })
        resp = conn.getresponse()
        if resp.status != 200:
            raise IcyReadError(f"HTTP {resp.status} for {slug}")

        # Read the icy-metaint header with the missing/unparseable guard BEFORE
        # any int() conversion - a metadata-less host must be a quiet None,
        # never a WARNING IcyReadError.
        raw = resp.getheader("icy-metaint")
        if raw is None:
            # This host serves no in-stream metadata.
            logger.debug(t("energy.icy.no_metaint"))
            return None
        try:
            metaint = int(raw)
        except (TypeError, ValueError):
            # Present-but-garbage header is also a quiet miss, not an error.
            logger.debug(t("energy.icy.no_metaint"))
            return None

        # Read and discard exactly metaint audio bytes.
        _read_exact(resp, metaint)

        # One length byte; meta_len = length * 16.
        length_byte = _read_exact(resp, 1)
        meta_len = length_byte[0] * 16
        if meta_len == 0:
            # No metadata in this block (between songs / silence).
            return None

        meta = _read_exact(resp, meta_len)
        # Strip trailing NUL padding, decode UTF-8 tolerantly so a rare bad byte
        # never crashes the poll loop.
        block = meta.rstrip(b"\x00").decode("utf-8", errors="replace")
    except IcyReadError:
        raise
    except (OSError, ssl.SSLError, http.client.HTTPException) as e:
        raise IcyReadError(str(e)) from e
    finally:
        # Close immediately, before parsing - the socket must not linger.
        conn.close()

    return parse_metadata(block)
