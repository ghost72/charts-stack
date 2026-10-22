> 🇬🇧 English version. Deutsche Version: [README.md](README.md)

# charts-config

Snippet/config web UI for the charts-stack. A password-protected web UI (default
port `5058`) that helps you assemble the chart and Energy sources: it shows
**ready-made snippets** to paste into `.env`/`charts.json` – it changes **nothing**
itself.

Sibling service of [`charts-dashboard`](../charts-dashboard): charts-config was
carved out of the dashboard. The source finder and the Energy-channel browser
that used to live in the dashboard now live here; the dashboard is a pure status
monitor.

---

## What it shows

Two **read-only** tools on one page – both only build snippets, neither writes
`.env` or `charts.json`:

### Source finder (find new chart sources)

Helps you assemble the `charts-sync` `CHARTS` list – no more fishing IDs out of
web links by hand:

- **Deezer playlist search:** type a query → matching playlists with their ID
  and a **ready-made JSON snippet** to copy
  (`{"service":"deezer","type":"playlist","source":"<id>",…}`).
- **Deezer artist search:** type an artist name (e.g. `Gölä`, `Polo Hofer`) →
  matching artists with their ID and fan count (helps tell apart artists with
  the same name) plus a **ready-made snippet** for the artist's top tracks
  (`{"service":"deezer","type":"artist","source":"<id>",…}`).
- **Deezer genre table:** the stable genre IDs as a list (cached once per day,
  refreshed on the first call after expiry – see `DASHBOARD_GENRE_CACHE_TTL`).
  If the live call fails, a built-in static fallback list is used.
- **Last.fm tag check:** enter a tag (e.g. `country`) → validates via
  `tag.getTopTracks` whether it is usable and shows the matching snippet
  (`{"service":"lastfm","method":"tag.getTopTracks","tag":"<tag>",…}`). This part
  is **only active with `LASTFM_API_KEY`**; without a key it is greyed out.

Every snippet can be copied and pasted straight into the `CHARTS` variable of
your `.env`/`compose.yml` (or `charts.json`). Full reference:
[`CHARTS-SOURCES.en.md`](../CHARTS-SOURCES.en.md).

### Energy channel browser (NRJ)

A second, equally **read-only** panel: a list of the confirmed Energy (NRJ)
webradio channels, so you do not have to hunt slugs in the README when assembling
`ENERGY_CHANNELS`. Per channel it shows the display name, slug, genre, a **status
badge** (active + the running mode, or inactive) and a **ready-made JSON snippet**
for the `ENERGY_CHANNELS` array. charts-config **never** writes `.env` or
`charts.json` – you paste by hand and restart the service yourself (**text hints**
only, no restart button).

The status comes from the running `charts-energy` config (which the service
writes into its status JSON, read read-only by charts-config); a missing/old
status file simply shows every channel as "inactive" (fail-open). Each default
mode needs one extra wiring step:

- **own** → append its `missing-energy-<name>.txt` (shown in the snippet) to
  `MISSING_FILES` so `charts-download` fetches its missing songs.
- **feed** → add `{"service":"energy","theme":"<theme>"}` to that theme's
  playlist in `charts-sync/charts.json` (theme byte-for-byte, e.g. `mundart`).
- **library** → append the shared `missing-energy-library.txt` to `MISSING_FILES`
  (all `library` channels share this one file).

Mode details: [`charts-energy/README.en.md`](../charts-energy/README.en.md).

## Where the data comes from

- **Deezer / Last.fm:** direct query of the public HTTP APIs (WAN). Without
  `LASTFM_API_KEY` only the Last.fm part is greyed out – the rest works.
- **Energy live status:** from the status JSON that `charts-energy` writes into
  `Charts/.charts-status/` – mounted **read-only** by charts-config.
- **No Docker access, no restart button:** unlike the dashboard, charts-config
  does **not** need the Docker SDK. It only reads Deezer/Last.fm and the status
  JSON – it starts/stops no container.

## Login

charts-config has its **own** login, separate from the dashboard/songrequest (it
shows internal snippets):

- `CONFIG_USERNAME` (default `admin`) + `CONFIG_PASSWORD`.
- If `CONFIG_PASSWORD` is empty, it falls back to the shared
  `APP_USERS`/`APP_PASSWORD`.

Without any password the service refuses to start (fail-closed) – the page may be
exposed.

## Configuration (central `.env`)

| Variable | Default | Purpose |
| --- | --- | --- |
| `CONFIG_PORT` | `5058` | host port of the web UI |
| `CONFIG_USERNAME` | `admin` | charts-config username |
| `CONFIG_PASSWORD` | – | password (empty = fall back to `APP_USERS`/`APP_PASSWORD`; if both are missing the service does not start) |
| `SESSION_SECRET` | – | random string for session cookies; empty = re-login after restart |
| `LASTFM_API_KEY` | – | optional, for the Last.fm tag check (empty = Last.fm box greyed out) |
| `DASHBOARD_GENRE_CACHE_TTL` | `86400` | cache TTL of the Deezer genre table in the source finder (seconds; refreshed on the first call after expiry) |
| `CHARTS_LANG` | – (empty = English) | language for the UI **and** logs (`de`/`fr` available) – one stack-wide switch, no in-UI toggle |

## Access

On the LAN directly via `http://<host>:5058`. For external access put it behind a
reverse proxy (e.g. `config.your-domain.tld` → `http://<host>:5058`, no WebSocket
needed) and use **HTTPS** – the login uses a session cookie.

## Security

- Runs as an **unprivileged user** (`PUID:PGID`), **not** as root.
- **No** Docker access: charts-config only talks to Deezer/Last.fm (WAN) and
  reads the charts-energy status JSON. No socket mount, no proxy, no
  container start/stop.
- Mounts the Charts folder **read-only** (only for the status JSONs).

## Installation

charts-config is part of the charts-stack and is **not** installed on its own,
but via the shared stack project (one `compose.yml` + one central `.env` in the
`charts-stack` folder). See the main README for full installation. To
rebuild/restart just this service: `docker compose up -d --build charts-config`
from the `charts-stack` folder.

## Language

charts-config follows the stack-wide `CHARTS_LANG` (empty = English; `de`/`fr`
available). One switch for the whole stack, no in-UI toggle – change the language
by editing the `.env` and restarting the container (no rebuild; the language
files come read-only from the `/lang` mount).
