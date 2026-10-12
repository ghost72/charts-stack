"""
Cover sourcing via iTunes/Apple (primary) and Deezer (fallback).

For every (manifest) song charts-import knows the clean artist + title. With
that it searches for the matching cover via open search APIs (no API key
needed) and embeds the image into the MP3.

The source order is controllable via COVER_SOURCES (default: itunes,deezer).
iTunes has the largest catalog with dedicated single artworks in high
resolution (always square); Deezer steps in when iTunes finds nothing.

Hit selection deliberately prefers the real single/the original album and
sorts out foreign versions (remix, sped up, slowed, live, instrumental, cover
bands, compilations) so the demo/sampler cover is not embedded.

Only MP3 is supported (ID3 APIC frame). Errors are not fatal - the import
succeeds even without a cover.
"""

import logging
import re
import time

import httpx
from mutagen.id3 import ID3, APIC, error as ID3Error

from . import config
from .i18n import t

logger = logging.getLogger(__name__)

_DEEZER_SEARCH_URL = "https://api.deezer.com/search"
_ITUNES_SEARCH_URL = "https://itunes.apple.com/search"

# Additions in title/album that hint at an unwanted foreign version. Such hits
# are downgraded/sorted out during selection.
_BAD_VERSION_MARKERS = (
    "remix", "sped up", "spedup", "slowed", "reverb", "nightcore",
    "instrumental", "karaoke", "piano", "guitar cover", "cover version",
    "live", "acoustic version", "tribute", "8d", "lofi", "demo",
    "rock version", "like a version", "made famous", "in the style of",
)
# Album/collection markers that hint at samplers/compilations.
_COMPILATION_MARKERS = (
    "now that's what i call", "kidz bop", "hits ", "top 100", "top hits",
    "greatest hits of", "the sound of", "party mix", "bravo hits",
    "as mais tocadas", "compilation",
)


