# Changelog

All notable changes to charts-stack are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Versioning note: charts-stack is versioned as **one stack**, not per service.
The services are tightly coupled (shared `.env`, shared `compose.yml`, shared
central `lang/` files, shared copy-into-every-service modules) and are always
deployed together, so a single stack version is the honest unit.

## [2.0.0] - 2026-10-28

A major feature release. charts-songrequest grows from a single search box into
a three-tab web app (request / MP3 upload / YouTube URL import), the dashboard
gains a live "in progress" overview, every imported song is now tagged with its
release year, and a three-machine source-health signal (watchdog / sync /
energy) surfaces a dead or empty source instead of failing silently. The major
bump also reflects the new runtime dependencies of the songrequest image (see
**Upgrade notes**). No new required environment variables are introduced.

### Added
- **charts-songrequest — three-tab web UI.** The single request box becomes a
  tabbed single card with three tabs sharing one login:
  - **Request** — search a song via slskd (as before).
  - **Upload** — add a local MP3. The file is checked with a real FFT
    frequency-cutoff measurement (`quality.py`, ffmpeg-backed) so an upsampled /
    faked MP3 is detected; a genuine upsample can still be kept through an
    explicit "add anyway" confirmation.
  - **YouTube** — import from a YouTube URL. yt-dlp extracts the audio to MP3
    and embeds the video thumbnail as cover art.
  The search/upload/fetch flows survive an app switch mid-request (abortable
  fetch + visibility handling) and add mobile haptics.
- **charts-dashboard — "in progress" overview.** A new section with four panels:
  live active downloads (joined from the slskd transfer list), the queue /
  missing backlog, the quarantine, and the last upgrade run, with a placeholder
  shown when a panel has no entries. Compact, scrollable panels with aligned
  bands.
- **charts-import — release-year tagging.** Every imported song now has its
  release year resolved and tagged, with a backfill pass for the existing
  library (`mbyear`/`yearmap` + MusicBrainz lookup).
- **Source-health signal across three services.** charts-sync flips its status
  to `error` when a whole run comes back empty or a source is dead across N
  runs (G3); charts-energy adds structural-break detection plus a daily liveness
  re-check (G1/G2); charts-watchdog pages when any service reports
  status=`error` (G0). New i18n keys back the watchdog/energy/sync/liveness
  strings.
- **charts-energy — multi-line config file.** Channels can be configured from a
  readable multi-line `energy-channels.json` file (`ENERGY_CHANNELS_FILE`) in
  addition to the inline `ENERGY_CHANNELS` JSON.
- **charts-dashboard — connection tiles.** The connections section now groups
  tiles by role, adds an Energy (NRJ) stream tile (streaming ICY probe), a
  MusicBrainz tile, and a Swiss Hitparade tile, with Energy/MusicBrainz tiles
  muted when disabled. Flow tiles support touch-drag on wide-screen touch
  devices.
- **charts-config — snippet copy buttons.** Line/block copy buttons and
  plain-language descriptions on the source finder and Energy-channel table.

### Changed
- **Manual-input normalization (charts-songrequest + charts-import).** The
  title match key now transliterates umlauts (ä → a, é → e) before the
  letters-and-digits fold and strips an unbalanced trailing decoration
  (e.g. `… (original`), so a decorated re-request folds onto the same dedup key
  as the clean library title. Manual input from all three tabs is canonicalized
  to the catalog spelling via Deezer (`mash` → `Mash`) for any case, while
  deliberate spellings (`SK.music`, `k.d. lang`) survive a Deezer miss intact.
- **Upload duplicate handling.** The library is checked before the quality
  branch and artist spelling variants are folded, so an upsampled re-upload of a
  song already present in decent quality is rejected — an upsampled fake-320
  never beats an honest copy. The upload path reports an honest "already in
  library" status.
- **charts-energy / charts-config polish.** Shorter one-line Pushover feed-
  handover lines; readable Energy-channel table layout (row spacing, dividers,
  full-width multi-line snippet with copy buttons).
