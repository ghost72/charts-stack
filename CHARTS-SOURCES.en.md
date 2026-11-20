> 🇬🇧 English version. Deutsch: [CHARTS-SOURCES.md](CHARTS-SOURCES.md)

# CHARTS sources – schema & examples

This document describes the `CHARTS` configuration of
[`charts-sync`](charts-sync): how to define **any number of playlists** and
compose each from **one or more sources** (Deezer, Last.fm, Apple Music or Energy). Everything is
configurable through a single environment variable – no code changes, no fixed
playlist names.

---

## 1. Introduction

`CHARTS` is an environment variable in your `.env` (or in the `environment:`
block of the `charts-sync` container). Its value is **a JSON array** of playlist
objects. Each object describes one `.m3u` playlist and the sources it is filled
from.

- A playlist can **bundle multiple sources**; the results are merged into **one**
  playlist and **deduplicated** by artist+title.
- The optional genre filter (`GENRE_FILTER=1`) keeps foreign genres out of the
  pop charts (details in the [charts-sync README](charts-sync/README.en.md)).
- `charts-sync` matches the chart tracks against your **existing** library.
  Missing songs are written to `missing-*.txt` and later fetched by
  `charts-download`.

> **Important:** In the `.env`, `CHARTS=` must be on a **single line**. The
> multi-line examples below are only for reading; every example also has a
> compact single-line form to copy.

### Alternative: define playlists in a file (`CHARTS_FILE`)

If you maintain several playlists with multiple sources each, the single-line
`CHARTS` variable gets unwieldy. `charts-sync` can therefore load the definition
from a **readable, multi-line JSON file** instead:

1. Create `charts-sync/charts.json` (template: `charts-sync/charts.json.example`,
   with ready-made Pop/Country/Schlager examples).
2. Set in `.env`: `CHARTS_FILE=/config/charts.json`

The file is mounted read-only to `/config/charts.json` via `compose.yml`.
**Priority:** `CHARTS_FILE` (if set, file exists and valid JSON) > `CHARTS` env >
built-in defaults. The schema is **identical** to the `CHARTS` env (same JSON
array), just nicely formatted and multi-line. If the file is broken (invalid
JSON), `charts-sync` falls back to the default playlists and logs a clear error —
it does **not** silently use the `CHARTS` env (so you notice the problem).

> **After every change**, restart the container so it reads the new config:
> ```bash
> docker compose up -d charts-sync   # or: docker restart charts-sync
> ```

