> 🇬🇧 English version. Deutsche Version: [README.md](README.md)

# charts-upgrade

Cleans up the **existing stock** of the Navidrome library in terms of quality.
Finds low-quality songs and fetches a genuinely better version — but replaces
**only** when the new file is provably better.

Two kinds of "bad" are detected:

1. **Low bitrate** – the file is honestly below `BAD_BITRATE` (e.g. an old
   128 kbps rip).
2. **Upsampling / faked bitrate** – the file *claims* e.g. 320 kbps but actually
   has only the frequency range of a 128 (transcoded up). This is the more
   common, invisible case: the tag lies.

## How upsampling is detected (frequency cutoff)

An MP3 encoder discards everything above a bitrate-dependent limit (lowpass).
Rule of thumb:

| Bitrate | real cutoff |
|---------|-------------|
| 128 kbps | ~16 kHz |
| 192 kbps | ~19 kHz |
| 256 kbps | ~19–20 kHz |
| 320 kbps | ~20–20.5 kHz |

If a file claims 320 kbps but actually has energy only up to ~16 kHz, it is very
likely upsampled. charts-upgrade measures the real cutoff with **ffmpeg**
(`aspectralstats`/`rolloff`, median across all frames) and compares it against
the bitrate expectation.

**Conservative by design** – the goal is to wrongly flag as few legitimate songs
as possible:

- If the cutoff cannot be measured (ffmpeg problem) → the file is considered
  **ok** and is never touched.
- Only if the cutoff is **well** below the expectation (tolerance
  `CUTOFF_TOLERANCE_KHZ`, default 2 kHz) is the file considered upsampled.
- Old/acoustic recordings with naturally few highs are a known edge case – the
  tolerance reduces false alarms. When in doubt, better not touch it.

## Safe replacement (download → measure → replace only on a real upgrade)

Soulseek is full of fake 320s – the new file could be just as upsampled. That is
why nothing is ever swapped blindly:

1. Search slskd for a replacement (`REJECT_COMPILATIONS=1`, no samplers; only
   MP3 ≥ `MIN_BITRATE`).
2. Download the best version, wait for completion.
3. **Measure the real cutoff of the downloaded file** and compare it with the
   existing one (`quality_key`: the real cutoff dominates, bitrate is only a
   tie-breaker).
4. **Only if genuinely better** → manifest entry with `replace=true`;
   charts-import replaces the existing file (old one into the trash
   `.charts-trash/`, reversible).
5. **Not better** → block the provider for exactly this song
   (blocklist, expires after 30 days) and do not keep it. This avoids endlessly
   downloading the same junk from the same provider. Other songs from the
   provider remain allowed.

If nothing better is left after filtering, the **existing file** stays – a worse
one is never brought in.

## Compilations are off-limits

Curated compilations (Bravo Hits, Kuschelrock, …) are complete albums and are
**never** replaced track by track with Soulseek singles. The scanner skips
compilation folders entirely – recognized via the path heuristic (`various`,
`best of`, `hits`, …) **plus** artist spread within the album folder (many
different artists = sampler). Controllable via `SKIP_COMPILATIONS`.

## Position in the chain

```
charts-sync  → charts-download → slskd → charts-import → Navidrome
                                              ▲
charts-upgrade (existing stock) ───────────────┘
   scans the library, downloads a replacement + writes a replace manifest;
   charts-import performs the swap (on a real cutoff upgrade)
```

charts-upgrade deliberately uses the same (copied) building blocks as its sister
services: `matching.py`, `slsk.py`, `manifest.py`, `notify.py`, `quality.py` –
no runtime dependency between the containers.

> **The upsampling check also runs in ongoing operation:** charts-import checks
> every freshly downloaded file with the same `quality.py` and rejects fake 320s
> (quarantine + re-search). This means that from now on no upsampled junk comes
> in through charts-download / charts-songrequest either – charts-upgrade takes
> care of the **existing stock**.

## Container

| Container | Purpose |
|-----------|---------|
| `charts-upgrade` | Scheduler loop: scans the library, downloads/checks/replaces. No web server. |

