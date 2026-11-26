"""
Charts-Sync – main service.

Flow per sync run:
  1. Index the music library (MUSIC_DIR) ONCE.
  2. For EVERY configured chart source (CHARTS):
     a. Fetch the current charts from Deezer (metadata only, no account).
     b. Match each chart track against the library.
     c. Write the hits as a dedicated M3U playlist in PLAYLIST_DIR.
     d. Write missing songs into a dedicated missing file in CHARTS_DIR.
  3. Optional: Pushover notification (only on change, if desired).

The service runs as an endless loop and repeats the run every
SYNC_INTERVAL_HOURS hours.

Note: this service deliberately downloads NO audio files. It works exclusively
with metadata and the already present library.
"""

import json
import logging
from html import escape
import os
import time
from datetime import datetime

import httpx

from . import (artist_catalog, config, deezer, health, library, notify, notify_budget, playlist, playlist_rules,
               presence, status)
from .i18n import t

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("charts-sync")


# --- State (for the "only on change" notification) ---

def _state_path() -> str:
    return os.path.join(config.CHARTS_DIR, config.STATE_FILE)


def _load_state() -> dict:
    """Load the last saved state per playlist."""
    try:
        with open(_state_path(), encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    os.makedirs(config.CHARTS_DIR, exist_ok=True)
    tmp = _state_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, _state_path())


def _library_path(lib, artist: str, title: str) -> str | None:
    track, _score = lib.find_match(artist, title, config.MATCH_THRESHOLD)
    if track is not None:
        return track.path
    return presence.find_existing(config.MUSIC_DIR, artist, title)


def _write_scene_playlists(scenes, find_path, filenames) -> None:
    for scene, tracks in scenes.items():
        name = filenames.get(scene) or artist_catalog.scene_filename(scene)
        matches = []
        seen = set()
        for position, track in enumerate(tracks, 1):
            path = find_path(track.artist, track.title)
            if path and path not in seen:
                matches.append((position, track.artist, track.title, path))
                seen.add(path)
        try:
            with playlist_rules.selection_lock(config.CHARTS_DIR):
                selection = artist_catalog.load_selection()
                current = artist_catalog.configured_scenes(selection)
                if artist_catalog.normalize_name(scene) not in current:
                    continue
                additions = playlist.write_scene_playlist(matches, name, scene)
        except (OSError, UnicodeError):
            logger.exception("Could not write Mundart scene playlist %s", name)
            continue
        if additions:
            message = notify_budget.fit_message(
                [f"<b>{escape(scene)}</b>", t("notify.new_in_playlist", count=len(additions))],
                [notify_budget.Block(entries=[
                    notify_budget.Entry([t("notify.scene_song_entry", artist=artist, title=title)])
                    for _pos, artist, title, _path in additions
                ])],
                [], more=lambda n: t("notify.more", count=n),
            )
            notify.send(t("notify.title", service="charts-sync"), message)


def _apply_scene_playlist_changes() -> None:
    with playlist_rules.selection_lock(config.CHARTS_DIR):
        _apply_locked_scene_playlist_changes()


def _apply_locked_scene_playlist_changes() -> None:
    selection = artist_catalog.load_selection()
    pending = selection.get("pending_scene_deletions", {})
    if pending and not os.path.isdir(config.PLAYLIST_DIR):
        raise FileNotFoundError(f"Playlist directory unavailable: {config.PLAYLIST_DIR}")
    for name in pending:
        playlist.remove_scene_playlist(name, strict=True)
    if pending:
        selection.pop("pending_scene_deletions")
        artist_catalog._atomic_write(artist_catalog._selection_path(), selection)
    disabled = selection.get("disabled_scene_playlists", {})
    blocked = set(disabled) | set(artist_catalog.blocked_scene_filenames(selection))
    for name in blocked:
        try:
            playlist.remove_scene_playlist(name)
        except OSError:
            logger.exception("Could not remove Mundart scene playlist %s", name)
    if os.path.isdir(config.PLAYLIST_DIR):
        try:
            playlist.migrate_scene_playlists(selection.get("scene_files", {}), blocked)
        except (OSError, UnicodeError, ValueError):
            logger.exception("Scene playlist migration failed; no scene regeneration will run.")
            raise


