# Changelog

All notable changes to charts-stack are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Versioning note: charts-stack is versioned as **one stack**, not per service.
The services are tightly coupled (shared `.env`, shared `compose.yml`, shared
central `lang/` files, shared copy-into-every-service modules) and are always
deployed together, so a single stack version is the honest unit.

## [Unreleased]

### Changed
- **All list-shaped Pushover messages** (charts-digest, -sync, -energy,
  -flashback, -hitparade, -import, -download, -recommend, -upgrade) now lead
  with a header and, where there is more than one category, **one overview
  line** with every category and its count. Detail lists are cut explicitly at
  Pushover's 1024-character budget, only between whole entries, with exactly
  one "… and N more" line. The old blunt "truncated" cut of sync, energy,
  flashback and hitparade is gone. Output is byte-identical to before when
  nothing needs to be cut (import, download, recommend, covered by golden
  tests).
- The 1024-character budget is now measured on the HTML-escaped text, i.e. the
  way Pushover receives it (`&`, `<`, `>` count as more than one character).

### Fixed
- **charts-digest no longer hides later sources.** With 112 new songs from six
  sources only the first source's count (Energy 59) was visible, the rest was
  silently dropped. The overview line now shows all six counts at the top and
  the final line counts the omitted songs.
- charts-hitparade's failure alert is cut on its escaped length, so a reason
  containing `&` or `<` can no longer exceed the limit.

### Added
- Shared `notify_budget.py` helper (byte-identical copy in the nine notifying
  services), frozen by `tests/test_shared_helper_parity.py` (byte identity and
  escape parity with every `notify.py`), plus unit tests in
  `tests/test_notify_budget.py` and per-service builder tests.
- 8 i18n keys (en/de/fr, 1172 → 1180 per language). Language files are
  bind-mounted: copy the updated `lang/*.json` to the NAS and restart the
  affected containers.

## [2.2.1] - 2026-11-03

Two threads land together. First, a **cross-service matching fix**: a song that
only survived in a compilation-named album (e.g. a Lidarr "Greatest Hits" folder)
was treated as missing by the chart-matching services and re-downloaded,
silently undoing earlier de-duplication. The presence check is now shared and
consistent across services and guarded by a parity test so the logic can no
longer drift apart. Second, the **Manual Tag Editor grows a hands-on toolkit**:
in-browser play, download, FLAC→MP3 conversion, a non-MP3 list, a trash manager
and a full-resolution cover download — all behind the existing admin login.

### Added
- **charts-config — tag-editor in-browser play & download.** A glass play-button
  overlay on the cover plays the song in the browser (MP3 only; FLAC is
  disabled), and a download button fetches the original file in any format.
  Both go through a new `/api/tageditor/audio` endpoint protected by the admin
  login **and** a path-traversal guard, and gated by an opt-in flag
  **`TAGEDITOR_AUDIO_ENABLED` (default off)** — a fresh clone exposes no audio
  route until the operator turns it on. A read-only field under the track shows
  the library-relative path plus bitrate/format.
- **charts-config — tag-editor FLAC→MP3 conversion.** For a non-MP3 song, an
  inline button converts it to MP3 320 CBR (ffmpeg, now in the charts-config
  image), carrying over tags, cover and mtime, moving the original into
  `.charts-trash/flac2mp3-<date>/` (reversible) and clearing the provenance
  markers so the result is hand-managed. An honest UI warning flags the
  lossless→lossy quality downgrade and the move to trash. Behind the admin login
  and path guard.
- **charts-config — non-MP3 list panel.** A separate read-only panel lists only
  non-MP3 **audio** files (an audio-extension allowlist minus mp3 — never
  sidecar txt/cover/m3u), each with path + format and an "open in tag editor"
  action. One deliberate click per song, so it never violates the "automation
  never touches the hand-managed library" rule.
- **charts-config — Trash (Papierkorb) manager panel.** A separate panel lists
  the whole `/music/Music/.charts-trash/` tree (upgrade trash, `dedup-<date>/`,
  `flac2mp3-<date>/`), each entry with path + format + **size** and a total size
  + count at the top, with per-file permanent delete and empty-all, each behind
  a confirm. Strictly scoped to `.charts-trash` (admin login + trash-root guard,
  symlink escape refused). README notes that `TRASH_RETENTION_DAYS` (charts-import,
  default 30) prunes by mtime, so a file moved to trash today can be cleaned up
  at the next charts-import run, not only after 30 days.
