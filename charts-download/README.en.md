> 🇬🇧 English version. Deutsche Version: [README.md](README.md)

# charts-download

Automatically downloads the missing chart songs via the **slskd API**. Reads
the `missing.txt` written by [`charts-sync`](../charts-sync), searches for each
song in slskd, picks the best **MP3 ≥ 256 kbps** and starts the download, so you
no longer have to search for them individually by hand.

The third sister project alongside `charts-sync` (finds the gaps) and
[`charts-import`](../charts-import) (sorts downloads in + cleans up).
`charts-download` only replaces the **manual search/download step** in slskd,
not charts-import.

---

## Charts automation – overview

`charts-download` is part of a chain of three services that together fully
automate the chart workflow. Each runs as its own container with its own
interval and its own `.env`. They are coupled exclusively through shared folders
(`/volume2/music/Charts`, `/volume2/music/Music`). The family is rounded out by
[`charts-songrequest`](../charts-songrequest), an optional web UI for
**manually** requesting individual songs (searches/downloads via slskd like this
service, import via the manifest).

Sister projects: [`charts-sync`](../charts-sync) · [`charts-import`](../charts-import) · [`charts-songrequest`](../charts-songrequest)

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

charts-sync picks up new **chart entries** via its regular interval (every
12 h). **Newly downloaded songs**, on the other hand, land in the playlist
almost immediately: after sorting them in, charts-import sets a signal that
prompts charts-sync to run within ~1 minute (resync trigger).

### Services at a glance

| Service | Task | Interval | Key settings |
|--------|---------|-----------|------------------------|
| **charts-sync** | Finds gaps, writes playlist + missing.txt, notifies your phone | 12 h | `SYNC_INTERVAL_HOURS`, `MATCH_THRESHOLD`, `NOTIFY_MISSING_LIMIT` |
| **charts-download** | Downloads missing songs via the slskd API, retries on failures | 60 min | `MAX_PER_RUN=10`, `MIN_BITRATE=256`, `DRY_RUN`, `SLSKD_API_KEY` |
| **charts-import** | Sorts downloads into the library, keeps the share stable | 30 min | `KEEP_LATEST=500`, `COPY_MODE=1`, `MIN_AGE_SECONDS=120` |
| **charts-recommend** | Personalized playlists (ListenBrainz/Last.fm) + missing | 24 h | `LISTENBRAINZ_USER`, `LASTFM_ENABLED`, `WRITE_MISSING` |
| **charts-upgrade** | Replaces songs with too-low bitrate with better versions | on-demand | `MIN_BITRATE`, `SLSKD_API_KEY` |
| **charts-songrequest** | Web UI for manually requesting individual songs | on-demand | `SONGREQUEST_USERS`, `SLSKD_API_KEY` |

### Directories on the NAS

| Path | Purpose |
|------|-------|
| `/volume2/music/Charts/` | slskd downloads + missing.txt + state files |
| `/volume2/music/Music/` | Navidrome library (import target) |
| `/volume2/music/SeedShare/` | Permanent seed folder (leecher protection) |
| `/path/to/charts-stack/` | Stack project folder (one `compose.yml` + one central `.env` for all services) |

---

## Table of contents

