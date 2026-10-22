> 🇬🇧 English version. Deutsche Version: [README.md](README.md)

# charts-sync

Automatically keeps a **charts playlist** in Navidrome up to date with the
current chart hits – based on your **already existing** music collection. By
default it fetches Deezer's **global Top 100** and **Swiss Top 100** and merges
them into **one** deduplicated playlist (**Aktuelle Hits & Charts 🔥🎶**).

The service periodically fetches the current charts from **Deezer** (open API,
no account, no API key), matches them against your music library and writes the
`.m3u` playlist where Navidrome sees it (`/volume2/music/Music`). Navidrome
reads it in automatically as a playlist. Songs that are not (yet) in the library
are logged in a `missing.txt`.

You can define any number of playlists, and each one can bundle several chart
sources (see [Configuration](#konfiguration)).

---

## Charts automation – overview

`charts-sync` is part of a chain of three services that together fully automate
the chart workflow. Each runs as its own container with its own interval and
its own `.env`. They are coupled exclusively through shared folders
(`/volume2/music/Charts`, `/volume2/music/Music`). The family is rounded out by
[`charts-songrequest`](../charts-songrequest), an optional web UI for
**manually** requesting individual songs (uses the same pipeline via the
manifest).

Sister projects: [`charts-download`](../charts-download) · [`charts-import`](../charts-import) · [`charts-songrequest`](../charts-songrequest)

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
│  slskd                                      (runs permanently)     │
│     │  Downloads the songs from other Soulseek users              │
│     │  Songs land in /volume2/music/Charts/                        │
│     │  Shares ~500 chart downloads + ~400 SeedShare songs          │
│     ▼                                                               │
│  ③ charts-import                            (every 30 minutes)     │
│     │  Scans /volume2/music/Charts/ for finished downloads         │
│     │  Waits MIN_AGE_SECONDS (120s) (no half files)                │
│     │  Reads tags → copies to Music/<Artist>/<Album>/<Track>       │
│     │  Cleans up: KEEP_LATEST=500 (keep newest, rest removed)     │
│     │  Pushover: report imported songs                             │
│     ▼                                                               │
│  Navidrome                                  (scans periodically)   │
│     │  Sees the new songs on the next scan                         │
│     ▼                                                               │
│  ① charts-sync (resync trigger, ~1 min)                            │
│     charts-import reports "new songs here" → charts-sync runs       │
│     at once → song drops from missing.txt, enters the playlist     │
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
| **charts-recommend** | Grows the library from the listening taste of multiple ListenBrainz users (download-only, no playlists) | 24 h | `RECOMMEND_LISTENBRAINZ_USERS`, `LASTFM_ENABLED`, `WRITE_MISSING` |
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
- [How it works](#how-it-works)
- [Project structure](#project-structure)
- [Architecture & modules](#architecture--modules)
- [Matching in detail](#matching-in-detail)
- [Installation on the Synology NAS](#installation-on-the-synology-nas)
- [Navidrome: importing the playlist](#navidrome-importing-the-playlist)
- [Configuration](#configuration)
- [Directory layout](#directory-layout)
- [Operation & maintenance](#operation--maintenance)
- [Output files](#output-files)
- [Troubleshooting](#troubleshooting)
- [FAQ](#faq)

---

## Overview

| Property               | Value |
|------------------------|------|
| Chart source           | Deezer (open REST API, no account/key) + optional Last.fm (API key) |
| Playlists              | Default: 2 (`charts.m3u` = Global + Switzerland + DE + UK, `mundart.m3u`); freely configurable. Decades (80s/90s etc.) now run through charts-flashback. |
| Base image             | `python:3.12-slim` |
| Dependencies           | `httpx`, `mutagen` |
| Operating mode         | Endless loop with a configurable interval |
| Writes                 | one `.m3u` per playlist (in `Music/`) + a `missing` file (in `Charts/`) |
| Runs as                | `youruser:users` (PUID 1027 / PGID 100), like Navidrome |
| Downloads audio files? | **No** (metadata only) |
| Notifications          | Pushover (optional, when the matches or the missing songs change) |

---

## How it works

```
Deezer Global Top 100  ┐
Deezer Switzerland (all) ┘► Merge + Dedup ─► charts.m3u    (metadata)
Deezer Swiss Mundart ─────────────────────► mundart.m3u
        │
        ▼
  Library match     ◄── /volume2/music/Music  (your existing collection)
        │
        ├──► /volume2/music/Music/<name>.m3u          (Navidrome playlist per list)
        └──► /volume2/music/Charts/missing*.txt       (missing songs per list)
```

A sync run:

1. **Index the library** – scan `/volume2/music/Music` recursively once
   (the expensive part, reused for all playlists).
2. For **each** configured playlist:
   - **Fetch charts** – load the playlist's feeds from Deezer and merge them
     into a deduplicated list (identical songs count only once).
   - **Match** – compare each chart track against the index.
   - **Write playlist** – matches as an `.m3u`.
   - **Write missing list** – songs not found as a `missing` file.
3. **Notify** – optional Pushover message, by default only when something has
   changed compared to the previous run.

Afterwards the service waits `SYNC_INTERVAL_HOURS` hours and repeats the run.

---

## Project structure

```
charts-sync/
├── app/
│   ├── __init__.py       # package marker
│   ├── config.py         # configuration from environment variables
│   ├── deezer.py         # Deezer chart client + feed dispatcher + genre filter
│   ├── lastfm.py         # Last.fm client (chart/geo/tag/user + genre tags)
│   ├── library.py        # library scan + matching (mutagen + difflib)
│   ├── playlist.py       # writes the .m3u playlists and missing files
│   ├── notify.py         # optional Pushover notification
│   └── main.py           # scheduler loop, ties all modules together
├── Dockerfile            # python:3.12-slim, starts app.main
├── compose.yml           # service definition for the NAS
├── requirements.txt      # httpx, mutagen
├── .env.example          # template for the configuration
├── .gitignore
└── README.md             # this file
```

---

## Architecture & modules

The service is deliberately split into small, testable modules. Each module has
a clearly delimited responsibility.

### `config.py`

Reads all settings from environment variables. Parses the `CHARTS` list
(JSON) into `ChartSource` objects with `Feed` objects per source (Deezer or
Last.fm, with an `enabled` switch); if `CHARTS` is not set, the default
playlists apply (charts.m3u = Global + Switzerland, mundart.m3u).
It also reads the optional `exclude_artists` per playlist (per-playlist artist
exclusion). It additionally contains the genre filter settings (`GENRE_FILTER`,
`EXCLUDE_TAGS` …), helpers for robust parsing and the list of file extensions
recognized as music (`AUDIO_EXTENSIONS`).

### `deezer.py`

Encapsulates access to the Deezer API **and** is the feed dispatcher.
`fetch_chart(chart_source)` loads all **active** feeds of a playlist – per feed
either Deezer (`/chart/<id>/tracks` or `/playlist/<id>/tracks`) or, for
`type: lastfm`, delegates to `lastfm.py` – merges them deduplicated and
optionally applies the genre filter. Returns `ChartTrack` objects (position,
artist, title). API errors (Deezer sometimes reports them with HTTP 200 and an
`error` field) are detected; a single failed feed does not abort the run.

### `lastfm.py`

Client for the Last.fm web API (AudioScrobbler 2.0). `fetch_feed(...)` serves
`chart/geo/tag/user.getTopTracks` and returns the same `ChartTrack` objects as
the Deezer client (source-independent matching). `get_top_tags(...)` fetches a
song's genre tags for the genre filter (fail-open on errors). Needs
`LASTFM_API_KEY`.

### `library.py`

The heart of the matching. Two responsibilities:

- **Indexing** (`build_index`): scans `MUSIC_DIR` recursively, reading
  artist/title per file preferably from the tags (`mutagen`), otherwise from the
  path.
- **Matching** (`Library.find_match`): finds the best library match for a chart
  track via a fuzzy text comparison (details below).

The `CHARTS_DIR` folder is excluded during the scan so that files stored there
do not show up as a source.

### `playlist.py`

Writes the two output files **atomically** (first into a `.tmp` file, then
rename) so that Navidrome never sees a half-written file:

- `write_playlist(matched, file_name, display_title)` produces an extended
  M3U (`#EXTM3U`, `#PLAYLIST:`, `#EXTINF:`) with paths **relative** to the
  music root directory – just as Navidrome expects them.
- `write_missing(missing, file_name)` writes the commented missing list.

### Playlist order (`PLAYLIST_ORDER`)

Controls the order in which songs are written into the `.m3u`:

- `chart` (default): by **chart position** (position 1 at the top) – just as the
  charts actually look.
- `newest-first`: the songs that **most recently** entered the playlist go on
  top, followed by the older ones. Intended for players like Substreamer, where
  you want to see the new additions at the top immediately on opening, instead
  of always the same chart-position-1 songs. Since the playlist is fed from
  multiple sources (Deezer + Last.fm) and constantly changes, this is often more
  practical.

For `newest-first`, charts-sync remembers for each song the date of its **first
appearance** (`first_seen` per song in the state `.charts-sync-state.json`).
Known songs keep their date, new ones get the current run's timestamp – so new
additions move to the top and the order stays stable across runs. With an equal
date (e.g. on the very first run) the chart position decides. If a song drops
out of the charts and comes back later, it rightly counts as new again (the
`first_seen` entry is discarded for songs no longer included).

### `notify.py`

Optional Pushover sending. Only becomes active if `PUSHOVER_TOKEN` and
`PUSHOVER_USER` are set, otherwise a silent no-op. Errors while sending are
logged but do not abort the run.

### `main.py`

The entry point (`python -m app.main`). `run_sync` indexes the library once and
then processes each chart source individually (`_sync_source`). It performs
dedup (the same song only once per playlist) and manages change detection via a
state file (`.charts-sync-state.json`) so Pushover only reports on real
changes. Both new or dropped playlist matches and changes to the list of
missing songs count as a change. The loop repeats the run at the configured
interval and catches errors of individual runs/sources so the service keeps
running permanently. In addition, the loop checks every `RESYNC_CHECK_SECONDS`
(default 60 s) whether `charts-import` has left a **resync signal**
(`.resync-requested` in `CHARTS_DIR`), and then triggers a run immediately. That
way freshly downloaded songs land in the playlist almost instantly, without
having to wait for the regular 12 h interval. The check is only a file-existence
query (practically free); an actual run only happens when the signal is there.

---

## Matching in detail

The goal is to robustly match Deezer metadata against the real file/tag names
in your library – despite spelling variants and additions.

**Step 1 – Normalization** (`normalize`): before each comparison, artist and
title are unified:

- lowercasing
- removing accents (`é` → `e`)
- removing additions: `(feat. …)`, `(ft. …)`, `(with …)`, `(Remastered)`,
  `(… Version)`, `(… Edit)`, `(Official …)`, `(Audio)`, `(Video)` and others
- unifying `&` and `and`
- turning everything non-alphanumeric into spaces

**Step 2 – Similarity** (`similarity`): comparison of two normalized strings via
`difflib.SequenceMatcher`, result as a value 0–100.

**Step 3 – Artist minimum threshold**: the artist score must reach at least
`ARTIST_MIN_SCORE` (default `60`), otherwise it is **not** a match, no matter
how well the title fits. This prevents the same title by a different artist from
matching incorrectly (e.g. chart "BLACKPINK - Jump" would otherwise match
"Van Halen - Jump"). Artist **and** title must therefore both fit.

**Step 4 – Weighted score**: if the artist and title tags are present, the
**title counts 60 %** and the **artist 40 %**. If tags are missing, the
comparison is against a combined "artist + title" string.

**Step 5 – Threshold**: if the best score is ≥ `MATCH_THRESHOLD` (default
`87`), the track counts as present and goes into the playlist. Otherwise it ends
up in that source's `missing-*.txt`.

**Tag reading vs. path fallback**: artist/title come preferably from the file
tags. If they are missing, the service guesses from the path by the pattern
`.../<Artist>/<Album>/<NN - Title>.<ext>` and removes leading track numbers as
well as a possible `Artist - Title` in the file name.

**Step 6 – Hiding compilations** (optional, default on): if
`IGNORE_COMPILATIONS=1`, library tracks that come from a compilation or a
sampler are **skipped** during matching. This is detected via markers in the
path, in the album or AlbumArtist tag (`Various Artists`, `Bravo Hits`,
`Top 100`, `Greatest Hits`, `Sampler` and others); the most reliable signal is
the AlbumArtist `Various Artists`. That way a chart song you only have as part
of a compilation counts as **missing** and gets re-fetched by `charts-download`
as a clean single/album version. During index building the log records how many
tracks are detected as compilations and ignored. With `IGNORE_COMPILATIONS=0`
the compilation version counts as present too.

---

## Installation on the Synology NAS

charts-sync is **not** installed on its own. It is part of the **charts-stack**,
which runs as ONE project: one `compose.yml` + one central `.env` in the
`charts-stack` folder (e.g. `/path/to/charts-stack/`). The full
installation (copy the folder, set up slskd, fill in `.env`, Container Manager /
`docker compose up -d --build`) is in the **[main README](../README.en.md)**.

To build/start just this service – from the `charts-stack` folder:

```bash
docker compose up -d --build charts-sync
```

Logs for this service:

```bash
docker compose logs -f charts-sync
```

On the first run you see `TREFFER` (match) or `FEHLT` (missing) for each chart
song, followed by a summary like `X von Y Chart-Tracks in Library gefunden`
(X of Y chart tracks found in library).

---

## Navidrome: importing the playlist

Navidrome automatically imports `.m3u` files from the music library as
playlists. Two things ensure it works:

- **Auto-import active:** in Navidrome, `ND_PLAYLISTSPATH` is set to the library
  by default, and the Charts folder lies within it. If you have restricted the
  path, add `Charts` or set `ND_PLAYLISTSPATH` so that `/music/Charts` is
  covered.
- **Scan:** Navidrome detects the playlist on the next library scan. With
  `ND_SCANSCHEDULE` (e.g. `1h`) or a manual "Scan" in the Navidrome UI it
  becomes visible.

The playlist appears in Navidrome under its title (default **Aktuelle Hits
& Charts 🔥🎶**).

> **Important – same library:** an M3U playlist in Navidrome can only reference
> songs that lie in the **same library** as the playlist file. If your songs
> are in the library `/Music`, the `.m3u` must lie there too (not in a separate
> `/Charts` library). That is why `PLAYLIST_DIR=/music/Music` and
> `MUSIC_DIR=/music/Music`, so that the song paths are correct relative to this
> root. The `missing-*.txt`, on the other hand, may lie in `/Charts`; they are
> not resolved by Navidrome.

### Why `.m3u` and not `.nsp`?

Navidrome knows two playlist formats: `.m3u` (fixed song list) and `.nsp`
(smart playlist, **rule-based**). For charts we need a fixed, hand-picked list
of concrete songs, which is exactly the `.m3u` use case. `.nsp`, on the other
hand, describes criteria ("all songs with rating ≥ 4") and cannot cleanly
represent a fixed title list. That is why `charts-sync` writes an `.m3u`.
Navidrome imports both formats equally and automatically.

---

## Configuration

The **chart sources** (which playlists are created) are defined in the
`compose.yml` via the `CHARTS` variable as a JSON list. All other settings come
from the `.env` or the compose defaults.

### Chart sources (`CHARTS`)

`CHARTS` is a JSON list. Each entry produces exactly **one** playlist and can
bundle **several** Deezer sources (`sources`), which are merged into a
deduplicated list. The default is **two** playlists:

> 📖 **Full field and ID reference + ready-made examples** (country, 80s/90s,
> …): [`CHARTS-SOURCES.en.md`](../CHARTS-SOURCES.en.md) in the repo root
> ([Deutsch](../CHARTS-SOURCES.md)). New source IDs are easy to find in the
> **"source finder"** in [charts-config](../charts-config) (port 5058).
>
> **Config from a file instead of env (recommended for many playlists):**
> Instead of the single-line `CHARTS` variable, the definition can be loaded from
> a readable, multi-line file – set `CHARTS_FILE=/config/charts.json` and create
> `charts.json` (template: [`charts.json.example`](charts.json.example) with
> Pop/Country/Schlager). Priority: `CHARTS_FILE` > `CHARTS` > defaults.
>
> **Startup validation:** charts-sync checks the `CHARTS` list on start. Broken
> JSON or unusable sources (e.g. an unknown Last.fm method) are **named in the
> log** and the affected source is disabled – the service **keeps running** (it
> falls back to the code defaults if needed).

```json
[
  {
    "sources": [
      {"type": "chart",    "source": "0",          "limit": 100},
      {"type": "playlist", "source": "1313617925", "limit": "all"}
    ],
    "name": "charts.m3u",
    "title": "Aktuelle Hits & Charts 🔥🎶",
    "missing": "missing.txt"
  },
  {
    "sources": [
      {"type": "playlist", "source": "658561775", "limit": "all"}
    ],
    "name": "mundart.m3u",
    "title": "Schweizer Mundart 🇨🇭",
    "missing": "missing-mundart.txt",
    "exclude_artists": ["Nemo", "Trauffer"]
  }
]
```

| Field             | Meaning |
|-------------------|-----------|
| `sources`         | List of sources to merge (Deezer **and/or** Last.fm). |
| `sources[].service`| Optional, recommended: `deezer`, `lastfm` or `energy` – makes the source unambiguous. **Backward-compatible:** if `service` is missing it is derived from `type`/`method` (old config without `service` behaves exactly as before). An explicit `service` overrides the heuristic (a conflict triggers a warning). |
| `sources[].type`  | Deezer: `chart` (chart ID, `0` = global) or `playlist` (playlist ID). Last.fm: `lastfm`. |
| `sources[].source`| Deezer: the ID as a string. |
| `sources[].limit` | Optional; max. tracks **for exactly this source** (`"all"` or a number). If missing, the entry's `limit` applies, otherwise `CHART_LIMIT`. |
| `sources[].enabled`| Optional (default `true`). `false` = the source is skipped without deleting it. This lets you, for example, switch off all Deezer feeds and enable only Last.fm (or vice versa). |
| `sources[].method`| Last.fm only: `chart.getTopTracks`, `geo.getTopTracks`, `tag.getTopTracks`, `user.getTopTracks`. |
| `sources[].country` / `.tag` / `.user` / `.period` | Last.fm only: the parameter matching the method (e.g. `"country": "Switzerland"`, `"tag": "80s"`, `"user": "deinname"`, `"period": "3month"`). |
| `sources[].theme` | `energy` only: the handoff-file theme key that `charts-energy` writes (e.g. `"mundart"`). |
| `name`            | File name of the `.m3u` (lands in `PLAYLIST_DIR`). |
| `title`           | Display name in Navidrome (emojis allowed, `{date}` = current date). |
| `missing`         | File name of the missing list (lands in `CHARTS_DIR`). |
| `limit`           | Optional; entry default for sources without their own `limit`. |
| `exclude_artists` | Optional; artists kept out of **exactly this** playlist (list or comma-separated string). See below. |

> **`energy` feed (handoff from charts-energy):** besides `deezer`/`lastfm` there
> is the feed kind `energy`. Such a source (`{"service":"energy","theme":"…"}`)
> reads **no** Deezer/Last.fm API; it reads the handoff JSON that
> [`charts-energy`](../charts-energy) writes per theme in `feed` mode
> (`CHARTS_DIR/.charts-energy-feed-<theme>.json`) and feeds its tracks into an
> **existing** playlist/theme (e.g. `mundart`). This is how a channel's NRJ
> detections land in the normal charts-sync playlist. A handoff file that is too
> old (> `ENERGY_FEED_MAX_AGE_HOURS`, default 48 h) is treated as empty (one log
> warning) so a dead collector never keeps stale tracks.

**Per-source limit:** each source can have its own `limit`. This way the global
chart is restricted to the real Top 100 (the longtail at positions 100–300 is
mostly international and unknown in Switzerland), while the Switzerland and the
Mundart playlists are fetched completely (`"all"`).

**Merge behavior:** the feeds are loaded in order and merged into one list. A
song that appears in several feeds is only taken over **once** (first occurrence
wins). The positions are reassigned consecutively after the merge.

Calculation example `charts.m3u` (Global Top 100 + Switzerland complete):

- Global delivers ~100, the Switzerland chart ~99 raw entries.
- **Duplicate songs are removed.** The Switzerland chart is anyway ~98 % within
  the global Top 300 and ~64 % within the global Top 100.
- Result: **about ~135 unique songs** – the worldwide top hits plus the
  Switzerland-typical ones, without the global longtail.

`mundart.m3u` contains the ~140 songs of the Deezer playlist "Swiss Mundart
Hits". Note: many pure Mundart titles cannot be found via slskd in sufficient
quality (≥256 kbps) and therefore first land in `missing-mundart.txt`; every run
tries to find them again.

The decades (80s/90s etc.) no longer run through charts-sync. Instead of a
`80er-90er.m3u`, the backfill service **charts-flashback** (FLASHBACK FM) grows
the library; the decade display is handled by your Navidrome Smart Playlists
(NSP), which filter on the year tag.

In addition, each playlist only includes songs you **actually have in the
library**; all the others are listed in the respective `missing` file.

**Simplified schema:** an entry with only one source may use `type` + `source`
directly instead of `sources` (backward compatible).

**Further/separate playlists**: just add more `CHARTS` entries, each with its
own `sources`, `name`, `title` and `missing`. The matching Deezer playlist ID is
in the web link (`https://www.deezer.com/…/playlist/<ID>`); official country
charts can be found via the "Deezer Charts" account. Known IDs:
`1313617925` = "Top Switzerland", `658561775` = "Swiss Mundart Hits".

### Last.fm as a second source (optional)

Besides Deezer, charts-sync can fetch chart/tag/country lists from **Last.fm**
(listener-/scrobble-based instead of streaming-based). For that you need a free
API key from <https://www.last.fm/api/account/create> and enter it as
`LASTFM_API_KEY` in the `.env`. Last.fm delivers – like Deezer – only metadata
(artist + title), no audio data.

A Last.fm feed looks like this (examples):

```json
{"type": "lastfm", "method": "chart.getTopTracks", "limit": 100, "enabled": true}
{"type": "lastfm", "method": "geo.getTopTracks", "country": "Switzerland", "limit": 100, "enabled": true}
{"type": "lastfm", "method": "tag.getTopTracks", "tag": "80s", "limit": 150, "enabled": true}
{"type": "lastfm", "method": "tag.getTopTracks", "tag": "90s", "limit": 150, "enabled": true}
{"type": "lastfm", "method": "user.getTopTracks", "user": "deinname", "period": "3month", "limit": 100, "enabled": true}
```

- `chart.getTopTracks` – global top tracks on Last.fm.
- `geo.getTopTracks` + `country` – country-based (e.g. `Switzerland`).
- `tag.getTopTracks` + `tag` – the most-tagged tracks of a tag (e.g. `80s`,
  `90s`, `pop`). Good for decade-/genre-based lists.
- `user.getTopTracks` + `user` (+ optional `period`: `7day`, `1month`,
  `3month`, `6month`, `12month`, `overall`) – **your** most-played tracks.

**On/off per source:** in the default charts, Last.fm feeds are already included
as a template, but with `"enabled": false`. To try them out, just set them to
`true`. Conversely, you can set the Deezer feeds to `false` and run only
Last.fm. Deezer and Last.fm feeds in the same playlist are merged quite normally
and deduplicated via artist+title.

### Genre filter (optional, pop only – no country/Schlager)

Neither Deezer nor Last.fm delivers a clean "pop only" chart; the official
charts are genre-mixed (pop, hip hop, country, Schlager …). The filter is
**opt-in** (default `GENRE_FILTER=0`). With `GENRE_FILTER=1`, charts-sync checks
each chart track's **Last.fm genre tags** and throws out tracks whose dominant
tag is on the exclusion list (`EXCLUDE_TAGS`, default includes `country`,
`schlager`, `volksmusik`, `folk`, `classical`; deliberately **without** hard
rock/metal, so rock classics stay).

- **Controllable per playlist:** the optional `genre_filter` field
  (`true`/`false`) on a CHARTS entry overrides the global `GENRE_FILTER`; if it
  is absent, the global default applies (precedence: explicit per-playlist >
  global). This lets you enable the filter only for dynamic chart playlists and
  leave artist/Mundart playlists untouched. Schema/example see CHARTS-SOURCES.md.
- Needs `LASTFM_API_KEY` (even when the sources themselves come from Deezer).
- **Fail-open:** if Last.fm returns no tags for a song, it stays in – rather one
  foreign genre too many than a pop hit wrongly removed.
- A track is thrown out if one of its strongest `EXCLUDE_TAG_TOP_N` tags is in
  `EXCLUDE_TAGS` **and** its count is `>= EXCLUDE_TAG_MIN_COUNT` (Last.fm tag
  counts range 0–100).
- It costs one additional API call per track (with a pause
  `GENRE_FILTER_SLEEP_MS`), so it makes the run a bit slower.

Example: "Shaboozey – A Bar Song (Tipsy)" has the dominant tag `country`
(count 100) → gets filtered. "Sabrina Carpenter – Espresso" has `pop` (100) →
stays in.

**Tag matching:** word-based. The exclusion tag `country` therefore also hits
`contemporary country`, `modern country`, `country rap` etc. Multi-word tags
(e.g. `hard rock`) are checked as a phrase. `hard rock` and `metal` are
deliberately **not** in the default list, so that rock classics (AC/DC, Bon
Jovi, Guns N' Roses, Europe) stay in the charts.

**Checking what was filtered.** Every exclusion is logged. So after a run you
can see which songs (with which tag) were thrown out:

```bash
# All exclusion decisions of the last run (deduplicated):
sudo docker logs charts-sync 2>&1 | grep "Genre-Filter:" | sort -u
# Only the summary per playlist ("X von Y ausgeschlossen"):
sudo docker logs charts-sync 2>&1 | grep "ausgeschlossen"
```

In addition, the number of filtered songs is shown in the Pushover message
("🚫 X Fremd-Genre gefiltert" (X foreign genre filtered)). If a song is wrongly
thrown out (or country
slips through), adjust `EXCLUDE_TAGS` / `EXCLUDE_TAG_MIN_COUNT`.

### Per-playlist artist exclusion (`exclude_artists`)

Some artists cannot be cleanly filtered out by genre, because the APIs list them
e.g. as "pop" – such as Swiss Mundart Schlager like **Trauffer**. For that there
is the optional field `exclude_artists` per playlist, directly in the `CHARTS`
JSON. It only affects **this one playlist**: an artist you ban from the Mundart
playlist stays in a future folk or Schlager playlist.

```json
{
  "sources": [{"type": "playlist", "source": "658561775", "limit": "all"}],
  "name": "mundart.m3u",
  "title": "Schweizer Mundart 🇨🇭",
  "missing": "missing-mundart.txt",
  "exclude_artists": ["Nemo", "Trauffer"]
}
```

- Spelling as a **list** (`["Nemo", "Trauffer"]`) or as a comma-separated
  **string** (`"Nemo, Trauffer"`).
- Case-insensitive. Hits the whole name **or** a feat. partner: `"Trauffer"`
  also filters `Trauffer feat. Beatrice Egli`.
- **No API call, no Last.fm key needed** – the comparison happens locally during
  the merge and takes effect immediately on the next sync.
- Independent of the genre filter: also applies with `GENRE_FILTER=0`.
- A change only to the `compose.yml` (not to the code) → `docker compose up -d`
  is enough, **no** rebuild.

Every exclusion is logged:

```bash
# Which songs were removed via exclude_artists:
sudo docker logs charts-sync 2>&1 | grep "Artist-Ausschluss"
# Summary per playlist:
sudo docker logs charts-sync 2>&1 | grep "per exclude_artists entfernt"
```

Note: `exclude_artists` prevents an artist from being **newly** written into the
playlist. Already present entries disappear automatically on the next sync,
because the `.m3u` is rewritten on every run (the music files in the library
stay untouched).

### Further settings (`.env`)

| Variable               | Default         | Meaning |
|------------------------|-----------------|-----------|
| `CHART_LIMIT`          | `all`           | Fallback limit per source if it has no own `limit` (`all` or a number). |
| `LASTFM_API_KEY`       | *(empty)*       | Last.fm API key (from last.fm/api/account/create). Only needed for `lastfm` feeds or the genre filter. |
| `GENRE_FILTER`         | `0`             | **Opt-in.** `1` = filter foreign genres (country/Schlager/folk …) out of the charts via Last.fm tags. Needs `LASTFM_API_KEY`. Overridable per playlist via the `genre_filter` field (see CHARTS-SOURCES.md). |
| `EXCLUDE_TAGS`         | *(list)*        | Tags to exclude (comma-separated). Empty = default (`country, schlager, volksmusik, folk, americana, bluegrass, classical`). Deliberately **without** hard rock/metal, so rock classics (AC/DC, Bon Jovi …) stay in the charts. |
| `EXCLUDE_TAG_TOP_N`    | `3`             | How many of a song's strongest tags are checked. |
| `EXCLUDE_TAG_MIN_COUNT`| `60`            | Minimum count (0–100) of a tag for it to count as "dominant". 60 keeps troll/niche tags out. |
| `GENRE_FILTER_SLEEP_MS`| `200`           | Pause between the genre queries (ms), easy on the Last.fm API. |
| `MUSIC_DIR`            | `/music/Music`  | Navidrome library root. Song paths are written relative to it. |
| `PLAYLIST_DIR`         | = `MUSIC_DIR`   | Target folder of the `.m3u` playlists (must be scanned by Navidrome). |
| `CHARTS_DIR`           | `/music/Charts` | Storage of the `missing-*.txt` and the state file. |
| `SYNC_INTERVAL_HOURS`  | `12`            | Hours between two runs. |
| `RUN_ON_START`         | `1`             | Sync immediately on start. |
| `RESYNC_CHECK_SECONDS` | `60`            | How often it is checked whether charts-import triggered a resync (just a file check, cheap). |
| `MATCH_THRESHOLD`      | `87`            | Similarity threshold 0–100 (higher = stricter). |
| `ARTIST_MIN_SCORE`     | `60`            | Minimum similarity of the artist, otherwise no match (prevents wrong title namesakes like "Van Halen - Jump"). |
| `IGNORE_COMPILATIONS`  | `1`             | Ignore compilation tracks (Various Artists, Bravo Hits, Top 100 …) during matching, so the single version gets re-fetched. `0` = count them in. |
| `PUID` / `PGID`        | `1027` / `100`  | User/group (youruser:users, like Navidrome). |
| `TZ`                   | `Europe/Zurich` | Time zone. |
| `PUSHOVER_TOKEN`       | *(empty)*       | Optional: Pushover app token. |
| `PUSHOVER_USER`        | *(empty)*       | Optional: Pushover user key. |
| `PUSHOVER_DEVICE`      | *(empty)*       | Optional: target device. Multiple devices possible, comma-separated (e.g. `iPhone-Max,Tablet-Anna`) — one message is sent per device. All devices belong to the same Pushover account. Empty field = delivery to all devices of the account. Invalid/disabled device names are stripped before sending — otherwise Pushover delivers an unknown name to **all** devices instead (double push). |
| `PUSHOVER_SOUND`       | `falling`       | Notification sound for this service. Valid Pushover sounds: `pushover, magic, cosmic, intermission, bike, bugle, cashregister, classical, falling, gamelan, incoming, mechanical, pianobar, siren, spacealarm, tugboat, alien, climb, persistent, echo, updown, none` (= silent), `vibrate`. Empty = account default (no `sound` field). Overridable per service via `PUSHOVER_SOUND_SYNC` in `compose.yml`. |
| `PUSHOVER_DEVICES_TTL` | `3600`          | Optional: cache duration (seconds) for the account device list used for filtering. |
| `NOTIFY_ONLY_ON_CHANGE`| `1`             | Only notify when something has changed: playlist matches **or** the list of missing songs. `0` = after every run. |
| `NOTIFY_MISSING_LIMIT` | `40`            | Max. number of missing songs in the Pushover message (to the phone, without a NAS login). `0` = only the count. |

### Global, Switzerland and Mundart

- **Global** (`chart:0`, limit `100`): worldwide Deezer top tracks, very
  international (US/UK/K-pop/Latin). Restricted to the real Top 100, because the
  longtail (positions 100–300) mostly contains songs that hardly anyone in
  Switzerland listens to.
- **Switzerland** (`playlist:1313617925`, limit `all`): official Deezer playlist
  "Top Switzerland", closer to the Swiss hit parade.
- **Mundart** (`playlist:658561775`, limit `all`): curated collection
  "Swiss Mundart Hits" (Bligg, Trauffer, Gölä, Patent Ochsner …). Lands in a
  **separate** `mundart.m3u`, so the hits playlist stays focused.

> **Decades (80s/90s etc.)** no longer run through charts-sync. They now come
> from the backfill service **charts-flashback** (FLASHBACK FM), which grows the
> library; they are displayed via your Navidrome Smart Playlists (NSP), which
> filter on the year tag.

The global and the Swiss chart overlap heavily: ~98 % of the Swiss titles are
also in the global Top 300, ~64 % in the global Top 100. That is why Global
(Top 100) and Switzerland (complete) are merged into **one** `charts.m3u` – the
international top hits plus the Switzerland-typical ones, without the global
longtail and without duplicates.

In addition, the official Deezer country charts **Top Germany**
(`1111143121`) and **Top UK** (`1111142221`, each limited to 100) feed in.
Reason: the global chart is heavily US-/Latin-/K-pop-weighted; DE and UK bring
the Western/Central European pop that better matches everyday taste here. Both
come from the official "Deezer Charts" account and are updated daily. Through the
merge/dedup, `charts.m3u` grows moderately as a result (the big hits overlap
with Global/CH anyway).

---

## Directory layout

### On the host (NAS)

| Path                            | Purpose |
|---------------------------------|-------|
| `/path/to/charts-stack` | Stack project directory (compose/build, all services). |
| `/volume2/music/Music`          | Music library + storage of the `.m3u` playlists. |
| `/volume2/music/Charts`         | Storage of the `missing-*.txt` + state file. |

### In the container

| Host (NAS)        | Container   | Purpose |
|-------------------|-------------|-------|
| `/volume2/music`  | `/music`    | the whole music share (read + write in subfolders). |

Concretely: `MUSIC_DIR`/`PLAYLIST_DIR` = `/music/Music`, `CHARTS_DIR` =
`/music/Charts`. The service reads the library and writes playlists to `Music/`
and missing lists/state to `Charts/`. It runs as `youruser:users` (PUID 1027 /
PGID 100), so the files have the same owner as the music.

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

**Rebuild after code changes:**

```bash
sudo docker compose up -d --build
```

**Stop / remove:**

```bash
sudo docker compose down
```

---

## Output files

Per chart source, one `.m3u` is created (in `PLAYLIST_DIR`, i.e. `Music/`) and
one `missing-*.txt` (in `CHARTS_DIR`). In addition, `CHARTS_DIR` holds a hidden
state file (`.charts-sync-state.json`) for change detection.

**Example output (default configuration, 2 playlists):**

```
/volume2/music/Music/charts.m3u
/volume2/music/Music/mundart.m3u
/volume2/music/Charts/missing.txt
/volume2/music/Charts/missing-mundart.txt
/volume2/music/Charts/.charts-sync-state.json
```

### `.m3u` (`charts.m3u`)

Extended M3U with the present chart songs, sorted by position:

```
#EXTM3U
#PLAYLIST:Aktuelle Hits & Charts 🔥🎶
#EXTINF:-1,Tame Impala - Dracula (with JENNIE)
Various Artists/Bravo Hits 132 (2026)/CD 01/....mp3
...
```

The paths are relative to the music root directory (`Music/`) so Navidrome can
map them.

### `missing.txt`

Commented list of the chart songs that were not found in the library:

```
# Fehlende Chart-Songs (Stand 02.09.2026 11:37)
# ...
  3. Ella Langley - Choosin' Texas
  4. Ella Langley - Be Her
  5. Ariana Grande - hate that i made you love me
```

Obtain these titles legally and place them in the library – on the next run they
land automatically in the respective playlist.

---

## Troubleshooting

**The playlist does not appear / is empty in Navidrome.**
Most common cause: the `.m3u` lies in a **different library** than the songs.
An M3U in the `/Charts` library cannot reference songs from the `/Music`
library → it stays empty. Solution: place the playlist in `/Music`
(`PLAYLIST_DIR=/music/Music`). Check whether the `.m3u` lies under
`/volume2/music/Music/` (`sudo docker compose logs` shows "Playlist
geschrieben: …" (playlist written)) and then trigger a **Full Scan** in
Navidrome.

**Songs I have show up in `missing.txt`.**
The matching is too strict or the tags/file names deviate a lot. Lower
`MATCH_THRESHOLD` step by step (e.g. `85`, `82`) and watch the logs. The log
line `FEHLT … (bester Score N)` (missing, best score N) shows how close the
best match was.

**Wrong songs land in the playlist.**
Raise `MATCH_THRESHOLD` (e.g. `90`, `92`). If it concerns the **same title by a
different artist** (e.g. "Van Halen - Jump" instead of "BLACKPINK - Jump"), it
mainly helps to raise `ARTIST_MIN_SCORE` (e.g. `75`); then the artist has to
match more strongly.

**A song I only have on a compilation gets re-downloaded anyway.**
That is intentional: with `IGNORE_COMPILATIONS=1` (default), compilation tracks
are ignored during matching, so `charts-download` fetches the clean
single/album version. If you don't want that and the compilation version should
count as present, set `IGNORE_COMPILATIONS=0`.

**A regular song is wrongly detected as a compilation.**
The detection is a heuristic over path/tag markers. If that happens with an
album that has one of the keywords in its name, you can either set
`IGNORE_COMPILATIONS=0` (then nothing is filtered) or adjust the
album/AlbumArtist tag.

**"Konnte Charts nicht laden" (could not load charts).**
Usually a temporary network problem or a Deezer rate limit. The service tries
again at the next interval. Test reachability:

```bash
curl -s "https://api.deezer.com/chart/0/tracks?limit=3"
```

**Container starts, but the library index is empty.**
Check whether `/volume2/music` is mounted correctly and contains audio files
(supported extensions: mp3, flac, m4a, aac, ogg, opus, wma, wav, alac).

**Time stamps are wrong.**
Set `TZ` in the `.env` (e.g. `Europe/Zurich`).

---

## FAQ

**Do I need a Deezer account or API key?**
No. The chart/playlist endpoints used are public.

**Are songs downloaded?**
No. The service only processes metadata and your existing music. You have to
obtain missing titles yourself by legal means.

**How often does the playlist update?**
By default every 12 hours (`SYNC_INTERVAL_HOURS`). On start the sync runs
immediately if `RUN_ON_START=1`. Deezer's charts themselves usually update
weekly; an interval of 12–24 h is therefore completely sufficient.

**When does a Pushover notification come?**
With `NOTIFY_ONLY_ON_CHANGE=1` (default), always when something has changed
compared to the previous run, in **one of the two** areas:
- the playlist matches (a song newly found or dropped), or
- the list of missing songs (new chart entries you still lack).

That way you also get a message with the updated missing list even if your match
list has not changed at all. The message names the newly added songs and lists
the missing songs. With `NOTIFY_ONLY_ON_CHANGE=0` a message comes after every
run, regardless of changes.

**How do I get the list of missing songs onto my phone?**
The Pushover message lists the missing chart songs directly (numbered, artist -
title), so you can search for them in slskd on the go, without logging into the
NAS. Via `NOTIFY_MISSING_LIMIT` you control how many titles are listed (default
40). Since Pushover limits a message to 1024 characters, with very many missing
songs it is truncated and a pointer to the full `missing` file is appended.
`NOTIFY_MISSING_LIMIT=0` lists no titles, only the count.

**Can I run multiple chart playlists?**
Yes – that is the default. Just add more entries to the `CHARTS` list of the
`compose.yml` (each with its own `name`, `title`, `missing`).
