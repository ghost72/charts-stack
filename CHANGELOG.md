# Changelog

All notable changes to charts-stack are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Versioning note: charts-stack is versioned as **one stack**, not per service.
The services are tightly coupled (shared `.env`, shared `compose.yml`, shared
central `lang/` files, shared copy-into-every-service modules) and are always
deployed together, so a single stack version is the honest unit.

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

[1.1.0]: https://keepachangelog.com/
[1.0.0]: https://keepachangelog.com/
