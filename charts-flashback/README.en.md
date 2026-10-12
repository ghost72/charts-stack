> 🇬🇧 English version. Deutsche Version: [README.md](README.md)

# charts-flashback

A download-only service that taps the music rotation of **FLASHBACK FM** and
grows your library from it. It reads the "last played" list of the **live main
stream** (plays all decades mixed) via the station's public GraphQL API,
collects the songs over time, and fetches missing ones through the existing
charts chain.

> **Only the main stream** (`flashbackfm.ffm`) exposes a retrievable "last
> played" history. The thematic decade/artist channels exist but return no
> history (`played=null`) over the API – so the main stream is queried, which
> mixes all decades anyway.

**Download-only:** charts-flashback writes **no** playlists. The decade display
is handled by **Navidrome Smart Playlists (NSP)** that filter the library by
year. For that to work, every fetched song gets a **year tag** (see "Year
hybrid").

> Same principle as `charts-recommend`: acquisition belongs in the stack,
> display/personalization in the client (Substreamer) or in the NSPs.

## How it works

```
FLASHBACK FM (GraphQL, last played)
      │  per run (main stream)
      ▼
charts-flashback ─► missing-flashback.txt ─► charts-download ─► slskd
      │  (year: title suffix (YYYY),                │
      │   MusicBrainz only as fallback)             │
      └─ state (already seen)               charts-import ─► library (with year tag)
         + year map (.charts-flashback-years.json)  │
                                            Navidrome Smart Playlists (decades)
```

1. Fetch the "last played" list per configured channel (GraphQL).
2. Process only **new** songs (not in the state) – the radio rotation repeats,
   the state prevents duplicate work.
3. Canonicalize the spelling of each new song (Deezer, see below) and resolve a
   release year (title suffix or MusicBrainz fallback, see below).
4. Match against the library; whatever is missing goes into
   `missing-flashback.txt` **without a year** (clean "artist - title" format
   like the other services). The year goes **separately** into the year map
   `.charts-flashback-years.json` – **not** into the file/folder name.
5. `charts-download` fetches it, `charts-import` reads the year map and sets the
   `date`/`originaldate` tag on import → the song shows up in the correct decade
   NSP.

## The year (for the decade NSPs)

FLASHBACK provides the release year **directly in the title** as a suffix, e.g.
`THE WILD BOYS (1984)`. That is the **reliable** year source (the station
itself). charts-flashback strips the `(YYYY)` → clean title + year; the year is
written as a `date`/`originaldate` tag on import so the song shows up in the
right decade NSP.

**MusicBrainz** is only a **fallback** for the rare titles **without** a
`(YYYY)` suffix (earliest studio first release, with rate limiting + cache;
disable via `MB_ENABLED=0`). If a year is in the title, MusicBrainz is not even
queried – faster and more reliable.

> The year is **never** stored in the file/folder name. It travels exclusively
> through the year map `.charts-flashback-years.json` to `charts-import`, which
> sets it as a `date`/`originaldate` tag.

## Canonical spelling (Deezer)

FLASHBACK FM provides artist and title in **UPPERCASE** (e.g.
`U2 / WHERE THE STREETS HAVE NO NAME`). These raw values would otherwise end up
unfiltered in the missing list and thus in the library's folder/file names.
That's why charts-flashback resolves the **official mixed-case spelling** of
each song via the open Deezer search API (no API key), consistent with
`charts-sync`:

- Plain query `q="<artist> <title>"` (the structured advanced query
  consistently returns 0 hits on Deezer).
- Validation via the same `normalize`/`similarity` logic as the library
  matching; a hit only counts above the `DEEZER_MATCH_MIN` similarity (artist
  **and** title).
- The **best** reliable hit is chosen (highest sum), not blindly the first –
  this protects against live/remix/edit variants that may rank higher.
- Cache `.charts-flashback-deezer-cache.json` (including misses) so recurring
  songs don't hit the API again.
- **fail-open:** with the switch off, no reliable hit, or a network/HTTP/JSON
  error, the raw value stays (better UPPERCASE than wrongly guessed).

Canonicalization runs **before** key/match/missing/year map, so the name written
to `missing-flashback.txt` determines the later folder/file name.

## Configuration (central `.env`)

| Variable | Default | Meaning |
|----------|---------|---------|
| `FLASHBACK_ENABLED` | `true` | On/off switch (`true`/`false`). `false` = pause the service: the container keeps running, no fetch – status **`idle`** (grey in the dashboard, **not** an error). Tolerant parser (`true`/`1`/`yes`/`on` resp. `false`/`0`/`no`/`off`). |
| `FLASHBACK_HASH` | *(example in .env.example)* | Persisted-query hash of the LastPlayed query. Public, **not a secret**. Service enabled but hash empty → status **`idle`** ("kein FLASHBACK_HASH gesetzt – nichts zu tun"). |
| `FLASHBACK_CHANNELS` | `flashbackfm.ffm` | Streams (streamName), comma-separated. Only the main stream exposes a history. |
| `FLASHBACK_INTERVAL_HOURS` | `2` | Run interval. |
| `FLASHBACK_LIMIT` | `50` | Max new songs per run. |
| `FLASHBACK_PLAYED_LIMIT` | `50` | How many "last played" entries per channel are fetched. |
| `MB_ENABLED` | `1` | MusicBrainz year resolution on/off. |
| `MB_USER_AGENT` | `charts-flashback/1.0 (self-hosted)` | MusicBrainz requires a meaningful user agent. |
| `DEEZER_CANONICAL_ENABLED` | `1` | Canonical spelling via Deezer on/off. |
| `DEEZER_MATCH_MIN` | `80` | Minimum similarity (artist and title, 0–100) for a Deezer hit to count as the canonical source. |
| `PUSHOVER_SOUND` | `falling` | Notification sound for this service (e.g. the `status=error` alert). Valid Pushover sounds: `pushover, magic, cosmic, intermission, bike, bugle, cashregister, classical, falling, gamelan, incoming, mechanical, pianobar, siren, spacealarm, tugboat, alien, climb, persistent, echo, updown, none` (= silent), `vibrate`. Empty = account default. Overridable via `PUSHOVER_SOUND_FLASHBACK` in `compose.yml`. |

Note: the `DEEZER_*` variables are **not** set in `compose.yml` → the code
default applies (canonical spelling is **on** by default).

### "Paused" vs. "runs empty" – status `idle`

`FLASHBACK_ENABLED=false` and an enabled service **without** a `FLASHBACK_HASH`
are both "deliberately do nothing" and therefore show as status **`idle`** (grey,
never red – neither the dashboard nor the watchdog alarm):

1. `FLASHBACK_ENABLED=false` → status `idle`, log "deaktiviert".
2. `FLASHBACK_ENABLED=true` but `FLASHBACK_HASH` empty → status `idle` with
   "kein FLASHBACK_HASH gesetzt – nichts zu tun" (previously `ok`/"nothing to
   do"; `idle` is more honest since no fetch ran at all).

A set but **dead** hash remains a real `error` (see below).

Note: the thematic channels (`flashbackfm-80er`, `queen`, …) expose **no** "last
played" history over the API (`played=null`) and are therefore not useful here.
The main stream `flashbackfm.ffm` covers all decades.

### Refreshing the hash

`FLASHBACK_HASH` can change if FLASHBACK FM rebuilds its web app. The service
then reports `status=error` (red dashboard tile + Pushover). To get the new hash:

1. Open flashbackfm.ch in the browser, dev tools → Network tab.
2. Filter for `played`, play/switch a channel.
3. Click the request; the URL ends with `.../LastPlayed/<HASH>?variables=…`.
4. Copy `<HASH>`, set it as `FLASHBACK_HASH` in `.env`, restart the service
   (`docker compose up -d charts-flashback`).

## Wiring with charts-download

For the songs to actually be fetched, `/charts/missing-flashback.txt` must be in
charts-download's `MISSING_FILES` variable (already prepared in `.env.example`).

## Container

| Container | Purpose |
|-----------|---------|
| `charts-flashback` | Scheduler loop: fetch rotation, resolve year, match, write missing. No web server. |

Deliberately reuses the same (copied) building blocks as the sibling services
(`library.py`/`notify.py`/`status.py`/`manifest.py`) – no runtime dependency.

## Data & paths

| Host path | Purpose |
|-----------|---------|
| `/volume2/music/Music` | Library (scanned, mounted **read-only**) |
| `/volume2/music/Charts` | `missing-flashback.txt`, year map, state, caches |

State, map and cache files (in the Charts folder):
`.charts-flashback-state.json` (already-seen songs),
`.charts-flashback-years.json` (year map, read by charts-import),
`.charts-flashback-mbcache.json` (MusicBrainz year cache),
`.charts-flashback-deezer-cache.json` (Deezer casing cache).

## Operation

```bash
docker compose up -d --build charts-flashback   # build + start
docker logs -f charts-flashback                 # watch the first run
```

## One-time migration / legacy

The 7 flashback songs already stored **UPPERCASE** in the library before the
Deezer canonicalization are cleaned up by the idempotent script
`scripts/migrate_flashback_casing.py` (it resolves the target spelling at
runtime via the same `deezer.resolve_canonical` function; `--dry-run`
supported).

> The real rename must run on the **NAS host** (as `root`, with `PYTHONPATH`
> pointing at the charts-flashback directory), because the container mounts the
> library **read-only** (`/music/Music:ro`) – a real run inside the container
> fails with "Read-only file system". The `--dry-run` runs fine in the
> container.

For the associated poisoned manifest entries (old UPPERCASE/`{YYYY}` titles),
`charts-import` provides the cleanup script
`scripts/cleanup_flashback_legacy.py`.

## Legal note

Like the whole stack, this service fetches music via Soulseek. Only download
content you are entitled to. FLASHBACK FM only serves as an idea source (which
songs are playing) – the actual acquisition goes through the existing charts
chain.