def _write_genre_playlists(lib) -> None:
    with playlist_rules.selection_lock(config.CHARTS_DIR):
        selection = artist_catalog.load_selection()
        rules = selection.get("genre_playlists", {})
        artist_names = {artist_catalog.normalize_name(scene)
                        for item in selection["artists"].values()
                        for scene in artist_catalog._scene_names(item)}
        filenames = {artist_catalog.scene_filename(scene): artist_catalog.normalize_name(scene)
                     for scene in artist_names}
        for name, rule in rules.items():
            key = artist_catalog.normalize_name(name)
            filename = artist_catalog.scene_filename(name)
            if (key in artist_names or filename in filenames
                    or filename in artist_catalog.blocked_scene_filenames(selection)):
                raise artist_catalog.ArtistCatalogError("Genre playlist conflicts with another playlist.")
            filenames[filename] = key
            if "genre_id" in rule:
                continue
            wanted = {artist_catalog.normalize_name(genre) for genre in rule["genres"]}
            selected = sorted(
                (track for track in lib.tracks
                 if ".charts-trash" not in str(getattr(track, "path", "")).split(os.sep)
                 and wanted.intersection(artist_catalog.normalize_name(genre)
                                         for genre in getattr(track, "genres", []))),
                key=lambda track: (track.artist.casefold(), track.title.casefold(), track.path),
            )
            matches = []
            seen = set()
            for track in selected:
                if track.path not in seen:
                    matches.append((len(matches) + 1, track.artist, track.title, track.path))
                    seen.add(track.path)
            playlist.write_genre_playlist(matches, filename, name)


def _sync_deezer_genres(lib) -> tuple[list, dict, list[str]]:
    selection = artist_catalog.load_selection()
    rules = {name: rule for name, rule in selection.get("genre_playlists", {}).items()
             if "genre_id" in rule}
    downloads = []
    errors = []
    completed_rules = {}
    for name, rule in rules.items():
        try:
            tracks = deezer.fetch_genre_chart(rule["genre_id"], rule["limit"])
        except (httpx.HTTPError, ValueError, RuntimeError) as exc:
            logger.error("Deezer genre playlist %s failed: %s", name, exc)
            errors.append(f"{name}: {exc}")
            continue
        matches = []
        seen_paths = set()
        for track in tracks:
            path = _library_path(lib, track.artist, track.title)
            if path and path not in seen_paths:
                matches.append((track.position, track.artist, track.title, path))
                seen_paths.add(path)
        with playlist_rules.selection_lock(config.CHARTS_DIR):
            current = artist_catalog.load_selection()
            filename = artist_catalog.scene_filename(name)
            if (current.get("genre_playlists", {}).get(name) != rule
                    or filename in artist_catalog.blocked_scene_filenames(current)):
                continue
            playlist.write_genre_playlist(matches, filename, name)
            downloads.extend((track.position, track, name) for track in tracks)
            completed_rules[name] = rule
        logger.info("Deezer genre playlist %s: %s library matches / %s chart tracks (limit %s)",
                    name, len(matches), len(tracks), rule["limit"])
    return downloads, completed_rules, errors


def _clear_scene_downloads() -> None:
    if os.path.isfile(os.path.join(config.CHARTS_DIR, artist_catalog.SCENE_MISSING_FILE)):
        playlist.write_missing([], artist_catalog.SCENE_MISSING_FILE)


