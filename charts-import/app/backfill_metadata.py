"""
Shared core for the library-wide conservative metadata backfill.

Background: charts-import only writes clean metadata (release year / real
album / track number) when a song passes through the import pipeline (a fresh
download or an upgrade replace). A file already sitting in the Navidrome library
in good quality is never re-touched, so it keeps whatever (often missing)
year/album/track it was imported with before the uniform-tagging work landed.

This module is the ONE place that decides, for a given file + its current tags,
which fields are MISSING and may be filled, and that writes the resolved values
mtime-preserving. The three callers - the periodic ``backfill._metadata_pass``,
the one-shot ``scripts/backfill_metadata.py`` and the year-only
``scripts/fix_youtube_years.py`` - all go through here, so the never-overwrite
rule, the YouTube-year exception and mtime preservation are defined once.

Rules:
  * Never overwrite an existing value (curated/Lidarr album, resolver-set year,
    existing positive track number). A field is written ONLY when it is
    "missing" per ``missing_fields`` below.
  * ``album == title`` (the single-as-album placeholder) and "Unknown Album"
    count as a missing real album and MAY be filled with a resolved
    non-compilation album.
  * One deliberate exception: a YouTube-sourced year (identified by the durable
    TXXX:CHARTS_SOURCE=youtube marker) MAY be REPLACED by the resolver's real
    release year; the marker is then cleared so the file is not reprocessed.
  * Compilations and junk album titles are never written as an album
    (``resolve.is_compilation_title`` / ``resolve.is_junk_album_title``) - a
    last-gate defence-in-depth against a stale cache value from the old code.
  * Every write routes through ``tags`` (write_tags / _write_year_tag /
    clear_charts_source), all inside ``tags.preserve_mtime`` - mtime stays
    stable (NFR1).
  * Fail-open: a resolver miss or any per-file error never raises.
"""

import logging
from dataclasses import dataclass

from mutagen import File as MutagenFile

from . import resolve, tags
from .i18n import t

logger = logging.getLogger("charts-import.metadata-backfill")

# The source label on the durable provenance marker that makes a present year
# eligible for replacement (FR5). Any other marker value is treated as
# trustworthy (no replacement).
_YOUTUBE_MARKER = "youtube"

# Value stamped on TXXX:CHARTS_ALBUM_SRC whenever the resolver/backfill SETS an
# album. A present album carrying this marker was written by the stack and is
# eligible for re-correction (--recorrect-albums); an album WITHOUT the marker
# is a user's curated value and is NEVER overwritten (original-album report §7).
_RESOLVER_ALBUM_MARKER = "resolver"


@dataclass(frozen=True)
class Plan:
    """What may be filled/replaced on one file, given its current tags."""
    fill_year: bool        # year missing (conservative fill)
    replace_year: bool     # year PRESENT but the youtube marker is set (FR5.2)
    fill_album: bool       # album missing per the album rule
    fill_track: bool       # track missing per the track rule
    is_youtube: bool       # marker present (drives clear-after-correct)
    recorrect_album: bool  # album PRESENT but eligible to overwrite with a
                           # freshly resolved non-secondary original (opt-in
                           # --recorrect-albums: marker-gated OR heuristic
                           # catch-up for pre-marker files)
    recorrect_heuristic: bool  # the recorrect eligibility came from the
                               # one-time heuristic (unmarked file) rather than
                               # the CHARTS_ALBUM_SRC=resolver marker

    def needs_lookup(self) -> bool:
        """True iff something could be written -> a resolver call is worth it."""
        return (self.fill_year or self.replace_year
                or self.fill_album or self.fill_track or self.recorrect_album)


@dataclass(frozen=True)
class FileState:
    """A file's current, backfill-relevant tag state (fail-open reads)."""
    artist: str
    title: str
    album: str
    year: str
    track: int
    source: str       # TXXX:CHARTS_SOURCE marker value ("" when absent)
    album_src: str    # TXXX:CHARTS_ALBUM_SRC marker value ("" when absent)


@dataclass(frozen=True)
class BackfillResult:
    """Outcome of one apply_backfill call (for logging + counters)."""
    changed: bool           # anything would be / was written
    year_filled: bool
    year_replaced: bool
    album_filled: bool
    album_recorrected: bool  # a PRESENT album was overwritten with a better
                             # non-secondary original (--recorrect-albums)
    track_filled: bool
    error: bool
    old_year: str = ""
    new_year: str = ""
    old_album: str = ""
    new_album: str = ""
    new_track: str = ""


