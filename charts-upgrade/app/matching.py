"""
Normalization, similarity comparison and compilation heuristic for matching
slskd search results against "Artist - Title".

COPY from charts-download/charts-songrequest (deliberately duplicated, no
runtime dependency between the services - like the other charts-* modules).
Reconcile changes here with the sister services when needed.
"""

import re
import unicodedata
from difflib import SequenceMatcher

# Additions removed from titles before comparison.
_NOISE_PATTERNS = [
    r"\(feat\.?.*?\)", r"\[feat\.?.*?\]",
    r"\(ft\.?.*?\)", r"\[ft\.?.*?\]",
    r"\(with.*?\)",
    r"\(remaster.*?\)", r"\[remaster.*?\]",
    r"\(.*?version\)", r"\(.*?edit\)",
    r"\(official.*?\)", r"\(lyric.*?\)", r"\(audio\)", r"\(video\)",
]


def normalize(text: str) -> str:
    """Normalizes a string for comparison (see charts-sync)."""
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
# (rather than a single or regular album). slskd only delivers paths, no real
# metadata - so this is a heuristic, not a reliable signal.
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
    "party hits", "decades", "collection", "anthology", "kuschelrock",
]


def is_compilation(path: str) -> bool:
    """
    Heuristic: True if the file/folder path hints at a compilation/sampler.
    Checks the ENTIRE path (not just the file name), since the compilation hint
    is usually in the folder name.
    """
    norm = normalize(path.replace("\\", "/"))
    return any(marker in norm for marker in _COMPILATION_MARKERS)


# Keywords that hint at an unwanted version: YouTube rips ("lyrics", "audio",
# "video"), bootlegs/edits (acapella, remix, mashup), live/karaoke/cover
# versions etc. Lidarr/Navidrome does not want such files - they are discarded
# entirely no matter how well the title matches.
DEFAULT_EXCLUDE_KEYWORDS = [
    # YouTube/rip markers
    "lyrics", "lyric video", "official video", "official audio", "music video",
    "visualizer", "video edit",
    # edits / bootlegs
    "acapella", "a capella", "instrumental", "karaoke",
    "remix", "mashup", "bootleg", "flip", "rework", "vip mix",
    "extended mix", "club mix", "radio mix", "dance mix",
    # alternative performances
    "live", "cover", "tribute", "unplugged",
    # trend versions
    "sped up", "spedup", "slowed", "reverb", "nightcore", "8d", "8d audio",
    # fragments
    "snippet", "teaser", "preview", "loop",
    # DJ edits / acapella break-ins
    "break in",
    "intro clean", "clean intro", "intro dirty", "dirty intro",
    "uptempo", "downtempo", "transition", "acap out", "acapella out",
    "quick hit", "short edit", "redrum", "hyper remix", "dj edit", "dj intro",
    # DJ pool / edit provider markers
    "froogle", "starjunk", "djcity", "mastermix", "ultimix", "funkymix",
    "hypeedit", "bpm supreme", "digital djpool", "clean edit", "dirty edit",
]


def looks_like_dj_edit(filename: str) -> bool:
    """
    Heuristic for DJ pool edits: the file name ends in a BPM number (70-199).
    Such files are almost always edits/transitions, not original versions.
    """
    import os

    base = os.path.splitext(os.path.basename(filename.replace("\\", "/")))[0].strip()
    m = re.search(r"(\d{2,3})$", base)
    if not m:
        return False
    n = int(m.group(1))
    return 70 <= n <= 199


def has_excluded_keyword(filename: str, keywords: list[str]) -> str | None:
    """
    Checks whether an exclude keyword appears in the file name.
    Returns the found keyword (for logging) or None.
    """
    import os

    base = os.path.basename(filename.replace("\\", "/")).lower()
    cleaned = re.sub(r"[^a-z0-9]+", " ", base)
    cleaned = f" {cleaned} "  # word-boundary protection
    for kw in keywords:
        kw_norm = f" {kw.lower().strip()} "
        if kw_norm in cleaned:
            return kw.strip()
    return None


def is_excluded_artist(artist: str, excluded: list[str]) -> bool:
    """
    True if 'artist' matches a globally excluded artist.
    Comparison via normalized names; also matches feat. combinations.
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
    Checks whether the artist name appears in the file PATH (folder + file
    name). To safeguard the title-only fallback search (cover guard).
    """
    a = normalize(artist)
    if not a:
        return False
    path_norm = normalize(filename.replace("\\", "/"))
    words = [w for w in a.split() if len(w) > 1]
    if not words:
        return a in path_norm
    present = sum(1 for w in words if w in path_norm)
    return present >= (len(words) + 1) // 2


def match_score(query_norm: str, filename: str) -> int:
    """
    Score 0-100 for how well an slskd file name matches the normalized search
    query ("artist title").
    """
    import os

    base = os.path.splitext(os.path.basename(filename.replace("\\", "/")))[0]
    base_norm = normalize(base)
    if not base_norm:
        return 0

    seq = similarity(query_norm, base_norm)

    q_words = [w for w in query_norm.split() if len(w) > 1]
    if q_words:
        present = sum(1 for w in q_words if w in base_norm)
        coverage = round(present / len(q_words) * 100)
    else:
        coverage = 0

    return round(coverage * 0.6 + seq * 0.4)