def _norm(s: str) -> str:
    """Normalizes a string for comparison (lowercase, no special characters)."""
    s = (s or "").lower().strip()
    # Remove parenthetical additions like (feat. ...), (Radio Edit) for the core comparison.
    s = re.sub(r"\s*[\(\[].*?[\)\]]\s*", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return s.strip()

# From which side deviation a cover counts as "rectangular" (needs correction).
# 0.15 = one side may be up to 15% longer and still counts as square. This way
# minimally deviating album covers (e.g. 500x498) stay untouched, while real CD
# scans (front+back, ~2:1) are detected. Conservative, so good covers are not
# replaced unnecessarily.
_SQUARE_TOLERANCE = 0.15


def _is_square(w: int, h: int) -> bool:
    """True if width/height count as square within the tolerance."""
    if w <= 0 or h <= 0:
        return True  # unknown -> do not treat as faulty
    longer, shorter = max(w, h), min(w, h)
    return (longer - shorter) / longer <= _SQUARE_TOLERANCE


def _score_candidate(want_artist: str, want_title: str,
                     cand_artist: str, cand_title: str, cand_album: str,
                     is_single: bool) -> int | None:
    """
    Scores a search hit. Higher = better. None = disqualified (wrong
    artist/title or clearly unwanted version).
    """
    wa, wt = _norm(want_artist), _norm(want_title)
    ca, ct = _norm(cand_artist), _norm(cand_title)
    calbum = (cand_album or "").lower()
    ctitle_raw = (cand_title or "").lower()

    # Artist must match (one contained in the other - covers "feat.").
    if wa and ca and wa not in ca and ca not in wa:
        return None
    # Title core must match.
    if wt and ct and wt != ct and wt not in ct and ct not in wt:
        return None
    # Drop unwanted foreign versions (remix/sped up/piano/cover ...) unless the
    # searched title itself contains these markers.
    for m in _BAD_VERSION_MARKERS:
        if m in ctitle_raw and m not in wt:
            return None

    score = 100
    if ct == wt:
        score += 30            # exact title
    if is_single:
        score += 25            # prefer a dedicated single
    for m in _COMPILATION_MARKERS:
        if m in calbum:
            score -= 60        # downgrade sampler/compilation
            break
    if "demo" in calbum:
        score -= 40            # downgrade demo EP (e.g. Dominic Fike)
    if "deluxe" in calbum:
        score -= 5
    return score


def _itunes_search(artist: str, title: str):
    """
    Calls the iTunes search API with retry on throttling (HTTP 429/403).
    Waits exponentially between attempts (ITUNES_RETRY_BASE_MS, then x2).
    Returns the JSON payload or None (finally failed/throttled).
    """
    params = {"term": f"{artist} {title}", "entity": "song", "limit": 15}
    attempts = max(1, config.ITUNES_MAX_RETRIES)
    with httpx.Client(timeout=15.0, follow_redirects=True,
                      headers={"User-Agent": "charts-import/1.0"}) as client:
        for attempt in range(attempts):
            try:
                resp = client.get(_ITUNES_SEARCH_URL, params=params)
            except httpx.HTTPError as e:
                logger.debug(t("import.cover.itunes_network_error", artist=artist,
                               title=title, error=e))
                return None
            # Throttling -> wait and retry (except on the last attempt).
            if resp.status_code in (429, 403):
                if attempt < attempts - 1:
                    wait = config.ITUNES_RETRY_BASE_MS * (2 ** attempt) / 1000.0
                    logger.debug(t("import.cover.itunes_throttled_retry",
                                   status=resp.status_code, wait=f"{wait:.1f}",
                                   artist=artist, title=title))
                    time.sleep(wait)
                    continue
                logger.debug(t("import.cover.itunes_throttled_fallback",
                               status=resp.status_code, artist=artist, title=title))
                return None
            try:
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                logger.debug(t("import.cover.itunes_search_failed", artist=artist,
                               title=title, error=e))
                return None
    return None


def _find_cover_url_itunes(artist: str, title: str) -> str | None:
    """Searches the best single/album cover at iTunes/Apple (with retry)."""
    payload = _itunes_search(artist, title)
    results = payload.get("results") if isinstance(payload, dict) else None
    if not results:
        return None

    best_url, best_score = None, -1
    for r in results:
        coll = r.get("collectionName") or ""
        is_single = coll.lower().endswith("- single") or " - single" in coll.lower()
        score = _score_candidate(
            artist, title,
            r.get("artistName", ""), r.get("trackName", ""), coll, is_single,
        )
        if score is None or score <= best_score:
            continue
        art = r.get("artworkUrl100") or r.get("artworkUrl60") or ""
        if not art:
            continue
        # Apple delivers the size via the file name; scale it up.
        size = config.ITUNES_COVER_SIZE
        art = re.sub(r"/\d+x\d+bb\.(jpg|png)$", f"/{size}x{size}bb.\\1", art)
        best_url, best_score = art, score

    return best_url


def _find_cover_url_deezer(artist: str, title: str) -> str | None:
    """Searches the best album cover at Deezer (fallback)."""
    for query in (f'artist:"{artist}" track:"{title}"', f"{artist} {title}"):
        try:
            with httpx.Client(timeout=15.0, follow_redirects=True,
                              headers={"User-Agent": "charts-import/1.0"}) as client:
                resp = client.get(_DEEZER_SEARCH_URL, params={"q": query, "limit": 10})
                resp.raise_for_status()
                payload = resp.json()
        except Exception as e:
            logger.debug(t("import.cover.deezer_search_failed", artist=artist,
                           title=title, error=e))
            continue

        data = payload.get("data") if isinstance(payload, dict) else None
        if not data:
            continue

        best_url, best_score = None, -1
        for tr in data:
            album = (tr or {}).get("album") or {}
            artist_obj = (tr or {}).get("artist") or {}
            score = _score_candidate(
                artist, title,
                artist_obj.get("name", ""), tr.get("title", ""),
                album.get("title", ""), is_single=False,
            )
            if score is None or score <= best_score:
                continue
            url = (album.get("cover_xl") or album.get("cover_big")
                   or album.get("cover_medium"))
            if url:
                best_url, best_score = url, score
        if best_url:
            return best_url
    return None


def _find_cover_url(artist: str, title: str) -> str | None:
    """
    Searches the best cover URL via the configured sources (COVER_SOURCES), in
    order. The first source with a hit wins.
    """
    for source in config.COVER_SOURCES:
        if source == "itunes":
            url = _find_cover_url_itunes(artist, title)
        elif source == "deezer":
            url = _find_cover_url_deezer(artist, title)
        else:
            continue
        if url:
            logger.debug(t("import.cover.source_hit", source=source, artist=artist,
                           title=title, url=url))
            return url
    return None


def _jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    """
    Reads width/height of a JPEG from the segment headers, WITHOUT a library.
    Returns (width, height) or None (not a/unreadable JPEG).
    """
    if not data or data[:2] != b"\xff\xd8":
        return None
    i = 2
    n = len(data)
    while i + 9 < n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        # SOF0..SOF15 (except DHT=C4, DAC=CC, RSTn) contain the dimensions.
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            if i + 9 <= n:
                height = (data[i + 5] << 8) | data[i + 6]
                width = (data[i + 7] << 8) | data[i + 8]
                return width, height
            return None
        # Skip the segment length.
        if i + 3 >= n:
            break
        seg_len = (data[i + 2] << 8) | data[i + 3]
        if seg_len <= 0:
            break
        i += 2 + seg_len
    return None


def _make_square(data: bytes) -> bytes:
    """
    Ensures the cover is square.

    - If Pillow is available: crop centered to a square (shorter edge), return
      as JPEG. Works for all image formats.
    - Without Pillow: return already-square JPEGs unchanged; rectangular ones
      are rejected (None) so no distorted cover is embedded.

    Returns: square image data (bytes) or None (rectangular + no Pillow -> do
    not embed).
    """
    dims = _jpeg_dimensions(data)
    # Already (nearly) square? Accept directly.
    if dims and _is_square(dims[0], dims[1]):
        return data

    try:
        import io
        from PIL import Image
    except Exception:
        # No Pillow: reject a rectangular cover, let a square one (dims=None
        # unclear) through to be safe.
        if dims is None:
            return data  # unknown -> do not block
        logger.info(t("import.cover.nonsquare_no_pillow", width=dims[0], height=dims[1]))
        return None

    try:
        img = Image.open(io.BytesIO(data)).convert("RGB")
        w, h = img.size
        if _is_square(w, h):
            return data
        side = min(w, h)
        left = (w - side) // 2
        top = (h - side) // 2
        img = img.crop((left, top, left + side, top + side))
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=90)
        logger.info(t("import.cover.cropped_square", w=w, h=h, side=side))
        return out.getvalue()
    except Exception as e:
        logger.debug(t("import.cover.square_failed", error=e))
        return data  # when in doubt, embed the original


