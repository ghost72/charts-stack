> 🇬🇧 English version. Deutsche Version: [README.md](README.md)

# charts-songrequest

Small **web UI** for manually requesting individual songs: enter artist + title
→ the song is searched in slskd, the best **MP3 ≥ 256 kbps** is downloaded and
automatically filed into the library. No file moving, no terminal – ideal also
while on the go (behind the reverse proxy).

Fourth service of the `charts-*` family alongside
[`charts-sync`](../charts-sync), [`charts-download`](../charts-download) and
[`charts-import`](../charts-import).

---

## How it works

```
Web UI (artist + title)
      │
      ▼
charts-songrequest ─► searches via slskd (same filters as charts-download:
      │                MP3 ≥ 256, no remixes/live/compilations)
      │             ─► enqueues the best match as a download
      │             ─► writes a manifest entry (source="manuell")
      ▼
slskd ───────────────► downloads into MUSIC_ROOT/Charts/
      ▼
charts-import ───────► picks up the file (manifest hit!) into the library
                       automatically, tags + cover, reports "1 aus manuell"
```

The trick: charts-songrequest writes into the **same manifest** that
`charts-download` writes and `charts-import` reads. This way the complete
existing import pipeline (tags, cover, Lidarr unmonitor, Navidrome) kicks in with
no extra logic at all. Because `REQUIRE_MANIFEST=1` applies in charts-import, the
manual song is imported **specifically** (not as a random downloads-folder find).

### Automatic quality comparison
charts-songrequest **always** searches in slskd and compares the best bitrate
found with the version possibly already present in the library. Three cases:

- **Not present** → download (normal new addition).
- **Present, but lower quality** → the better version is downloaded and
  **replaces** the existing one (upgrade). The UI message is in German:
  „⬆️ Bessere Version wird geladen (320 statt 256 kbps)" (*"loading a better
  version, 320 instead of 256 kbps"*).
- **Present in equal/better quality** → **nothing** is downloaded. The UI shows
  the actual backend message (in German): either „ℹ️ Schon in guter Qualität
  vorhanden (X kbps)" (*"already present in good quality"*) OR – if the existing
  version itself is below `MIN_BITRATE` and nothing better currently shows up on
  the network – honestly „ℹ️ Vorhanden mit X kbps, aktuell keine bessere Version
  gefunden" (*"present at X kbps, no better version found right now"*).

For this the Navidrome library is mounted **read-only** (`/music/Music:ro`);
`mutagen` reads the bitrate of the existing file. Song detection is identical to
charts-import (normalized title, case-insensitive artist).

### Retry across multiple providers
Soulseek is P2P: the best match may come from a provider (end user who shares the
file) who is currently offline or declines the upload. That is why
charts-songrequest does not look only for the single best match but keeps all
matching candidates (sorted by quality) ready and triggers them **one after
another** until a provider actually accepts the download. For an upgrade only
candidates that are genuinely better than the existing version are considered.
Only if not a single provider delivers does the UI report an error.

**Upgrade mechanics:** On an upgrade charts-songrequest writes a
`"replace": true` into the manifest entry. charts-import then replaces the
existing file **only if** the new bitrate is really higher – the old version
moves into a trash (`MUSIC_DIR/.charts-trash/<Datum>/`, reversible), the new one
lands in the same place (no duplicate).

### Pushover notification only on availability
charts-songrequest itself no longer sends a Pushover message when requesting
(the download start is already shown in the web UI). Instead the requesting user
receives **one** message on **their own device** once the song is actually in
the library – sent by `charts-import` after the import ("🎵 Dein Song ist da!"
with artist, title, album, source).

For this, a Pushover device is configured per user (`USER_PUSHOVER_DEVICES`,
format `user:device`), matched against the **login username**. On request,
charts-songrequest writes that device as `notify_device` into the manifest;
charts-import reads it and notifies availability to exactly that device.

**Privacy/security:** If a user is **not** listed in `USER_PUSHOVER_DEVICES` (or
the variable is empty), **no** message is sent – never to all account devices.
There is deliberately no device picker in the UI.

---

## Access protection

The page is reachable from outside via the reverse proxy, so a login is
**mandatory**. There is **multi-user support**:

- `SONGREQUEST_USERS="user1:pass1,user2:pass2"` – any number of users
  (comma-separated; a password must not contain `:` or `,`). This is the only
  login method (pure multi-user login).

At least one user must be defined, otherwise the
container does not start. The logged-in user appears in the log and determines
(via `USER_PUSHOVER_DEVICES`) the target device of the availability message.
Login via cookie session; set
`SESSION_SECRET` so sessions survive restarts.

---

## Installation on the Synology NAS

charts-songrequest is part of the charts-stack and is **not** installed on its
own, but via the shared stack project (one `compose.yml` + one central `.env` in
the `charts-stack` folder). See the main README for full installation. To
rebuild/restart just this service: `docker compose up -d --build charts-songrequest`
from the `charts-stack` folder.

1. **Copy the files to the NAS** (`app/`, `Dockerfile`, `compose.yml`,
   `requirements.txt`, `.env`).

