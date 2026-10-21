> 🇬🇧 English version. Deutsche Version: [README.md](README.md)

# charts-import

Automatically sorts the chart songs downloaded by **slskd** into the
**Navidrome library** and cleans up the download folder again after a certain
time.

The sister project to [`charts-sync`](../charts-sync): while `charts-sync`
determines the missing chart songs (`missing.txt`) and maintains the playlist,
`charts-import` takes care of the files that were subsequently fetched via
slskd. `charts-sync` deliberately touches **no** audio files, so sorting in +
cleaning up is a separate, independent service.

---

## Charts automation – overall overview

`charts-import` is part of a chain of three services that together fully
automate the chart workflow. Each runs as its own container with its own
interval and its own `.env`. The coupling happens exclusively via shared folders
(`/volume2/music/Charts`, `/volume2/music/Music`). The family is complemented by
[`charts-songrequest`](../charts-songrequest), an optional web UI for
**manually** requesting individual songs. Their downloads are pulled into the
library automatically by `charts-import` via the manifest (label `manuell`).

Sister projects: [`charts-sync`](../charts-sync) · [`charts-download`](../charts-download) · [`charts-songrequest`](../charts-songrequest)

### Flow with default timings

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Charts automation                               │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ① charts-sync                              (every 12 hours)       │
│     │  Fetches current Deezer charts (API)                         │
│     │  Matches against /volume2/music/Music (library)              │
│     │  Writes charts.m3u / mundart.m3u                            │
│     │  Writes missing*.txt (missing songs per playlist)            │
│     │  Pushover: missing list to the phone                         │
│     ▼                                                               │
│  ② charts-download                          (every 60 minutes)     │
│     │  Reads missing.txt                                           │
│     │  Checks failed downloads → cleans up, releases them          │
│     │  Searches per song via slskd API (max. 10/run)               │
│     │  Picks best MP3 >= 256 kbps (singles before compilations)    │
│     │  Starts the download in slskd                                │
│     ▼                                                               │
│  slskd                                      (runs continuously)    │
│     │  Downloads the songs from other Soulseek users               │
│     │  Songs land in /volume2/music/Charts/                        │
│     │  Shares ~500 chart downloads + ~400 SeedShare songs          │
│     ▼                                                               │
│  ③ charts-import                            (every 30 minutes)     │
│     │  Scans /volume2/music/Charts/ for finished downloads         │
│     │  Waits MIN_AGE_SECONDS (120s) (no half files)                │
│     │  Reads tags → copies to Music/<Artist>/<Album>/<Track>       │
│     │  Cleans up: KEEP_LATEST=500 (keep newest, rest removed)      │
│     │  Pushover: report imported songs (with playlist origin)      │
│     ▼                                                               │
│  Navidrome                                  (scans periodically)   │
│     │  Sees the new songs at the next scan                         │
│     ▼                                                               │
│  ① charts-sync (resync trigger, ~1 min)                            │
│     charts-import reports "new songs here" → charts-sync runs      │
│     immediately → song drops out of missing.txt, into the playlist │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

New **chart entries** are detected by charts-sync via its regular interval
(every 12 h). **Downloaded songs**, on the other hand, land in the playlist
almost immediately: after sorting them in, charts-import sets a signal that
prompts charts-sync to run within ~1 minute (resync trigger).

### Services at a glance

| Service | Task | Interval | Key settings |
|--------|---------|-----------|------------------------|
| **charts-sync** | Finds gaps, writes playlist + missing.txt, notifies to the phone | 12 h | `SYNC_INTERVAL_HOURS`, `MATCH_THRESHOLD`, `NOTIFY_MISSING_LIMIT` |
| **charts-download** | Downloads missing songs via slskd API, retry on failures | 60 min | `MAX_PER_RUN=10`, `MIN_BITRATE=256`, `DRY_RUN`, `SLSKD_API_KEY` |
| **charts-import** | Sorts downloads into the library, keeps the share stable | 30 min | `KEEP_LATEST=500`, `COPY_MODE=1`, `MIN_AGE_SECONDS=120` |

### Directories on the NAS

| Path | Purpose |
|------|-------|
| `/volume2/music/Charts/` | slskd downloads + missing.txt + state files |
| `/volume2/music/Music/` | Navidrome library (import target) |
| `/volume2/music/SeedShare/` | permanent seed folder (leecher protection) |
| `/path/to/charts-stack/` | Stack project folder (one `compose.yml` + one central `.env` for all services) |

---

## Table of contents

