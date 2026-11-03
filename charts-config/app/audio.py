"""
Range-aware audio streaming for the Manual Tag Editor's in-browser play +
download controls.

This module is intentionally thin and route-free: it contains ONLY the HTTP
Range parser + streaming-response builder. All security (the _current_user auth
gate, the TAGEDITOR_AUDIO_ENABLED opt-in flag, and the editor._guarded_realpath
path guard + audio-extension allowlist) lives in main.py and is enforced BEFORE
this module is ever called. `stream_file` therefore assumes `path` is an
already-guarded, in-library file and never does its own guarding.

Starlette's FileResponse does not give us dependable partial-content seeking
across versions, so we implement an explicit RFC 7233 single-range handler:
  * no / unparseable Range          -> 200, full body
  * bytes=START-END / START-        -> 206, the requested slice
  * bytes=-N suffix (0 < N < size)  -> 206, the last N bytes
  * bytes=-N with N >= size         -> 206, the whole file
  * unsatisfiable (START >= size,
    or bytes=-0)                     -> 416, empty body, Content-Range bytes */SIZE
The body is streamed in 64 KiB chunks so a large file is never fully buffered.
"""

import logging
import os

from starlette.responses import JSONResponse, StreamingResponse

from .i18n import t

logger = logging.getLogger(__name__)

# 64 KiB read chunk: small enough to keep memory flat on a large file, big
# enough to avoid excessive syscalls while streaming.
_CHUNK = 64 * 1024


def _parse_range(header: str, size: int):
    """
    Parse a single HTTP Range header against a known file `size`.

    Returns one of:
      None                 -> no Range / unparseable -> caller serves full 200.
      (start, end)         -> a satisfiable inclusive byte range [start, end].
      "unsatisfiable"      -> a syntactically valid but unsatisfiable range
                              (start past EOF, or a zero-length suffix) -> 416.

    Only the FIRST range of a (possibly multi-range) header is honored; a single
    range or the whole file is spec-compliant and enough for <audio> seeking.
    """
    if not header:
        return None
    header = header.strip()
    if not header.lower().startswith("bytes="):
        return None  # unknown unit -> lenient full body
    spec = header[len("bytes="):].split(",", 1)[0].strip()
    if "-" not in spec:
        return None  # malformed -> lenient full body
    start_s, _, end_s = spec.partition("-")
    start_s, end_s = start_s.strip(), end_s.strip()
    try:
        if start_s == "":
            # Suffix range "bytes=-N": the last N bytes.
            if end_s == "":
                return None  # "bytes=-" with no number -> malformed -> full 200
            n = int(end_s)
            if n <= 0:
                return "unsatisfiable"  # bytes=-0 -> unsatisfiable (416)
            if n >= size:
                return (0, size - 1)    # N >= size -> whole file
            return (size - n, size - 1)
        start = int(start_s)
        if start < 0:
            return None  # malformed
        if start >= size:
            return "unsatisfiable"      # start past EOF -> 416
        if end_s == "":
            end = size - 1              # open-ended "bytes=START-"
        else:
            end = int(end_s)
            if end < start:
                return None             # malformed -> full 200
            if end >= size:
                end = size - 1          # clamp to EOF
        return (start, end)
    except ValueError:
        return None  # non-numeric -> lenient full body


def _sanitize_filename(name: str) -> str:
    """
    Sanitize a basename for a Content-Disposition header: strip CR/LF (header
    injection) and backslashes/quotes, then fall back to a plain ASCII name so
    the quoted filename is always well-formed even for non-ASCII titles.
    """
    name = (name or "file").replace("\r", "").replace("\n", "")
    name = name.replace("\\", "_").replace('"', "_")
    ascii_name = name.encode("ascii", "replace").decode("ascii").replace("?", "_")
    return ascii_name or "file"


def _file_iterator(path: str, start: int, end: int):
    """Yield bytes of `path` from `start` to `end` (inclusive) in 64 KiB chunks."""
    remaining = end - start + 1
    with open(path, "rb") as fh:
        fh.seek(start)
        while remaining > 0:
            chunk = fh.read(min(_CHUNK, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def stream_file(request, path: str, content_type: str,
                as_download: bool) -> "StreamingResponse | JSONResponse":
    """
    Build a Range-aware streaming response for an already-guarded audio file.

    `request` is the Starlette/FastAPI Request (only its Range header is read).
    Header invariants (so they can never contradict):
      * Accept-Ranges: bytes on EVERY response (200, 206, 416).
      * 200 -> Content-Length = full size; whole-file body.
      * 206 -> Content-Length = served slice length; Content-Range bytes a-b/SIZE.
      * 416 -> Content-Range bytes */SIZE, Content-Length 0, EMPTY body.
    `as_download` adds Content-Disposition: attachment so ALL formats (incl.
    FLAC) download even though only MP3 plays in the browser.

    Fail-open: if the file cannot be stat'd (vanished between the guard and here)
    a 404 JSONResponse is returned (logged once at WARNING), never a traceback.
    """
    try:
        size = os.path.getsize(path)
    except OSError as e:
        logger.warning("tageditor audio: cannot stat %s: %s", path, e)
        return JSONResponse(status_code=404,
                            content={"error": t("config.tageditor.not_found")})

    headers = {"Accept-Ranges": "bytes"}
    if as_download:
        fname = _sanitize_filename(os.path.basename(path))
        headers["Content-Disposition"] = f'attachment; filename="{fname}"'

    rng = _parse_range(request.headers.get("range", ""), size)

    if rng == "unsatisfiable":
        # 416: empty body, Content-Range bytes */SIZE, explicit zero length.
        headers["Content-Range"] = f"bytes */{size}"
        headers["Content-Length"] = "0"
        return StreamingResponse(iter(()), status_code=416, headers=headers,
                                 media_type=content_type)

    if rng is None:
        # No / unparseable Range -> full body, 200.
        headers["Content-Length"] = str(size)
        return StreamingResponse(_file_iterator(path, 0, size - 1) if size else iter(()),
                                 status_code=200, headers=headers,
                                 media_type=content_type)

    start, end = rng
    length = end - start + 1
    headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    headers["Content-Length"] = str(length)
    return StreamingResponse(_file_iterator(path, start, end),
                             status_code=206, headers=headers,
                             media_type=content_type)