- **Dashboard "in progress" panels polish.** The four panels use a fixed
  header / scrollable file list / footer layout (≈⅓ shorter, the full list
  scrolls behind a thin scrollbar framed by a subtle 1px border), the header and
  footer bands are aligned across the row, the upgrade panel shows an honest
  placeholder so its footer pins to the bottom, and the wording was clarified
  (e.g. "noch nicht geladen (warten auf Download)", "Snapshot vom letzten Sync",
  honest quarantine subtitle, per-source line).

### Fixed
- **DST scheduler double-run** in charts-digest and charts-hitparade is
  prevented.
- **charts-dashboard** counts library songs recursively, excluding
  non-library folders.
- **charts-songrequest UI** fixes: raw i18n keys resolved in the tabbed layout,
  tabs laid out in a row with only the active one coloured on mobile Safari, the
  login page primary-button style restored, and the logout link kept above a
  tall card.

### Upgrade notes
- **Rebuild the charts-songrequest image after pulling.** It now requires
  **ffmpeg** (installed via apt in the Dockerfile) for the upload FFT quality
  check and yt-dlp's MP3 extraction, plus **numpy** and **yt-dlp** (pinned in
  `charts-songrequest/requirements.txt`). On Synology use a cache-free rebuild
  (`docker compose build --no-cache charts-songrequest`).
- **No new required environment variables.** The upload and YouTube tabs add no
  mandatory settings. Notable NEW *optional* knobs that appeared this cycle and
  are documented in `.env.example`:
  - charts-energy: `ENERGY_ENABLED`, `ENERGY_CHANNELS` / `ENERGY_CHANNELS_FILE`,
    `ENERGY_POLL_SECONDS`, `ENERGY_MATCH_INTERVAL_MINUTES`, and the liveness
    knobs (`ENERGY_LIVENESS_INTERVAL_HOURS`, `ENERGY_LIVENESS_ON_START`,
    `ENERGY_LIVENESS_DEAD_PROBES_THRESHOLD`).
  - source-health: `SYNC_DEAD_RUNS_THRESHOLD` (+ `SYNC_HEALTH_FILE`),
    `ENERGY_DEAD_RUNS_THRESHOLD` / `ENERGY_CANARY_DEAD_RUNS_THRESHOLD`
    (+ `ENERGY_HEALTH_FILE`).
  - charts-songrequest: `PUSHOVER_SOUND_SONGREQUEST`.

## [1.6.1] - 2026-10-22

UI polish for the new charts-config service and its dashboard tile.

### Fixed
- **charts-config: restored the donate footer** that was lost when the service
  was carved out of charts-dashboard (PayPal/Revolut + translated label,
  hideable via `CONFIG_DONATE=0`).
- **charts-config: the Energy-channel table now spans the full width** instead
  of being crushed into one half of the finder's two-column grid — readable
  columns (no character-by-character wrapping), the active/mode status badge is
  no longer clipped, and the copy button no longer overflows the right edge.
- **charts-dashboard: the charts-config tile is now the same compact height** as
  the other report-zone tiles (watchdog/dashboard/digest); its full description
  shows on expand instead of inflating the tile.

## [1.6.0] - 2026-10-22

Energy (NRJ/energy.ch) webradio as a new source, a dedicated config web UI,
clearer notifications, and a status-only dashboard.

### Added
- **New service `charts-energy`.** Taps the Swiss NRJ / energy.ch webradio
  channels via their Icecast ICY in-stream metadata (no API, no headless
  browser), accumulates the observed spins and feeds the missing songs into the
  existing charts-download pipeline. Config-driven per channel via the
  `ENERGY_CHANNELS` JSON, with THREE modes:
  - `own` — writes its own `energy-<slug>.m3u` playlist (reserved
    `energy-*.m3u` namespace, collision-guarded).
  - `feed` — hands tracks to an EXISTING charts-sync theme (e.g. tops up the one
    `mundart.m3u` or `charts.m3u`), deduped against the Deezer/Last.fm sources —
    no second playlist.
  - `library` — download-only like charts-flashback: no playlist, resolves a
    release year via MusicBrainz into the shared year map, so songs appear in
    the right decade via the Navidrome Smart Playlists.
  Ships **disabled by default** (`ENERGY_ENABLED=0`, empty channel list) so a
  fresh clone never polls unexpectedly. Includes a channel-discovery helper.