def _sync_source(chart_source, lib, state: dict) -> str:
    """
    Process a single chart source.

    Returns a per-source outcome for the G3 structural-break detector:
      "error" - the internal fetch except branch fired (transport failure),
      "empty" - fetched successfully but zero chart tracks,
      "ok"    - a successful fetch (tracks returned) + the usual write/notify.
    The match/dedupe/write/notify path below is unchanged; only the return value
    is added. A source that raises PAST this function is caught and mapped to
    "error" by run_sync's per-source try/except.
    """
    logger.info(t("sync.source.header", name=chart_source.title))

    # a. Fetch charts
    try:
        if (not getattr(chart_source, "_collect_artist_scenes", False)
                and getattr(chart_source, "name", "") == config.CHARTS_ARTIST_PLAYLIST):
            _clear_scene_downloads()
            _apply_scene_playlist_changes()
        chart_tracks = deezer.fetch_chart(chart_source)
    except Exception as e:
        logger.error(t("sync.source.fetch_failed", name=chart_source.title, error=e))
        return "error"
    scenes = getattr(chart_source, "scene_tracks", {})
    scene_filenames = getattr(chart_source, "scene_filenames", {})
    paths: dict[tuple[str, str], str | None] = {}

    def find_path(artist: str, title: str) -> str | None:
        if not scenes:
            return _library_path(lib, artist, title)
        key = (artist, title)
        if key not in paths:
            paths[key] = _library_path(lib, artist, title)
        return paths[key]

    if not getattr(chart_source, "_collect_artist_scenes", False):
        _write_scene_missing(getattr(chart_source, "scene_download_tracks", []), find_path)

    if not chart_tracks:
        if any(scenes.values()) and not getattr(chart_source, "_collect_artist_scenes", False):
            _write_scene_playlists(scenes, find_path, scene_filenames)
        logger.warning(t("sync.source.no_tracks", name=chart_source.title))
        return "empty"

    # b. Match
    matched: list[tuple[int, str, str, str]] = []
    missing: list[tuple[int, str, str]] = []
    for ct in chart_tracks:
        # Presence guard also accepts real library copies excluded by compilation matching.
        existing = find_path(ct.artist, ct.title)
        if existing is not None:
            matched.append((ct.position, ct.artist, ct.title, existing))
            continue
        missing.append((ct.position, ct.artist, ct.title))

    matched.sort(key=lambda x: x[0])
    missing.sort(key=lambda x: x[0])

    deduped: list[tuple[int, str, str, str]] = []
    seen_paths: set[str] = set()
    for entry in matched:
        if entry[3] in seen_paths:
            continue
        seen_paths.add(entry[3])
        deduped.append(entry)
    removed = len(matched) - len(deduped)
    if removed:
        logger.info(t("sync.source.dedup_removed", name=chart_source.title, count=removed))
    matched = deduped
    ordered = _order_matched(chart_source, matched, state)
    playlist.write_playlist(ordered, chart_source.name, chart_source.title)
    playlist.write_missing(missing, chart_source.missing)
    if not getattr(chart_source, "_collect_artist_scenes", False):
        _write_scene_playlists(scenes, find_path, scene_filenames)
    logger.info(t(
        "sync.source.done",
        name=chart_source.title, matched=len(matched), total=len(chart_tracks),
    ))
    _handle_notify(chart_source, matched, missing, state)
    return "ok"