2. **Create `.env`:**
   ```bash
   cp .env.example .env
   # set SLSKD_API_KEY (same as charts-download), SONGREQUEST_USERS and SESSION_SECRET
   #   openssl rand -hex 32   → for SESSION_SECRET
   ```

3. **Build and start:**
   ```bash
   sudo docker compose up -d --build
   sudo docker compose logs -f
   ```

4. **Access – LAN vs. internet:** By default the app only runs on the local
   network at `http://<NAS-IP>:5056`. That is enough if you only request songs
   from home.

   **For access while on the go (from the internet)** you additionally need:
   - a **reverse proxy** with HTTPS that forwards a subdomain (e.g.
     `songs.example.com`) to `http://<NAS-IP>:5056` (no WebSocket needed), with a
     Let's Encrypt certificate for the subdomain;
   - **port forwarding/DNS** so the subdomain is reachable from outside (router
     port forwarding to the reverse proxy, or DynDNS).

   > ⚠️ **Security:** Once the app is reachable over the internet, anyone can
   > open it. That is why `SONGREQUEST_USERS` is **mandatory** (the app will not
   > start without it) and HTTPS is required. Use strong passwords. If you don't
   > need external access, skip the reverse proxy and use the LAN only.

5. Open the URL in the browser → log in with a user/password from `SONGREQUEST_USERS` → request a song.

---

## Configuration (`.env`)

| Variable            | Default | Meaning |
|---------------------|---------|---------|
| `SLSKD_HOST`        | `http://slskd:5030` | slskd URL (container name in the same Docker network). |
| `SLSKD_API_KEY`     | *(empty, mandatory)* | readwrite key from slskd.yml (same as charts-download is fine). |
| `SONGREQUEST_USERS` | *(empty, mandatory)* | multi-user login: `user1:pass1,user2:pass2`. The only login method. |
| `SESSION_SECRET`    | *(empty)* | random string for session cookies. Empty = re-login after restart. |
| `MIN_BITRATE`       | `256` | minimum MP3 bitrate. |
| `MATCH_THRESHOLD`   | `80` | similarity 0-100 between "Artist Title" and filename. |
| `REQUIRE_FREE_SLOT` | `1` | only providers with a free upload slot. |
| `EXCLUDE_KEYWORDS`  | *(default list)* | only original versions (no remixes/live/lyrics …). `off`/`none` = off. |
| `REJECT_COMPILATIONS`| `1` | reject samplers/compilations (prefer a real single/album version). |
| `MANIFEST_FILE`     | `/charts/.charts-manifest.json` | **Must be the same file as for charts-import/-download.** |
| `MANUAL_SOURCE_LABEL`| `manuell` | label in the charts-import message. |
| `SONGREQUEST_PORT`  | `5056` | web UI port. |
| `PUID` / `PGID`     | `1027` / `100` | user/group (youruser:users). |
| `PUSHOVER_*`        | *(empty)* | Pushover account (token/user). charts-songrequest no longer sends anything itself; the availability message comes from charts-import. |
| `USER_PUSHOVER_DEVICES` | *(empty)* | Pushover device per user (`user1:device1,user2:device2`), matched against the login name. Only listed users get the availability message on their device. Not listed = no message. |
| `PUSHOVER_DEVICE`   | *(empty)* | Optional: target device. Multiple devices possible, comma-separated (e.g. `iPhone-Max,Tablet-Anna`) — one message is sent per device. All devices belong to the same Pushover account. Empty field = delivery to all devices of the account. Invalid/disabled device names are stripped before sending — otherwise Pushover delivers an unknown name to **all** devices instead (double push). |
| `PUSHOVER_SOUND` | `magic` | Notification sound (code default). **Note:** currently has no effect — charts-songrequest does **not** send any Pushover message itself; the availability message ("🎵 Your song is here!") comes from **charts-import** and uses `PUSHOVER_SOUND_MANUAL` (= `magic`) there. The variable is kept for consistency and only takes effect if songrequest later sends on its own. Valid Pushover sounds: `pushover, magic, cosmic, intermission, bike, bugle, cashregister, classical, falling, gamelan, incoming, mechanical, pianobar, siren, spacealarm, tugboat, alien, climb, persistent, echo, updown, none` (= silent), `vibrate`. Overridable via `PUSHOVER_SOUND_SONGREQUEST` in `compose.yml`. |
| `PUSHOVER_DEVICES_TTL` | `3600` | Optional: cache duration (seconds) for the account device list used for filtering. |

---

## Endpoints

| Path            | Purpose |
|-----------------|---------|
| `GET /`         | web UI (after login). |
| `GET /login`    | login page. |
| `POST /login`   | log in (password). |
| `GET /logout`   | log out. |
| `POST /api/request` | request a song (form: `artist`, `title`). |
| `GET /health`   | health check. |

---

## Limits

- slskd allows **no** custom download target directory per download (open
  upstream feature request). That is why this route goes via the manifest
  instead of a separate folder.
- If slskd finds no matching version (MP3 ≥ bitrate, no remix/sampler), the UI
  reports "no match" – then check the spelling or try again later.
- Result quality depends on the Soulseek network (as with charts-download).