- **charts-config — full-resolution cover download.** A discreet grey "download
  cover" pill above the cover fetches the embedded art at original size — useful
  when the cover is larger than the inline preview limit.

### Fixed
- **Cross-service — stop re-downloading songs that only live in
  compilation-named albums.** The chart-matching services (charts-sync,
  charts-energy, charts-flashback, charts-recommend, charts-hitparade) now share
  a single presence guard (`presence.py`, byte-identical across services) that
  mirrors songrequest's `find_existing` and applies **no** compilation filter on
  the missing-song path, and `_looks_like_compilation` now short-circuits on a
  Lidarr `(Year)` album folder. A song that exists only inside a "Greatest Hits"
  (or similar) album is no longer reported missing and re-fetched. Live result:
  the hitparade match counted 134 real tracks instead of ~8242 inflated by
  compilation entries.
- **Shared helpers — converge the duplicated matching logic** so every service
  behaves the same: `_artist_key` transliterates accents stack-wide
  (`Motörhead` == `Motorhead`), `_strip_title_decoration` is applied identically
  in charts-import and charts-config, the compilation marker sets match
  (`kuschelrock` removed from the upgrade path), and charts-import presence folds
  through `_artist_key`.
- **charts-config — tag-editor search after a folder rename.** Search query
  folding is now symmetric with the index `title_key` (it strips the same
  decoration such as `(remix)`), so a renamed song is found again instead of
  disappearing from results.
- **charts-config — embedded cover read for m4a/flac.** Cover extraction is now
  format-agnostic (MP4 `covr`, FLAC pictures, OGG) instead of ID3/APIC-only, so
  non-MP3 songs show their cover in the editor.
- **charts-config — cover-download pill no longer overlaps the search results.**
  Loading a song removed the hint spacer, which let the cover column's negative
  margin pull the "download cover" pill up over the results list; the editor form
  now keeps a fixed top margin.

### Changed
- **charts-config — two download buttons disambiguated** (en/de/fr): the cover
  control reads "download cover" and the song control reads "download song", so
  it is clear which file each one fetches.

### Testing
- **Repo-root shared-helper parity guard** (`tests/test_shared_helper_parity.py`)
  freezes the duplicated matching helpers: it asserts `presence.py` is
  byte-identical across the services that copy it and checks behavioural parity
  of `_title_key` / `_artist_key` / `_looks_like_compilation` / the marker sets /
  the quality helper. Future drift now fails a test instead of resurfacing as a
  re-download bug.

## [2.2.0] - 2026-11-01

charts-config gains a **Manual Tag Editor** — a password-protected web tool to
fix a library song's tags and cover art by hand, in place, and (when the artist
changes) move the file into the correct artist folder so one artist always lives
in exactly one folder. Two scheduler services (charts-hitparade, charts-digest)
are hardened so a failed run no longer burns its once-per-slot schedule and now
alerts on failure.

### Added
- **charts-config — Manual Tag Editor.** Search the music library, pick a song,
  and edit artist/title/album/year/track + cover art in place. Saving writes
  only changed fields, clears the stack provenance markers
  (`TXXX:CHARTS_SOURCE` + `TXXX:CHARTS_ALBUM_SRC`) so the song becomes
  hand-managed and the automatic backfill never re-touches it, and preserves
  mtime (no Navidrome "recently added" flood). MP3-only on save; cover replace
  via upload or http/https URL (square-cropped, SSRF-guarded). Admin login like
  the dashboard. Needs a read-write `/music/Music` mount (added to compose.yml)
  and `mutagen`/`Pillow` (no ffmpeg); tunables `TAGEDITOR_INDEX_TTL`,
  `TAGEDITOR_COVER_MAX_BYTES`, `TAGEDITOR_COVER_FETCH_TIMEOUT`.
- **charts-config — "folder follows the artist tag".** When a save changes the
  artist, the file is moved into the canonical artist folder so one artist =
  one folder, never a duplicate created by guessing. A strict key
  (case/punctuation/accent fold) auto-merges unambiguous variants
  (`rod stewart`/`Rod Stewart`, `Motörhead`/`Motorhead`, casing/hyphen/apostrophe
  differences); when the old folder empties it is renamed; a looser key
  (leet/stylization: `P!nk`↔`Pink`, `Ke$ha`↔`Kesha`) that matches a different
  populated folder raises a decision dialog (file in / create new / cancel); an
  exact song-file collision raises a keep-existing/cancel dialog and **never
  overwrites**. `os.rename` only (mtime preserved; cross-device is refused);
  empty source folders are cleaned up (skipping `@eaDir`/`#recycle`/`.DS_Store`).
  Scope is stack-single `<Artist>/<Title>/` files only — Lidarr `<Album> (Year)/`
  albums and curated compilations are left in place. An amber ⚠️ pre-save hint
  warns before the move.
