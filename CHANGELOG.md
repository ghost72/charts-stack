# Changelog

All notable changes to charts-stack are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Versioning note: charts-stack is versioned as **one stack**, not per service.
The services are tightly coupled (shared `.env`, shared `compose.yml`, shared
central `lang/` files, shared copy-into-every-service modules) and are always
deployed together, so a single stack version is the honest unit.

## [1.3.0] - 2026-10-21

Clearer notifications and a small dashboard wording improvement.

### Added
- **Combined per-song source labels in the download and import notifications.**
  A chart song can belong to several playlists at once (e.g. it is listed in
  both `missing.txt` → `charts` and `missing-hitparade.txt` → `hitparade`).
  Previously the per-song label in the charts-download and charts-import
  Pushover messages showed only the first source, which looked like a bug.
  Now every contributing playlist is listed as one combined label per song
  (e.g. "aus charts, hitparade"); the song still appears once, never duplicated
  per source. `charts-download`'s manifest now records all sources (`sources`
  list) while keeping the single `source` string for backward compatibility, so
  older manifest entries keep working. Per-source download fairness is
  unchanged (it still uses each song's primary source). Playlist writing was
  already correct and is untouched.

### Changed
- Dashboard descriptions for the source services (sync, flashback, recommend)
  now mention that missing songs are fetched hourly by charts-download, matching
  the existing hitparade wording.

## [1.2.1] - 2026-10-19

Patch release fixing a critical regression in 1.2.0.

### Fixed
- **Critical: charts-download spun in an infinite loop and never searched.**
  The per-source fairness interleave introduced in 1.2.0 used a loop condition
  that never terminated (the source buckets were never drained), so every run
  pinned the CPU at 100% right after loading the missing list and never reached
  the search phase - no songs were downloaded at all. Replaced with a bounded
  loop. Anyone on 1.2.0 should upgrade.
- **Dashboard service tiles now expand by tap on iPad / wide touch screens.**
  The 1.1.0 touch handler relied on a `click` event that iOS Safari does not
  reliably fire on a plain element; it now uses pointer events with a tap/scroll
  threshold.
- charts-flashback notification also lists the songs that are already in the
  library (not just a count).

## [1.2.0] - 2026-10-19

Reliability and usability pass: fair download scheduling so hard-to-find
sources can no longer starve the findable chart songs, a file-mtime guard so
retagging no longer disturbs Navidrome's "date added", richer notifications,
and a Navidrome setup example in the README.

### Added
- **Per-source download fairness budget.** charts-download now interleaves the
  missing sources round-robin and caps searches per source (`MAX_PER_SOURCE`,
  default 10) within a larger combined `MAX_PER_RUN` (default raised to 40). A
  long, hard-to-find source (e.g. the 100+ Mundart titles Soulseek rarely has)
  can no longer fill every slot and permanently starve the findable chart
  songs - every source now gets a turn each run.
- **File-mtime preservation in charts-import** (`PRESERVE_MTIME`, default on).
  Writing tags / embedding covers / applying ReplayGain to an existing library
  file no longer bumps its filesystem mtime, so old songs stop wrongly
  surfacing as "recently added" in Navidrome smart playlists; an upgraded file
  inherits the replaced file's original date.
- **charts-flashback notification lists the songs** - both the to-download set
  and the already-in-library set - instead of only a count.
- **Navidrome setup example** in the README (a ready `compose.yml` mounting the
  same Music + Charts folders and running as the same uid/gid), plus a list of
  free/paid Subsonic client apps for iPhone and Android.
- Dashboard screenshots in the README.

### Changed
- **Dashboard source finder tables are sortable** - default descending by the
  numeric column (Tracks/Fans), click a header to sort/toggle; the Last.fm tag
  check shows the real total instead of a misleading probe count and previews
  up to 10 example tracks.

### Fixed
- **Dashboard service tiles can be expanded by tap on wide touch screens
  (iPad).** They previously only reacted to a mouse-drag handler, leaving
  touch-on-wide-screen with no way to open a tile.
- **charts-watchdog no longer false-alarms on rebuilds** - a container briefly
  in a transient state (`created`/`starting`) during `docker compose up -d
  --build` no longer triggers a "Problem" alert; the state word is translated.
- Finder "Tracks" column widened so the sort-direction arrow is not clipped.
- charts-upgrade notification: a blank line between songs for readability.

## [1.1.0] - 2026-10-12

Follow-up release: a sortable source finder, honest Last.fm counts, a watchdog
false-alarm fix, and a full pass to make the codebase English-only (user-facing
strings, exceptions, comments and docstrings) with English as the default.

### Added
- **Sortable finder result tables.** The Deezer playlist and artist result
  tables now sort by default descending on their numeric column (Tracks / Fans),
  so the most relevant hits appear first. Clicking a column header sorts by that
  column and toggles ascending/descending (numeric columns sort numerically,
  text columns alphabetically). One shared renderer drives all finder tables.
- Steering/convention: commit messages and all code comments/docstrings are
  English; user-facing text stays in `lang/{en,de,fr}.json` only.

### Changed
- **Honest Last.fm tag count.** The Last.fm tag check now shows the real total
  number of tracks for a tag (`~N tracks`) instead of the misleading probe count
  that always looked like "(5)". It also previews up to 10 example tracks
  (was 3).
- **English is now the single default everywhere.** All remaining hardcoded
  user-facing strings (service exception messages, the Docker-SDK warning, the
  charts-songrequest web UI, the one-off maintenance scripts) were routed
  through i18n or translated, with English as the default value. The language
  files are grouped by service for readability.

### Fixed
- **Watchdog no longer false-alarms on rebuilds.** A container briefly in a
  transient state (`created`/`starting`) during `docker compose up -d --build`
  no longer triggers a "Problem" Pushover alert. Transient states use a separate
  higher threshold, so a genuinely stuck container is still reported, while real
  problems (exited/dead/unhealthy) keep alarming as before. The state word in
  the alert is now translated instead of showing the raw English `created`.
- Restored proper German umlauts (ä/ö/ü) and French accents in the language
  files and in user-facing strings where ASCII digraphs had slipped in.
- Widened the finder "Tracks" column so the sort-direction arrow is no longer
  clipped.

## [1.0.0] - 2026-10-12

First public release. A self-hosted set of services that keeps Navidrome
playlists in sync with current charts, fills gaps via Soulseek (slskd), and
monitors the whole pipeline — Deezer/Last.fm sources, matching against your
existing library, download/import/upgrade, recommendations, a status dashboard
and a watchdog.

### Added
- **Internationalization (i18n) across all services.** Every user-facing text —
  log messages, Pushover notifications and the dashboard UI — is switchable via
  the single `CHARTS_LANG` environment variable. English is the default and
  fallback; German (`de`) and French (`fr`) ship out of the box. Translations
  live centrally in `lang/{en,de,fr}.json` (one file per language, shared by all
  services and mounted read-only at `/lang`). A new language is added by
  dropping in `lang/<code>.json` — no code change. A test guards that all
  shipped language files carry the same keys and placeholders.
  - Python services use a stdlib-only loader (`app/i18n.py`) with a
    chosen→English→key-name fallback chain that never raises.
  - The shell-based watchdog has its own BusyBox-safe `t()` using the same
    central language files.
- **Per-service Pushover switch.** `PUSHOVER_<SERVICE>_ENABLED` (default on) lets
  you silence individual services without removing credentials, so notification
  volume can be tuned per service.
- **Per-playlist genre filter override.** Each playlist entry may set
  `genre_filter: true|false`; an explicit value beats the global default.
- **Dashboard source finder** for Deezer playlists, Deezer artists, Last.fm tags
  and the live Deezer genre list — each result yields a ready-to-paste snippet.
- **Deezer `artist` source type** (`type: "artist"`) to pull an artist's top
  tracks as a playlist source, alongside `chart` and `playlist`.
- **Donate footer** in the dashboard plus a GitHub `FUNDING.yml`.
- Bilingual documentation (`README.md` DE/EN, `CHARTS-SOURCES.md` DE/EN) and a
  stack `VERSION` file.

### Changed
- **Genre filter is now opt-in.** The shipped default is `GENRE_FILTER=0`
  (previously on). It only ever made sense for dynamic chart sources (unknown
  incoming songs), not for fixed snippet sources (artists/playlists), and an
  always-on default silently dropped genres (e.g. Schlager) for fresh users.
- **`.env.example` comments are now English** (values unchanged) for an
  international audience; a single example file, no per-language variants.
- Dashboard source-finder result snippets no longer wrap character-by-character;
  they are single-line and horizontally scrollable, and the finder uses a
  two-column layout for wider boxes.
- `exclude_artists` now also matches an excluded artist that appears only as a
  collaboration contributor (not just the main artist).

### Fixed
- Dashboard live-check now follows HTTP 308 redirects for all external
  read APIs (ListenBrainz, Deezer, Last.fm, iTunes, MusicBrainz).
- ListenBrainz "no data yet" (404) and transient external 5xx/timeouts are no
  longer reported as service failures.
- Three watchdog debounce/recover/cooldown variables are now actually passed
  through to the container.

[1.2.1]: https://keepachangelog.com/
[1.2.0]: https://keepachangelog.com/
[1.1.0]: https://keepachangelog.com/
[1.0.0]: https://keepachangelog.com/