- [Overview](#overview)
- [Overall workflow](#overall-workflow)
- [How it works](#how-it-works)
- [Project structure](#project-structure)
- [Architecture & modules](#architecture--modules)
- [Installation on the Synology NAS](#installation-on-the-synology-nas)
- [Configuration (`.env`)](#configuration-env)
- [Directory layout](#directory-layout)
- [Copy vs. move & cleanup](#copy-vs-move--cleanup)
- [Operation & maintenance](#operation--maintenance)
- [Troubleshooting](#troubleshooting)
- [Expansion path: automated search with Soularr](#expansion-path-automated-search-with-soularr)
- [FAQ](#faq)

---

## Overview

| Property            | Value |
|------------------------|------|
| Source                 | slskd download folder (`/volume2/music/Charts`) |
| Target                   | Navidrome library (`/volume2/music/Music/<Artist>/<Album>/<Track>`) |
| Base image            | `python:3.12-slim` |
| Dependencies         | `httpx`, `mutagen` |
| Operating mode          | endless loop with configurable interval (minutes) |
| Import mode           | copy (default, original stays for seeding) or move |
| Cleanup              | count-based (`KEEP_LATEST`: keep the N newest, default 500; `all` = never delete) or age-based (`RETENTION_DAYS`) |
| Runs as              | `youruser:users` (PUID 1027 / PGID 100), like Navidrome |
| Notifications     | Pushover (optional, when songs were imported) |

---

## Overall workflow

`charts-import` is one building block of a chain of several services:

```
Deezer charts
     │
     ▼
charts-sync ───► missing.txt        (which chart songs are missing in the library)
     │           /volume2/music/Charts/missing.txt
     ▼
slskd ─────────► downloads missing songs to /volume2/music/Charts/
     │
     ▼
charts-import ─► copies to /volume2/music/Music/<Artist>/<Album>/<Track>
     │           and keeps the Charts folder at KEEP_LATEST files
     ▼
Navidrome ─────► sees the new songs at the next scan
     │
     ▼
charts-sync ───► matches them into the playlist at the next run
```

This closes the loop: a song that is in `missing.txt` today gets downloaded via
slskd, sorted in by `charts-import`, and lands automatically in the charts
playlist at the next `charts-sync` run.

---

## How it works

A run consists of two steps:

1. **Import** (`importer.py`)
   - Scan `DOWNLOADS_DIR` recursively for audio files.
   - Only take **stable** files (not modified for `MIN_AGE_SECONDS`), so no
     half-finished downloads get sorted in.
   - **Manifest requirement (`REQUIRE_MANIFEST=1`, default):** only files with
     an entry in the charts-download manifest are imported – that is, only songs
     that charts-download fetched **deliberately**. The downloads folder often
     contains entire sampler/album folders that came along with the song
     download from Soulseek; without this lock, tracks would be imported from
     them indiscriminately (e.g. compilation versions), and that at **every**
     run anew as long as the source files sit in the shared folder. With
     `REQUIRE_MANIFEST=0`, everything that can be assigned is imported (e.g. for
     manually placed files).
   - Build the target path `Music/<Artist>/<Album>/<Track>.<ext>` from the tags.
   - **Copy** the file (`COPY_MODE=1`) or **move** it (`COPY_MODE=0`).
   - **Duplicate protection (folder-local):** if a file with the same track name
     already exists in the target album folder (regardless of filename,
     `(n)` suffix and extension), it is skipped. This way no `(2)/(3)`
     duplicates arise, even if the same song was downloaded from several
     providers with different filenames. The run is thereby **idempotent**.
   - **Duplicate protection (cross-album):** additionally it is checked whether
     the same song already lies in **another** album folder of the same artist.
     This covers the common case that another source (e.g. Lidarr) already lists
     the song as an album track (`Into You (2016)/
     Ariana Grande - Into You - 01 - Into You.mp3`), while charts-download would
     fetch it as a single (`Into You/Into You.mp3`). Without this check, two
     album folders (a duplicate) would arise for the same song. The comparison
     is done via the normalized **song title**, so the differing file/album
     naming does not matter.
   - **Quality upgrade (`replace`):** if the manifest entry carries
     `"replace": true` (set by `charts-songrequest` for "download better
     version") **and** the new file has a higher bitrate than the existing one,
     the existing one is **replaced** instead of skipped: the old version moves
     into a trash folder (`MUSIC_DIR/.charts-trash/<YYYYMMDD>/`, reversible), the
     new one lands in the same place (no duplicate). If the new one is **not**
     better, everything stays as it is (no pointless swap). The trash is emptied
     automatically (entries older than `TRASH_RETENTION_DAYS`, default 30 days) –
     no manual cleanup needed.

   - **Bouncer:** before a file is sorted in, charts-import checks whether it is
     usable. It is rejected on an exclusion keyword in the name
     (Lyrics/Remix/Live/Acapella …) or if no usable artist can be determined
     (`Unknown Artist`). Rejected files are moved to `_rejected` (quarantine, not
     deleted) and the song is released in the charts-download state so it gets
     downloaded again (hopefully better). This way no junk lands in the library,
     no matter where the file comes from. Can be disabled via `REJECT_ENABLED=0`.
   - **Embed cover:** for sorted-in songs (manifest hits) the matching cover is
     searched via the configured sources (iTunes→Deezer) and embedded directly
     into the MP3 (as an ID3 tag, **no** separate `cover.jpg`). With
     `OVERWRITE_COVER=1` an already present cover is replaced (important for
     songs from compilations that bring along the sampler cover). The cover is
     also made **square** (center-cropped) so it does not look distorted during
     playback. Can be disabled via `EMBED_COVERS=0`.

2. **Cleanup** (`cleanup.py`)
   - Default: keep the newest `KEEP_LATEST` downloads, delete the rest.
   - Alternatively (`KEEP_LATEST=0`): delete downloads older than
     `RETENTION_DAYS`.
   - Afterwards remove empty subfolders.

If the **Lidarr integration** is configured (`LIDARR_URL` + `LIDARR_API_KEY`),
the newly imported artists are subsequently set to *unmonitored* in Lidarr (or
newly added directly as unmonitored). This way Lidarr knows and tags the chart
singles but does **not** download their complete catalog. Lidarr's normal
monitoring for real albums/compilations can thus stay enabled. Best to also
disable "Watch Root Folders for file changes" in Lidarr, otherwise Lidarr might
grab before charts-import can react. Errors of the Lidarr connection are only
logged and do not affect the import.

If new songs were imported, an optional **Pushover notification** follows (with
the list of sorted-in titles plus a breakdown by playlist, e.g.
"3 aus charts, 2 aus mundart" – derived from the manifest) and a **resync
signal** to `charts-sync` (`.resync-requested` in the Charts folder). This
signal prompts `charts-sync` to update the playlist within ~1 minute instead of
waiting for the regular 12 h interval.

In addition to the summary message, charts-import sends a dedicated availability
message ("🎵 Dein Song ist da!") for every song **requested manually via
charts-songrequest**, to **the requesting user's device**. The target device is
stored as `notify_device` in the manifest entry (set by charts-songrequest from
`USER_PUSHOVER_DEVICES`). If the field is missing (normal chart downloads, or a
user without a configured device), no such per-song message is sent – it is
never delivered to all devices.

Afterwards the service waits `INTERVAL_MINUTES` minutes and repeats the run.

---

## Project structure

```
charts-import/
├── app/
│   ├── __init__.py       # package marker
│   ├── config.py         # configuration from environment variables
│   ├── tags.py           # manifest + tag reading/writing + target path building
│   ├── importer.py       # scan + copy/move + bouncer
│   ├── cover.py          # search cover (iTunes→Deezer), embed, square
│   ├── replaygain.py     # measure loudness (ffmpeg) + write ReplayGain tag
│   ├── cleanup.py        # clean up (KEEP_LATEST or RETENTION_DAYS)
│   ├── lidarr.py         # keep chart artists unmonitored in Lidarr
│   ├── backfill_covers.py    # one-time script: backfill covers / --fix-broken
│   ├── backfill_replaygain.py# one-time script: backfill ReplayGain
│   ├── notify.py         # optional Pushover notification
│   └── main.py           # scheduler loop, connects all modules
├── scripts/
│   ├── cleanup_flashback_legacy.py  # one-time script: remove old flashback legacy entries
│   └── strip_provenance_tags.py     # one-time script: strip provenance/encoder tags from chart songs
├── Dockerfile            # python:3.12-slim + ffmpeg, starts app.main
├── compose.yml           # service definition for the NAS
├── requirements.txt      # httpx, mutagen, Pillow
├── .env.example          # template for the configuration
├── .gitignore
└── README.md             # this file
```

---

## Architecture & modules

### `config.py`

Reads all settings from environment variables (paths, import mode, retention
period, interval, Pushover). Contains the list of file extensions recognized as
music (`AUDIO_EXTENSIONS`), identical to `charts-sync`.

### `tags.py`

Determines artist/album/title in this order:

1. **Manifest from charts-download** (`MANIFEST_FILE`) – preferred. Contains the
   real chart metadata (artist/title). On a hit these values are used **and**
   written into the file tags via `mutagen` (`artist`, `albumartist`, `title`,
   `album`), so that Navidrome and `charts-sync` see correct values. The album
   is thereby set to the title (chart songs are singles), so nothing lands under
   "Unknown Album".
   **Year tag:** `charts-import` additionally reads the year map
   `.charts-flashback-years.json` (in the Charts folder, written by
   `charts-flashback`; key = lowercase `artist|title`, `_flashback_year`). If a
   year is found there, `charts-import` sets the `date`/`originaldate` tags – for
   the decade smart playlists in Navidrome. If the entry is absent (all other
   services), the date tags are left untouched – no overwriting of existing
   years. The year thus lives **only** in the tag, **never** in the file/folder
   name.
2. **File tags** (`mutagen`, `albumartist` preferred).
3. **Fallback** to the filename or `Unknown Artist` / `Unknown Album`.

For the target path, the components are cleaned of filesystem/SMB-critical
characters (e.g. `/` → `_`); the **tags** however keep the original value (e.g.
`HUNTR/X`), so that `charts-sync` matches cleanly. Result:
`Music/<Artist>/<Album>/<Track>.<ext>`.

**Canonical artist spelling:** if the artist already exists as a folder in the
library (no matter which upper/lower case), `charts-import` uses its **real**
folder name for path **and** tag (`canonical_artist`). This prevents duplicates
like `rod stewart` next to `Rod Stewart` on case-sensitive file systems (ext4)
and lets the established spelling win, regardless of what manifest/tags say.

**Canonical spelling via Deezer (last resort):** if a chart song's manifest name
is still **UPPERCASE**/suspicious (e.g. a FLASHBACK raw value that slipped
through, like `OMD / ENOLA GAY`), `charts-import` resolves the official
mixed-case spelling **before** building path/tags via the open Deezer search API
(`app/deezer.py`, `resolve_canonical`). This is the **central last resort** so no
UPPERCASE name ever reaches the library — even when `charts-flashback` could not
resolve it safely while writing the missing list. Match validation is
conservative (asymmetric similarity thresholds `DEEZER_MATCH_MIN`/`_STRONG`/
`_RELAX` + trailing-parenthesis strip): better leave it UPPERCASE than guess
wrong. Deezer is only queried on UPPERCASE suspicion (clean names stay untouched,
no API overhead); the result is cached (`.charts-flashback-deezer-cache.json`,
shared with flashback).

> **Unresolvable cases:** if Deezer finds no safe hit (alias artists like OMD =
> "Orchestral Manoeuvres in the Dark", or a title FLASHBACK simply named wrong),
> the song is **still imported** (no gap in the library) and reported at the end
> of the run via a **Pushover summary** for manual review/rename
> (`✏️ charts-import: N name(s) to check`) — it never guesses.

**Stripping provenance/encoder tags:** on import, `charts-import` removes
provenance traces from the tags (`strip_provenance_tags`): the comment frame
(`COMM` – on Soulseek downloads it almost always carries uploader/release-group
signatures like "eXenz", "PMEDIA", `www.t.me/…` or the fre:ac ad text),
`TENC`/`TSSE` (encoder/rip tool), the URL frames `WXXX`/`WCOM`/`WOAF`/`WPUB`,
`TOWN` (file owner), and user-defined `TXXX` free-text frames (e.g. `TXXX:LOG`).
This keeps any source trace out of the library.
**Protected (never touched):** `TXXX:MusicBrainz*` and `TXXX:*replaygain*`
(metadata IDs + loudness normalization) and all standard tags
(title/album/artist/date/track/cover). Lossless (metadata only, no re-encoding).
Applies to **all** services that use charts-import (songrequest, sync, download,
flashback).

### `importer.py`

The actual import. Scans the download folder, checks the stability of each file,
builds the target path and copies or moves it. Detects already present files via
a size comparison (cheap and sufficient for dedup) and assigns a counter suffix
on real name collisions (`Track (2).mp3`).

### `cleanup.py`

Cleans up the download folder and afterwards removes empty subfolders. Two
strategies, `KEEP_LATEST` takes precedence: either keep the N newest downloads
(`KEEP_LATEST`) or delete downloads older than `RETENTION_DAYS`. If both are
`0`, nothing happens (cleanup disabled).

### `replaygain.py`

Measures the loudness of an MP3 with ffmpeg (EBU R128) and writes the track gain
as a ReplayGain 2.0 tag (`REPLAYGAIN_TRACK_GAIN`/`_PEAK`). The audio stream stays
unchanged (lossless); a ReplayGain-capable player regulates the playback volume.
Target level via `REPLAYGAIN_TARGET_LUFS` (default −14 LUFS, streaming level).
Needs ffmpeg in the image. Fails silently if ffmpeg is missing. **Note:** for it
to become audible, ReplayGain mode must be enabled in the player (Navidrome web
or app).

### `lidarr.py`

Optional Lidarr integration (only active with `LIDARR_URL` + `LIDARR_API_KEY`).
After each import, sets the newly imported chart artists to *unmonitored* in
Lidarr (or adds them directly as unmonitored), so that Lidarr knows and tags
them but does not download their complete catalog. Errors are only logged and do
not affect the import.

### `backfill_covers.py`

One-time script (not part of the scheduler loop). Backfills covers for the
existing library. Without a flag only missing covers; with `--fix-broken`,
rectangular covers (CD scans) of the **chart songs** (playlist entries in
`PLAYLIST_DIR`) are additionally replaced by the square cover. With
`--force-refresh` the cover is re-fetched for **all** chart songs (source per
`COVER_SOURCES`, usually iTunes single artwork), including already square ones
(needed for cassette/CD cover scans as well as to replace old, worse covers like
a demo EP with the dedicated single cover). `--dry-run` only shows what would
happen. See "Operation & maintenance".

### `backfill_replaygain.py`

One-time script. Backfills ReplayGain tags for the existing **chart songs**
(only playlist entries, `PLAYLIST_DIR`). `--dry-run` only counts, `--overwrite`
also recalculates existing ones. See "Operation & maintenance".

### `backfill.py` (automatic, in the scheduler loop)

Unlike the two manual `backfill_*` one-time scripts, this module runs
**automatically**: the import is "fail-open" — if an external source is briefly
unreachable while filing a song (iTunes/Deezer for covers, ffmpeg for
ReplayGain), the song is imported **anyway**, but without cover/RG. Since
charts-import never touches an already-present song again, that gap would
otherwise be permanent. `backfill.py` re-fetches it periodically
(`BACKFILL_INTERVAL_HOURS`, default 24 h), only over the **chart songs from the
manifest** — the rest of the (Lidarr) library is left untouched. It embeds
missing covers and computes missing ReplayGain; existing covers/tags are never
overwritten. It also reconciles **UPPERCASE flashback names** against the
official Deezer spelling (`BACKFILL_RECANON=1`, default on): it renames only on a
**safe** Deezer match (library folder/file + tags + manifest), leaving unsafe
cases untouched (alias artists, names Deezer itself lists uppercase like
ABBA/R.E.M.) — never guessing. The affected Deezer cache entries are invalidated
first so old "frozen" misses get re-resolved. It reuses the same logic as the
manual `scripts/recanonicalize_flashback.py` (extracted into `app/recanon.py`);
the manual script remains for targeted one-off runs.

### `notify.py`

Optional Pushover sending, only active when `PUSHOVER_TOKEN` and `PUSHOVER_USER`
are set (otherwise a silent no-op). Identical to `charts-sync` and
`jellyfin-webhook`.

### `main.py`

Entry point (`python -m app.main`). `run_once()` runs import + cleanup, logs a
summary and sends a Pushover notification on new songs. The loop repeats this at
the configured interval and catches errors so the service runs continuously.

---

## Installation on the Synology NAS

charts-import is **not** installed on its own. It is part of the **charts-stack**,
which runs as ONE project: one `compose.yml` + one central `.env` in the
`charts-stack` folder (e.g. `/path/to/charts-stack/`). The full
installation is in the **[main README](../README.en.md)**.

To build/start just this service – from the `charts-stack` folder:

```bash
docker compose up -d --build charts-import
```

Logs for this service:

```bash
docker compose logs -f charts-import
```

On the first run you see per song `Kopiert: … -> …` (copied) or
`Verschoben: …` (moved), followed by a summary like
`Lauf fertig: X Songs importiert, Y alte Downloads entfernt.` (run finished:
X songs imported, Y old downloads removed.)

---

## Configuration (`.env`)

| Variable          | Default          | Meaning |
|-------------------|------------------|-----------|
| `COPY_MODE`       | `1`              | `1` = copy (original stays for seeding), `0` = move. |
| `REQUIRE_MANIFEST`| `1`              | `1` = import only songs deliberately fetched by charts-download (manifest entry) – keeps sampler/album tracks from the shared downloads folder out. `0` = import everything that can be assigned (e.g. for manually placed files). |
| `KEEP_LATEST`     | `500`            | Keep the N newest downloads, delete the rest. Applies to **all** downloads together (all playlists in the same folder), not per playlist. Keeps the share stable at ~N files (leecher protection). `all` = never delete, `0` = disabled. Takes precedence over `RETENTION_DAYS`. |
| `RETENTION_DAYS`  | `0`              | Fallback (only when `KEEP_LATEST=0`): delete downloads older than this many days. `0` = never delete. |
| `TRASH_RETENTION_DAYS` | `30`        | Automatically empty the upgrade trash (`MUSIC_DIR/.charts-trash/`): replaced versions older than this many days are deleted. `0` = never (empty manually). |
| `MIN_AGE_SECONDS` | `120`            | Only sort a file in once it has been unchanged for this long. |
| `INTERVAL_MINUTES`| `30`             | Minutes between two runs. |
| `HITPARADE_SOURCE_LABEL` | `hitparade` | Source label whose **membership** in an import's `+`-joined source (e.g. `charts+hitparade`) marks it as hitparade-relevant. Only then does charts-import additionally drop the `.hitparade-resync-requested` signal so `charts-hitparade` re-renders `hitparade.m3u` at once (instead of only on the next Sunday). Compared per `+`-part, not as a substring. |
| `RUN_ON_START`    | `1`              | Do a run immediately at start. |
| `REJECT_ENABLED`  | `1`              | Bouncer active: move suspicious/unusable files to `_rejected` instead of sorting them in. `0` = off. |
| `REJECT_UNKNOWN_ARTIST` | `1`        | Reject if no usable artist can be determined. Album is not checked (singles). |
| `REJECT_KEYWORDS` | *(default list)*| Reject on a keyword in the filename (Lyrics/Remix/Live …). Empty = default list, `off`/`none` = off, a custom list replaces it. |
| `MIN_DURATION_SECONDS` | `90`        | Shorter files count as aborted downloads → rejected and searched again. `0` = off. |
| `REPLAYGAIN`      | `1`              | Normalize volume on import (ReplayGain tag, lossless). Needs ffmpeg. The player must have ReplayGain enabled. `0` = off. |
| `REPLAYGAIN_TARGET_LUFS` | `-14`     | Target loudness. `-18` = ReplayGain reference (quieter), `-16` = Apple Music, `-14` = Spotify/YouTube (louder). Larger = louder. |
| `EMBED_COVERS`    | `1`              | Search for a cover via the configured sources (`COVER_SOURCES`, default iTunes→Deezer) and embed it into the MP3 (ID3 tag, no separate file). `0` = off. |
| `COVER_SOURCES`   | `itunes,deezer`  | Cover sources and order. iTunes has the best single artworks (always square), Deezer is fallback. `deezer` = Deezer only. |
| `ITUNES_COVER_SIZE` | `1000`         | Edge length of the iTunes covers in px. |
| `ITUNES_MAX_RETRIES` | `3`           | On iTunes throttling (HTTP 429/403), retry this many times before falling back to Deezer. |
| `ITUNES_RETRY_BASE_MS` | `800`       | Base wait time before the first iTunes retry (doubled thereafter). |
| `OVERWRITE_COVER` | `1`              | For chart songs, replace an already embedded cover with the newly searched **single** cover (important for songs from compilations/samplers). Only replaces if a source provides a cover. `0` = never overwrite an existing cover. |
| `BACKFILL_INTERVAL_HOURS` | `24`     | **Automatic** metadata backfill: periodically re-fetches missing covers/ReplayGain for the chart songs (manifest) inside the scheduler loop — for songs left without cover/RG at import time because a source was briefly unreachable. `0` = off. |
| `BACKFILL_SLEEP_MS` | `400`          | Pause between cover API calls in the backfill (eases iTunes/Deezer rate limits). |
| `BACKFILL_LIMIT`  | `0`              | Max files fixed per backfill run (`0` = all). Cap against very long runs. |
| `BACKFILL_RECANON`| `1`              | In the backfill, reconcile UPPERCASE flashback names against the official Deezer spelling and fix on a safe match (rename + tags + manifest). `0` = off. |
| `PUID` / `PGID`   | `1027` / `100`   | User/group (youruser:users, like Navidrome). |
| `TZ`              | `Europe/Zurich`  | Time zone. |
| `PUSHOVER_TOKEN`  | *(empty)*         | Optional: Pushover app token. |
| `PUSHOVER_USER`   | *(empty)*         | Optional: Pushover user key. |
| `PUSHOVER_DEVICE` | *(empty)*         | Optional: target device. Multiple devices possible, comma-separated (e.g. `iPhone-Max,Tablet-Anna`) — one message is sent per device. All devices belong to the same Pushover account. Empty field = delivery to all devices of the account. Invalid/disabled device names are stripped before sending — otherwise Pushover delivers an unknown name to **all** devices instead (double push). |
| `PUSHOVER_SOUND` | `incoming`         | Notification sound for the regular import summary message (plus rejected/flagged). Valid Pushover sounds: `pushover, magic, cosmic, intermission, bike, bugle, cashregister, classical, falling, gamelan, incoming, mechanical, pianobar, siren, spacealarm, tugboat, alien, climb, persistent, echo, updown, none` (= silent), `vibrate`. Empty = account default. Overridable via `PUSHOVER_SOUND_IMPORT` in `compose.yml`. |
| `PUSHOVER_SOUND_MANUAL` | `magic`     | Dedicated sound **only** for the availability message of a song manually requested via charts-songrequest ("🎵 Your song is here!"). This way the user hears from the sound that **their** song arrived, not just a routine import. Same valid sounds as `PUSHOVER_SOUND`; empty = account default. Overridable via `PUSHOVER_SOUND_MANUAL` in `compose.yml`. |
| `PUSHOVER_DEVICES_TTL` | `3600`         | Optional: cache duration (seconds) for the account device list used for filtering. |
| `LIDARR_URL`      | *(empty)*         | Optional: Lidarr base URL (e.g. `http://<NAS-IP>:8686`). Empty = integration off. |
| `LIDARR_API_KEY`  | *(empty)*         | Lidarr API key (Settings → General → Security). |
| `LIDARR_UNMONITOR`| `1`              | Set imported chart artists to *unmonitored* in Lidarr (no catalog download). |
| `LIDARR_ADD_MISSING` | `1`           | Add not-yet-present artists newly in Lidarr (directly as unmonitored). `0` = only unmonitor existing ones. |
| `LIDARR_QUALITY_PROFILE_ID` | `4`    | Quality profile ID for the new entry. |
| `LIDARR_METADATA_PROFILE_ID`| `1`    | Metadata profile ID for the new entry. |
| `LIDARR_ROOT_FOLDER` | `/music/Music` | Root folder path for the new entry. |

The paths in the container (`DOWNLOADS_DIR=/music/Charts`,
`MUSIC_DIR=/music/Music`) are set in the `compose.yml` and normally do not need
to be changed. Important: `DOWNLOADS_DIR` must be the **same** folder into which
slskd puts its finished downloads (`directories.downloads` in the `slskd.yml`).

---

## Directory layout

### On the host (NAS)

| Path                            | Purpose |
|---------------------------------|-------|
| `/path/to/charts-stack` | Stack project directory (compose/build, all services). |
| `/volume2/music/Charts`         | slskd downloads (source for the import). |
| `/volume2/music/Music`          | Navidrome library (import target). |

### In the container

| Host (NAS)        | Container   | Purpose |
|-------------------|-------------|-------|
| `/volume2/music`  | `/music`    | whole music share (read from `Charts`, write to `Music`). |

Concretely: `DOWNLOADS_DIR=/music/Charts`, `MUSIC_DIR=/music/Music`. The service
runs as `youruser:users` (PUID 1027 / PGID 100), so that the sorted-in files have
the same owner as the rest of the music and Navidrome can read them.

---

## Copy vs. move & cleanup

**Why copy (default)?**
Soulseek expects downloaded files to remain available for sharing for a while.
When **copying** (`COPY_MODE=1`), the original stays in the Charts folder
(= slskd share), but your song is also immediately in the library. This way you
share freshly downloaded material for a while without releasing your entire
collection.

**Why count-based cleanup (`KEEP_LATEST`)?**
Soulseek/slskd knows **no** seed counter like BitTorrent ("delete after X
uploads" is technically not possible). More important anyway is the **number**
of shared files (see the leecher section). Therefore the default strategy is
**count-based**: `charts-import` always keeps the `KEEP_LATEST` newest downloads
(default 500) and only deletes the surplus.

The advantage over time-based deletion: the share stays **stable at ~500 files**
(with `KEEP_LATEST=500`), no matter how often you manually search for new songs.
With time-based deletion (`RETENTION_DAYS`) the share would run empty during
longer search pauses and turn you into a leecher. `KEEP_LATEST` fully decouples
the cleanup from your search rhythm.

**Why does that protect against leecher status?**
Other Soulseek users rate you by **how many files** you share, not by how much
is actually uploaded. Whoever offers only a handful of files is considered a
"leecher" and gets throttled or blocked (slskd even has its own `leechers` group
for that). With `KEEP_LATEST=500` you permanently share around 500 files once
enough downloads have accumulated. That is usually enough not to be considered a
leecher. If it is not enough, additionally share a small, fixed seed folder (see
below).

**Alternative: age-based (`RETENTION_DAYS`).**
Whoever prefers time-based cleanup sets `KEEP_LATEST=0` and `RETENTION_DAYS` to
the desired number of days. Downloads are then deleted as soon as they are
older. Only sensible if new downloads come in regularly (e.g. with automatic
search), otherwise the share runs empty.

**Limit upload without being considered a leecher.**
Do not reduce the number of files, but limit the **bandwidth**. In the
`slskd.yml` under `transfers.upload`, set `slots` and `speed_limit` moderately
(e.g. 3 slots, 300 KiB/s). This keeps your upstream free while you still share
enough files.

**Optional fixed seed folder.**
Whoever surely does not want to be considered a leecher additionally shares a
small, permanent folder with, e.g., 100–300 of their own tracks (not the whole
library). Add it in slskd as a second share (`/seedmusic`). This way you are a
"fair" user without releasing the complete collection.

**When to move instead of copy?**
If you do not want to (continue) sharing at all: `COPY_MODE=0`. Then the file is
immediately moved out of the slskd share into the library and no longer offered.
(Note: permanent non-sharing slows you down in the Soulseek network as a
"leecher".)

---

## Operation & maintenance

**Status / logs:**

```bash
sudo docker compose logs -f          # follow live
sudo docker compose logs --tail=50   # last 50 lines
```

**Trigger a run immediately** (restart with `RUN_ON_START=1`):

```bash
sudo docker compose restart
```

**Change configuration:** adjust `.env`, then:

```bash
sudo docker compose up -d
```

**Backfill covers for existing songs (one-time):**
The import embeds covers only for newly sorted-in songs. To improve the
**existing** library, there is a one-time script that finds all MP3s without a
cover and backfills them via the configured sources (iTunes→Deezer):

```bash
# 1. First count/list (changes nothing):
sudo docker compose exec charts-import python -m app.backfill_covers --dry-run
# 2. Then actually backfill:
sudo docker compose exec charts-import python -m app.backfill_covers
```

**Repair faulty (rectangular) covers.** Some slskd files bring distorted covers
along (e.g. CD scans with front and back side next to each other). With
`--fix-broken` the backfill replaces **rectangular** covers with the square
single cover. Important: `--fix-broken` considers **only the chart songs** (the
files listed in the `.m3u` playlists, `PLAYLIST_DIR`), **not** the whole
library, so that the rest of your (Lidarr-managed) collection with its
album/compilation covers stays untouched. Square covers stay untouched anyway,
and a replacement happens only if a source provides a cover:

```bash
# First list what would be repaired (changes nothing):
sudo docker compose exec charts-import python -m app.backfill_covers --fix-broken --dry-run
# Then repair:
sudo docker compose exec charts-import python -m app.backfill_covers --fix-broken
```

**Replace cassette/CD scan covers (square, but wrong).** Some source files have
as "cover" the scan of a cassette or CD case (with track list, side A/B).
Example: "Kylie Minogue – I Should Be So Lucky" showed a cassette case scan. Such
images are **square** and are therefore **not** detected by `--fix-broken`. With
`--force-refresh` the cover is re-fetched for **all** chart songs (source per
`COVER_SOURCES`, usually iTunes single artwork; only playlist songs, not the
whole library). This also replaces old, worse covers (e.g. a demo EP with the
real single cover):

```bash
# Preview (changes nothing):
sudo docker compose exec charts-import python -m app.backfill_covers --force-refresh --dry-run
# Replace:
sudo docker compose exec charts-import python -m app.backfill_covers --force-refresh
```

**Capture all chart songs ever downloaded (`--all-charts`).** `--force-refresh`
alone considers only the songs currently listed in the playlists. To capture
**all chart songs ever downloaded by charts-download** – including those that
have since dropped out of the charts but still lie in the library – you combine
it with `--all-charts`. The scope then comes from the **manifest**
(`MANIFEST_FILE`), which is written exclusively by charts-download.

> **Important – the Lidarr collection stays untouched:** `--all-charts` touches
> **only** files that are in the manifest (= chart songs downloaded by slskd).
> The rest of your Lidarr-managed collection is never in the manifest and is
> therefore **never** changed. Still, always check with `--dry-run` first.

```bash
# Preview: ALL chart covers ever downloaded that would be re-fetched (changes nothing):
sudo docker compose exec charts-import python -m app.backfill_covers --all-charts --force-refresh --dry-run
# Replace (only manifest songs, not the Lidarr collection):
sudo docker compose exec charts-import python -m app.backfill_covers --all-charts --force-refresh
```

For a **single** song it also works in a targeted way without touching all:

```bash
sudo docker compose exec charts-import python -c "
from app import cover
cover.embed_cover('/music/Music/Kylie Minogue/Kylie (1988)/Kylie Minogue - Kylie - 01 - I Should Be So Lucky.mp3', 'Kylie Minogue', 'I Should Be So Lucky', overwrite=True)
"
```

**Backfill ReplayGain for existing chart songs.** The import normalizes new
songs automatically (`REPLAYGAIN=1`). For the already present chart songs there
is a one-time script (only playlist songs, not the whole library):

```bash
# First count what is missing (changes nothing):
sudo docker compose exec charts-import python -m app.backfill_replaygain --dry-run
# Then calculate + write (only songs without a ReplayGain tag):
sudo docker compose exec charts-import python -m app.backfill_replaygain
# Recalculate all chart songs (e.g. after changing REPLAYGAIN_TARGET_LUFS):
sudo docker compose exec charts-import python -m app.backfill_replaygain --overwrite
```

For the normalization to become audible, enable ReplayGain mode in the player
(Navidrome web: Settings → ReplayGain; apps like Symfonium have their own
ReplayGain option).

Control via environment variables: `BACKFILL_SLEEP_MS` (pause between API calls,
default 400 ms, spares the APIs) and `BACKFILL_LIMIT` (max. number per run,
0 = all). Already present covers are never overwritten, songs without readable
tags are skipped. Newly backfilled covers are also cropped square. Runs over the
whole `MUSIC_DIR`, so with a large library it can take a while.

**Clean up flashback legacy entries (one-time).** Before the Deezer
canonicalization, `charts-flashback` wrote artist/title in UPPERCASE and with
`{YYYY}` in the name; the corresponding manifest entries persist and keep acting
(charts-import builds path/tag from the manifest, not from the filename). The
idempotent script `scripts/cleanup_flashback_legacy.py` removes these poisoned
`src=flashback` legacy entries (UPPERCASE or `{YYYY}` titles) from the manifest
and clears the associated `{YYYY}` library duplicates. The year map
(`.charts-flashback-years.json`) is left untouched. Run it on the **NAS host**
(library writable), first with `--dry-run`:

```bash
python charts-import/scripts/cleanup_flashback_legacy.py --dry-run
python charts-import/scripts/cleanup_flashback_legacy.py
```

**Strip provenance/encoder tags from the existing library (one-time).** New
imports are cleaned automatically (see `tags.py`); for the **existing** stock
there is a one-time script with the **same** removal logic. It runs over the
**whole library** and removes `COMM`/`TENC`/`TSSE`/URL frames + unprotected
`TXXX` (e.g. uploader signatures, `TXXX:LOG`, the fre:ac comment) – but
**protects** `TXXX:MusicBrainz*` and `TXXX:*replaygain*` and all standard tags.
Run it inside the container (where `/music` is writable), **always with
`--dry-run` first** (add `--verbose` to list the affected frames; `--artist
"<name>"` limits it to one artist folder for testing):

```bash
sudo docker compose exec charts-import python /app/scripts/strip_provenance_tags.py --dry-run
sudo docker compose exec charts-import python /app/scripts/strip_provenance_tags.py
```

Then trigger a Navidrome scan so the cleaned tags become visible.

**Rebuild after code changes:**

```bash
sudo docker compose up -d --build
```

**Stop / remove:**

```bash
sudo docker compose down
```

---

## Troubleshooting

**Songs land under `Unknown Artist/Unknown Album`.**
If the download came from charts-download, this should be prevented by the
manifest (clean tags are set). If it still happens, the manifest entry is
missing (e.g. a file placed manually into the Charts folder) and the file has no
readable tags. Then maintain the tags afterwards (e.g. with MusicBrainz Picard)
and place the file again into the Charts folder, or download the song from a
better-tagged source. slskd downloads are usually tagged, very old rips
sometimes are not.

**A currently running download is already being sorted in.**
Should be prevented by `MIN_AGE_SECONDS`. Increase the value (e.g. `300`) if
your downloads are slow.

**The song is in the library but does not appear in Navidrome.**
Navidrome must scan first. Wait for the next `ND_SCANSCHEDULE` or trigger a scan
in the Navidrome UI. Only after that can `charts-sync` match it into the
playlist.

**Files belong to the wrong user.**
Check `PUID`/`PGID` (on the NAS with `id youruser`). They must match Navidrome and
slskd, otherwise Navidrome may not be able to read the files.

**Cleanup deletes nothing.**
With `KEEP_LATEST`, deletion only happens once more than `KEEP_LATEST` files lie
in the folder. For the age-based variant, check `RETENTION_DAYS` (`0` =
disabled). If both are `0`, cleanup is switched off. Sorting/deleting is done by
the modification date (`mtime`) of the file.

---

## Expansion path: automated search with Soularr

The manual way is: open `missing.txt`, search the songs in the slskd web
interface and download them. `charts-import` then takes over the sorting-in. For
**automatic search** there is [Soularr](https://github.com/mrusse/soularr).

**What is Soularr?**
Soularr is the bridge between **Lidarr** (the "*arr" tool for music, analogous to
Sonarr for series / Radarr for movies) and **slskd**. It reads the "Wanted" list
from Lidarr, automatically searches the titles in slskd, downloads them and
reports them back to Lidarr. It is intended **for music**, not for audiobooks.

**Important restriction for this charts workflow:**
Soularr **cannot process a `missing.txt` directly**. It mandatorily needs
**Lidarr** as the source of the wishlist. For Soularr to close the chart gaps,
you would have to:

1. Set up **Lidarr** (its own container).
2. Mark the missing artists/albums in Lidarr as "Monitored / Wanted".
3. Connect **slskd** as a download client in Lidarr / via Soularr.
4. Run **Soularr** periodically; it downloads the Wanted entries via slskd.

Lidarr is moreover **album-oriented**, while charts are individual **singles**.
Conceptually that fits only moderately – for individual chart songs the manual
slskd way is often more direct.

**Sketch of a Soularr setup** (only if you want to go the Lidarr way):

```yaml
# Addition to the existing slskd/Lidarr landscape. Sketch only!
services:
  soularr:
    image: mrusse08/soularr:latest
    container_name: soularr
    restart: unless-stopped
    environment:
      - TZ=Europe/Zurich
      - SCRIPT_INTERVAL=300        # seconds between runs
    volumes:
      # Soularr configuration (config.ini + timestamp)
      - /path/to/arr-stack/soularr:/data
      # Must see the same download folder as slskd
      - /volume2/music/Charts:/downloads
```

The actual configuration happens in `config.ini` (in the `/data` volume): there
you enter the **Lidarr URL + API key**, the **slskd URL + API key** and the
shared `download_dir`. You create the slskd API key beforehand in the
`slskd.yml` (`web.authentication.api_keys`). Details in the
[Soularr README](https://github.com/mrusse/soularr).

> **Recommendation:** For your pure charts use case, the chain
> `charts-sync` → manual slskd search → `charts-import` is enough. Soularr only
> pays off once you use **Lidarr** anyway for a larger, album-based music
> management.

*Note: content about Soularr was summarized and rephrased for license
compliance.*

---

## FAQ

**What happens to rejected files (bouncer)?**
They are moved to `_rejected` in the Charts folder (not deleted), so you can look
inside if needed. charts-import no longer touches this folder. The associated
song is released in the charts-download state, so that charts-download searches
for a different (hopefully better) version at the next run. If no clean version
is found, the song stays in the missing.txt. If you no longer need the
`_rejected` folder, you can simply delete it.

**Are many files rejected?**
Normally no. charts-download already filters out Lyrics/Remix/FLAC at download
time, so the bouncer rarely sees anything. It is mainly the safety net for old
stock and manually placed files.

**A legitimate song is wrongly rejected.**
Check the `_rejected` folder. In case of a false alarm by a keyword you can
adjust `REJECT_KEYWORDS` or disable the bouncer entirely with
`REJECT_ENABLED=0`. For "no usable artist" it helps to maintain the file tags.

**Where do the covers come from?**
charts-import searches for each sorted-in song (with clean chart metadata) the
matching cover via the configured sources (`COVER_SOURCES`, default
**iTunes → Deezer**) and embeds it directly into the MP3 (ID3 tag, no separate
image file). **iTunes/Apple** is the primary source because it has the largest
catalog with dedicated **single artworks** in high resolution (always square);
**Deezer** steps in when iTunes does not know a song. The hit selection prefers
the real single or the original album and sorts out foreign versions (remix,
sped up, slowed, live, instrumental, piano/cover bands,
compilations/"Now That's What I Call"/KIDZ BOP as well as demo EPs). This way
e.g. "Dominic Fike – Babydoll" gets the dedicated
"Babydoll / White Keys" single cover instead of the demo EP cover.

The cover is searched via artist+title, so it delivers the **single/original
album cover**, even if the file comes from a compilation. With
`OVERWRITE_COVER=1` an already embedded (e.g. sampler) cover is replaced. iTunes
covers are already square; a possibly rectangular Deezer cover is center-cropped
(with Pillow). Navidrome reads embedded covers with the highest priority. If no
source finds a cover, the song stays without artwork (not an error). Can be
disabled via `EMBED_COVERS=0`. Note: only MP3 is supported; FLAC/other files are
skipped.

**Are my original downloads deleted?**
Only during cleanup. With `KEEP_LATEST` (default) the oldest downloads are
deleted once more than `KEEP_LATEST` lie in the folder; the newest remain. In
copy mode (default), until then both the copy in the library and the original in
the Charts folder exist.

**What happens with duplicate songs?**
If a file of the same size already exists at the target location, it is skipped.
On a real name collision with a different size, a counter suffix is appended.

**Do I get to know what was imported?**
Yes, if Pushover is configured (`PUSHOVER_TOKEN` + `PUSHOVER_USER`). After each
run with new songs a notification comes with the list of sorted-in titles (up to
10, the rest as "… und X weitere"), each title with a `[playlist]` prefix and a
breakdown of how many songs come from which playlist.

**Does `charts-import` have to wait for `charts-sync`?**
No. Both run independently on their own interval. The chain arises via the
shared folders (`Charts`, `Music`), not via direct communication.
