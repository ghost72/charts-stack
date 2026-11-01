"""
Search index + normalization helpers for the Manual Tag Editor (copied subset of
charts-songrequest/app/library.py plus a new in-memory search index).

Copied verbatim from charts-songrequest so the folding keys stay identical
across services: AUDIO_EXTENSIONS, _clean_component, _title_key, _artist_key,
_extract_title_from_filename. The build_search_index / search / classify
functions below are NEW to this copy.

Design trade-offs (documented per the design):
  * Lazy TTL-cached index. A keystroke must not re-walk thousands of folders, so
    the index is built once by a single os.walk and reused until
    config.TAGEDITOR_INDEX_TTL expires; then the next search rebuilds it. Cost:
    one full walk every <= TTL seconds and a few MB of RAM; benefit: every
    search is an in-memory O(n) substring scan over cached strings.
  * Path-derived display. During indexing NO per-file mutagen read happens (that
    is the expensive part, deferred to editor.load_song on selection). The
    display artist/album/title come from the PATH (artist = parent-of-parent
    dir, album = parent dir, title = _extract_title_from_filename(name)). This
    can differ from the real tags; it is acceptable because the index is only
    for finding candidates and load_song re-reads the authoritative tag values.
"""

import os
import re
import threading
import time
import unicodedata

from . import config, tags

