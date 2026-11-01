"""
Cover helpers for the Manual Tag Editor (copied subset of
charts-import/app/cover.py plus editor-specific embed/read/download).

Copied verbatim from charts-import so the square-crop/JPEG logic stays identical:
_jpeg_dimensions, _is_square, _make_square, _embedded_cover_bytes. The
embed_cover_data / read_embedded_cover / download_cover functions below are NEW
to this copy, built for the editor's replace-cover flow (upload or URL).

Key safety invariants (design):
  * _make_square returns bytes | None - a falsy result must NEVER reach
    APIC(data=...); embed_cover_data asserts truthiness before building the
    frame (defence-in-depth; the caller also rejects a falsy square result).
  * The caller (editor.save_song) owns the outer preserve_mtime block, so
    embed_cover_data does NOT open its own.
  * download_cover guards the scheme (http/https only), does NOT follow
    redirects (a 3xx is a fetch failure), caps the body size, times out, and
    sniffs JPEG/PNG magic bytes (content-type is advisory only).
"""

import logging

import httpx
from mutagen.id3 import ID3, APIC, error as ID3Error

from . import config
from .i18n import t

logger = logging.getLogger(__name__)

# From which side deviation a cover counts as "rectangular" (needs correction).
# Copied from charts-import.
_SQUARE_TOLERANCE = 0.15


def _is_square(w: int, h: int) -> bool:
    """True if width/height count as square within the tolerance. Copied."""
    if w <= 0 or h <= 0:
        return True  # unknown -> do not treat as faulty
    longer, shorter = max(w, h), min(w, h)
    return (longer - shorter) / longer <= _SQUARE_TOLERANCE


def _jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    """
    Read width/height of a JPEG from the segment headers, WITHOUT a library.
    Returns (width, height) or None. Copied from charts-import.
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
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            if i + 9 <= n:
                height = (data[i + 5] << 8) | data[i + 6]
                width = (data[i + 7] << 8) | data[i + 8]
                return width, height
            return None
        if i + 3 >= n:
            break
        seg_len = (data[i + 2] << 8) | data[i + 3]
        if seg_len <= 0:
            break
        i += 2 + seg_len
    return None


def _make_square(data: bytes) -> bytes | None:
    """
    Ensure the cover is square. Returns square JPEG bytes, or None when the
    image is rectangular and Pillow is unavailable / cannot open it (so a
    distorted cover is never embedded). Copied from charts-import.
    """
    dims = _jpeg_dimensions(data)
    if dims and _is_square(dims[0], dims[1]):
        return data

    try:
        import io
        from PIL import Image
    except Exception:
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


def _embedded_cover_bytes(path: str) -> bytes | None:
    """Return the raw data of the first embedded cover (APIC) or None. Copied."""
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


# --- Editor-specific cover functions (new in this copy) ---------------------

# Magic-byte prefixes for the only two image formats the editor accepts.
_JPEG_MAGIC = b"\xff\xd8\xff"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _sniff_image_mime(data: bytes) -> str | None:
    """
    Return 'image/jpeg' / 'image/png' from the leading magic bytes, else None.
    This is the AUTHORITATIVE image check (a content-type header is advisory).
    """
    if not data:
        return None
    if data[:3] == _JPEG_MAGIC:
        return "image/jpeg"
    if data[:8] == _PNG_MAGIC:
        return "image/png"
    return None


def embed_cover_data(path: str, data: bytes, mime: str) -> None:
    """
    Embed `data` as the single front-cover APIC frame (delall('APIC') first),
    using the raw ID3 interface. Asserts `data` is truthy so a falsy
    _make_square result can never be embedded. The CALLER owns the outer
    preserve_mtime block, so this does NOT open its own.
    """
    assert data, "embed_cover_data requires non-empty image bytes"
    try:
        tags = ID3(path)
    except ID3Error:
        tags = ID3()  # no ID3 tags present yet
    tags.delall("APIC")
    tags.add(APIC(
        encoding=3,          # UTF-8
        mime=mime,
        type=3,              # cover (front)
        desc="Cover",
        data=data,
    ))
    tags.save(path)


def read_embedded_cover(path: str) -> tuple[bytes, str] | None:
    """
    Return (bytes, mime) of the first embedded cover for the data-URL preview,
    or None when there is none / it is unreadable. The mime is sniffed from the
    image bytes (JPEG/PNG), defaulting to 'image/jpeg' when a cover is present
    but not sniffable.
    """
    data = _embedded_cover_bytes(path)
    if not data:
        return None
    mime = _sniff_image_mime(data) or "image/jpeg"
    return data, mime


def download_cover(url: str) -> tuple[bytes, str] | None:
    """
    Fetch a cover image from an http/https URL with a bounded, safe guard:
      * scheme guard: only http/https (file:/data:/ftp:/gopher: rejected before
        any request);
      * follow_redirects=False: a 3xx response is treated as a fetch failure;
      * streamed read capped at config.TAGEDITOR_COVER_MAX_BYTES;
      * timeout config.TAGEDITOR_COVER_FETCH_TIMEOUT;
      * JPEG/PNG magic-byte sniff (content-type is advisory only).
    Returns (bytes, mime) on success, else None (the caller maps None to a
    localized cover_fetch_failed).
    """
    u = (url or "").strip()
    scheme = u.split(":", 1)[0].lower() if ":" in u else ""
    if scheme not in ("http", "https"):
        return None
    cap = config.TAGEDITOR_COVER_MAX_BYTES
    try:
        with httpx.Client(timeout=config.TAGEDITOR_COVER_FETCH_TIMEOUT,
                          follow_redirects=False,
                          headers={"User-Agent": "charts-config/1.0"}) as client:
            with client.stream("GET", u) as resp:
                # A redirect (no body followed) counts as a fetch failure.
                if resp.status_code >= 300:
                    return None
                chunks = []
                total = 0
                for chunk in resp.iter_bytes():
                    total += len(chunk)
                    if total > cap:
                        return None  # over the hard cap -> reject
                    chunks.append(chunk)
    except Exception as e:
        logger.debug(t("import.cover.download_failed", url=u, error=e))
        return None
    data = b"".join(chunks)
    mime = _sniff_image_mime(data)
    if not mime:
        return None
    return data, mime