def _write_scene_missing(download_tracks, find_path, *, genre_rules: dict | None = None) -> None:
    pending = {}
    pending_scenes: dict[tuple[str, str], set[str]] = {}
    for rank, track, scene in download_tracks:
        if (" - " in track.artist
                or any(len(value.splitlines()) != 1 for value in (track.artist, track.title))):
            continue
        if find_path(track.artist, track.title) is None:
            key = (artist_catalog.normalize_name(track.artist),
                   artist_catalog.normalize_name(track.title))
            pending_scenes.setdefault(key, set()).add(scene)
            previous = pending.get(key)
            if previous is None or rank < previous[0]:
                pending[key] = (rank, track.artist, track.title)
    if pending:
        with playlist_rules.selection_lock(config.CHARTS_DIR):
            selection = artist_catalog.load_selection()
            active = set(artist_catalog.configured_scenes(selection)) & {
                artist_catalog.normalize_name(scene) for scene in selection.get("scene_downloads", {})}
            active.update(artist_catalog.normalize_name(name)
                          for name, rule in (genre_rules or {}).items()
                          if selection.get("genre_playlists", {}).get(name) == rule
                          and "genre_id" in rule
                          and artist_catalog.scene_filename(name)
                          not in artist_catalog.blocked_scene_filenames(selection))
            retained = {
                key: {scene for scene in sources if artist_catalog.normalize_name(scene) in active}
                for key, sources in pending_scenes.items()
            }
            pending = {key: entry for key, entry in pending.items() if retained[key]}
            playlist.write_missing(
                sorted(pending.values()), artist_catalog.SCENE_MISSING_FILE,
                scene_sources={(entry[1], entry[2]): sorted(retained[key])
                               for key, entry in pending.items()},
            )


def _order_matched(chart_source, matched, state: dict):
    """
    Put the matched tracks into the configured order while maintaining the
    "first_seen" date per song in the state.

    matched: list (position, artist, title, absolute_path), already
             chart-sorted + deduplicated.
    Returns: list in the same tuple form, in the desired order.

    PLAYLIST_ORDER="chart" (default): unchanged (chart position, number 1 on top).

    PLAYLIST_ORDER="newest-first": songs most recently added on top. For this
      the date of first appearance is kept per song in the state
      (first_seen[relpath] = ISO timestamp). Known songs keep their date, new
      ones get "now" -> move to the top. On an equal date (e.g. on the very
      first run all are new) the chart position decides, so the order stays
      deterministic.
    """
    if config.PLAYLIST_ORDER != "newest-first":
        return matched

    prev = state.get(chart_source.name, {})
    first_seen: dict[str, str] = dict(prev.get("first_seen", {}))
    now_iso = datetime.now().isoformat(timespec="seconds")

    # Maintain first_seen for current songs (relative path = stable key,
    # identical to what is written in the m3u).
    current_relpaths: list[str] = []
    for _pos, _a, _t, path in matched:
        rel = os.path.relpath(path, config.MUSIC_DIR)
        current_relpaths.append(rel)
        if rel not in first_seen:
            first_seen[rel] = now_iso

    # Keep only entries for currently present songs (do not let the state grow;
    # a song returning later rightly counts as new again).
    first_seen = {rel: first_seen[rel] for rel in current_relpaths}

    # Remember it so _handle_notify does not discard it on the state update.
    chart_source._first_seen = first_seen  # noqa: SLF001 (internal transport)

    # Sort stably in two stages (Python's sort is stable):
    #   1. by chart position ascending (tiebreaker on equal date),
    #   2. by first_seen descending (main criterion: newest on top).
    ordered = sorted(matched, key=lambda e: e[0])  # chart position ascending
    ordered.sort(
        key=lambda e: first_seen.get(os.path.relpath(e[3], config.MUSIC_DIR), now_iso),
        reverse=True,
    )
    return ordered


