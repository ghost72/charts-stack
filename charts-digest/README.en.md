> 🇬🇧 English version. Deutsche Version: [README.md](README.md)

# charts-digest

Sends a daily **Pushover summary** of the songs sorted into the library in the
last 24 h – grouped by source (current charts, Swiss Mundart, FLASHBACK FM,
manually requested …).

A pure notification/report service of the `charts-*` family (like
[`charts-watchdog`](../charts-watchdog) and [`charts-dashboard`](../charts-dashboard)) –
it touches **no** audio files and downloads nothing.

## How it works

```
charts-import  ──writes one line per sorted-in song──►  .charts-imported-log.jsonl
                                                                │
charts-digest  ──reads log, filters to 24 h, groups────────────┘
      │
      ▼
  Pushover: "🎉 N new songs in your library" (by source)
```

1. `charts-import` writes one line to the import log
   (`.charts-imported-log.jsonl` in the Charts folder) for **every** sorted-in
   song: `{ts, source, artist, title}`.
2. `charts-digest` waits until the target time (default **09:00**), reads the
   log, filters to the look-back window (default **24 h** = yesterday) and
   groups the songs by source.
3. It sends a formatted Pushover message with an emoji per source. If nothing
   is new, it stays silent by default (no "0 songs" spam).
4. Afterwards the log is rewritten compactly (entries older than
   `LOG_RETENTION_DAYS` are dropped) so it does not grow unbounded.

**Trustworthy data basis:** the service does **not** guess from file timestamps
(mtime) or titles – it uses only what `charts-import` actually recorded on
import (correct source, canonical name). Later retagging/cover backfill
therefore does not distort the summary.

## Trigger manually (any time)

Independently of the daily schedule, the summary can be triggered immediately
(sends an honest reply even with 0 new songs):

```bash
docker exec charts-digest python -m app.main --now
```

## Configuration (central `.env`)

| Variable | Default | Meaning |
|----------|---------|---------|
| `DIGEST_HOUR` | `9` | Hour (local time) of the daily message. |
| `DIGEST_MINUTE` | `0` | Minute of the daily message. |
| `DIGEST_WINDOW_HOURS` | `24` | Look-back window in hours (24 = since yesterday same time). |
| `DIGEST_MAX_PER_SOURCE` | `100` | Safety cap per source; the real limit is Pushover's 1024-char message size (long titles are shortened at the end). Rest shown as "… and N more". |
| `DIGEST_NOTIFY_WHEN_EMPTY` | `0` | Send a message even with 0 new songs (`1`) or stay silent (`0`). |
| `DIGEST_LOG_RETENTION_DAYS` | `7` | Import-log entries older than this many days are dropped on each run. |
| `DIGEST_RUN_ON_START` | `0` | Send a summary immediately on container start (for testing only). |
| `PUSHOVER_TOKEN` / `PUSHOVER_USER` | – | Pushover access (without it the service stays silent, log only). |
| `PUSHOVER_DEVICE` | – | Optional target device (comma-separated allowed). |
| `PUSHOVER_SOUND` | `magic` | Notification sound for the daily summary. Valid Pushover sounds: `pushover, magic, cosmic, intermission, bike, bugle, cashregister, classical, falling, gamelan, incoming, mechanical, pianobar, siren, spacealarm, tugboat, alien, climb, persistent, echo, updown, none` (= silent), `vibrate`. Empty = account default. Overridable via `PUSHOVER_SOUND_DIGEST` in `compose.yml`. |

> **Setting up Pushover (optional).** Notifications are entirely optional —
> without `PUSHOVER_TOKEN`/`PUSHOVER_USER` the service keeps running silently
> (log only). If you want them, you need **two** values from your Pushover
> account ([pushover.net](https://pushover.net)):
> 1. `PUSHOVER_USER` — your personal **user key** (shown at the top of the
>    dashboard after login).
> 2. `PUSHOVER_TOKEN` — the **API token of your own Application**, created once
>    via "Create an Application/API Token".
>
> The **sender name** shown on your phone is the **name of that Application** —
> changeable only in the Pushover web UI (edit the Application), not via any
> variable in this repo. Rename the app and new messages arrive under the new
> name; already-delivered ones keep the old.

## Container

| Container | Purpose |
|-----------|---------|
| `charts-digest` | Scheduler loop: waits until the target time, sends the summary. No web server. |

Reuses the same copied building blocks as the sibling services
(`notify.py`/`status.py`) – no runtime dependency.

## Data & paths

| Host path | Purpose |
|-----------|---------|
| `/volume2/music/Charts` | Import log (`.charts-imported-log.jsonl`) + status (`.charts-status/`) |

**No** music folder is mounted – the service only reads the log.

## Operation

```bash
docker compose up -d --build charts-digest   # build + start
docker logs -f charts-digest                  # watch scheduler/runs
docker exec charts-digest python -m app.main --now   # send immediately
```

The service appears on the **charts-dashboard** in the monitoring zone (status,
last run, next scheduled run, cumulative summaries).

## Legal note

Pure notification service – it neither downloads nor modifies content. See the
main [README](../README.md) for the legal framework of the whole stack.