- [Overview](#overview)
- [Overall workflow](#overall-workflow)
- [How it works](#how-it-works)
- [Project structure](#project-structure)
- [Architecture & modules](#architecture--modules)
- [Prerequisite: create an API key in slskd](#prerequisite-create-an-api-key-in-slskd)
- [Installation on the Synology NAS](#installation-on-the-synology-nas)
- [Configuration (`.env`)](#configuration-env)
- [DRY_RUN: test first, then download](#dry_run-test-first-then-download)
- [Download selection logic](#download-selection-logic)
- [Excluding artists globally](#excluding-artists-globally)
- [Operation & maintenance](#operation--maintenance)
- [Troubleshooting](#troubleshooting)
- [FAQ](#faq)

---

## Overview

| Property               | Value |
|------------------------|------|
| Source                 | `missing.txt` from charts-sync (`/volume2/music/Charts/missing.txt`) |
| Target                 | slskd downloads (`/volume2/music/Charts/`), triggered via the slskd API |
| slskd connection       | [`slskd-api`](https://pypi.org/project/slskd-api/) (REST API, API key) |
| Base image             | `python:3.12-slim` |
| Dependencies           | `httpx`, `slskd-api` |
| Format                 | **MP3 ≥ `MIN_BITRATE` only** (default 256 kbps) |
| Prioritization         | Singles/albums before compilations (compilations can be rejected entirely: `REJECT_COMPILATIONS`) |
| Amount per run         | max. `MAX_PER_RUN` (default 10) |
| Default mode           | **`DRY_RUN=1`** – downloads nothing, only reports what *would* be downloaded |
| Operating mode         | Endless loop with an interval (minutes) |
| Runs as                | `youruser:users` (PUID 1027 / PGID 100) |
| Notifications          | Pushover (optional) |

---

## Overall workflow

`charts-download` closes the gap between "song is missing" and "song is downloaded":

```
Deezer charts
     │
     ▼
charts-sync ─────► missing.txt          (which chart songs are missing)
     │             /volume2/music/Charts/missing.txt
     ▼
charts-download ─► reads missing.txt, searches via slskd API,
     │             picks best MP3 ≥ 256 and starts the download
     ▼
slskd ───────────► downloads to /volume2/music/Charts/
     │
     ▼
charts-import ───► copies to /volume2/music/Music/<Artist>/<Album>/
     │             and keeps the Charts folder at KEEP_LATEST files
     ▼
Navidrome ───────► sees the new songs
     │
     ▼
charts-sync ─────► matches them into the playlist, missing.txt gets shorter
```

All services in this flow (charts-sync, charts-download, slskd, charts-import)
run independently on their own interval and are only coupled through the shared
folders (`Charts`, `Music`). `charts-songrequest` additionally hooks in on
demand (manual single requests through the same pipeline).

---

## How it works

A run (`run_once`):

1. **check failed ones** – query the status of already requested downloads in
   slskd. Failed ones (`Aborted`, `Errored`, `TimedOut`, `Rejected`,
   `Cancelled`) are removed from the state and cleared away in slskd, so they
   are searched for again in this run. Running downloads (`InProgress`,
   `Queued`) are left alone.
2. **read missing** – parse one or more `missing.txt` (`MISSING_FILES`), sort
   by chart position, consolidate duplicates (the same song in several charts).
3. **connect** – build the slskd client via the API key and test the connection.
4. **per song** (until `MAX_PER_RUN` is reached):
   - skip already requested songs (state file),
   - skip songs searched for recently without success (cooldown, see
     `SEARCH_COOLDOWN_HOURS`) – **without** using up a `MAX_PER_RUN` slot, so
     that permanently unfindable songs (e.g. many Mundart titles) do not block
     every run,
     > **Cooldown reversal on "slskd not yet searchable":** if in **one** run
     > **all** searched songs return 0 hits and nothing was triggered,
     > charts-download treats this as "slskd logged in but not yet searchable"
     > (timing right after a rebuild/restart) and **takes back the cooldowns it
     > just set** – so the backlog does not sit idle for up to
     > `SEARCH_COOLDOWN_HOURS`, and the next run retries normally.
   - search in slskd (two-stage): first with "Artist Title"; if that yields
     **0 hits** (Soulseek matches search words against file paths – if the
     artist is not in the folder, everything otherwise falls away), it searches
     as a **fallback by the title only**. In the title fallback the artist
     **must** appear in the file path (protection against cover versions),
   - wait for the search to complete,
   - pick the best **MP3 ≥ `MIN_BITRATE`** from all results, prioritizing
     singles/albums before compilations (see
     [selection logic](#download-selection-logic)),
   - **`DRY_RUN=1`**: only log/report what *would* be downloaded;
     **`DRY_RUN=0`**: start the download via the slskd API, note it in the
     state and write the clean chart metadata (artist/title) into the
     **manifest**.
5. **report** – optional Pushover summary. It also shows the origin of the songs
   by playlist (e.g. "3 aus charts, 2 aus mundart"), derived from the
   respective `missing` file; each line is prefixed with `[playlist]`.

**Manifest for clean tags.** slskd downloads often have bad or missing tags. So
that `charts-import` does not sort them in as "Unknown Album", `charts-download`
writes the real chart metadata into `MANIFEST_FILE`
(`/charts/.charts-manifest.json`) at download time: per file the real artist and
title. `charts-import` reads this manifest and tags the file correctly. That way
the songs are sorted in cleanly and matched reliably by `charts-sync`.

Afterwards the service waits `INTERVAL_MINUTES` minutes and repeats the run.

---

## Project structure

```
charts-download/
├── app/
│   ├── __init__.py       # package marker
│   ├── config.py         # configuration from environment variables
│   ├── matching.py       # normalization + similarity (like charts-sync)
│   ├── missing.py        # parser for the missing.txt from charts-sync
│   ├── slsk.py           # slskd API: search, result selection, download
│   ├── state.py          # remembers requested songs (no double downloads)
│   ├── manifest.py       # writes chart metadata for charts-import
│   ├── notify.py         # optional Pushover notification
│   └── main.py           # scheduler loop, connects all modules
├── Dockerfile            # python:3.12-slim, starts app.main
├── compose.yml           # service definition for the NAS
├── requirements.txt      # httpx, slskd-api
├── .env.example          # template for the configuration
├── .gitignore
└── README.md             # this file
```

---

## Architecture & modules

### `config.py`
Reads all settings from environment variables: slskd access (`SLSKD_HOST`,
`SLSKD_API_KEY`), source file(s), selection criteria (`MIN_BITRATE`,
`MATCH_THRESHOLD`), amount control (`MAX_PER_RUN`, `DRY_RUN`), scheduler and
Pushover.

### `matching.py`
The same `normalize`/`similarity` logic as in charts-sync, plus `match_score`:
rates how well an slskd file name fits "Artist Title". Since slskd only delivers
file paths (no clean tags), besides the pure string similarity it is above all
the **word coverage** that counts (do all search words appear in the file
name?). That protects against random hits. In addition, `is_compilation`
detects, via keywords in the path, whether a hit comes from a compilation/a
sampler (for the prioritization in the selection).

### `missing.py`
Parses the charts-sync format (`  12. Artist - Title`), ignores the header
comments and blank lines. `load_all` consolidates several files and
deduplicates by the normalized "Artist|Title".

### `slsk.py`
The slskd connection via the library `slskd-api`:
- `connect()` builds the client (API key) and tests the connection.
- `find_candidates()` searches in two stages: first "Artist Title", and on
  0 hits as a fallback the title only (then the artist must appear in the path,
  `artist_in_path`, protection against cover versions). Returns **all** matching
  hits sorted by quality (best first), so the caller can tick them off one by
  one on retry. Raises `SlskdError` on connection problems (so an slskd outage
  does not count as "not findable"). `find_download()` is a thin wrapper that
  returns only the best hit.
- `_rank_candidates()` filters for **MP3 ≥ `MIN_BITRATE`**, free upload slots
  and `MATCH_THRESHOLD`, discards exclusion keywords and DJ-pool edits
  (`looks_like_dj_edit`: file name ends in a BPM number), then sorts by score,
  bitrate and upload speed. Singles/albums are prioritized before compilations.
- `enqueue()` starts the download. **Retry across several providers:** because
  Soulseek is P2P, the best hit may come from a provider (end user) who is
  currently offline / rejects the upload. Per song, the candidates are therefore
  started one after another until one accepts the download; only if none
  delivers does the song count as (for now) failed.
- `check_download_states()` checks the status of the requested downloads in
  slskd and detects failed ones (`Aborted`, `Errored`, `TimedOut` etc.).
- `cleanup_failed()` removes failed downloads in slskd.

### `state.py`
Persists in `.charts-download-state.json` which songs have already been
requested (incl. slskd username + file name for status tracking), so nothing is
downloaded twice. Failed downloads are automatically removed from the state and
retried. In the dry run nothing is marked, so a later real run considers the
same songs again.

### `manifest.py`
At download time, writes the clean chart metadata (real artist + title) into
`MANIFEST_FILE`, with the file base name as the key. `charts-import` reads this
manifest and tags the file correctly instead of taking over the often bad slskd
tags. This prevents "Unknown Album" entries and ensures that `charts-sync`
matches the songs reliably.

### `notify.py`
Optional Pushover sending, only active if `PUSHOVER_TOKEN` and `PUSHOVER_USER`
are set. Identical to the sister services.

### `main.py`
Entry point (`python -m app.main`). Runs `run_once` (read missing, connect,
per song search/select/download with the `MAX_PER_RUN` limit and the dry-run
switch) and repeats at the interval. Errors are caught so the service keeps
running permanently.

---

## Prerequisite: create an API key in slskd

`charts-download` needs an API key with write rights. In the `slskd.yml`
(on the NAS under `/path/to/arr-stack/slskd/slskd.yml`) enter a key
with the role `readwrite`:

```yaml
web:
  authentication:
    api_keys:
      charts_download:
        key: <a random string, 16-255 characters>
        role: readwrite
        cidr: 0.0.0.0/0,::/0
```

You generate the `key` value yourself (e.g. a long random string). Then
reload/restart slskd. You enter this key in the `.env` as `SLSKD_API_KEY`.

> Security note: `role: readwrite` is enough (search + downloads).
> `administrator` is not needed. With `cidr` you can restrict access to the
> Docker/LAN network.

---

## Installation on the Synology NAS

charts-download is **not** installed on its own. It is part of the
**charts-stack**, which runs as ONE project: one `compose.yml` + one central
`.env` in the `charts-stack` folder (e.g. `/path/to/charts-stack/`).
The full installation (including slskd setup and `.env`) is in the
**[main README](../README.en.md)**. Additionally important for charts-download:

- **Create the API key in slskd** (see above) and enter it as `SLSKD_API_KEY`
  in the central `.env`.
- For the first test keep `DRY_RUN=1` (see
  [DRY_RUN](#dry_run-test-first-then-download)).

To build/start just this service – from the `charts-stack` folder:

```bash
docker compose up -d --build charts-download
docker compose logs -f charts-download
```

In the first (dry-run) run you see `[DRY_RUN] wuerde laden: …` (would download)
per song and at the end `Lauf fertig (DRY_RUN): X Songs wuerden geladen.`
(run finished (DRY_RUN): X songs would be downloaded.)

---

## Configuration (`.env`)

| Variable            | Default | Meaning |
|---------------------|---------|-----------|
| `SLSKD_HOST`        | `http://slskd:5030` | slskd URL. Default = container name in the shared Docker network; otherwise the host IP (`http://<NAS-IP>:5030`). |
| `SLSKD_API_KEY`     | *(empty, required)* | API key from the slskd.yml (`readwrite`). |
| `SLSKD_URL_BASE`    | `/` | Only if slskd runs under a subpath. |
| `MISSING_FILES`     | `/charts/missing.txt;/charts/missing-mundart.txt;/charts/missing-flashback.txt;/charts/missing-hitparade.txt;/charts/missing-recommend.txt` | Source file(s), one per service/playlist, separate several with `;`. |
| `MIN_BITRATE`       | `256` | Minimum MP3 bitrate (kbps). Other formats are ignored. |
| `MATCH_THRESHOLD`   | `80` | Similarity 0-100 between "Artist Title" and the file name. |
| `SEARCH_COOLDOWN_HOURS` | `24` | Skip songs searched for without success for this long (without using up a `MAX_PER_RUN` slot). Prevents permanently unfindable songs from blocking every run. `0` = off. |
| `EXCLUDE_KEYWORDS`  | *(default list)* | Files with these words in the name are discarded (original versions only). Empty = default list, `off`/`none` = filter off, your own comma-separated list replaces the default list. |
| `EXCLUDE_ARTISTS`   | *(empty)* | Artists to exclude globally (comma-separated). Songs by these artists are **never** downloaded – no matter which chart source. Complements the per-playlist `exclude_artists` in charts-sync. See [Excluding artists globally](#excluding-artists-globally). |
| `REJECT_COMPILATIONS` | `0` | `1` = **completely reject** compilation/sampler hits (the song stays "missing" until a real single/album version appears). `0` = download a compilation if there is nothing better. See [selection logic](#download-selection-logic). |
| `REQUIRE_FREE_SLOT` | `1` | Only providers with a free upload slot. |
| `MAX_PER_RUN`       | `40` | Max. songs per run across ALL sources (hard total cap). |
| `MAX_PER_SOURCE`    | `10` | Max. songs searched per source (playlist/missing file) per run. Fairness cap so a long, hard-to-find source (e.g. Mundart) cannot fill every slot and starve the findable chart songs. Sources take turns (round-robin). `0` = off (single combined `MAX_PER_RUN` list). |
| `DRY_RUN`           | `1` | `1` = test only (download nothing), `0` = really download. |
| `SEARCH_TIMEOUT_MS` | `15000` | Timeout of an slskd search. |
| `SEARCH_WAIT_SECONDS`| `25` | Max. wait time for the search to complete. |
| `INTERVAL_MINUTES`  | `60` | Minutes between two runs. |
| `RUN_ON_START`      | `1` | Do a run immediately on start. |
| `STATE_FILE`        | `/charts/.charts-download-state.json` | Remembers requested songs. |
| `MANIFEST_FILE`     | `/charts/.charts-manifest.json` | Chart metadata for charts-import (clean tags). |
| `PUID` / `PGID`     | `1027` / `100` | User/group (youruser:users). |
| `TZ`                | `Europe/Zurich` | Time zone. |
| `PUSHOVER_*`        | *(empty)* | Optional Pushover credentials. |
| `PUSHOVER_DEVICE`   | *(empty)* | Optional: target device. Multiple devices possible, comma-separated (e.g. `iPhone-Max,Tablet-Anna`) — one message is sent per device. All devices belong to the same Pushover account. Empty field = delivery to all devices of the account. Invalid/disabled device names are stripped before sending — otherwise Pushover delivers an unknown name to **all** devices instead (double push). |
| `PUSHOVER_SOUND` | `falling` | Notification sound for this service. Valid Pushover sounds: `pushover, magic, cosmic, intermission, bike, bugle, cashregister, classical, falling, gamelan, incoming, mechanical, pianobar, siren, spacealarm, tugboat, alien, climb, persistent, echo, updown, none` (= silent), `vibrate`. Empty = account default. Overridable via `PUSHOVER_SOUND_DOWNLOAD` in `compose.yml`. |
| `PUSHOVER_DEVICES_TTL` | `3600` | Optional: cache duration (seconds) for the account device list used for filtering. |

---

## DRY_RUN: test first, then download

`charts-download` deliberately starts in **test mode** (`DRY_RUN=1`). In this
mode **nothing is downloaded**. The service searches quite normally, selects
hits and writes to the log/Pushover what it *would* download, including bitrate
and match score. This way you check whether the hit quality is right before
downloading for real.

Recommended procedure:

1. Start with `DRY_RUN=1` and look at one or two runs in the log.
2. Check whether the planned downloads are plausible (right song, sensible
   bitrate, score clearly above the threshold).
3. If the quality is good, set `DRY_RUN=0` in the `.env` and restart.
4. If false hits remain, raise `MATCH_THRESHOLD` (stricter) and test again.

Important: in the dry run, songs are **not** noted in the state. A later real
run therefore considers the same songs again, nothing is lost.

---

## Download selection logic

From all search results of a song, exactly one hit is chosen. A file only comes
into question if **all** conditions are met:

- the format is **MP3** and the bitrate **≥ `MIN_BITRATE`** (default 256),
- the file is not locked (`isLocked`),
- the file name contains **no exclusion keyword** (`EXCLUDE_KEYWORDS`, see
  below) – so only original versions are downloaded,
- the provider has a free upload slot (with `REQUIRE_FREE_SLOT=1`),
- the match score (file name against "Artist Title") is **≥ `MATCH_THRESHOLD`**.

**Exclusion keywords (original versions only).** Files whose name contains one of
the keywords are discarded completely – regardless of the score. In addition,
**DJ-pool edits** are discarded whose file name ends in a BPM number
(e.g. "… (Uptempo Edit) 124.mp3"); four-digit years like "(2024)" are not
affected. The built-in default list covers, among others: `lyrics`,
`lyric video`, `official video/audio`, `remix`, `mashup`, `bootleg`, `acapella`,
`instrumental`, `karaoke`, `live`, `cover`, `unplugged`, `sped up`, `slowed`,
`reverb`, `nightcore`, `8d`, `snippet`, `teaser`, `extended/club/radio mix`,
`break in`, as well as DJ-pool markers like `intro clean`, `uptempo`,
`transition`, `hyper remix`. That way lyrics videos, DJ edits, live and trend
versions are not even downloaded. (Multi-word markers prevent false hits on real
titles like "Clean Bandit" or "Dirty Diana".) Via `EXCLUDE_KEYWORDS` in the
`.env` you can replace the list (`off`/`none` switches the filter off). The
detection checks whole words, so that e.g. "The Living End" (`living` ≠ `live`)
or "Coverdale" (≠ `cover`) are passed through correctly.

Among the remaining candidates, sorting happens in this order:

1. **Singles/albums before compilations** – hits whose path hints at a
   compilation/sampler (e.g. `VA -`, `Various`, `Varios`, `Top 100`,
   `Charts`, `Hits`, `Best Of`, `Mastermix`, `New Music Friday`, `Week 34`)
   are treated **with lower priority**.
   - With `REJECT_COMPILATIONS=0` (default): they are **not discarded** –
     if there is no single/album hit, the compilation is taken (in the
     log: "nur Compilation-Treffer verfuegbar").
   - With `REJECT_COMPILATIONS=1`: compilation hits are **completely
     discarded**. The song then stays "missing" until a real single/album
     version appears – so no sampler tracks land in the library.
2. highest **match score**,
3. highest **bitrate**,
4. fastest **upload speed**.

This way you preferably get the single or album version, in the best quality and
the fastest available. Whether a compilation is accepted in an emergency
(`REJECT_COMPILATIONS=0`) or the song is rather kept missing
(`REJECT_COMPILATIONS=1`), you control yourself.

> Note: slskd only delivers file paths, no real metadata. The compilation
> detection is therefore a heuristic based on keywords in the path, not a
> reliable signal. It can misclassify individual cases, but only changes the
> priority, never whether a song is downloaded at all.

---

## Excluding artists globally

Some artists you **never** want in the library – not even when they appear in a
chart source. Typical case: Swiss Mundart Schlager like **Trauffer**, which the
music APIs wrongly list as "pop".

For that there is `EXCLUDE_ARTISTS` (comma-separated) in the `.env`:

```bash
EXCLUDE_ARTISTS=Trauffer,Nemo
```

Songs by these artists are already sorted out when reading the `missing` lists –
they are **never searched for, never downloaded, never sorted in**, no matter
which chart source. The comparison is case- and accent-insensitive and also hits
`feat.` combinations (e.g. "X feat. Trauffer").

**Distinction from the per-playlist exclusion in charts-sync.** There are two
exclusion levels, deliberately kept separate:

| Level | Place | Effect |
|-------|-----|---------|
| `exclude_artists` (per playlist) | charts-sync (`CHARTS` JSON) | The artist does **not appear in this playlist**, but can be in other playlists and in the library. |
| `EXCLUDE_ARTISTS` (global) | charts-download (`.env`) | The artist is **not downloaded at all** and never lands in the library. |

For an artist you want to get rid of completely, you set them in **both**:
in charts-sync (out of the playlists) and in charts-download (no download).

```bash
# Which songs were not downloaded due to EXCLUDE_ARTISTS:
sudo docker logs charts-download 2>&1 | grep "Artist-Ausschluss"
```

---

## Operation & maintenance

**Status / logs:**

```bash
sudo docker compose logs -f
sudo docker compose logs --tail=50
```

**Trigger a run immediately** (restart with `RUN_ON_START=1`):

```bash
sudo docker compose restart
```

**Change configuration:** adjust `.env`, then `sudo docker compose up -d`.

**Reset the state** (treat all songs as "not requested" again):

```bash
rm /volume2/music/Charts/.charts-download-state.json
```

**Stop / remove:** `sudo docker compose down`.

---

## Troubleshooting

**`slskd nicht erreichbar` / connection error.**
Check `SLSKD_HOST` and `SLSKD_API_KEY`. The key must be in the slskd.yml under
`web.authentication.api_keys` with the role `readwrite` and slskd must have been
reloaded afterwards. As a test, open the slskd web UI at `SLSKD_HOST`.

**`kein passendes MP3 >= 256 gefunden`.**
For the song there is no MP3 in sufficient quality with a free slot, or the
match score was below the threshold (even after the title fallback). The song is
skipped for `SEARCH_COOLDOWN_HOURS` and retried afterwards. Options: lower
`MATCH_THRESHOLD` (more tolerant), `REQUIRE_FREE_SLOT=0` (also providers without
a free slot), or the song is left for the next run.

**Wrong songs are planned/downloaded.**
Raise `MATCH_THRESHOLD` (stricter). Cross-check in the dry run first.

**Nothing happens / 0 songs.**
Check whether `MISSING_FILES` points to the right file and that it contains
entries. The path is the **container path** (`/charts/missing.txt`), not the
NAS path.

**Downloads start, but nothing lands in the library.**
That is charts-import's job, not charts-download's. charts-download only starts
the download in slskd; the sorting-in is done by charts-import.

---

## FAQ

**Does charts-download replace the charts-import project?**
No. charts-download only replaces the **manual search/download** in slskd.
charts-import still sorts the downloaded files into the library and cleans up
the Charts folder.

**Are compilations preferred or avoided?**
Always avoided. By default (`REJECT_COMPILATIONS=0`), compilation/sampler hits
are only sorted to the back and only downloaded as a last resort if there is no
single/album version. With `REJECT_COMPILATIONS=1` they are **completely
rejected** – the song then stays "missing" until a real single/album version
appears (recommended if you only want clean singles in the library). The
detection is a heuristic based on keywords in the path (`VA -`, `Various`,
`Varios`, `Charts`, `Hits`, `Best Of`, `Mastermix`, `Week`, `New Music Friday`
etc.), since slskd delivers no metadata.

**How do I exclude an artist completely?**
See [Excluding artists globally](#excluding-artists-globally).

**Lyrics videos, remixes or live versions are downloaded. How do I prevent that?**
That is already prevented by default: files with keywords like `lyrics`,
`remix`, `live`, `acapella`, `sped up` etc. in the name are discarded completely
(`EXCLUDE_KEYWORDS`). Only original versions are downloaded. If a pattern is
missing anyway, add it in `EXCLUDE_KEYWORDS`. A song for which only such versions
exist then stays in the `missing.txt` (deliberately: better no song than a bad
version).

**Why only MP3 and no FLAC?**
Configured that way (`MIN_BITRATE=256`, MP3 only). Whoever wants FLAC would have
to extend the selection logic in `slsk.py`; currently non-MP3 files are
deliberately ignored.

**Why a maximum of 10 per run?**
Deliberately conservative (`MAX_PER_RUN`), so that not dozens of downloads start
at once and you can observe the hit quality in peace. The rest follows in the
next runs. Raise the value as you like.

**Does it download the same song twice?**
No. Requested songs are noted in the state file and skipped (only in the real
run, not in the dry run).

**What happens if a download fails in slskd (Aborted/Errored)?**
At the start of each run, charts-download automatically checks the status of all
requested downloads. Failed ones (`Aborted`, `Errored`, `TimedOut`, `Rejected`,
`Cancelled`) are released from the state **and** cleared away in slskd (the red
entry disappears). In the same run the song is then searched for again and
downloaded from another provider. Running downloads (`InProgress`, `Queued`)
stay untouched.

**Is this legal?**
`charts-download` only controls slskd. For the legal assessment of downloading
copyrighted music you are responsible yourself (see the notes in the slskd docs
and the charts-sync README).