# Extensions recognized as audio (identical to charts-songrequest/charts-import).
AUDIO_EXTENSIONS = {".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".wma"}

# Invalid characters for folder names (like tags._clean_component elsewhere).
_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _clean_component(value: str, fallback: str) -> str:
    """Sanitize a path component - copied from charts-songrequest."""
    value = (value or "").strip()
    if not value:
        value = fallback
    value = _INVALID_CHARS.sub("_", value)
    value = re.sub(r"\s+", " ", value).strip()
    value = value.rstrip(". ")
    return value or fallback


def _title_key(title: str) -> str:
    """Normalized song title for the comparison (identical to charts-songrequest).

    Lowercase, strip (feat/ft/with) and version parentheticals, transliterate
    accents (NFKD fold), then keep only [a-z0-9]. MATCH key only.
    """
    t = (title or "").lower()
    t = re.sub(r"\((?:feat|ft|with)\.?[^)]*\)", " ", t)
    t = re.sub(r"\[(?:feat|ft|with)\.?[^\]]*\]", " ", t)
    t = re.sub(r"\((?:remaster|remastered|.*version|.*edit|official|audio|video|lyric[s]?)[^)]*\)", " ", t)
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", t)


def _artist_key(artist: str) -> str:
    """Normalized artist key for matching (identical to charts-songrequest).

    Folds dot/underscore/whitespace/case to one key (e.g. 'DJ.ILHAM' ==
    'DJ ILHAM' -> 'djilham'). Accent-folds like _title_key so the two axes
    agree. MATCH key only - never used for on-disk folder names.
    """
    a = unicodedata.normalize("NFKD", (artist or "").lower())
    a = "".join(c for c in a if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", a)


def _extract_title_from_filename(filename: str) -> str:
    """Get the song title from a file name (identical to charts-songrequest)."""
    base = os.path.splitext(os.path.basename(filename))[0]
    base = re.sub(r"\s*\(\d+\)\s*$", "", base)   # (2), (3)
    base = re.sub(r"_\d{6,}$", "", base)          # slskd suffix
    if " - " in base:
        base = base.rsplit(" - ", 1)[-1]
    base = re.sub(r"^\s*\d{1,3}\s*[-.\)]\s*", "", base)  # leading track no.
    return base.strip()


# --- Lazily-built, TTL-cached in-memory search index ------------------------
# A module-level list of per-file records, guarded by a lock. Rebuilt on the
# next search after the TTL expires. Each record: path, folded artist-key,
# folded title-key, a path-derived display (artist/album/title) and the
# lowercased basename (for the advisory manifest fast-path lookup in classify).

_INDEX_LOCK = threading.Lock()
_index: list[dict] | None = None
_index_dir: str | None = None
_index_ts: float = 0.0


def _folded_query(query: str) -> str:
    """Accent-fold a query to [a-z0-9] the same way the keys are folded."""
    q = unicodedata.normalize("NFKD", (query or "").lower())
    q = "".join(c for c in q if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", q)


def _build_records(music_dir: str) -> list[dict]:
    """
    One bounded os.walk over music_dir collecting a record per audio file. NO
    per-file mutagen read (display values are path-derived). Symlink loops are
    avoided via followlinks=False. Fail-open: an unreadable tree yields [].
    """
    records: list[dict] = []
    if not music_dir or not os.path.isdir(music_dir):
        return records
    try:
        for root, _dirs, files in os.walk(music_dir, followlinks=False):
            for name in files:
                ext = os.path.splitext(name)[1].lower()
                if ext not in AUDIO_EXTENSIONS:
                    continue
                path = os.path.join(root, name)
                # Path-derived display: artist = parent-of-parent dir,
                # album = parent dir, title from the file name.
                album_dir = os.path.basename(root)
                artist_dir = os.path.basename(os.path.dirname(root))
                disp_title = _extract_title_from_filename(name)
                records.append({
                    "path": path,
                    "artist_key": _artist_key(artist_dir),
                    "title_key": _title_key(disp_title),
                    "artist": artist_dir,
                    "album": album_dir,
                    "title": disp_title,
                    "basename": name.lower(),
                })
    except OSError:
        return records
    return records


def build_search_index(music_dir: str) -> list[dict]:
    """
    Return the cached search index for music_dir, rebuilding it when it is
    missing, older than config.TAGEDITOR_INDEX_TTL, or built for a different
    directory. Thread-safe (guarded by a module lock).
    """
    global _index, _index_dir, _index_ts
    now = time.monotonic()
    with _INDEX_LOCK:
        fresh = (_index is not None and _index_dir == music_dir
                 and (now - _index_ts) < config.TAGEDITOR_INDEX_TTL)
        if not fresh:
            _index = _build_records(music_dir)
            _index_dir = music_dir
            _index_ts = now
        return _index


def search(query: str, limit: int = 20) -> list[dict]:
    """
    Case-insensitive, accent-folded SUBSTRING search over the index.

    A query shorter than 2 (trimmed) chars returns []. limit is clamped to
    [1, 50]. Only the first 200 chars of the query are folded (defensive). A
    result matches when the folded query is a substring of its folded artist-key
    OR title-key OR the folded 'artist title'. Returns up to `limit` records
    (path + path-derived display); origin classification is done by the caller
    only for the returned paths.
    """
    q = (query or "").strip()
    if len(q) < 2:
        return []
    limit = max(1, min(50, limit))
    folded = _folded_query(q[:200])
    if not folded:
        return []
    index = build_search_index(config.MUSIC_DIR)
    out: list[dict] = []
    for rec in index:
        combined = rec["artist_key"] + rec["title_key"]
        if (folded in rec["artist_key"] or folded in rec["title_key"]
                or folded in combined):
            out.append(rec)
            if len(out) >= limit:
                break
    return out


def classify(path: str, manifest_entries: dict) -> str:
    """
    Classify a file's origin as 'stack' or 'curated'.

    'stack' when the file carries a TXXX:CHARTS_SOURCE or TXXX:CHARTS_ALBUM_SRC
    marker (AUTHORITATIVE - durable across Lidarr renames) OR when a manifest
    entry matches its lowercased basename (ADVISORY fast path, slskd-suffix
    tolerant like charts-import's tags._manifest_lookup). Otherwise 'curated'.
    Called only for the <= limit returned paths, so the per-result marker reads
    (up to ~2 ID3 opens each) stay bounded.
    """
    # Authoritative: a durable TXXX marker written into the file bytes.
    if tags.read_charts_source(path) or tags.read_charts_album_src(path):
        return "stack"
    # Advisory: a manifest entry keyed by the (lowercased) basename, tolerating
    # an slskd numeric suffix before the extension ('name_639....mp3' -> 'name.mp3').
    base = os.path.basename(path.replace("\\", "/")).lower()
    if base in manifest_entries:
        return "stack"
    stripped = re.sub(r"_\d{6,}(\.[a-z0-9]+)$", r"\1", base)
    if stripped != base and stripped in manifest_entries:
        return "stack"
    return "curated"