def _handle_notify(chart_source, matched, missing, state: dict) -> None:
    """
    Send Pushover, optionally only when something changed.

    "Change" covers both the playlist hits (matched paths) and the list of
    missing songs. So a message also goes out when only the songs to search for
    change (e.g. new chart entries), even if the hit list stays identical.
    """
    current_paths = sorted(p for _pos, _a, _t, p in matched)
    # Missing songs as a comparable signature (artist|title), position-independent.
    current_missing = sorted(f"{a}|{t}" for _pos, a, t in missing)

    prev = state.get(chart_source.name, {})
    prev_paths = prev.get("paths", [])
    prev_missing = prev.get("missing", [])

    paths_changed = current_paths != prev_paths
    missing_changed = current_missing != prev_missing
    changed = paths_changed or missing_changed

    # Newly added songs (for a descriptive message)
    new_songs = [
        (pos, a, t) for pos, a, t, p in matched if p not in set(prev_paths)
    ]

    # Always update the state (hits AND missing songs). Carry first_seen along
    # (for PLAYLIST_ORDER=newest-first) if _order_matched set it - otherwise the
    # order history would be lost on the next run. With PLAYLIST_ORDER=chart it
    # does not exist -> omit it.
    entry = {"paths": current_paths, "missing": current_missing}
    first_seen = getattr(chart_source, "_first_seen", None)
    if first_seen is not None:
        entry["first_seen"] = first_seen
    state[chart_source.name] = entry

    if config.NOTIFY_ONLY_ON_CHANGE and not changed:
        logger.info(t("sync.notify.no_change", name=chart_source.title))
        return

    total = len(matched) + len(missing)
    # Pushover title = service emoji + service name (like all other charts
    # services: "⬇️ charts-download", "🎧 charts-recommend" ...). The playlist
    # name with its own emojis sits as a bold header in the body - so it is
    # identical to the Navidrome display name. {date} is replaced as in the
    # playlist writer.
    title = t("notify.title", service="charts-sync")
    playlist_name = chart_source.title.replace(
        "{date}", datetime.now().strftime("%d.%m.%Y"))
    lines = [f"<b>{playlist_name}</b>",
             t("notify.summary", matched=len(matched), total=total)]

    stats = getattr(chart_source, "last_stats", None) or {}
    source_lines = []
    for kind, name in (("deezer", "🎧 Deezer"), ("lastfm", "📻 Last.fm"),
                       ("apple", "🍎 Apple Music"), ("energy", "⚡ Energy")):
        if f"{kind}_raw" not in stats:
            continue
        line = t("notify.source_item", name=name, received=stats[f"{kind}_raw"],
                 additional=stats[f"{kind}_new"])
        if stats[f"{kind}_failed"]:
            line += " " + t("notify.source_failed", count=stats[f"{kind}_failed"])
        source_lines.append(line)
    if source_lines:
        lines.extend(["", t("notify.source_header"), *source_lines,
                      "", t("notify.source_explanation")])
    filter_lines = []
    if stats.get("genre_filtered"):
        filter_lines.append(t("notify.genre_filtered", count=stats["genre_filtered"]))
    if stats.get("artist_filtered"):
        filter_lines.append(t("notify.artist_filtered", count=stats["artist_filtered"]))

    # Overview line first: the new/missing counts must survive the Pushover
    # budget cut, so they sit in the head (never cut). Order = detail order.
    new_count = len(new_songs) if (new_songs and changed) else 0
    overview_parts = []
    if new_count:
        overview_parts.append(t("notify.overview.new", count=new_count))
    if missing:
        overview_parts.append(t("notify.overview.missing", count=len(missing)))
    overview = notify_budget.overview_line(overview_parts)
    if source_lines and (filter_lines or overview):
        lines.append("")
    lines.extend(filter_lines)
    if overview:
        lines.append(overview)

    blocks = []
    if new_count:
        # Songs 9+ were never listed (the overview carries the full count).
        blocks.append(notify_budget.Block(
            header=["", t("notify.new_in_playlist", count=len(new_songs))],
            entries=[notify_budget.Entry([t("notify.song_entry", pos=pos,
                                            artist=a, title=song_title)])
                     for pos, a, song_title in new_songs[:8]]))

    # List missing songs so you can search for them in slskd straight from the
    # phone (without a NAS login). Amount limited via NOTIFY_MISSING_LIMIT.
    if missing and config.NOTIFY_MISSING_LIMIT > 0:
        limit = config.NOTIFY_MISSING_LIMIT
        blocks.append(notify_budget.Block(
            header=["", t("notify.missing.header", count=len(missing))],
            entries=[notify_budget.Entry([t("notify.song_entry", pos=pos,
                                            artist=a, title=song_title)])
                     for pos, a, song_title in missing[:limit]],
            hidden=max(0, len(missing) - limit),
            cap_note=(t("notify.missing.more", count=len(missing) - limit)
                      if len(missing) > limit else "")))
    elif missing:
        blocks.append(notify_budget.Block(entries=[notify_budget.Entry(
            ["", t("notify.missing.none_listed", count=len(missing))],
            weight=0)]))

    # Pushover limits a message to 1024 characters. Entries are cut as whole
    # songs with one explicit "... and N more" line.
    message = notify_budget.fit_message(
        lines, blocks, [], more=lambda n: t("notify.more", count=n))
    notify.send(title, message)


