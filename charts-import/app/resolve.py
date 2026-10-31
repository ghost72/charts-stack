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
# These are long / multi-word and are matched as plain substrings because they
# cannot collapse to a short ambiguous token after normalization.
#
# The widened best-of / singles / decade-comp markers below are a cheap
# SECONDARY net only (resolver report §3/§9.5). The durable fix is the
# structured VA-album / record_type signals on the /album/<id> detail. Note the
# deliberate accepted over-rejection under the user's hard rule (never write a
# wrong album; empty beats wrong):
#   * "collection" / "anthology" are plain substrings, so a real studio album
#     whose title merely contains the word (e.g. "Grace (Collection)") is also
#     left empty - acceptable, never wrong.
#   * the decade markers "90s"/"80s"/"70s" are reject-biased substrings, NOT
#     word-bounded tokens: any title whose normalized form contains the
#     digit-run (e.g. "1990s Demos" -> "1990s demos") is also emptied. Likewise
#     an accepted casualty of the never-write-wrong rule.
_COMPILATION_TITLE_MARKERS = (
    "various", "varios", "varias", "verschiedene",
    "compilation", "sampler", "mastermix", "megamix", "mixtape",
    "best of", "greatest hits", "top 100", "top100", "top 40", "top40",
    "bravo hits", "now that", "kontor", "clubland", "dance max",
    "party hits", "hitlist", "hit list",
    # Widened secondary net (report §3/§9.5): best-of / singles-collection /
    # generic best-of language.
    "singles collection", "the singles", "the hits", "all the best",
    "collection", "anthology", "the essential", "definitive",
    # Decade / language compilation markers. "annees 90" is the normalized form
    # of "annees 90"/"annees 90"; include it so the precomputed marker carries
    # the accent-folded spelling.
    "90s", "80s", "70s", "hits uit de", "nederpop", "annees 90",
    # Second widening (live dry-run over the real NAS library): best-of /
    # remix / edition compilations that slipped through with a plain
    # record_type=="album" + correct album-artist (only a title marker can
    # catch them). Honouring the user's hard rule "never write a wrong album -
    # empty beats wrong", the deliberate over-rejection of a rare real album
    # literally named with these words is ACCEPTABLE.
    #   * "hits"  -> catches "All Hits", "Jukebox Hits", "Unlimited Hits &
    #     Remixes"; a rare genuine album with "Hits" in the name being left
    #     empty is explicitly accepted. (Longer "..hits" markers above still
    #     fire too; this plain substring generalises them.)
    #   * remix/deluxe compilation editions. (These normalize to the bracket-
    #     free, accent-/ampersand-folded forms below - "(party mix)" ->
    #     "party mix", "& remixes" -> "and remixes" - so the normalized marker
    #     set carries the folded spelling.)
    "hits", "party mix", "special edition", "remixes", "and remixes",
)

# A YEAR RANGE in the title (e.g. "Jukebox Hits 1930-1950") is a reliable
# compilation signal - a studio album is not titled with a span of years.
# Implemented as a regex (not a literal substring) because the years vary.
_YEAR_RANGE_RE = re.compile(r"\b\d{4}\s*-\s*\d{4}\b")

# Short / ambiguous "Various Artists" abbreviations. These collapse to a tiny
# token after normalization ("VA -"/"va-" -> "va", "V.A." -> "v a") and MUST
# only match on word boundaries, never as a substring, otherwise real album
# titles that merely contain the letters "va" (Nirvana, Supernova, Lava,
# "Viva la Vida", Avantasia) would be wrongly flagged. Matched as whole,
# space-delimited phrases via _NORMALIZED_TOKEN_MARKERS.
_COMPILATION_TOKEN_MARKERS = ("va", "v.a.")

