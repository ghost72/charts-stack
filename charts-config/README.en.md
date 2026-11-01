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

### Manual tag editor (read-write)

The one **write** tool on the page. Search the music library, pick a song and
edit its tags (artist, title, album, year, track) and/or replace the embedded
cover (upload or image URL), then **Save**. The tag edit is always written
**in place first** in the existing `.mp3` (fail-open: the edit is never lost even
if the move below fails), the file's modification time is **preserved** (so
Navidrome/audiobookshelf-style "recently added" floods are avoided), and only the
fields you actually changed are written.

On every successful save the two stack provenance markers (`TXXX:CHARTS_SOURCE`
and `TXXX:CHARTS_ALBUM_SRC`) are **cleared**, so the automatic
backfill/album-recorrection treats the song as hand-curated and never overwrites
your edit again. The editor **never empties a field** – leaving the year or track
box blank keeps the existing value (an emptied field on a manifest song would be
refilled by the backfill). A small badge marks each hit as **stack** (downloaded
by the stack, via the marker or the manifest) or **curated** (your own file).

**Folder follows the artist tag (automatic move).** When a save *changes the
artist*, the file is moved on disk so its folder matches the corrected artist —
with a strict **one-folder guarantee**:

- An existing artist folder with the **same name** or the **same folded key**
  (casing, hyphen, apostrophe or accent variants like `Motörhead`/`Motorhead`,
  `rod stewart`/`Rod Stewart`, `DJ.ILHAM`/`DJ ILHAM`) → the file is **merged
  into** that one folder automatically, no second folder is ever created.
- If the old artist folder **empties out** after the file leaves and there is no
  matching folder → the old folder is **renamed** to the new artist name (one
  folder, carrying everything with it).
- A **truly new** artist with no match → a new folder is created automatically.
- A **decision dialog** appears only for two genuinely ambiguous cases, and
  **nothing is moved until you choose**:
  - a *stylized near-spelling* (e.g. `P!nk`) that matches an existing `Pink/`
    folder which still holds **other songs** → *file into `Pink/`* /
    *create new `P!nk/`* / *cancel*;
  - a **song collision** — the destination already has this song (exact file or
    the same title under a Lidarr `NN - Title` name) → *keep existing, don't move
    mine* / *cancel*. The editor **never overwrites** and never writes a `(2)`.
- Cancelling a dialog keeps the **saved tags at the old path** — the tag edit is
  already committed, only the move is skipped.

Moves are **stack-singles only** (`<Artist>/<Title>/<Title>.mp3`); Lidarr
`<Album> (Year)/NN - Title` albums and curated/`Various Artists` compilations are
left **in place**. The move is a pure `os.rename` (mtime preserved, no copy
fallback: a cross-device layout is hard-refused and the file stays put with its
saved tags). The one-folder durability **depends on Lidarr "Rename Tracks" being
OFF** — otherwise a later Lidarr scan can rename the moved single into an
`<Album> (Year)/` folder and reintroduce a duplicate.

Notes and limits:

- **MP3 only.** Non-MP3 files can be selected and viewed but are shown
  read-only (saving is restricted to `.mp3`).
- **Cover replace only** (no removal); a cover URL must be `http`/`https`
  (redirects are not followed – a small SSRF guard).
- This section needs the library mounted **read-write** at `/music/Music` (see
  Installation). Without that mount the editor is inert: the search returns
  nothing and nothing is writable. **`/charts` stays read-only** – the manifest
  is only read there.

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
| `CONFIG_DONATE` | `1` | discreet donate footer (PayPal/Revolut); `0`/`false`/`no` hides it |

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

### Manual tag editor – mount and rebuild

The tag editor needs the music library mounted **read-write**. The stack
`compose.yml` already adds this to the `charts-config` service:

```yaml
    volumes:
      - ${MUSIC_ROOT:-/volume2/music}/Charts:/charts:ro          # status + manifest, read-only
      - ./lang:/lang:ro                                          # language files
      - ${MUSIC_ROOT:-/volume2/music}/Music:/music/Music         # read-write for the tag editor
```

`/charts` stays **read-only** (the manifest is only read there); only
`/music/Music` is read-write. After pulling the new code, rebuild the image so
the new dependencies (`mutagen`, `Pillow`) are installed:

```bash
docker compose build --no-cache charts-config
docker compose up -d charts-config
```

**New language keys need a restart.** The `lang/{en,de,fr}.json` files are
bind-mounted read-only at `/lang`; a `--no-cache` rebuild alone does **not**
pick up changed language files. After copying the updated `lang/` files into the
NAS mount, run `docker restart charts-config` so the loader re-reads the table –
otherwise the new tag-editor labels show as raw keys. On Synology, if raw keys
persist, re-create the container (`docker compose up -d --force-recreate
charts-config`).

## Language

charts-config follows the stack-wide `CHARTS_LANG` (empty = English; `de`/`fr`
available). One switch for the whole stack, no in-UI toggle – change the language
by editing the `.env` and restarting the container (no rebuild; the language
files come read-only from the `/lang` mount).