def _album_is_missing(album: str, title: str) -> bool:
    """
    True when the album counts as a MISSING real album: absent/empty, equal to
    the title (the single-as-album placeholder), or the literal "Unknown
    Album". A non-empty album that differs from the title and is not "Unknown
    Album" is treated as curated/Lidarr and never overwritten.
    """
    a = (album or "").strip()
    if not a:
        return True
    al = a.lower()
    if al == (title or "").strip().lower():
        return True
    if al == "unknown album":
        return True
    return False


def read_state(path: str) -> "FileState | None":
    """
    Read a file's backfill-relevant tags (artist/title/album/year/track +
    youtube marker), fail-open. Returns None when the file's tags cannot be read
    at all (then the file is skipped this pass, not marked done).
    """
    try:
        audio = MutagenFile(path, easy=True)
        if audio is None:
            return None
        artist = tags._first_tag(audio, "albumartist", "artist")
        title = tags._first_tag(audio, "title")
        album = tags._first_tag(audio, "album")
    except Exception:
        return None
    year, _album = tags.read_existing_year_album(path)
    track = tags.read_track(path)
    source = tags.read_charts_source(path)
    album_src = tags.read_charts_album_src(path)
    return FileState(artist=artist, title=title, album=album, year=year,
                     track=track, source=source, album_src=album_src)


def missing_fields(state: FileState, *, recorrect_albums: bool = False) -> Plan:
    """
    Compute the per-file Plan from its current tag state. The heart of the
    never-overwrite rule: a field is "fillable" ONLY when it is missing per the
    definitions in the design (§5). The YouTube-year exception is the only path
    that flags a PRESENT year (replace_year) - and only when the durable
    marker says the year came from YouTube.

    recorrect_albums (opt-in --recorrect-albums, original-album report §7) is
    the ONLY path that flags a PRESENT, non-placeholder album for overwrite. It
    is eligible in exactly two cases, both manifest-scoped by the caller so a
    non-manifest file is never seen here:
      * MARKER-gated: the album carries TXXX:CHARTS_ALBUM_SRC=resolver (the
        stack wrote it) -> eligible, let apply_backfill decide whether the fresh
        resolve is a strictly-better non-secondary original.
      * HEURISTIC catch-up (report §7.3): the album carries NO marker (it was
        backfilled before the marker shipped) BUT its title matches a
        secondary-edition pattern. Only then is an unmarked album touched, and
        apply_backfill additionally requires a strictly-earlier-date
        non-secondary original before overwriting. A user's curated ORIGINAL
        album (no marker, no secondary pattern) is never eligible.
    """
    is_youtube = state.source == _YOUTUBE_MARKER
    has_year = bool(state.year)
    album_present = not _album_is_missing(state.album, state.title)

    recorrect_album = False
    recorrect_heuristic = False
    if recorrect_albums and album_present:
        if state.album_src == _RESOLVER_ALBUM_MARKER:
            recorrect_album = True
        elif not state.album_src and resolve.is_secondary_edition_title(state.album):
            # Pre-marker catch-up: an unmarked album is touched ONLY when its
            # title already looks like a secondary edition; apply_backfill then
            # still requires a strictly-earlier-date non-secondary original.
            recorrect_album = True
            recorrect_heuristic = True

    return Plan(
        fill_year=not has_year,
        replace_year=has_year and is_youtube,
        fill_album=_album_is_missing(state.album, state.title),
        fill_track=state.track <= 0,
        is_youtube=is_youtube,
        recorrect_album=recorrect_album,
        recorrect_heuristic=recorrect_heuristic,
    )


