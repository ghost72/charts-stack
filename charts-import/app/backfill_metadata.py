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

Rules (see .agents/tasks/metadata-backfill/01-design.md):
  * Never overwrite an existing value (curated/Lidarr album, resolver-set year,
    existing positive track number). A field is written ONLY when it is
    "missing" per ``missing_fields`` below.
  * ``album == title`` (the single-as-album placeholder) and "Unknown Album"
    count as a missing real album and MAY be filled with a resolved
    non-compilation album.
  * One deliberate exception: a YouTube-sourced year (identified by the durable
    TXXX:CHARTS_SOURCE=youtube marker) MAY be REPLACED by the resolver's real
    release year; the marker is then cleared so the file is not reprocessed.
  * Compilations are never written as an album (``resolve.is_compilation_title``).
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


@dataclass(frozen=True)
class Plan:
    """What may be filled/replaced on one file, given its current tags."""
    fill_year: bool       # year missing (conservative fill)
    replace_year: bool    # year PRESENT but the youtube marker is set (FR5.2)
    fill_album: bool      # album missing per the album rule
    fill_track: bool      # track missing per the track rule
    is_youtube: bool      # marker present (drives clear-after-correct)

    def needs_lookup(self) -> bool:
        """True iff something could be written -> a resolver call is worth it."""
        return (self.fill_year or self.replace_year
                or self.fill_album or self.fill_track)


@dataclass(frozen=True)
class FileState:
    """A file's current, backfill-relevant tag state (fail-open reads)."""
    artist: str
    title: str
    album: str
    year: str
    track: int
    source: str   # TXXX:CHARTS_SOURCE marker value ("" when absent)


@dataclass(frozen=True)
class BackfillResult:
    """Outcome of one apply_backfill call (for logging + counters)."""
    changed: bool           # anything would be / was written
    year_filled: bool
    year_replaced: bool
    album_filled: bool
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
    return FileState(artist=artist, title=title, album=album, year=year,
                     track=track, source=source)


def missing_fields(state: FileState) -> Plan:
    """
    Compute the per-file Plan from its current tag state. The heart of the
    never-overwrite rule: a field is "fillable" ONLY when it is missing per the
    definitions in the design (§5). The YouTube-year exception is the only path
    that flags a PRESENT year (replace_year) - and only when the durable
    marker says the year came from YouTube.
    """
    is_youtube = state.source == _YOUTUBE_MARKER
    has_year = bool(state.year)
    return Plan(
        fill_year=not has_year,
        replace_year=has_year and is_youtube,
        fill_album=_album_is_missing(state.album, state.title),
        fill_track=state.track <= 0,
        is_youtube=is_youtube,
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

    # --- decide the final album (never a compilation) ---
    final_album = state.album
    album_filled = False
    if plan.fill_album and resolved_album \
            and not resolve.is_compilation_title(resolved_album):
        final_album = resolved_album
        album_filled = True

    # --- decide the final track ---
    final_track = str(state.track) if state.track > 0 else ""
    track_filled = False
    if plan.fill_track and resolved_track:
        head = resolved_track.split("/", 1)[0].strip()
        if head.isdigit() and int(head) > 0:
            final_track = head
            track_filled = True

    changed = year_filled or year_replaced or album_filled or track_filled
    result = BackfillResult(
        changed=changed, year_filled=year_filled, year_replaced=year_replaced,
        album_filled=album_filled, track_filled=track_filled, error=False,
        old_year=state.year, new_year=final_year if (year_filled or year_replaced) else "",
        old_album=state.album, new_album=final_album if album_filled else "",
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
        # Graduate a corrected YouTube file: clear the marker so it is not
        # reprocessed forever. Only after an actual year replacement.
        if year_replaced:
            tags.clear_charts_source(path)
    except Exception as e:  # defensive: tags.* already fail-open internally
        logger.debug(t("import.metafill.write_error", path=path, error=e))
        return BackfillResult(
            changed=False, year_filled=False, year_replaced=False,
            album_filled=False, track_filled=False, error=True,
            old_year=state.year, old_album=state.album,
        )
    return result
