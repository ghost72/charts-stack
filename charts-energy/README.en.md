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

## Two integration modes per channel

- **OWN (own playlist)** – e.g. Pop/Hits/Love: charts-energy writes its own
  `energy-<slug>.m3u` + `missing-energy-<slug>.txt`, exactly like hitparade.
- **FEED (into an existing theme)** – e.g. Mundart, 80s/90s: charts-energy
  writes **no** playlist of its own but a **handoff file per theme**
  (`.charts-energy-feed-<theme>.json`). [`charts-sync`](../charts-sync) reads it
  as an additional feed of an existing theme – so there stays **exactly one**
  `mundart.m3u` (written by charts-sync). Energy tracks are deduped there against
  the Deezer/Last.fm sources and merged **after** them (position priority stays
  with the curated charts).

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
| `ENERGY_CHANNELS` | *(empty)* | Channel list as **JSON** (single line). Each entry: `slug`, optional `title`, `enabled`, `mode` (`own`/`feed`), for `own` optional `playlist`/`missing`, for `feed` the `theme`. |
| `ENERGY_CHANNELS_FILE` | *(empty)* | Path to a multi-line JSON file – takes priority over `ENERGY_CHANNELS`. |
| `ENERGY_POLL_SECONDS` | `90` | Poll interval per channel (60–120 s recommended). |
| `ENERGY_MATCH_INTERVAL_MINUTES` | `30` | How often matching + output runs (expensive library scan, hence decoupled from the poll). |
| `ENERGY_TRACK_RETENTION_DAYS` | `30` | Spins older than X days fall out of the buffer. |
| `ENERGY_MAX_TRACKS_PER_CHANNEL` | `500` | Per-channel cap (keep the newest). |
| `MATCH_THRESHOLD` | `87` | match threshold (like charts-sync). |
| `ARTIST_MIN_SCORE` | `60` | artist minimum score. |
| `IGNORE_COMPILATIONS` | `1` | ignore compilation tracks when matching. |
| `PUSHOVER_SOUND` | `falling` | Notification sound for this service. Empty = account default. |

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
ENERGY_CHANNELS=[{"slug":"energypop","title":"Energy Pop 🎶","mode":"own","enabled":true}]
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

### Optional: decade theme (80s/90s) as ONE playlist

The 80s/90s channels run in **FEED** mode onto a shared theme. The repo ships
**no** decade playlist – you create the theme yourself in `charts-sync`'s
`CHARTS`. The producer and consumer side must be **byte-identical** on the
`theme`; the block below is meant as **one copy-paste pair**:

```jsonc
// 1) Producer: charts-energy ENERGY_CHANNELS (.env)
//    energy80s + energy90s on the same theme "achtziger-neunziger".
ENERGY_CHANNELS=[{"slug":"energy80s","mode":"feed","theme":"achtziger-neunziger","enabled":true},{"slug":"energy90s","mode":"feed","theme":"achtziger-neunziger","enabled":true}]

// 2) Consumer: charts-sync CHARTS – a new decade playlist that consumes the
//    Energy feed of the same theme.
{
  "sources": [
    { "service": "energy", "theme": "achtziger-neunziger", "enabled": true }
  ],
  "name": "achtziger-neunziger.m3u",
  "title": "80s & 90s \uD83D\uDCFB",
  "missing": "missing-achtziger-neunziger.txt"
}
```

> ⚠️ **Lockstep:** change the `theme` value on **both** lines or on neither.
> charts-energy cannot validate the other side – a typo on one side looks like
> "Energy contributed nothing," not like a misconfiguration. Hence the
> copy-paste pair.

## Monitoring

Missed ICY reads are transient (one WARN line, carry on). Only when the
**match/output run** itself throws does the service report `status=error`
(dashboard tile red + Pushover). Disabled = `idle` (grey tile).