def run_sync() -> tuple[dict, int, int, dict]:
    """
    Run a single sync run over all chart sources.

    Returns a 4-tuple (counters, produced, attempted, health_map):
      counters:   dashboard counters {"sources": ..., "library_tracks": ...}.
                  "sources" counts successful CHARTS sources plus completed
                  Deezer genre playlists.
      produced:   detector-internal count of successful sources.
      attempted:  configured CHARTS sources plus completed genre playlists.
      health_map: the per-source health map (already persisted this run).

    produced/attempted/health_map are detector-internal (G3) and are NEVER
    written into counters. EVERY return site yields the same 4-tuple shape so
    _guarded_sync's four-value unpack can never raise ValueError.
    """
    logger.info(t("sync.run.start", sources=len(config.CHARTS)))

    _clear_scene_downloads()

    selection = artist_catalog.load_selection()
    if not config.CHARTS and not selection.get("genre_playlists"):
        if any(selection.get(field) for field in
               ("pending_scene_deletions", "disabled_scene_playlists", "scene_files")):
            _apply_scene_playlist_changes()
        logger.warning(t("sync.run.no_sources"))
        # MEDIUM-1: match the 4-tuple arity. attempted == 0 means the
        # whole_run_empty guard (attempted > 0 and produced == 0) is False, so
        # an empty-CHARTS deployment is correctly NON-alarming; _load_health()
        # yields a fail-open health dict whose "sources" map is {} -> no dead
        # sources.
        return {"sources": 0, "library_tracks": 0}, 0, 0, health.load()

    _apply_scene_playlist_changes()
    # 1. Index the library ONCE (the expensive part, reused for all sources)
    lib = library.build_index(config.MUSIC_DIR)
    if not lib.tracks:
        logger.warning(t("sync.run.library_empty", dir=config.MUSIC_DIR))
    if any("genre_id" not in rule for rule in selection.get("genre_playlists", {}).values()):
        artist_catalog.record_library_genres(lib)
    else:
        artist_catalog.record_playlist_reservations()
    _write_genre_playlists(lib)
    genre_downloads, genre_rules, genre_errors = _sync_deezer_genres(lib)
    if not config.CHARTS:
        _write_scene_missing(genre_downloads, lambda artist, title: _library_path(lib, artist, title),
                             genre_rules=genre_rules)
        if genre_errors:
            raise RuntimeError("Deezer genre playlists failed: " + "; ".join(genre_errors))
        logger.info(t("sync.run.done"))
        count = len(genre_rules)
        return {"sources": count, "library_tracks": len(lib.tracks)}, count, count, health.load()

    # 2. Load state + health, process all sources, save both
    state = _load_state()
    health_map = health.load()
    scenes: dict[str, list] = {}
    filenames: dict[str, str] = {}
    download_tracks = []
    scene_sources_ok = True
    manual_source = next((source for source in config.CHARTS
                          if source.name == config.CHARTS_ARTIST_PLAYLIST), config.CHARTS[0])
    ok_sources = 0
    for chart_source in config.CHARTS:
        chart_source._collect_artist_scenes = True
        chart_source._scene_manual_feeds = chart_source is manual_source
        chart_source.scene_tracks = {}
        chart_source.scene_download_tracks = []
        chart_source.scene_filenames = {}
        try:
            outcome = _sync_source(chart_source, lib, state)  # "ok"|"empty"|"error"
            if outcome == "ok":
                ok_sources += 1  # UNCHANGED existing "sources" counter
        except Exception as e:
            logger.exception(t("sync.source.exception", name=chart_source.title, error=e))
            outcome = "error"  # a run-loop crash is an "error" outcome
        finally:
            del chart_source._collect_artist_scenes
            del chart_source._scene_manual_feeds
        if outcome == "error" or not getattr(chart_source, "scene_complete", True):
            scene_sources_ok = False
        else:
            for scene, tracks in chart_source.scene_tracks.items():
                scenes.setdefault(scene, []).extend(tracks)
            filenames.update(chart_source.scene_filenames)
            download_tracks.extend(chart_source.scene_download_tracks)
        # One place records the per-source outcome into the health counter.
        health._record_source_outcome(health_map, chart_source.name, outcome)
    if scene_sources_ok:
        scene_paths: dict[tuple[str, str], str | None] = {}

        def find_path(artist: str, title: str) -> str | None:
            key = (artist, title)
            if key not in scene_paths:
                scene_paths[key] = _library_path(lib, artist, title)
            return scene_paths[key]

        _write_scene_missing(download_tracks + genre_downloads, find_path, genre_rules=genre_rules)
        _write_scene_playlists(scenes, find_path, filenames)
    else:
        logger.warning("Scene playlists were not regenerated because a chart source failed; "
                       "the scene download queue remains empty.")
        _write_scene_missing(genre_downloads, lambda artist, title: _library_path(lib, artist, title),
                             genre_rules=genre_rules)
    _save_state(state)
    try:
        health.save(health_map)
    except OSError as e:
        # Keep the in-memory verdict for this run; retry next run.
        logger.warning(t("sync.health.write_failed", error=e))

    if genre_errors:
        raise RuntimeError("Deezer genre playlists failed: " + "; ".join(genre_errors))
    logger.info(t("sync.run.done"))
    counters = {
        "sources": ok_sources + len(genre_rules),
        "library_tracks": len(lib.tracks),
    }
    attempted = len(config.CHARTS) + len(genre_rules)
    return counters, ok_sources + len(genre_rules), attempted, health_map


