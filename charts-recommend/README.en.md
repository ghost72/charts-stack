> 🇬🇧 English version. Deutsche Version: [README.md](README.md)

# charts-recommend

**Multi-user download ("Nachlade") service:** grows the **shared library** from
the **public listening taste of several ListenBrainz users**. The service writes
**no playlists** anymore – anything not yet in the library is downloaded
automatically through the existing charts chain. Personalization (who sees/hears
which songs) is left to the client (Navidrome/Substreamer) per account.

This is deliberately **multi-user capable**: instead of forcing one person's
taste playlists onto everyone, the favourite songs of all listed users flow into
the same library – just like charts-sync/charts-hitparade add new songs.

Two data sources (both return artist + title **directly**, no MBID resolving):

1. **ListenBrainz stats** (`stats/user/<user>/recordings`) – a user's
   most-listened recordings. Available immediately once scrobbling happens.
2. **ListenBrainz createdfor** (Weekly Jams / Weekly Exploration) – real
   "discovery", but can be empty for the first 2–4 weeks.

Optionally also **Last.fm** (a global extra source of the operator, not
per-user): "similar songs" (`track.getSimilar`) or top tracks
(`user.getTopTracks`).

> The CF recommendation endpoint (`cf/recommendation/user/<user>/recording`) is
> **deliberately not** used: it only returns raw `recording_mbid` + score, whose
> name resolution would need MusicBrainz (1 req/s) or a fragile player endpoint.

## Prerequisite: just one public username

To read a user you only need **their public ListenBrainz username** – no token,
no password. ListenBrainz profiles are publicly readable.

- **No password needed.** The ListenBrainz **main account** (e.g. `yourusername`) is
  now only used for its **token** – and only to **raise the rate limit** when
  reading the listed users. The ListenBrainz **password** is **no longer
  needed** and is in **no** `.env`.
- The token is **optional** but recommended when you query **several** users
  (higher rate limit).

For recommendations to exist at all, a user naturally needs listening history on
ListenBrainz:

1. **ListenBrainz account** (via MusicBrainz login, free).
2. **Enable scrobbling → ListenBrainz.** In Navidrome: Profile →
   "Scrobble to ListenBrainz" + insert the personal user token. Each Navidrome
   user uses their **own** token in **their** profile – this service only reads
   the **public** profiles of all listed users.
3. **createdfor takes time:** ListenBrainz only generates Weekly
   Jams/Exploration after **2–4 weeks** of history. The **stats** source has
   data immediately.

To verify scrobbles are arriving: `https://listenbrainz.org/user/<username>/`.

## How it works

```
ListenBrainz user A ─┐
ListenBrainz user B ─┼─► charts-recommend ─► missing-recommend.txt ─►
Last.fm (optional)  ─┘                        charts-download ─► slskd ─►
                                              charts-import ─► Navidrome
```

1. Builds a library index (identical matching to charts-sync: tolerant,
   artist minimum score, compilation handling).
2. Iterates over **all** configured ListenBrainz users × active sources:
   - **stats:** `GET /1/stats/user/<user>/recordings?range=…&count=…` → returns
     `artist_name` + `track_name` directly.
   - **createdfor:** `GET /1/user/<user>/playlists/createdfor` → selects the
     playlists by title (Weekly Jams / Weekly Exploration) → `GET /1/playlist/
     <mbid>` returns the tracks (title + artist) in JSPF format.
3. **Merges all results and deduplicates** (the same song liked by several users
   is downloaded only **once**).
4. Matches the merged list **once** against the library:
   - **present** → nothing to do (count only),
   - **missing** → into **one** combined `missing-recommend.txt` (same format as
     charts-sync).
5. Pushover notification ("X new songs to download from the taste of N users").

For `PUSHOVER_DEVICE`, multiple devices are possible, comma-separated (e.g.
`iPhone-Max,Tablet-Anna`) — one message is sent per device. All devices
belong to the same Pushover account. Empty field = delivery to all devices of
the account. Invalid/disabled device names are stripped before sending —
otherwise Pushover delivers an unknown name to **all** devices instead (double
push). The device list used for this is cached for `PUSHOVER_DEVICES_TTL`
seconds (default `3600`).

`PUSHOVER_SOUND` controls the notification sound of this service (code default
`falling`). Valid Pushover sounds: `pushover, magic, cosmic, intermission,
bike, bugle, cashregister, classical, falling, gamelan, incoming, mechanical,
pianobar, siren, spacealarm, tugboat, alien, climb, persistent, echo, updown,
none` (= silent), `vibrate`. Empty = account default (no `sound` field).
Overridable via `PUSHOVER_SOUND_RECOMMEND` in `compose.yml`.

## Downloading: interplay with charts-download

When `WRITE_MISSING=1` (default), the missing songs land in **one** combined
file:

- `/charts/missing-recommend.txt`

For charts-download to actually fetch them, **this one file must be listed in its
`MISSING_FILES`**. It is pre-filled in the central `.env`, e.g.:

