> 🇬🇧 English version. Deutsche Version: [README.md](README.md)

# charts-watchdog

Silent guard for the charts-stack: watches all containers and sends a Pushover
message when one fails – and an all-clear when it recovers. No UI, no custom
image (uses `docker:cli` + a small shell script).

Counterpart to [`charts-dashboard`](../charts-dashboard) (status UI to look at);
the watchdog **actively** notifies you when something is wrong.

---

## What gets reported

Per container, only on a state **change**:

- **unhealthy** – the healthcheck trips (e.g. slskd no longer logged in to the
  Soulseek server).
- **restart loop** (`restarting`) – a container won't come up.
- **stopped** (`exited`/`dead`) – unexpectedly terminated and stays that way.
- **recovery** – back to "running", all-clear.

## External/dependent services

On top of the containers, the watchdog periodically checks the **reachability of
the external services** the stack depends on – using the exact same reachability
criteria as the [`charts-dashboard`](../charts-dashboard):

- **slskd** – `/api/v0/server` reports logged in (`isLoggedIn`).
- **Lidarr** – `/api/v1/system/status` responds (only if `LIDARR_URL` +
  `LIDARR_API_KEY` are set, otherwise not monitored).
- **ListenBrainz** – `validate-token` responds at all (HTTP 400 counts as
  reachable too).
- **Deezer** – chart endpoint returns HTTP 200 with no error object.
- **Last.fm** – chart endpoint returns HTTP 200 without an error (only with
  `LASTFM_API_KEY`, otherwise not monitored).
- **iTunes** – search endpoint reachable; being **throttled** (HTTP 429/403)
  deliberately counts as reachable, **not** as an outage.

If a service is unreachable **continuously** for longer than
`WATCH_EXTERNAL_DEBOUNCE_SECONDS` (default `600` = **10 min**), you get **one**
Pushover message – plus an all-clear once it recovers. Short transient timeouts
**never** trigger an immediate message (same debounce/cooldown logic as the
containers, just with its own state namespace so the external slskd check does
not collide with the slskd **container**).

**Pushover** is checked as well, but **deliberately never reported via
Pushover** – reporting a Pushover outage through Pushover would be circular. An
outage or invalid credentials therefore only go to the **container log**, never
as a push.

## No notification spam

Per problem episode there is guaranteed to be **one** problem message and **one**
all-clear:

- **Debounce:** a problem is only reported once it persists for
  `WATCH_DEBOUNCE_POLLS` polls in a row (against short start/restart blips on
  deploy).
- **Stable recovery:** the all-clear only comes after `WATCH_RECOVER_POLLS` OK
  polls in a row. A flapping app (ok/problem/ok/…) never reaches the threshold →
  no all-clear → no repeated problem message either.
- **Cooldown:** after a problem message it stays quiet for
  `WATCH_COOLDOWN_SECONDS` for that app – no matter how often it flaps.

## Configuration (central `.env`)

| Variable | Default | Purpose |
| --- | --- | --- |
| `WATCH_CONTAINERS` | all stack containers | space-separated list |
| `WATCH_INTERVAL_SECONDS` | `30` | poll interval |
| `WATCH_DEBOUNCE_POLLS` | `2` | polls before a problem is reported |
| `WATCH_RECOVER_POLLS` | `3` | OK polls before the all-clear |
| `WATCH_COOLDOWN_SECONDS` | `1800` | quiet period after a message (per app) |
| `WATCH_EXTERNAL_DEBOUNCE_SECONDS` | `600` | seconds continuously unreachable before a message (external services, default 10 min) |
| `SLSKD_HOST` / `SLSKD_API_KEY` | `http://slskd:5030` / – | slskd check (`/api/v0/server`) |
| `LIDARR_URL` / `LIDARR_API_KEY` | *(empty)* | optional – Lidarr monitored only with both set |
| `LASTFM_API_KEY` | *(empty)* | optional – Last.fm monitored only with a key |
| `PUSHOVER_TOKEN` / `PUSHOVER_USER` | – | without these only logs, no message |
| `PUSHOVER_DEVICE` | *(empty)* | Optional: target device. Multiple devices possible, comma-separated (e.g. `iPhone-Max,Tablet-Anna`) — one message is sent per device. All devices belong to the same Pushover account. Empty field = delivery to all devices of the account. Invalid/disabled device names are stripped before sending — otherwise Pushover delivers an unknown name to **all** devices instead (double push). |
| `PUSHOVER_SOUND` | `siren` | Notification sound for the watchdog alerts. Valid Pushover sounds: `pushover, magic, cosmic, intermission, bike, bugle, cashregister, classical, falling, gamelan, incoming, mechanical, pianobar, siren, spacealarm, tugboat, alien, climb, persistent, echo, updown, none` (= silent), `vibrate`. Empty = account default. Overridable via `PUSHOVER_SOUND_WATCHDOG` in `compose.yml`. |
| `PUSHOVER_DEVICES_TTL` | `3600` | Optional: cache duration (seconds) for the account device list used for filtering. |

## How it works

The `watch.sh` script queries each container's status + health. It modifies
**nothing** on other containers – it only reads and sends a Pushover message when
needed.

Access to the Docker engine does **not** go directly through the socket but
**read-only** through the `charts-docker-proxy`
(`DOCKER_HOST=tcp://charts-docker-proxy:2375`). The watchdog therefore runs as an
**unprivileged user** (youruser), not as root – only the small proxy touches the
socket, and it only allows read requests.

## Language

The user-facing watchdog texts (Pushover messages, state descriptions, log
lines) follow `CHARTS_LANG` – empty = English, `de`/`fr` available – from the
central `charts-stack/lang/` set (the same files the Python services use;
mounted read-only at `/lang`). If the language file is missing, the watchdog
degrades cleanly to English or the key name and keeps running.

Deliberately **not** translated are the internal state words (`running-healthy`,
`ok`, `problem`, `transient`, …) and the proper names of the external services
(slskd, Lidarr, ListenBrainz, Deezer, Last.fm, iTunes) – they are technical
markers or product names, not display text.