- **charts-hitparade / charts-digest — failure Pushover alert.** A failed
  scheduled run now sends a Pushover message (the watchdog only sees container
  state, not a failed run inside a running container).

### Fixed
- **charts-hitparade — a failed run no longer burns the weekly slot.** The
  once-per-week run-gate is now stamped only on a genuine success, so a run that
  hit a transient hitparade.ch 5xx (or a `RUN_ON_START` run after a restart) is
  retried at the next scheduler check the same day instead of waiting a week.
- **charts-digest — a failed run no longer burns the daily slot.** Same fix: the
  once-per-day gate is stamped only on success, so a failed daily digest is
  retried the same day instead of being silently lost until tomorrow.
- **charts-config — tag-editor search no longer lists trash/sidecar/hidden
  folders.** The search index prunes `.charts-trash`, `@eaDir`, `#recycle`,
  `.DS_Store` and any dot-folder, so a deleted-file bin entry can no longer be
  opened for editing.
- **charts-config — tag-editor cover no longer shows the previous song's art.**
  Switching to a song without an embedded cover now resets the cover area and
  shows only the placeholder, instead of leaving the prior image behind it.

### Changed
- **charts-config — "curated" badge renamed to hand-managed** (de:
  `handgepflegt`, fr: `géré à la main`, fixing the previous French `curé` =
  "parish priest" mistranslation) across badge, legend and editor hint.
- **charts-config — tag-editor UI** polished: side-by-side fields/cover layout,
  cover sized to the field stack with visibly rounded corners, upload row with
  equal-width file/URL inputs, full-width legend, and a brighter amber ⚠️ move
  hint + legible "no matches" message.
- Root README: the charts-config service description no longer says "read-only"
  (it now hosts the Manual Tag Editor).

## [2.1.0] - 2026-11-01

charts-import gains a uniform, source-independent tagging path and a
conservative, manifest-scoped metadata backfill so every stack-managed song ends
up with a correct release year, a real (non-compilation) album and a track
number — while the user's hand-curated library (compilations, hand-added back
catalog) is never touched. charts-energy's library matching is made tolerant of
the longer canonical titles the backfill now writes.

### Added
- **charts-import — import-time metadata resolver** (`app/resolve.py`). Every
  song that passes through the import pipeline (fresh download, manual
  request/upload/YouTube, or an upgrade replace) now resolves a release **year**,
  a real **album** name and a **track** number at import time via Deezer-first →
  MusicBrainz-fallback, instead of waiting for the deferred MusicBrainz year
  backfill. Fail-open: any lookup miss/error leaves the field unresolved rather
  than blocking the import.
- **charts-import — conservative, manifest-scoped backfill** for stack-managed
  back catalog. A periodic pass (`METADATA_BACKFILL_ENABLED`, default on; shares
  the backfill cadence/caps) plus two one-shot scripts:
  `scripts/backfill_metadata.py` (fill missing year/album/track) and
  `scripts/fix_youtube_years.py` (correct the wrong yt-dlp year on past
  YouTube-sourced songs). Scope is **manifest songs only** — only files the
  stack itself downloaded/imported (same `REQUIRE_MANIFEST` rule the import path
  uses). Files not in the manifest — the user's hand-curated compilations and
  hand-added back catalog — are skipped entirely and never modified (no year,
  album, track, or any other tag write).
- **charts-import — album re-correction mode** (`backfill_metadata.py
  --recorrect-albums`, opt-in, off by default). Re-resolves and overwrites an
  album **only** when the file carries the `TXXX:CHARTS_ALBUM_SRC=resolver`
  provenance marker (stamped whenever the stack sets an album, whitelisted
  against the provenance strip), so a user's hand-curated album is never
  overwritten; a bounded one-time heuristic additionally upgrades a
  pre-marker secondary edition when a strictly-earlier original exists. The
  re-correction is **conservative**: a replacement is written only when the new
  candidate is clearly a non-compilation, non-live, non-best-of, non-soundtrack
  studio album/single of the same recording and is a clean artist+title match
  (release date is only a tiebreaker among guard-passing matches); when in doubt
  the existing album is kept. The periodic pass and `fix_youtube_years.py` never
  re-correct silently.