```
MISSING_FILES=/charts/missing.txt;/charts/missing-mundart.txt;/charts/missing-80er-90er.txt;/charts/missing-hitparade.txt;/charts/missing-recommend.txt
```

Then restart charts-download. The rest runs automatically: download → import →
the songs grow into the shared library.

> If you want **no** automatic downloading (just show what you already have),
> set `WRITE_MISSING=0`.

> **Cleaning up old artifacts:** old `missing-recommend-jams.txt`,
> `missing-recommend-exploration.txt`, `missing-recommend-lastfm.txt` and old
> `recommend-*.m3u` files in `/charts` or `MUSIC_DIR` may be deleted manually –
> the service no longer writes/reads them. Leftover `.m3u` would otherwise remain
> in Navidrome as orphan playlists.

## Setup

### 1. Configuration

```bash
cp .env.example .env
# Set RECOMMEND_LISTENBRAINZ_USERS (comma list of public usernames).
# LISTENBRAINZ_TOKEN optional (rate limit only). Last.fm optional.
```

| Variable | Default | Meaning |
|----------|---------|-----------|
| `RECOMMEND_LISTENBRAINZ_USERS` | – | comma list of public ListenBrainz usernames (no secret). Empty = feature off. |
| `LISTENBRAINZ_USER` | – | backward-compat: old single value, fallback when the list is empty |
| `RECOMMEND_SOURCES` | `stats,createdfor` | active sources (`stats` and/or `createdfor`) |
| `RECOMMEND_STATS_RANGE` | `month` | range of the stats source (`week`…`all_time`) |
| `LISTENBRAINZ_TOKEN` | – | optional, only to raise the rate limit – **NO password** |
| `LISTENBRAINZ_KINDS` | `jams,exploration` | which createdfor playlists |
| `LASTFM_ENABLED` | `0` | Last.fm as an optional extra source |
| `LASTFM_MODE` | `similar` | `similar` or `top` |
| `RECOMMEND_LIMIT` | `100` | max. tracks per user and source |
| `WRITE_MISSING` | `1` | download missing songs |
| `INTERVAL_HOURS` | `24` | run interval |
| `HTTP_TIMEOUT` | `30` | timeout per HTTP request (seconds) |
| `HTTP_RETRIES` | `3` | attempts per request on transport errors |
| `NOTIFY_ONLY_ON_CHANGE` | `1` | error alert only on state change |

> In compose/`.env` these last three are named `RECOMMEND_HTTP_TIMEOUT`,
> `RECOMMEND_HTTP_RETRIES` and `RECOMMEND_NOTIFY_ONLY_ON_CHANGE`.

## Robustness & error visibility

If a user's/source's fetch fails (timeout, HTTP or connection error), each
request is retried up to `HTTP_RETRIES` times with a short backoff. Only when
**all** fetches (all users × all sources) fail after the retries does the service
report `status="error"` – the dashboard turns the tile red and shows the error
message, and a **Pushover alert** is sent. If at least one user/source still
produced results, the run stays `ok` but names the broken source in the message.

Important: an **empty but successful** result (createdfor not generated yet, or a
fresh user returns HTTP 404) is **not** an error and stays `ok` – only a real
transport error triggers an alert. If **no** user is configured (empty list,
feature off), the service keeps running idle ("nothing to do"), also not an
error. With `NOTIFY_ONLY_ON_CHANGE=1` (default) the alert fires only on the first
failed run (no daily spam) plus one recovery message once the fetch works again.
Without Pushover config everything stays quiet (log + dashboard only).

### 2. Deploy (on `your-nas`)

Copy the code to `/path/to/charts-recommend/`, then:

```bash
sudo docker compose up -d --build
```

### 3. Wire up charts-download

Extend `MISSING_FILES` in the central `.env` with the one `missing-recommend.txt`
(see above) and restart charts-download.

### 4. Watch

```bash
sudo docker compose logs -f charts-recommend
```

As long as ListenBrainz has not generated anything for the createdfor source
yet, the service reports "noch keine createdfor-Playlists" – the **stats** source
still returns data once scrobbling happened. To test immediately you can also
enable **Last.fm** (`LASTFM_ENABLED=1` + API key, optionally `LASTFM_SEEDS` or
`LASTFM_USER`).

## Container

| Container | Purpose |
|-----------|-------|
| `charts-recommend` | Scheduler loop: fetch several users' recommendations, merge, match, write the download list. No web server. |

Deliberately uses the same (copied) building blocks as the sister services
(`library.py`/`notify.py` from charts-sync) – no runtime dependency between the
containers.

## Data & paths

| Host path | Purpose |
|-----------|-------|
| `/volume2/music/Music` | library – only a **scanned** source (read-only), no playlists are written anymore |
| `/volume2/music/Charts` | shared folder: `missing-recommend.txt` |

## Operation

```bash
sudo docker compose logs -f charts-recommend   # Logs
sudo docker compose up -d --build              # rebuild/start
sudo docker compose down                       # stop
```