Needs **ffmpeg** in the image (for the cutoff measurement) – it is included in
the Dockerfile.

## Setup

### 1. Configuration

```bash
cp .env.example .env
# set at least SLSKD_API_KEY. Pushover optional.
```

For `PUSHOVER_DEVICE`, multiple devices are possible, comma-separated (e.g.
`iPhone-Max,Tablet-Anna`) — one message is sent per device. All devices
belong to the same Pushover account. Empty field = delivery to all devices of
the account. Invalid/disabled device names are stripped before sending —
otherwise Pushover delivers an unknown name to **all** devices instead (double
push). The device list used for this is cached for `PUSHOVER_DEVICES_TTL`
seconds (default `3600`).

`PUSHOVER_SOUND` controls the notification sound of this service (code default
`cosmic`). Valid Pushover sounds: `pushover, magic, cosmic, intermission,
bike, bugle, cashregister, classical, falling, gamelan, incoming, mechanical,
pianobar, siren, spacealarm, tugboat, alien, climb, persistent, echo, updown,
none` (= silent), `vibrate`. Empty = account default (no `sound` field).
Overridable via `PUSHOVER_SOUND_UPGRADE` in `compose.yml`.

Important switches (see `.env.example` for all):

| Variable | Default | Meaning |
|----------|---------|---------|
| `BAD_BITRATE` | `256` | songs with a real bitrate below this are considered bad |
| `CHECK_UPSAMPLED` | `1` | detect upsampling via frequency cutoff |
| `CUTOFF_TOLERANCE_KHZ` | `2.0` | how far below the expectation the cutoff may be |
| `SKIP_COMPILATIONS` | `1` | never upgrade compilation folders |
| `MAX_PER_RUN` | `10` | max. confirmed upgrades per run |
| `MAX_SCAN_PER_RUN` | `200` | max. files analyzed with ffmpeg per run (CPU protection) |
| `INTERVAL_MINUTES` | `1440` | run interval (default 24h) |
| `DRY_RUN` | `1` | **only analyze/report, download nothing** – start this way! |

### 2. Deploy (on `your-nas`)

Copy the code to `/path/to/charts-upgrade/` (NVMe), then:

```bash
sudo docker compose up -d --build
```

or via the Container Manager (point the project at the folder with `compose.yml`).

### 3. Observe in a test run first (DRY_RUN)

With `DRY_RUN=1` (default) the service downloads **nothing**. It scans, reports
via log/Pushover which songs it classifies as bad/upsampled, and what it would
download:

```bash
sudo docker compose logs -f charts-upgrade
```

**Check the reported cases against real examples from your library** (e.g. look
at the spectrogram with Spek). If the detection fits, fine-tune
`CUTOFF_TOLERANCE_KHZ` if needed (larger = more lenient). Only then set
`DRY_RUN=0`.

### 4. Go live

```bash
# in .env: DRY_RUN=0
sudo docker compose up -d
```

## CPU note (DS1621+)

The cutoff measurement decodes each checked file once with ffmpeg. On the
DS1621+ (4 cores, no HW transcoding) the **first pass** over the entire library
is noticeable. Therefore:

- The analysis cache in the state (`.charts-upgrade-state.json`, key `seen`)
  remembers `mtime:size` per file – each file is decoded **only once**. Later
  runs only check new/changed files.
- `MAX_SCAN_PER_RUN` caps the analysis per run. The service works through the
  stock gently over several runs instead of computing everything at once.
- Runs only every 24h by default. For a faster first scan, temporarily raise the
  interval/`MAX_SCAN_PER_RUN` (at night).

## Data & paths

| Host path | Purpose |
|-----------|---------|
| `/volume2/music/Music` | Navidrome library (scanned read-only) |
| `/volume2/music/Charts` | shared folder: state, manifest, slskd downloads |
| `/volume2/music/Music/.charts-trash/` | trash of replaced versions (charts-import, reversible) |

## Operation

```bash
# Logs
sudo docker compose logs -f charts-upgrade

# Rebuild/restart after code change
sudo docker compose up -d --build

# Stop
sudo docker compose down
```