# Secondary-edition markers (original-album report §4). UNLIKE the compilation
# markers these are a RANKING DEMOTE signal, NOT a reject: a title carrying one
# of them (a live album, deluxe/expanded/anniversary/remaster edition, a
# soundtrack) is ranked BELOW any original but is still chosen when it is the
# only passing candidate (a correct-artist reissue beats an empty album, per
# the user's hard rule "a wrong/secondary album is worse than leaving what's
# there" - and empty is NOT better than a correct-artist secondary edition).
#
# Only robust, qualified phrases are plain substrings. DELIBERATELY EXCLUDED:
#   * bare "edition" / "version" - real studio albums are named "... Edition" /
#     carry "(... version)"; only QUALIFIED forms (deluxe/expanded/special/
#     anniversary edition) demote, so those qualifiers appear here, never the
#     bare words.
#   * bare "live" as a substring would hit "Alive", "Live It Up", "Living" -
#     it is a WORD-BOUNDED token instead (see _SECONDARY_EDITION_TOKEN_MARKERS),
#     mirroring the _COMPILATION_TOKEN_MARKERS discipline.
_SECONDARY_EDITION_MARKERS = (
    "deluxe", "expanded", "anniversary", "remaster", "remastered",
    "soundtrack", "ost", "motion picture", "super deluxe", "collector",
    "bonus track",
)

# Tail-only secondary-edition tokens: a token that only demotes when it is the
# LAST word of the normalized title (a title tail "... (Live)" collapses to a
# trailing " live") OR the whole title. "live" lives here so it fires on
# "On The Night (Live)" / "Live" / "The Way We Walk Vol.1 (Live)" but NOT on
# "Alive" / "Living" (not whole words) and NOT on "Live It Up" (leading word, a
# real phrase - report §4). Matched tail-anchored, never as a bare substring.
_SECONDARY_EDITION_TAIL_MARKERS = ("live",)

# Various-Artists identifiers. Used for BOTH the file-artist short-circuit
# (NO_RESOLVE_ARTISTS) and the album-detail artist guard (is_va_album). Compared
# case-insensitively against the raw (not normalized) lowercased string.
VA_ARTIST_NAMES = frozenset({
    "various artists", "various", "va", "v.a.", "v/a",
})
# The set that blocks resolution entirely when it is the FILE's artist.
NO_RESOLVE_ARTISTS = frozenset({"unknown artist"}) | VA_ARTIST_NAMES
# Deezer's canonical Various-Artists album owner (artist.id on /album/<id>).
VA_ARTIST_ID = 5080
# record_type values accepted as a real album name (positive requirement).
_ACCEPTABLE_RECORD_TYPES = frozenset({"album", "ep"})


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


# Precompute the normalized long/substring markers once at module load (avoids
# re-normalizing every marker on each call). Defined after _normalize.
_NORMALIZED_TITLE_MARKERS = tuple(
    norm for norm in (_normalize(m) for m in _COMPILATION_TITLE_MARKERS) if norm
)
_NORMALIZED_TOKEN_MARKERS = frozenset(
    norm for norm in (_normalize(m) for m in _COMPILATION_TOKEN_MARKERS) if norm
)
_NORMALIZED_SECONDARY_MARKERS = tuple(
    norm for norm in (_normalize(m) for m in _SECONDARY_EDITION_MARKERS) if norm
)
_NORMALIZED_SECONDARY_TAIL_MARKERS = tuple(
    norm for norm in (_normalize(m) for m in _SECONDARY_EDITION_TAIL_MARKERS) if norm
)


def is_compilation_title(album_title: str) -> bool:
    """
    True if the album title looks like a compilation/sampler (title-safe
    heuristic). A compilation album is dropped (album="") so the importer keeps
    album=title rather than filing a sampler name.
    """
    # A year range ("1930-1950") is checked on the RAW title, before
    # normalization collapses the hyphen to a space - a studio album is never
    # titled with a span of years, so this is a reliable compilation signal.
    if album_title and _YEAR_RANGE_RE.search(album_title):
        return True
    norm = _normalize(album_title)
    if not norm:
        return False
    # Long / multi-word markers are unambiguous and matched as plain substrings.
    for marker in _NORMALIZED_TITLE_MARKERS:
        if marker in norm:
            return True
    # Short / ambiguous markers (e.g. "va" / "v a" for Various Artists) are
    # matched only as whole, space-delimited phrases so they never fire inside a
    # real word like "Nirvana" / "Supernova" / "Lava" / "Viva la Vida" /
    # "Avantasia". The space padding enforces the word boundaries on both sides.
    padded = f" {norm} "
    for marker in _NORMALIZED_TOKEN_MARKERS:
        if f" {marker} " in padded:
            return True
    return False