- **charts-songrequest/charts-import — durable YouTube provenance marker**
  (`TXXX:CHARTS_SOURCE=youtube`, whitelisted against the provenance strip). Lets
  the backfill safely replace a YouTube-derived year with the real release year
  and then clear the marker.

### Changed
- **Uniform tagging across all sources.** The album/year/track resolution and
  the provenance strip now apply identically to automatic downloads and all
  manual sources (request/upload/YouTube) and to charts-upgrade replaces — one
  shared code path, no per-source branch.
- **charts-upgrade re-correction.** When charts-upgrade replaces an existing
  file with a higher-bitrate version, the replacement is re-tagged with the full
  clean metadata and the old file's year/album is captured before trashing, so
  an upgrade never leaves a file with worse tags than before.
- **Compilation/junk-album avoidance hardened.** The resolver rejects
  various-artists albums (Deezer `album.artist.id == 5080`), junk album titles
  (single-char / empty / "Various Artists"), and `record_type` singles, and
  prefers a studio-album release over a best-of/compilation via a bounded
  preference scan over the ranked hits. Widened compilation title markers
  (`singles collection`, `the hits`, `all the best`, `hits`, `(party mix)`,
  `(special edition)`, `remixes`, decade/year-range titles, …) catch best-ofs
  that carry `record_type:"album"`. Hard rule: when no clean album is found the
  album is left empty (importer keeps album = title) — a wrong album is never
  written.
- **Prefer the original studio album.** The resolver now collects the passing
  album candidates and picks the earliest-release, non-secondary one instead of
  the first match, so a track is tagged with the studio album it first appeared
  on rather than a live album, deluxe / expanded / anniversary / remaster
  edition, soundtrack, or tribute. Secondary editions are demoted (by title
  markers `deluxe`/`expanded`/`anniversary`/`remaster`/`soundtrack`/`ost`/
  word-bounded `live`/… and, on the MusicBrainz fallback, the release-group
  `secondary-types` Live/Compilation/Soundtrack/Remix), never hard-rejected, so
  a single available secondary edition still beats an empty album. MusicBrainz
  stays a per-field fallback (no extra load on the critical path).
- **Provenance strip extended** to also remove `TMED`, `WOAR`, `TOAL` (only when
  the value contains `(CD-Rip)`), and `UFID` (only when the owner is NOT the
  MusicBrainz URL — the MusicBrainz Recording ID is preserved).
- **YouTube import no longer keeps the wrong yt-dlp date** — the embedded
  upload/publish year is dropped so the resolver's real release year is used
  instead.

### Fixed
- **Umlaut / decoration normalization in the title match key** (transliterate
  ä→a, strip an unbalanced trailing `(…`), so a decorated manual re-request
  folds onto the same dedup key as the clean library title.
- **charts-energy library matching tolerates a trailing `(subtitle)`.** After
  the backfill canonicalized on-disk titles to the longer Deezer form
  (e.g. `Against All Odds (Take a Look at Me Now)`) while the ICY radio stream
  feeds the short title (`Against All Odds`), energy stopped recognizing owned
  songs as present and re-queued them every run. The title comparison now
  matches on a subtitle-stripped core key (match-key only; numeric/part tails
  like `(Part 2)` stay distinct), so owned songs register as present again.

### Upgrade notes
- **mtime is preserved on every tag write** (new year/album/track writes, the
  upgrade re-correction, and the backfill), so Navidrome's "date added" stays
  stable and corrected songs do not flood "recently added".
- Rebuild **charts-import** with `docker compose build --no-cache charts-import`
  and **charts-energy** with `docker compose build --no-cache charts-energy`
  (the energy library-match fix). New i18n keys were added, so refresh the
  mounted `lang/{en,de,fr}.json` and `docker restart` the affected services so
  the table reloads (if a container shows raw i18n keys after the lang refresh,
  recreate it with `docker compose up -d --force-recreate <service>` — a
  Synology bind-mount can go stale on a plain restart).
- New optional switches in `.env.example`: `IMPORT_RESOLVE_ENABLED`,
  `IMPORT_DEEZER_RESOLVE_LIMIT`, `IMPORT_MB_RESOLVE_LIMIT`,
  `METADATA_BACKFILL_ENABLED`, `METADATA_BACKFILL_LIMIT`,
  `METADATA_BACKFILL_MAX_TRIES`. Run the one-shots in the container with
  `python3 -m scripts.backfill_metadata --dry-run` (then `--apply`) and
  `python3 -m scripts.fix_youtube_years --dry-run` (then `--apply`).

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