def apply_backfill(path: str, state: FileState, plan: Plan,
                   meta: "resolve.ResolvedMeta", *, dry_run: bool) -> BackfillResult:
    """
    Apply a resolved meta onto a file conservatively, per the Plan:
      * fill a missing year with meta.year (fallback meta.album_year);
      * REPLACE a present youtube-marked year with the resolver's real year and
        clear the marker (FR5.2);
      * fill a missing album with a resolved non-compilation album;
      * fill a missing track with a confident positive track number.
    Present values are passed through unchanged, so nothing is overwritten.
    Everything is written in ONE mtime-preserving tags.write_tags call. Returns
    a BackfillResult; fail-open (a write error sets error=True, never raises).
    """
    resolved_year = (meta.year or meta.album_year or "").strip()
    resolved_album = (meta.album or "").strip()
    resolved_track = (meta.track or "").strip()

    # --- decide the final year ---
    final_year = state.year
    year_filled = False
    year_replaced = False
    if plan.fill_year and resolved_year:
        final_year = resolved_year
        year_filled = True
    elif plan.replace_year and resolved_year and resolved_year != state.year:
        final_year = resolved_year
        year_replaced = True

    # A resolved album is only ever usable (fill OR recorrect) when it is a
    # real, non-secondary ORIGINAL: present, not a compilation, not junk, and
    # NOT itself a secondary edition. This single gate honours the hard rule
    # "prefer the original studio album; a wrong/secondary album is worse than
    # leaving what's there".
    resolved_album_is_original = bool(
        resolved_album
        and not resolve.is_compilation_title(resolved_album)
        and not resolve.is_junk_album_title(resolved_album)
        and not resolve.is_secondary_edition_title(resolved_album)
        # A resolved album that merely equals the song TITLE is the
        # single-as-album placeholder, not a real studio album. It is never a
        # valid RE-CORRECTION target (overwriting a present album with the
        # track's own name is never "strictly better" - e.g. the observed
        # "Push The Feeling On -> Push The Feeling On"). The fill path already
        # treats album==title as missing, so this only guards the recorrect
        # path; a fill never reaches here with such a value as an improvement.
        and resolved_album.strip().lower() != (state.title or "").strip().lower())

    # --- decide the final album (never a compilation / never a secondary) ---
    final_album = state.album
    album_filled = False
    album_recorrected = False
    if plan.fill_album and resolved_album_is_original:
        # A missing/placeholder album -> fill with the resolved original.
        final_album = resolved_album
        album_filled = True
    elif plan.recorrect_album and resolved_album_is_original \
            and resolved_album.strip().lower() != (state.album or "").strip().lower():
        # A PRESENT album is overwritten ONLY when the fresh resolve is a
        # different, non-secondary original. Marker-gated files (the stack wrote
        # them) need only that. The one-time HEURISTIC catch-up for unmarked
        # pre-marker files additionally requires the resolved original to have a
        # STRICTLY EARLIER release year than the present edition (report §7.3) -
        # the original predates its live/deluxe/anniversary/remaster edition.
        if plan.recorrect_heuristic:
            r_year = (meta.album_year or meta.year or "").strip()
            if (r_year.isdigit() and state.year.isdigit()
                    and int(r_year) < int(state.year)):
                final_album = resolved_album
                album_recorrected = True
        else:
            final_album = resolved_album
            album_recorrected = True

    # --- decide the final track ---
    final_track = str(state.track) if state.track > 0 else ""
    track_filled = False
    if plan.fill_track and resolved_track:
        head = resolved_track.split("/", 1)[0].strip()
        if head.isdigit() and int(head) > 0:
            final_track = head
            track_filled = True

    album_written = album_filled or album_recorrected
    changed = year_filled or year_replaced or album_written or track_filled
    result = BackfillResult(
        changed=changed, year_filled=year_filled, year_replaced=year_replaced,
        album_filled=album_filled, album_recorrected=album_recorrected,
        track_filled=track_filled, error=False,
        old_year=state.year, new_year=final_year if (year_filled or year_replaced) else "",
        old_album=state.album, new_album=final_album if album_written else "",
        new_track=final_track if track_filled else "",
    )
    if not changed or dry_run:
        return result

    try:
        # One mtime-preserving write. The present artist/title are passed back
        # unchanged; year is ALWAYS the final (present-or-filled) value so the
        # uniform no-year clear in write_tags never drops a present year.
        tags.write_tags(path, state.artist, state.title, final_album,
                        year=final_year or None,
                        track=final_track or None)
        # Stamp the durable album provenance marker whenever THIS write set the
        # album (fill OR recorrect), so future re-corrections are marker-gated
        # and safe (report §7.2). Mirrors the YouTube CHARTS_SOURCE marker.
        if album_written:
            tags.stamp_charts_album_src(path, _RESOLVER_ALBUM_MARKER)
        # Graduate a corrected YouTube file: clear the marker so it is not
        # reprocessed forever. Only after an actual year replacement.
        if year_replaced:
            tags.clear_charts_source(path)
    except Exception as e:  # defensive: tags.* already fail-open internally
        logger.debug(t("import.metafill.write_error", path=path, error=e))
        return BackfillResult(
            changed=False, year_filled=False, year_replaced=False,
            album_filled=False, album_recorrected=False,
            track_filled=False, error=True,
            old_year=state.year, old_album=state.album,
        )
    return result