def _next_run_iso() -> str:
    """Time of the next regular run as ISO-8601 (for the dashboard)."""
    return time.strftime("%Y-%m-%dT%H:%M:%S%z",
                         time.localtime(time.time() + max(1, config.SYNC_INTERVAL_HOURS) * 3600))


def _prev_status() -> str:
    """
    Read the status of the PREVIOUS run from the status JSON (before
    status.start() sets it to 'running'). Serves the state-change-gated alarm:
    this way it can be detected whether the last run was already an error. We
    read the file via the status module (same file/path) but do not create an
    additional state file. If the file is missing, the run counts as 'ok' (no
    previous error). Copied verbatim from charts-recommend.
    """
    data = status._read()
    val = data.get("status")
    return val if isinstance(val, str) else "ok"


def _alert_structural(message: str, was_error_before: bool) -> None:
    """
    Pushover alarm on a structural break - only on state change, when
    NOTIFY_ONLY_ON_CHANGE is set, so the persisting outage does not re-alarm on
    every run. Without PUSHOVER_* notify.send() is a no-op anyway. Mirrors
    charts-recommend's _alert_fetch_error.
    """
    if config.NOTIFY_ONLY_ON_CHANGE and was_error_before:
        logger.info(t("sync.alert.still_failing"))
        return
    notify.send(t("sync.alert.structural_title"), message)