If `CHARTS` is unset or broken (invalid JSON), `charts-sync` uses the built-in
default playlists and logs a clear error pointing to this document (see
[Troubleshooting](#7-troubleshooting)).

---

## 2. Full JSON schema

### Playlist object (ChartSource)

| Field | Required | Type | Meaning |
|-------|----------|------|---------|
| `name` | yes | string | File name of the `.m3u` (ends up in `PLAYLIST_DIR`) |
| `title` | no | string | Display name in Navidrome (emojis + `{date}` allowed); default = `name` |
| `missing` | no | string | File name of the missing list (in `CHARTS_DIR`); default `missing-<i>.txt` |
| `limit` | no | `"all"` or number | Default limit for sources without their own `limit`; default = `CHART_LIMIT` |
| `sources` | yes* | list of source objects | *or a single source inline on the object (backward compatible) |
| `exclude_artists` | no | list or CSV string | Artists removed **only** from this playlist |
| `genre_filter` | no | `true`/`false` | Per-playlist override of the global `GENRE_FILTER`; absent = global default |

### Source object (Feed) – common fields

| Field | Required | Type | Meaning |
|-------|----------|------|---------|
| `service` | no (recommended) | `"deezer"` / `"lastfm"` / `"apple"` / `"energy"` | Data source; if missing, it is derived from `type` (`type:"lastfm"` → Last.fm, otherwise Deezer) |
| `limit` | no | `"all"` or number | max. tracks for **this** source |
| `enabled` | no | bool | `false` = disable the source without deleting it; default `true` |

### `service` × `type`/`method` → required fields

| service | subtype | additional required field(s) | minimal source |
|---------|---------|-------------------------------|----------------|
| `deezer` | `type:"chart"` | `source` (chart ID, `0` = global) | `{"service":"deezer","type":"chart","source":"0"}` |
| `deezer` | `type:"playlist"` | `source` (playlist ID) | `{"service":"deezer","type":"playlist","source":"1313617925"}` |
| `deezer` | `type:"artist"` | `source` (artist ID) | `{"service":"deezer","type":"artist","source":"16529"}` |
| `apple` | `type:"chart"` (optional) | `country` (two-letter storefront code) | `{"service":"apple","country":"ch","limit":100}` |
| `lastfm` | `method:"chart.getTopTracks"` | – | `{"service":"lastfm","method":"chart.getTopTracks"}` |
| `lastfm` | `method:"geo.getTopTracks"` | `country` (English country name) | `{"service":"lastfm","method":"geo.getTopTracks","country":"Switzerland"}` |
| `lastfm` | `method:"tag.getTopTracks"` | `tag` | `{"service":"lastfm","method":"tag.getTopTracks","tag":"pop"}` |
| `lastfm` | `method:"user.getTopTracks"` | `user` (+ optional `period`) | `{"service":"lastfm","method":"user.getTopTracks","user":"rj","period":"3month"}` |

> **Note:** The Last.fm `method` is **case-insensitive** – the parser lowercases
> it internally. So `geo.getTopTracks` and `geo.gettoptracks` are equivalent.

Last.fm sources need the user's own `LASTFM_API_KEY`
(https://www.last.fm/api/account/create). According to Last.fm's announcement,
the key owner needs **Last.fm Pro from 1 December 2026**. Last.fm remains
optional; Deezer, Apple Music and Energy do not need this key.
A Deezer `source` accidentally set on a
Last.fm source is **ignored** (not passed to the API).

### Apple Music without an API key (optional)

`service:"apple"` reads the public [Apple RSS/JSON feed](https://rss.marketingtools.apple.com/)
of a country's most-played songs. `country` is, for example, `ch`, `de`,
`gb` or `us` (not a spelled-out country name as used by Last.fm).
`limit` is 1–100; `"all"` or the built-in all default means **at most 100**,
without pagination. An explicit numeric limit above 100, invalid country code
or unsupported `type` disables the feed with a warning.
HTTP failures and empty or malformed responses are logged as feed errors.
For transport errors (such as timeouts) and HTTP 5xx, charts-sync attempts the
same feed at most three times, logging a warning and waiting 1 then 2 seconds
before retrying. HTTP 4xx and invalid content are not retried. Persistent failures
remain feed errors; there is no silent switch to another country or source.
Dashboard/watchdog checks remain independent.
Only metadata is fetched; no Apple subscription or API key is required.
Existing default sources are unchanged.

<!-- runnable:apple_ch -->
```json
[{"name":"charts.m3u","genre_filter":false,"sources":[{"service":"deezer","type":"chart","source":"0","limit":100},{"service":"apple","country":"ch","limit":100}]}]
```

Additional Deezer sources remain regular playlist feeds, for example
`7966514882` (Alt Trends, 80 tracks) and `668126235` (Alternative Essentials,
50 tracks), publicly checked on 2026-11-19. Deliberately append them to the
existing `sources` list; they also bring other songs, not just Last.fm
replacements. The first feed wins for identical artist/title pairs;
Energy is still merged last. Apple's contribution appears as `apple`
in source logs and as `Apple Music` in notifications.

The [`charts-config`](charts-config/README.en.md) source finder checks
CH/DE/GB/US feeds on demand, with a preview and copyable snippet. To enable
the separate dashboard tile and watchdog outage/recovery notifications, set
`APPLE_CHARTS_ENABLED=1` and `APPLE_CHARTS_COUNTRY=ch` in the existing `.env`
(match the desired feed's country), then recreate both services.
Monitoring defaults to off; these settings do not enable sync feeds.
One country is monitored, not automatically every configured Apple country.
The iTunes cover check remains independent.

**Running without Last.fm:** Keep all `lastfm` feeds with `enabled:false`,
set `genre_filter:false` per playlist and `GENRE_FILTER=0`. To stop all Last.fm
usage also set `LASTFM_ENABLED=0` and `LASTFM_API_KEY=` in the existing local
`.env`. An empty key also disables the optional Last.fm status probes.
Recreate affected containers rather than merely restarting them.
The genre filter remains available to users with their own key;
curated sources do not guarantee particular genres.

**Updating an existing installation:** First back up the existing `.env` and
your source configuration. Merge changes into those files rather than
replacing them with examples; preserve existing sources, exclusions,
credentials and data paths. Add the Apple feed and optionally enable
monitoring as described above. Disable Last.fm only if desired.
The updated main `compose.yml` already forwards the Apple variables to
dashboard and watchdog. No separate Apple container, additional Compose file
or private NAS helper script is required. From the project root with the
updated project files:

```sh
docker compose config --quiet &&
docker compose build charts-sync charts-dashboard charts-config &&
docker compose up -d --no-deps --force-recreate \
  charts-sync charts-recommend charts-dashboard charts-config charts-watchdog
```

Recommend only needs recreation when its Last.fm settings change; the sequence
above also covers completely disabling Last.fm. Watchdog reloads its mounted
script on recreation and needs no image build. A plain restart does not apply
changed environment variables.

> **`type:"artist"`** fetches the top tracks of a SINGLE artist
> (`/artist/<id>/top`, capped internally by Deezer at ~100). Handy for targeted
> artist sources that no playlist/chart covers well – e.g. Swiss dialect
> ("Mundart"): Gölä, Polo Hofer, Patent Ochsner. The `source` is the **artist
> ID**, not a playlist ID (see
> [section 5](#5-finding-idstags-without-charts-config) on how to find it).

> **`genre_filter`** toggles the genre filter **per playlist** – `true`/`false`
> override the global `GENRE_FILTER` (off by default, opt-in); if the field is
> absent, the global default applies. Only makes sense for **dynamic** chart
> sources (unknown songs); artist/Mundart playlists are usually left untouched.
> Example – filter only this pop-chart playlist (not a `CHARTS=` single line, so
> no `runnable` marker):
>
> ```json
> {"name":"charts.m3u","title":"Aktuelle Hits & Charts 🔥🎶","genre_filter":true,"sources":[{"service":"deezer","type":"chart","source":"0","limit":100,"enabled":true}]}
> ```

---

## 3. Deezer genre IDs (snapshot + live path)

The following table is a **snapshot** of the well-known, stable Deezer genre IDs
(as of this document):

| ID | Genre | ID | Genre |
|----|-------|----|-------|
| `0` | All | `98` | Classical |
| `132` | Pop | `84` | Country |
| `116` | Rap/Hip Hop | `129` | Jazz |
| `152` | Rock | `173` | Films/Games |
| `113` | Dance | `464` | Metal |
| `165` | R&B | `169` | Soul & Funk |
| `85` | Alternative | `2` | African Music |
| `106` | Electro | `12` | Brazilian Music |
| `466` | Folk | `16` | Indian Music |
| `153` | Blues | | |

**Live path:** The current, complete list is always available at
`https://api.deezer.com/genre` (JSON, `data[].id` / `data[].name`). The same list
appears as a searchable table in the charts-config "Source finder" panel.

**Freshness of the charts-config table:** The genre table in charts-config does
**not** refresh via a cron job; it refreshes on the first page view **after the
24h TTL** (`DASHBOARD_GENRE_CACHE_TTL`) expires. If charts-config has not been
opened for a while, the table may be older than 24h until the next view refreshes
it – which satisfies the "updated daily" goal.

> **Important:** These IDs are **genre catalog IDs** (`/genre`), **not**
> automatically valid `chart` IDs. For country-/genre-specific playlists the most
> reliable path is a **playlist ID** (`type:"playlist"`) of an official Deezer
> country/genre playlist. That is why this document recommends the **playlist
> path** for "pop/country/…" first (playlist search in charts-config or
> `https://api.deezer.com/search/playlist?q=…`).

---

## 4. The four Last.fm methods

### `chart.getTopTracks`
Global top tracks. **No** extra parameters.
```json
{"service":"lastfm","method":"chart.getTopTracks"}
```

### `geo.getTopTracks`
Top tracks of a country. Required parameter `country` = ISO-3166-1 country name
in **English** (e.g. `Switzerland`, `Germany`, `United States`).
```json
{"service":"lastfm","method":"geo.getTopTracks","country":"Switzerland"}
```

### `tag.getTopTracks`
Top tracks for a **tag/genre**. Required parameter `tag` (e.g. `pop`, `rock`,
`country`, `80s`, `swiss`). Tags are freely assigned and quality varies – check
usefulness via the charts-config tag finder.
```json
{"service":"lastfm","method":"tag.getTopTracks","tag":"pop"}
```

### `user.getTopTracks`
Top tracks of a public Last.fm user. Required parameter `user`, optional `period`
(`overall`, `7day`, `1month`, `3month`, `6month`, `12month`).
```json
{"service":"lastfm","method":"user.getTopTracks","user":"rj","period":"3month"}
```

---

## 5. Finding IDs/tags (without charts-config)

If you do not use the charts-config "Source finder" panel, here is how to get the
values:

- **Deezer playlist ID:** open the playlist in the Deezer web app; the ID is in
  the URL (`https://www.deezer.com/.../playlist/<ID>`). Or via API:
  `https://api.deezer.com/search/playlist?q=pop`.
- **Deezer artist ID** (for `type:"artist"`): open the artist in the Deezer web
  app; the ID is in the URL (`https://www.deezer.com/.../artist/<ID>`). Or via
  API: `https://api.deezer.com/search/artist?q=Gölä` (the `id` field of the
  matching result; `tracklist` shows the corresponding `/artist/<id>/top` URL).
- **Deezer genre list:** `https://api.deezer.com/genre` (JSON with
  `data[].id` / `data[].name`).
- **Last.fm tag:** check in the browser at `https://www.last.fm/tag/<tag>`; via
  API `https://ws.audioscrobbler.com/2.0/?method=tag.getTopTracks&tag=<tag>&api_key=<KEY>&format=json`.
- **Last.fm country:** use English country names; validate with the same
  `geo.getTopTracks` call.

> The charts-config "Source finder" panel makes this more convenient and returns the
> ready-to-use `.env` snippet (including tag/country validation).

---

## 6. Copy-paste examples

Every example is shown **multi-line** (for reading) and as a **compact single
line** (to put in the `.env`). The compact line below the `<!-- runnable:… -->`
marker is exactly what goes after `CHARTS=` in the `.env`.

> **Reminder:** In the `.env`, `CHARTS=` must be on **one** line.

### Group A — ready to run (real IDs / no ID needed)

These examples use **only** verified, real Deezer IDs (`0` = global chart,
`1313617925` = Top Switzerland, `658561775` = Swiss Mundart Hits) **or** ID-less
Last.fm methods. They run immediately.

#### A1 – Minimal / default (Deezer global + Top Switzerland)

```json
[
  {
    "sources": [
      {"service": "deezer", "type": "chart", "source": "0", "limit": 100, "enabled": true},
      {"service": "deezer", "type": "playlist", "source": "1313617925", "limit": "all", "enabled": true}
    ],
    "name": "charts.m3u",
    "title": "Aktuelle Hits & Charts 🔥🎶",
    "missing": "missing.txt"
  }
]
```

Compact single line (`CHARTS=` on ONE line):

<!-- runnable:minimal -->
```json
[{"sources":[{"service":"deezer","type":"chart","source":"0","limit":100,"enabled":true},{"service":"deezer","type":"playlist","source":"1313617925","limit":"all","enabled":true}],"name":"charts.m3u","title":"Aktuelle Hits & Charts 🔥🎶","missing":"missing.txt"}]
```

#### A2 – Mundart (Deezer playlist + `exclude_artists`)

```json
[
  {
    "sources": [
      {"service": "deezer", "type": "playlist", "source": "658561775", "limit": "all", "enabled": true}
    ],
    "name": "mundart.m3u",
    "title": "Schweizer Mundart 🇨🇭",
    "missing": "missing-mundart.txt",
    "exclude_artists": ["Nemo", "Trauffer"]
  }
]
```

Compact single line (`CHARTS=` on ONE line):

<!-- runnable:mundart -->
```json
[{"sources":[{"service":"deezer","type":"playlist","source":"658561775","limit":"all","enabled":true}],"name":"mundart.m3u","title":"Schweizer Mundart 🇨🇭","missing":"missing-mundart.txt","exclude_artists":["Nemo","Trauffer"]}]
```

#### A3 – Country fan WITHOUT ID lookup (Last.fm only)

A dedicated `country.m3u` playlist from two Last.fm tags – needs **no** Deezer
playlist ID, runs immediately (only `LASTFM_API_KEY` required).

```json
[
  {
    "name": "country.m3u",
    "title": "Country 🤠",
    "sources": [
      {"service": "lastfm", "method": "tag.getTopTracks", "tag": "country", "limit": 100, "enabled": true},
      {"service": "lastfm", "method": "tag.getTopTracks", "tag": "classic country", "limit": 100, "enabled": true}
    ]
  }
]
```

Compact single line (`CHARTS=` on ONE line):

<!-- runnable:country_lastfm -->
```json
[{"name":"country.m3u","title":"Country 🤠","sources":[{"service":"lastfm","method":"tag.getTopTracks","tag":"country","limit":100,"enabled":true},{"service":"lastfm","method":"tag.getTopTracks","tag":"classic country","limit":100,"enabled":true}]}]
```

#### A4 – Pop fan WITHOUT ID lookup (Deezer global + Last.fm tag)

```json
[
  {
    "name": "pop.m3u",
    "title": "Pop 🎤",
    "sources": [
      {"service": "deezer", "type": "chart", "source": "0", "limit": 100, "enabled": true},
      {"service": "lastfm", "method": "tag.getTopTracks", "tag": "pop", "limit": 100, "enabled": true}
    ]
  }
]
```

Compact single line (`CHARTS=` on ONE line):

<!-- runnable:pop_lastfm -->
```json
[{"name":"pop.m3u","title":"Pop 🎤","sources":[{"service":"deezer","type":"chart","source":"0","limit":100,"enabled":true},{"service":"lastfm","method":"tag.getTopTracks","tag":"pop","limit":100,"enabled":true}]}]
```

#### A5 – Mix with a disabled source (`enabled: false`)

Shows how a source is temporarily switched off without deleting it (here the Top
Switzerland playlist).

```json
[
  {
    "name": "charts.m3u",
    "title": "Aktuelle Hits & Charts 🔥🎶",
    "sources": [
      {"service": "deezer", "type": "chart", "source": "0", "limit": 100, "enabled": true},
      {"service": "deezer", "type": "playlist", "source": "1313617925", "limit": "all", "enabled": false}
    ]
  }
]
```

Compact single line (`CHARTS=` on ONE line):

<!-- runnable:with_disabled_source -->
```json
[{"name":"charts.m3u","title":"Aktuelle Hits & Charts 🔥🎶","sources":[{"service":"deezer","type":"chart","source":"0","limit":100,"enabled":true},{"service":"deezer","type":"playlist","source":"1313617925","limit":"all","enabled":false}]}]
```

#### A6 – Mundart from artist top tracks (`type:"artist"`)

A Mundart playlist that bundles the top tracks of several Swiss artists instead
of (only) a curated Deezer playlist. All IDs are verified (Deezer artist IDs):
Gölä `16529`, Polo Hofer `449929`, Patent Ochsner `71287`, Trauffer `187153`,
Hecht `4459005`, Bligg `99572`. Plus the largest Mundart playlist "Schwiizer
MundArt" (`14877041603`). Everything is merged into one playlist and deduplicated.

```json
[
  {
    "name": "mundart.m3u",
    "title": "Schweizer Mundart 🇨🇭",
    "missing": "missing-mundart.txt",
    "sources": [
      {"service": "deezer", "type": "playlist", "source": "14877041603", "limit": "all", "enabled": true},
      {"service": "deezer", "type": "artist", "source": "16529", "limit": 50, "enabled": true},
      {"service": "deezer", "type": "artist", "source": "449929", "limit": 50, "enabled": true},
      {"service": "deezer", "type": "artist", "source": "71287", "limit": 50, "enabled": true},
      {"service": "deezer", "type": "artist", "source": "187153", "limit": 50, "enabled": true},
      {"service": "deezer", "type": "artist", "source": "4459005", "limit": 50, "enabled": true},
      {"service": "deezer", "type": "artist", "source": "99572", "limit": 50, "enabled": true}
    ]
  }
]
```

Compact single line (`CHARTS=` on ONE line):

<!-- runnable:mundart_artists -->
```json
[{"name":"mundart.m3u","title":"Schweizer Mundart 🇨🇭","missing":"missing-mundart.txt","sources":[{"service":"deezer","type":"playlist","source":"14877041603","limit":"all","enabled":true},{"service":"deezer","type":"artist","source":"16529","limit":50,"enabled":true},{"service":"deezer","type":"artist","source":"449929","limit":50,"enabled":true},{"service":"deezer","type":"artist","source":"71287","limit":50,"enabled":true},{"service":"deezer","type":"artist","source":"187153","limit":50,"enabled":true},{"service":"deezer","type":"artist","source":"4459005","limit":50,"enabled":true},{"service":"deezer","type":"artist","source":"99572","limit":50,"enabled":true}]}]
```

### Group B — template with placeholder (fetch the ID first)

These templates are **not directly runnable**: every `<id>` must first be
replaced with a real ID via the charts-config finder (Deezer playlist search) or
`https://api.deezer.com/search/playlist?q=…`. They are **deliberately shown
multi-line only** as templates – never as a compact copy-paste line – so nobody
accidentally goes live with an `<id>`.

#### B1 – Pop fan with a curated Deezer playlist

```json
[
  {
    "name": "pop.m3u",
    "title": "Pop 🎤",
    "sources": [
      {"service": "deezer", "type": "playlist", "source": "<id of a Deezer pop playlist>", "limit": "all", "enabled": true}
    ]
  }
]
```

#### B2 – Country fan with a curated Deezer playlist

```json
[
  {
    "name": "country.m3u",
    "title": "Country 🤠",
    "sources": [
      {"service": "deezer", "type": "playlist", "source": "<id of a Deezer country playlist>", "limit": "all", "enabled": true}
    ]
  }
]
```

#### B3 – Fully dynamic, multiple playlists (recommended style)

`service` is set throughout; mixed from real Group-A IDs and `<id>` templates.

```json
[
  {
    "name": "charts.m3u",
    "title": "Aktuelle Hits & Charts 🔥🎶",
    "sources": [
      {"service": "deezer", "type": "chart", "source": "0", "limit": 100, "enabled": true},
      {"service": "deezer", "type": "playlist", "source": "1313617925", "limit": "all", "enabled": true},
      {"service": "lastfm", "method": "geo.getTopTracks", "country": "Switzerland", "limit": 100, "enabled": true}
    ]
  },
  {
    "name": "country.m3u",
    "title": "Country 🤠",
    "sources": [
      {"service": "lastfm", "method": "tag.getTopTracks", "tag": "country", "limit": 100, "enabled": true},
      {"service": "deezer", "type": "playlist", "source": "<id of a Deezer country playlist>", "limit": "all", "enabled": true}
    ]
  }
]
```

---

## 7. Troubleshooting

`charts-sync` validates the `CHARTS` config at startup and logs clearly without
ever crashing. View the logs:

```bash
docker logs charts-sync
```

Typical lines and their meaning:

- `CHARTS geladen: N Playlist(s), M aktive Quelle(n).` – everything is fine; `M`
  counts the sources **after** any `enabled:false` deactivations.
- `CHARTS geladen: … , K Warnung(en). Details oben; …` – there were `K` warnings,
  logged individually right above this line.
- `CHARTS: Playlist '…', Quelle #i: unbekannter service '…'` – treated as Deezer.
- `CHARTS: Playlist '…', Quelle #i: unbekannte Last.fm-method '…'` or
  `… braucht Parameter '…'` – that source is **disabled** (`enabled=False`).
- `CHARTS: Playlist '…', Quelle #i: unbekannter Deezer-type '…'` – falls back to
  `chart`.
- `CHARTS ist kein gueltiges JSON an Position … (…)` – the whole `CHARTS` variable
  is broken; `charts-sync` uses the built-in default playlists until you fix the
  JSON error (usually a `.env` where `CHARTS=` is not on **one** line, or a
  missing comma/quote).

Broken individual sources are disabled or mapped to a sensible default – the rest
of the playlist keeps working.