def _download(url: str) -> bytes | None:
    """Downloads the cover image (JPEG)."""
    try:
        with httpx.Client(timeout=20.0, follow_redirects=True,
                          headers={"User-Agent": "charts-import/1.0"}) as client:
            resp = client.get(url)
            resp.raise_for_status()
            return resp.content
    except Exception as e:
        logger.debug(t("import.cover.download_failed", url=url, error=e))
        return None


def _has_embedded_cover(path: str) -> bool:
    """True if the MP3 already has an embedded image (APIC)."""
    try:
        tags = ID3(path)
        return any(k.startswith("APIC") for k in tags.keys())
    except ID3Error:
        return False
    except Exception:
        return False


def has_cover(path: str) -> bool:
    """Public: True if the MP3 already has an embedded cover."""
    return _has_embedded_cover(path)


def _embedded_cover_bytes(path: str) -> bytes | None:
    """Returns the raw data of the first embedded cover (APIC) or None."""
    try:
        tags = ID3(path)
    except Exception:
        return None
    for k in tags.keys():
        if k.startswith("APIC"):
            try:
                return tags[k].data
            except Exception:
                return None
    return None


def cover_status(path: str) -> str:
    """
    Classifies the embedded cover of an MP3:
      "none"      - no cover
      "square"    - square cover (fine)
      "nonsquare" - rectangular cover (e.g. CD scan front+back) -> faulty
      "unknown"   - cover present, dimensions not readable
    """
    data = _embedded_cover_bytes(path)
    if not data:
        return "none"
    dims = _jpeg_dimensions(data)
    if dims is None:
        # Not a JPEG (possibly PNG) - try dimensions via Pillow.
        try:
            import io
            from PIL import Image
            with Image.open(io.BytesIO(data)) as im:
                dims = im.size
        except Exception:
            return "unknown"
    w, h = dims
    return "square" if _is_square(w, h) else "nonsquare"


def fetch_and_embed(path: str, artist: str, title: str) -> bool:
    """
    Searches the Deezer cover for artist/title and embeds it into the MP3.
    Does NOT check the EMBED_COVERS flag and NOT whether a cover is already
    there - the caller decides that. Returns: True on success.
    """
    if not path.lower().endswith(".mp3"):
        return False

    url = _find_cover_url(artist, title)
    if not url:
        logger.debug(t("import.cover.none_found", artist=artist, title=title))
        return False

    data = _download(url)
    if not data:
        return False

    # Ensure the cover is square (rectangular ones look distorted on playback).
    # If rectangular + Pillow missing: do not embed.
    data = _make_square(data)
    if not data:
        return False

    try:
        try:
            tags = ID3(path)
        except ID3Error:
            tags = ID3()  # no ID3 tags present yet
        tags.delall("APIC")
        tags.add(APIC(
            encoding=3,          # UTF-8
            mime="image/jpeg",
            type=3,              # cover (front)
            desc="Cover",
            data=data,
        ))
        tags.save(path)
        return True
    except Exception as e:
        logger.warning(t("import.cover.embed_failed", path=path, error=e))
        return False


def embed_cover(path: str, artist: str, title: str, overwrite: bool = False) -> bool:
    """
    Searches + embeds a Deezer cover into the MP3 (for the import).
    Respects the EMBED_COVERS flag.

    overwrite=False: files that already have a cover are skipped.
    overwrite=True:  an existing cover is REPLACED. This is important for chart
        songs that come from a compilation - their embedded image is the
        sampler cover, not the single's. The Deezer cover is searched via
        artist/title and returns the ORIGINAL album/single cover. Replaced only
        if Deezer actually returns a cover (otherwise the existing one is kept -
        no cover loss).

    Returns: True if a cover was embedded/replaced.
    """
    if not config.EMBED_COVERS:
        return False
    if not path.lower().endswith(".mp3"):
        return False
    if _has_embedded_cover(path) and not overwrite:
        return False
    # On overwrite: fetch_and_embed only sets it if Deezer returns a cover
    # (delall("APIC") happens only AFTER a successful download). No risk of
    # deleting an existing cover without replacement.
    return fetch_and_embed(path, artist, title)