- **New service `charts-config`.** A standalone, password-protected web UI
  (port 5058) that hosts the source finder (Deezer playlist/artist search,
  Last.fm tag check, Deezer genre table) and a new Energy-channel browser
  (per-channel ready-to-paste `ENERGY_CHANNELS` snippet, active/mode status
  badge, and text-only "where to paste / what to restart" hints). Read-only: no
  Docker access, never writes your config files.
- **charts-sync `energy` feed kind.** charts-sync can consume a charts-energy
  handoff file as an additional source of an existing theme
  (`{"service":"energy","theme":"…"}`), so Energy tracks merge into the curated
  playlists (deduped, energy merged after the curated sources).
- **Affected songs listed in more notifications.** The charts-download
  "re-downloaded" group and the charts-recommend message now list the songs,
  not just a count.
- **charts-digest Energy group.** Library-mode Energy downloads now show up as a
  clean "⚡ Energy" group in the daily digest.

### Changed
- **charts-dashboard is a pure status monitor again.** The source finder and the
  Energy browser moved out of the dashboard into the new charts-config service;
  the dashboard now shows service status and a charts-config tile (grayed out
  when a service is disabled).
- The charts-energy notification header total now spans both own- and
  library-mode channels instead of only own, so it matches the per-section
  detail below.
- Documentation synced across the stack for the two new services (root README
  tables, ports, finder location → charts-config, decade sources, and a new
  charts-config README).

## [1.5.0] - 2026-10-21

Hitparade playlist updates promptly when a missing song arrives.

### Added
- **Resync trigger for charts-hitparade.** The Swiss hitparade playlist
  (`hitparade.m3u`) was previously only rebuilt by the weekly Sunday 20:00 scan,
  so a song that was fetched hourly by charts-download and filed in by
  charts-import could stay missing from the playlist for up to a week even
  though it was already in the library. charts-hitparade now watches a dedicated
  resync-signal file (`HITPARADE_RESYNC_SIGNAL`, checked every
  `HITPARADE_RESYNC_CHECK_SECONDS`, default 60) and re-renders the playlist
  within about a minute of a relevant import. The re-render re-matches the
  library against a cached copy of the last parsed chart
  (`CHART_CACHE_FILE`) — it does NOT re-fetch hitparade.ch, and the weekly
  scheduled scan is unchanged. charts-import drops the hitparade signal only
  when an imported song actually belongs to the hitparade chart (membership test
  on the `+`-joined source label), so the ~90s library scan is not triggered by
  unrelated imports. A dedicated signal file (separate from the charts-sync
  `.resync-requested`) avoids a clear-race between the two consumers.

## [1.4.0] - 2026-10-21

Resilient song requests and self-healing stale downloads.

### Added
- **Provider verification and a dead-peer blocklist in charts-songrequest.**
  After a download is enqueued, songrequest now actively verifies that the
  Soulseek provider really starts sending bytes (configurable
  `VERIFY_SECONDS` / `VERIFY_POLL_SECONDS`). If a provider stalls, the transfer
  is cancelled and the next candidate is tried. Repeatedly failing providers are
  remembered in a blocklist (`BLOCKLIST_FILE`, default under `/charts`) and
  skipped for `PROVIDER_BLOCK_DAYS` (default 14), so a single dead peer no longer
  blocks a song that other peers can deliver.
- **Stale-transfer cleanup in charts-download.** A new `STALE_CLEANUP` step
  removes Soulseek transfers that have been stuck without progress for longer
  than `STALE_DOWNLOAD_HOURS` (default 6), keeping the slskd queue clean so new
  requests are not starved by abandoned ones.

### Changed
- Combined per-song source labels now join with `+` instead of `, ` (e.g.
  "charts+hitparade"), which is more compact and scales better with three or
  more sources.

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
