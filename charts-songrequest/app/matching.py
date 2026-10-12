"""
Normalization and similarity comparison for matching slskd search results
against "Artist - Title".

The logic is deliberately identical to charts-sync (app/library.py) so both
services compare songs in the same tolerant way.
"""

import re
import unicodedata
from difflib import SequenceMatcher

# Additions that are stripped from titles before the comparison.
_NOISE_PATTERNS = [
    r"\(feat\.?.*?\)", r"\[feat\.?.*?\]",
    r"\(ft\.?.*?\)", r"\[ft\.?.*?\]",
    r"\(with.*?\)",
    r"\(remaster.*?\)", r"\[remaster.*?\]",
    r"\(.*?version\)", r"\(.*?edit\)",
    r"\(official.*?\)", r"\(lyric.*?\)", r"\(audio\)", r"\(video\)",
]


def normalize(text: str) -> str:
    """Normalizes a string for the comparison (see charts-sync)."""
    if not text:
        return ""
    text = text.lower()
    for pattern in _NOISE_PATTERNS:
        text = re.sub(pattern, " ", text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def similarity(a: str, b: str) -> int:
    """Similarity of two normalized strings as a value 0-100."""
    if not a or not b:
        return 0
    return round(SequenceMatcher(None, a, b).ratio() * 100)


# Markers in the path that hint at a compilation / sampler / chart collection
# (rather than a single or a regular album). slskd only delivers paths, no real
# metadata - this is therefore a heuristic, not a reliable signal.
_COMPILATION_MARKERS = [
    "va -", "va-", "various", "verschiedene",
    "varios", "varias",                  # Spanish/Italian "Various"
    "compilation", "sampler", "mastermix", "megamix", "mixtape",
    "top 100", "top100", "top 40", "top40", "charts", "hitlist", "hit list",
    "hits", "greatest hits", "best of", "best ever", "the essential",
    "nouveautes", "nouveaute",           # normalized without accent
    "new music friday", "new music",
    "muziek singles", "singles flac", "singles pack",
    "week ", "semaine", "vol ", "volume ", "disc ", "cd1", "cd 1", "cd2", "cd 2",
    "now that", "bravo hits", "kontor", "clubland", "dance max", "maxi power",
    "party hits", "decades", "collection", "anthology",
]


def is_compilation(path: str) -> bool:
    """
    Heuristic: True if the file/folder path hints at a compilation/sampler.
    Checks the ENTIRE path (not just the file name), since the compilation hint
    usually sits in the folder name.
    """
    norm = normalize(path.replace("\\", "/"))
    return any(marker in norm for marker in _COMPILATION_MARKERS)


# Keywords that hint at an unwanted version: YouTube rips ("lyrics", "audio",
# "video"), bootlegs/edits (acapella, remix, mashup), live/karaoke/cover
# versions etc. Lidarr/Navidrome do not want such files - they are discarded
# completely, no matter how well the title matches.
# Checked against the normalized file name (normalize strips parentheses, so
# the markers here are without parentheses).
DEFAULT_EXCLUDE_KEYWORDS = [
    # YouTube/rip markers
    "lyrics", "lyric video", "official video", "official audio", "music video",
    "visualizer", "video edit",
    # edits / bootlegs
    "acapella", "a capella", "instrumental", "karaoke",
    "remix", "mashup", "bootleg", "flip", "rework", "vip mix",
    "extended mix", "club mix", "radio mix", "dance mix",
    # alternate performances
    "live", "cover", "tribute", "unplugged",
    # trend versions
    "sped up", "spedup", "slowed", "reverb", "nightcore", "8d", "8d audio",
    # fragments
    "snippet", "teaser", "preview", "loop",
    # your concrete case (DJ edits / acapella break-ins)
    "break in",
    # DJ-pool / Serato edits (show up especially in the title-only fallback).
    # Deliberately as multi-word markers so single legitimate title words (e.g.
    # "Dirty Diana", "Clean Bandit") are NOT discarded by mistake.
    "intro clean", "clean intro", "intro dirty", "dirty intro",
    "uptempo", "downtempo", "transition", "acap out", "acapella out",
    "quick hit", "short edit", "redrum", "hyper remix", "dj edit", "dj intro",
    # DJ-pool / edit-provider markers (come from bulk rips, are not original
    # versions). "froogle" e.g. in "DtMF (Froogle Edit)".
    "froogle", "starjunk", "djcity", "mastermix", "ultimix", "funkymix",
    "hypeedit", "bpm supreme", "digital djpool", "clean edit", "dirty edit",
]


def looks_like_dj_edit(filename: str) -> bool:
    """
    Heuristic for DJ-pool edits: the file name ends in a BPM number (70-199),
    e.g. "... Die With A Smile (Uptempo Edit) 124.mp3". Such files are almost
    always edits/transitions, not original versions. We only want originals, so
    discard them.

    Deliberately narrow: only a number IMMEDIATELY at the end of the base name
    (without extension). This way years in parentheses (e.g. "(2024)") or track
    numbers at the start are not matched.
    """
    import os

    base = os.path.splitext(os.path.basename(filename.replace("\\", "/")))[0].strip()
    m = re.search(r"(\d{2,3})$", base)
    if not m:
        return False
    n = int(m.group(1))
    # Typical BPM range. Years (19xx/20xx) are 4-digit -> not affected. Numbers
    # like "124", "128", "140" are.
    return 70 <= n <= 199


def has_excluded_keyword(filename: str, keywords: list[str]) -> str | None:
    """
    Checks whether an exclusion keyword appears in the file name.
    Returns the found keyword (for logging) or None.

    Important: the RAW file name (lowercased) is checked, NOT the one sanitized
    by normalize() - because normalize deliberately strips exactly these markers
    (e.g. "(Lyrics)") for matching. Here, though, we want to see them.
    """
    import os

    base = os.path.basename(filename.replace("\\", "/")).lower()
    # Parentheses/characters to spaces so "(lyrics)" is found as "lyrics".
    cleaned = re.sub(r"[^a-z0-9]+", " ", base)
    cleaned = f" {cleaned} "  # word-boundary protection
    for kw in keywords:
        kw_norm = f" {kw.lower().strip()} "
        if kw_norm in cleaned:
            return kw.strip()
    return None


def is_excluded_artist(artist: str, excluded: list[str]) -> bool:
    """
    True if 'artist' matches a globally excluded artist (config.EXCLUDE_ARTISTS).
    Comparison via normalized names so case, accents and special characters do
    not matter.

    Also matches feat. combinations: the excluded name must appear as a
    contiguous word sequence in the normalized artist string. This way
    "Trauffer" also matches "Trauffer feat. X" or "X & Trauffer" - but the
    core name comparison prevents a partial word from matching by mistake.
    """
    if not excluded:
        return False
    a = normalize(artist)
    if not a:
        return False
    a_padded = f" {a} "
    for name in excluded:
        n = normalize(name)
        if n and f" {n} " in a_padded:
            return True
    return False


def artist_in_path(artist: str, filename: str) -> bool:
    """
    Checks whether the artist name appears in the file PATH (folder + file name).
    Used to secure the title-only fallback search: since a pure title search can
    also return cover versions by other artists, we only accept a hit if the
    artist appears somewhere in the path.

    Tolerant: checks whether (almost) all word parts of the normalized artist
    name appear in the normalized path. This way "Lady Gaga" also matches in
    ".../Lady Gaga - Die With A Smile.mp3" or ".../Gaga/Die With A Smile.mp3"
    - but not in a foreign-artist path.
    """
    a = normalize(artist)
    if not a:
        return False
    path_norm = normalize(filename.replace("\\", "/"))
    words = [w for w in a.split() if len(w) > 1]
    if not words:
        # Single-letter/short artists: the whole string must appear.
        return a in path_norm
    present = sum(1 for w in words if w in path_norm)
    # At least half the artist words (rounded up) must be in the path -
    # tolerates feature additions but requires the core of the name.
    return present >= (len(words) + 1) // 2


def match_score(query_norm: str, filename: str) -> int:
    """
    Score 0-100 for how well a slskd file name matches the normalized search
    query ("artist title").

    Since slskd results only deliver file paths (no clean tags), the base name
    of the file is normalized and compared against the query. In addition it is
    checked whether all word parts of the query appear in the file name
    (protects against purely coincidental SequenceMatcher similarity).
    """
    import os

    base = os.path.splitext(os.path.basename(filename.replace("\\", "/")))[0]
    base_norm = normalize(base)
    if not base_norm:
        return 0

    seq = similarity(query_norm, base_norm)

    # Word coverage: share of query words that appear in the file name.
    q_words = [w for w in query_norm.split() if len(w) > 1]
    if q_words:
        present = sum(1 for w in q_words if w in base_norm)
        coverage = round(present / len(q_words) * 100)
    else:
        coverage = 0

    # Combined: word coverage is the stronger signal for file names.
    return round(coverage * 0.6 + seq * 0.4)