def is_secondary_edition_title(album_title: str) -> bool:
    """
    True if the album title looks like a SECONDARY edition (live album, deluxe/
    expanded/anniversary/remaster edition, soundtrack, collector/bonus-track
    edition) rather than the original studio album. This is a RANKING DEMOTE
    signal only (used by the Deezer preference scan to rank an original above a
    secondary edition); it is NOT a reject gate - a sole secondary edition is
    still chosen over an empty album. Reuses the same _normalize as the
    compilation check. See _SECONDARY_EDITION_MARKERS for the excluded bare
    words ("edition"/"version"/bare "live").
    """
    norm = _normalize(album_title)
    if not norm:
        return False
    # Qualified multi-word / robust phrases as plain substrings.
    for marker in _NORMALIZED_SECONDARY_MARKERS:
        if marker in norm:
            return True
    # Tail-only tokens ("live"): demote when the marker is the WHOLE title or
    # the LAST word (a title tail "... (Live)" normalizes to a trailing
    # " live"). This never fires inside a word ("Alive"/"Living") and never on
    # a leading/middle word ("Live It Up"), honouring report §4.
    tokens = norm.split()
    for marker in _NORMALIZED_SECONDARY_TAIL_MARKERS:
        if tokens and tokens[-1] == marker:
            return True
    return False


def is_no_resolve_artist(artist: str) -> bool:
    """
    True if the FILE's artist must never be resolved (Various Artists / VA /
    unknown). Blocks any Deezer/MusicBrainz lookup upstream so a Various-Artists
    file never produces a bogus album and MusicBrainz is never queried (it 503'd
    / hung ~50 s on VA queries). Compared case-insensitively on the raw string.
    """
    return (artist or "").strip().lower() in NO_RESOLVE_ARTISTS


def is_junk_album_title(album_title: str) -> bool:
    """
    True if an album title is unusable as an album value: empty or single-char
    after normalization, or literally "various artists" / "various". Applied as
    the last gate before writing so even a stale cache entry cannot be filed. A
    legitimately one-character real album title (Ed Sheeran "÷" -> "", "X" ->
    "x") is a deliberate accepted casualty of the never-write-wrong rule.
    """
    raw = (album_title or "").strip().lower()
    if raw in {"various artists", "various"}:
        return True
    return len(_normalize(album_title)) <= 1


def is_va_album(album_artist_id, album_artist_name: str) -> bool:
    """
    True if a Deezer album DETAIL belongs to Various Artists: artist.id == 5080
    or artist.name in the VA set. The bulletproof compilation signal (catches
    "2gether 90 (Années 90)", whose album artist.id is 5080). A non-numeric id
    falls back to the name check.
    """
    try:
        if album_artist_id is not None and int(album_artist_id) == VA_ARTIST_ID:
            return True
    except (TypeError, ValueError):
        pass
    return (album_artist_name or "").strip().lower() in VA_ARTIST_NAMES


def album_detail_acceptable(album_title: str, record_type,
                            album_artist_id, album_artist_name: str) -> bool:
    """
    Accept an album DETAIL as the written album iff it is not various-artists,
    not junk, not a title-marked compilation, and its record_type is album/ep.
    record_type is a POSITIVE requirement: an empty/unknown record_type is not
    provably album/ep and therefore fails safe to False. Any uncertainty ->
    False (leave album empty; never wrong).
    """
    if is_va_album(album_artist_id, album_artist_name):
        return False
    if is_junk_album_title(album_title):
        return False
    if is_compilation_title(album_title):
        return False
    rt = (record_type or "").strip().lower()
    return rt in _ACCEPTABLE_RECORD_TYPES


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
    if not a or not ti or is_no_resolve_artist(a) or ti.lower() == "unknown album":
        # VA/unknown file artist: never resolve, and never reach _mb_meta below
        # (so MusicBrainz is not queried for a Various-Artists file).
        return ResolvedMeta()
    primary = _deezer_meta(a, ti)
    # Only pay for MB when Deezer left a field empty.
    if primary.year and primary.album and primary.track:
        return primary
    fallback = _mb_meta(a, ti)
    return _merge(primary, fallback)