def _structural_reason_msg(whole_run_empty: bool, attempted: int,
                           dead_sources: list[str]) -> str:
    """Build the readable status/alarm reason line from the verdict parts."""
    parts: list[str] = []
    if whole_run_empty:
        parts.append(t("sync.alert.whole_run_empty", attempted=attempted))
    if dead_sources:
        parts.append(t("sync.alert.sources_dead",
                       count=len(dead_sources),
                       runs=config.SYNC_DEAD_RUNS_THRESHOLD,
                       names=", ".join(sorted(dead_sources))))
    return " ".join(parts)


def _guarded_sync() -> None:
    """Run run_sync(), evaluate the structural-break verdict, write status."""
    # Remember the previous status BEFORE start() (start() sets 'running').
    prev_status = _prev_status()
    status.start()
    try:
        counters, produced, attempted, health_map = run_sync()
        # Whole-run empty fires immediately (no threshold): every configured
        # source produced nothing this run.
        whole_run_empty = attempted > 0 and produced == 0
        # A previously-ok source dead across N consecutive runs.
        dead_sources = [
            name for name, h in health_map.get("sources", {}).items()
            if isinstance(h, dict)
            and h.get("dead_runs", 0) >= config.SYNC_DEAD_RUNS_THRESHOLD]

        if whole_run_empty or dead_sources:
            reason = _structural_reason_msg(whole_run_empty, attempted, dead_sources)
            # counters unchanged; produced/attempted/health never enter it.
            status.finish("error",
                          t("sync.status.structural_error", reason=reason),
                          counters, _next_run_iso())
            _alert_structural(reason, prev_status == "error")
            return

        parts = [f"{v} {k}" for k, v in counters.items() if isinstance(v, (int, float)) and v]
        msg = ", ".join(parts) if parts else "nichts zu tun"
        status.finish("ok", msg, counters, _next_run_iso())
        if prev_status == "error":
            notify.send(t("notify.title", service="charts-sync"),
                        t("sync.notify.structural_recovered"))
    except Exception as e:
        logger.exception(t("sync.unexpected_error", error=e))
        status.finish("error", str(e), {}, _next_run_iso())
        _alert_structural(str(e), prev_status == "error")


def main() -> None:
    """Start the scheduler loop."""
    status.init("charts-sync")
    logger.info(t(
        "sync.scheduler.started",
        sources=", ".join(f"{c.title} ({c.name})" for c in config.CHARTS) or "keine",
        interval=config.SYNC_INTERVAL_HOURS,
        resync=config.RESYNC_CHECK_SECONDS,
        playlist_dir=config.PLAYLIST_DIR,
        charts_dir=config.CHARTS_DIR,
    ))

    if config.RUN_ON_START:
        _guarded_sync()
        _clear_resync_signal()  # clear a signal possibly set during the run
    else:
        logger.info(t("sync.scheduler.run_on_start_off"))

    interval_seconds = max(1, config.SYNC_INTERVAL_HOURS) * 3600
    check_seconds = max(5, config.RESYNC_CHECK_SECONDS)

    logger.info(t("sync.scheduler.waiting", interval=config.SYNC_INTERVAL_HOURS))
    next_regular = time.time() + interval_seconds
    while True:
        time.sleep(check_seconds)

        trigger = None
        if _resync_requested():
            trigger = t("sync.scheduler.trigger.resync")
        elif time.time() >= next_regular:
            trigger = t("sync.scheduler.trigger.interval")

        if not trigger:
            continue

        logger.info(t("sync.scheduler.triggered", trigger=trigger))
        _guarded_sync()
        _clear_resync_signal()
        next_regular = time.time() + interval_seconds


def _resync_requested() -> bool:
    """True when charts-import has left a resync signal."""
    return os.path.exists(config.RESYNC_SIGNAL)


def _clear_resync_signal() -> None:
    """Remove the resync signal after a run (idempotent)."""
    try:
        os.remove(config.RESYNC_SIGNAL)
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.debug(t("sync.resync.clear_failed", error=e))


if __name__ == "__main__":
    main()
