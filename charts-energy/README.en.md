> 🇬🇧 English version. Deutsche Version: [README.md](README.md)

# charts-energy

Polls the Swiss **NRJ / energy.ch** webradio channels for their currently
playing track (via the Icecast **ICY** in-stream metadata on the Infomaniak
stream hosts), accumulates the observed spins, matches them against your library
and feeds the missing ones into the existing
[`charts-download`](../charts-download) pipeline – same mechanics as
[`charts-hitparade`](../charts-hitparade), just a different source.

There is **no** usable JSON API; the ICY metadata block is the only path
(details: `.agents/tasks/energy-api-feasibility.md`). The service ships
**disabled by default** (`ENERGY_ENABLED=0`, empty channel list) so a fresh
clone never polls Icecast unexpectedly.

---

## Three integration modes per channel

- **OWN (own playlist)** – e.g. Pop/Hits/Love: charts-energy writes its own
  `energy-<slug>.m3u` + `missing-energy-<slug>.txt`, exactly like hitparade. The
  playlist name **must** sit in the reserved `energy-*.m3u` namespace (names are
  lowercased/normalized; any non-`energy-` name is refused). That way an OWN
  channel can never collide with a curated charts-sync playlist. Two writers
  never target the same `.m3u` (nor the same missing file). To write into an
  existing/shared playlist always use `feed`, never `own`.
- **FEED (into an existing theme)** – e.g. Mundart: charts-energy writes **no**
  playlist of its own but a **handoff file per theme**
  (`.charts-energy-feed-<theme>.json`). [`charts-sync`](../charts-sync) reads it
  as an additional feed of an existing theme – so there stays **exactly one**
  `mundart.m3u` (written by charts-sync). Energy tracks are deduped there against
  the Deezer/Last.fm sources and merged **after** them (position priority stays
  with the curated charts).
- **LIBRARY (download-only, no playlist)** – e.g. decades (80s/90s):
  charts-energy writes **no** `.m3u`; like [`charts-flashback`](../charts-flashback)
  it only tops up the **shared library**: per run a shared missing file
  (`missing-energy-library.txt`, fetched by charts-download) and – for each
  **missing** song – a release year into the **shared year map**
  (`.charts-flashback-years.json`, read by charts-import). The year never
  appears in the path/filename, only as a tag – so the song shows up in the
  right decade via the **Navidrome Smart Playlists (NSP)**. The **decade** is
  reliable (from the slug `energy80s` → 1980s + clamp); the exact year is best
  effort via MusicBrainz.

## How the data is fetched (no headless browser, no API)

Per channel the Infomaniak stream is opened with the header `Icy-MetaData: 1`,
**one** metadata block is read and the connection is closed immediately. The
`StreamTitle` is split into artist/title on **U+02D7 `˗`** (MODIFIER LETTER
MINUS SIGN, **not** the ASCII hyphen); only `type=music` entries are kept (ads/
jingles dropped). Dedupe per spin is by `startedAt`+`plId`. The ICY read uses
the stdlib (`http.client`/`ssl`, normal TLS verification) – **no** new
dependency.

A missed/empty block (between two songs, a metadata-less host) is a quiet
`None`; a transient read error is a single WARN line, never a service error.
Polite: 60–120 s per channel, close the connection promptly.

## Discover channels

```bash
docker compose run --rm charts-energy python -m app.discover          # list
docker compose run --rm charts-energy python -m app.discover --probe energypop
```

`--list` prints the confirmed slugs (display | slug | genre); `--probe <slug>`
checks a guessed slug (HTTP 200 + an `icy-name` header ⇒ a real channel).

## Configuration (central `.env`)

| Variable | Default | Purpose |
| --- | --- | --- |
| `ENERGY_ENABLED` | `0` (**off**) | On/off switch. `false` = pause the service: the container keeps running, no polling/playlist – status **`idle`** (grey in the dashboard, **not** an error). Tolerant parser (`true`/`1`/`yes`/`on` resp. `false`/`0`/`no`/`off`). |
| `ENERGY_CHANNELS` | *(empty)* | Channel list as **JSON** (single line). Each entry: `slug`, optional `title`, `enabled`, `mode` (`own`/`feed`/`library`), for `own` optional `playlist`/`missing` (namespace `energy-*.m3u`), for `feed` the `theme`, for `library` optional `decade` (overrides the slug decade). |
| `ENERGY_CHANNELS_FILE` | *(empty)* | Path to a multi-line JSON file – takes priority over `ENERGY_CHANNELS`. |
| `ENERGY_POLL_SECONDS` | `90` | Poll interval per channel (60–120 s recommended). |
| `ENERGY_MATCH_INTERVAL_MINUTES` | `30` | How often matching + output runs (expensive library scan, hence decoupled from the poll). |
| `ENERGY_TRACK_RETENTION_DAYS` | `30` | Spins older than X days fall out of the buffer. |
| `ENERGY_MAX_TRACKS_PER_CHANNEL` | `500` | Per-channel cap (keep the newest). |
| `MATCH_THRESHOLD` | `87` | match threshold (like charts-sync). |
| `ARTIST_MIN_SCORE` | `60` | artist minimum score. |
| `IGNORE_COMPILATIONS` | `1` | ignore compilation tracks when matching. |
| `MB_ENABLED` | `1` | **library mode:** MusicBrainz year resolution on/off (for the decade NSPs). |
| `MB_USER_AGENT_ENERGY` | `charts-energy/1.0 (self-hosted)` | User-Agent for the MusicBrainz lookup (MB requires a meaningful UA). |
| `MB_RATE_SLEEP` | `1.1` | Sleep (s) before each MB request (~1 req/s, polite to MusicBrainz). |
| `MB_SCORE_MIN` | `90` | Minimum MB recording score for a hit to count. |
| `ENERGY_LIBRARY_MISSING_FILE` | `missing-energy-library.txt` | **library mode:** ONE shared missing file for all library channels. Append it to `MISSING_FILES`. |
| `PUSHOVER_SOUND` | `falling` | Notification sound for this service. Empty = account default. |

