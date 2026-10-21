> 🇬🇧 English version. Deutsche Version: [README.md](README.md)

# charts-hitparade

Fetches the **official Swiss charts** from [hitparade.ch](https://hitparade.ch)
weekly (Sundays) – the singles Top 100 –, matches them against your library and
writes a `hitparade.m3u` playlist plus a `missing-hitparade.txt` list that
[`charts-download`](../charts-download) then fetches.

A fifth chart source next to [`charts-sync`](../charts-sync) (Deezer/Last.fm) –
same mechanics, just a different source: the official IFPI Switzerland chart.

---

## What it does

- **Sunday 20:00** (configurable): fetches the current Top 100 from hitparade.ch.
- Matches each song against the library (same logic as charts-sync: fuzzy match,
  `IGNORE_COMPILATIONS`).
- **Hits** → `hitparade.m3u` (Navidrome playlist).
- **Missing** → `missing-hitparade.txt` → charts-download fetches the findable
  ones.

The playlist is a **snapshot of the current week** (exactly the 100 songs of the
week, rewritten every Sunday). Songs that drop out disappear from the playlist,
but the downloaded files stay in the library (findable via Navidrome "Recently
Added" and under the artist).

## How the data is fetched (no headless browser)

hitparade.ch serves the Top 100 as **HTML** – provided you send a real browser
`User-Agent` (without a UA you only get an empty placeholder). Each chart entry
is `<div class="chart_title"><a ...><b>ARTIST</b><br>TITLE</a></div>` in the
HTML; artist/title are read from there. A simple HTTP request is enough, **no**
headless browser, **no** API. The page is ISO-8859-1 encoded (umlauts/accents
are decoded correctly).

### Why only the Top 100 (`singles`) and not the CH-Artist charts

hitparade.ch also has a "CH Artists" list (`singles-ch`). But that one is loaded
**via JavaScript/AJAX** – the HTML contains 0 songs. It is not retrievable with
the plain HTTP method (it would need a headless browser, which would needlessly
bloat the lean stack). That's acceptable: the currently successful Swiss acts
appear in the Top 100 anyway. `HITPARADE_CHARTS=singles-ch` is therefore not
recommended (would permanently report an error because 0 songs).

## Monitoring

If hitparade.ch returns **0 songs** (page rebuilt/blocked) or is unreachable, the
service reports `status=error` → the dashboard tile turns red/orange and a
Pushover message is sent. So a broken fetch is noticed immediately instead of
silently writing an empty playlist.

## Configuration (central `.env`)

| Variable | Default | Purpose |
| --- | --- | --- |
| `HITPARADE_ENABLED` | `true` | On/off switch (`true`/`false`). `false` = pause the service: the container keeps running, no fetch, no playlist – status **`idle`** (grey in the dashboard, **not** an error). Tolerant parser (`true`/`1`/`yes`/`on` resp. `false`/`0`/`no`/`off`). |
| `HITPARADE_CHARTS` | `singles` | which charts (comma-separated) |
| `HITPARADE_WEEKDAY` | `6` | weekday (0=Mon … 6=Sun) |
| `HITPARADE_HOUR` / `HITPARADE_MINUTE` | `20` / `0` | run time |
| `HITPARADE_RESYNC_CHECK_SECONDS` | `60` | How often the loop checks between the Sunday runs whether `charts-import` triggered a **resync** (just a file check, cheap). When a signal is present and a hitparade-relevant song was imported, `hitparade.m3u` is re-rendered immediately from the cached chart (without re-fetching hitparade.ch; the Sunday run stays unchanged). The signal file `.hitparade-resync-requested` and the chart cache `.charts-hitparade-chart.json` live in `CHARTS_DIR`. |
| `MATCH_THRESHOLD` | `87` | match threshold (like charts-sync) |
| `ARTIST_MIN_SCORE` | `60` | artist minimum score |
| `IGNORE_COMPILATIONS` | `1` | ignore compilation tracks when matching |
| `PUSHOVER_SOUND` | `falling` | Notification sound for this service. Valid Pushover sounds: `pushover, magic, cosmic, intermission, bike, bugle, cashregister, classical, falling, gamelan, incoming, mechanical, pianobar, siren, spacealarm, tugboat, alien, climb, persistent, echo, updown, none` (= silent), `vibrate`. Empty = account default. Overridable via `PUSHOVER_SOUND_HITPARADE` in `compose.yml`. |

**Important:** `missing-hitparade.txt` must be in the `MISSING_FILES` variable of
the `.env` so charts-download fetches the missing songs (already pre-filled in
`.env.example`).
