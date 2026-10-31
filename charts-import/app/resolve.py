"""
Import-time metadata resolver for charts-import.

Resolves a song's release year + a real (non-compilation) album + album year +
track number so EVERY imported song is tagged uniformly, regardless of source
(charts-download, Request, Upload, YouTube, upgrade replaces). The two source
adapters live in deezer.py / mbyear.py and are called here through the single
`ResolvedMeta` shape:

  resolve_metadata(artist, title) -> ResolvedMeta

Order: Deezer first, then MusicBrainz as a fallback, each fail-open (any
exception/timeout yields an all-empty ResolvedMeta - a lookup never raises into
the import path). The two sources are merged field-wise: Deezer wins where it
has a value, MB only fills fields Deezer left empty.

This module must NOT import importer/tags (to avoid import cycles); the adapters
are lazy-imported inside the wrappers.

Compilation avoidance (is_compilation_title) uses a title-safe marker subset
that is DELIBERATELY NARROWER than charts-download/app/matching.py's path-tuned
_COMPILATION_MARKERS. matching.py's list targets slskd FOLDER PATHS and includes
edition/disc/volume/chart markers (hits, charts, collection, anthology, decades,
vol , disc , cd1, the essential, new music, singles ...) that would over-reject
legitimate ALBUM TITLES (a real studio album called "... Collection" or
"... Vol. 2"). We intentionally keep only the strong, unambiguous sampler
signals here and do not reuse / import matching.py.
"""

import logging
import re
import unicodedata
from dataclasses import dataclass

from .i18n import t
from . import config

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResolvedMeta:
    """Single return shape for the orchestrator AND both source adapters."""
    year: str = ""        # release/recording year "YYYY" (track-level if known, else album)
    album: str = ""       # real non-compilation album OR single name; "" => importer keeps album=title
    album_year: str = ""  # album release year "YYYY" - FALLBACK source for date only (Decision 6)
    track: str = ""       # track number as a string; "" when not confidently resolvable


# Title-safe compilation markers. Deliberately narrower than matching.py's
# path-tuned list (see the module docstring): only strong, unambiguous
# sampler/compilation signals that will not collide with a real album title.
_COMPILATION_TITLE_MARKERS = (
    "various", "va -", "va-", "varios", "varias", "verschiedene",
    "compilation", "sampler", "mastermix", "megamix", "mixtape",
    "best of", "greatest hits", "top 100", "top100", "top 40", "top40",
    "bravo hits", "now that", "kontor", "clubland", "dance max",
    "party hits", "hitlist", "hit list",
)


def _normalize(text: str) -> str:
    """
    Normalize a title for marker matching (local reimplementation, NOT an import
    of charts-download): lowercase, accent-fold, '&' -> 'and', every run of
    non-alphanumeric collapsed to a single space, trimmed.
    """
    if not text:
        return ""
    text = text.lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def is_compilation_title(album_title: str) -> bool:
    """
    True if the album title looks like a compilation/sampler (title-safe
    heuristic). A compilation album is dropped (album="") so the importer keeps
    album=title rather than filing a sampler name.
    """
    norm = _normalize(album_title)
    if not norm:
        return False
    # Compare the normalized title against the normalized markers so spacing /
    # punctuation differences ("VA-" vs "va -") do not matter.
    padded = f" {norm} "
    for marker in _COMPILATION_TITLE_MARKERS:
        if _normalize(marker) and _normalize(marker) in norm:
            return True
    # Guard "va" as a standalone leading token (Various Artists abbreviation)
    # without matching the substring "va" inside real words.
    if padded.startswith(" va "):
        return True
    return False


# --- Per-run live-lookup budget (reset once per import pass) -----------------
# Owned here (mirrors tags.reload_manifest): the importer calls reset_run_budget()
# once at the start of import_downloads. Cache hits in the adapters do not call
# these; only a LIVE network lookup consumes budget.
_deezer_budget = 0
_mb_budget = 0
_deezer_cap_logged = False
_mb_cap_logged = False


def reset_run_budget() -> None:
    """Reset the per-pass live-lookup counters. Called once per import pass."""
    global _deezer_budget, _mb_budget, _deezer_cap_logged, _mb_cap_logged
    _deezer_budget = 0
    _mb_budget = 0
    _deezer_cap_logged = False
    _mb_cap_logged = False


def deezer_budget_available() -> bool:
    """True while the Deezer live-lookup cap has room this pass."""
    if _deezer_budget < config.IMPORT_DEEZER_RESOLVE_LIMIT:
        return True
    global _deezer_cap_logged
    if not _deezer_cap_logged:
        logger.info(t("import.resolve.deezer_cap",
                      limit=config.IMPORT_DEEZER_RESOLVE_LIMIT))
        _deezer_cap_logged = True
    return False


def mb_budget_available() -> bool:
    """True while the MusicBrainz live-lookup cap has room this pass."""
    if _mb_budget < config.IMPORT_MB_RESOLVE_LIMIT:
        return True
    global _mb_cap_logged
    if not _mb_cap_logged:
        logger.info(t("import.resolve.mb_cap",
                      limit=config.IMPORT_MB_RESOLVE_LIMIT))
        _mb_cap_logged = True
    return False


def note_deezer_live() -> None:
    """Count one LIVE Deezer meta lookup against the per-pass cap."""
    global _deezer_budget
    _deezer_budget += 1


def note_mb_live() -> None:
    """Count one LIVE MusicBrainz meta lookup against the per-pass cap."""
    global _mb_budget
    _mb_budget += 1


def _merge(primary: ResolvedMeta, fallback: ResolvedMeta) -> ResolvedMeta:
    """Prefer `primary`'s values; `fallback` fills only the empty fields."""
    return ResolvedMeta(
        year=primary.year or fallback.year,
        album=primary.album or fallback.album,
        album_year=primary.album_year or fallback.album_year,
        track=primary.track or fallback.track,
    )


def _deezer_meta(artist: str, title: str) -> ResolvedMeta:
    """Deezer adapter wrapped so any exception yields an empty ResolvedMeta."""
    try:
        from . import deezer
        return deezer.resolve_release_meta(artist, title)
    except Exception as e:  # never raise into the import path
        logger.debug(t("import.resolve.adapter_failed", source="deezer",
                       artist=artist, title=title, error=e))
        return ResolvedMeta()


def _mb_meta(artist: str, title: str) -> ResolvedMeta:
    """MusicBrainz adapter wrapped so any exception yields an empty ResolvedMeta."""
    try:
        from . import mbyear
        return mbyear.resolve_mb_meta(artist, title)
    except Exception as e:  # never raise into the import path
        logger.debug(t("import.resolve.adapter_failed", source="musicbrainz",
                       artist=artist, title=title, error=e))
        return ResolvedMeta()


def resolve_metadata(artist: str, title: str) -> ResolvedMeta:
    """
    Resolve release year + album + album_year + track for a song. Deezer first,
    MusicBrainz as a per-field fallback. Returns an all-empty ResolvedMeta when
    the resolver is disabled, the inputs are empty/"Unknown", or both sources
    miss. Never raises.
    """
    if not config.IMPORT_RESOLVE_ENABLED:
        return ResolvedMeta()
    a = (artist or "").strip()
    ti = (title or "").strip()
    if not a or not ti or a.lower() == "unknown artist" or ti.lower() == "unknown album":
        return ResolvedMeta()
    primary = _deezer_meta(a, ti)
    # Only pay for MB when Deezer left a field empty.
    if primary.year and primary.album and primary.track:
        return primary
    fallback = _mb_meta(a, ti)
    return _merge(primary, fallback)