> Each `library` channel may carry an optional `decade` override in the JSON
> (e.g. `"decade":"1980"`); without it the decade is derived from the slug
> `energy80s` → `1980`. A non-decade slug (e.g. `energypop`) yields "no decade":
> then only the MusicBrainz year, otherwise no year tag at all.

The state (`.charts-energy-state.json`) and the FEED handoff files
(`.charts-energy-feed-<theme>.json`) live in `CHARTS_DIR`.

## Turning it on

Set `ENERGY_ENABLED=1` and add a channel list. Two examples:

**Mundart top-up (FEED) – fills the existing `mundart.m3u`:**

```dotenv
ENERGY_CHANNELS=[{"slug":"energymadeinswitzerland","mode":"feed","theme":"mundart","enabled":true}]
```

> Note: the Swiss channel is **"Energy Made In Switzerland"**
> (`energymadeinswitzerland`) – there is no channel literally named "Mundart".
> It is slightly broader than pure Mundart (also Swiss acts in other languages),
> which makes it well suited to **top up** the Deezer Mundart list.

**Pop as its own playlist (OWN):**

```dotenv
ENERGY_CHANNELS=[{"slug":"energypop","title":"Energy Pop 🎶","mode":"own","playlist":"energy-pop.m3u","enabled":true}]
```

For an **OWN** channel, append its generated missing file to `MISSING_FILES`
(one line in the `.env`) so charts-download fetches it:

```dotenv
MISSING_FILES=/charts/missing.txt;/charts/missing-energy-pop.txt
```

> The shipped `MISSING_FILES` default stays `"/charts/missing.txt"`. FEED
> channels produce **no** `missing-energy-*` file of their own – their missing
> songs go into the theme's missing file (e.g. `missing-mundart.txt`, written by
> charts-sync).

### Decades (80s/90s) – download-only via LIBRARY

Decades no longer need a playlist of their own: the decade channels run in
**LIBRARY** mode, top up the shared library and get a release year per song (as
a tag, **not** in the file/folder name). The decade display is handled by the
**Navidrome Smart Playlists**, which filter by year. Just append the one shared
library missing file to `MISSING_FILES`:

```dotenv
# Decades (download-only -> NSP, no playlist).
ENERGY_CHANNELS=[{"slug":"energy80s","mode":"library","enabled":true},{"slug":"energy90s","mode":"library","enabled":true}]
MISSING_FILES=/charts/missing.txt;/charts/missing-energy-library.txt
```

> The **decade** is reliable (from the slug + clamp); the exact year is best
> effort via MusicBrainz – which is exactly what the decade NSP needs. A
> per-channel `decade` override is possible (e.g. `"decade":"1980"`).

### Combined config (several channels, all modes at once)

The modes mix freely – all in **one** `ENERGY_CHANNELS` line. A realistic
example: Mundart **and** Hits each as `feed` into the existing playlists
`mundart.m3u`/`charts.m3u`, the decades as `library`:

```dotenv
ENERGY_ENABLED=1
ENERGY_CHANNELS=[{"slug":"energymadeinswitzerland","mode":"feed","theme":"mundart","enabled":true},{"slug":"energyhits","mode":"feed","theme":"charts","enabled":true},{"slug":"energy80s","mode":"library","enabled":true},{"slug":"energy90s","mode":"library","enabled":true}]
# library channels need the shared missing file in MISSING_FILES:
MISSING_FILES=/charts/missing.txt;/charts/missing-mundart.txt;/charts/missing-energy-library.txt
```

For every `feed` theme the **other side** in `charts-sync/charts.json` must
carry the same `theme` value (see `charts-sync/charts.json.example`): in the
`mundart.m3u` block `{ "service": "energy", "theme": "mundart" }`, in the
`charts.m3u` block `{ "service": "energy", "theme": "charts" }`. Producer
(`.env`) and consumer (`charts.json`) must be **byte-identical** on the `theme`
– a typo looks like "Energy contributed nothing," not like an error.

## Monitoring

Missed ICY reads are transient (one WARN line, carry on). Only when the
**match/output run** itself throws does the service report `status=error`
(dashboard tile red + Pushover). Disabled = `idle` (grey tile).
