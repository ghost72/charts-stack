> 🇬🇧 English version. Deutsche Version: [README.md](README.md)

# slskd (download engine for the charts stack)

[slskd](https://github.com/slskd/slskd) is a modern Soulseek client with a
web UI and a REST API. The charts stack uses it to search for and download
songs: **charts-download**, **charts-upgrade** and **charts-songrequest** talk
to slskd over the REST API.

> Note: slskd is a standalone service (it needs its own Soulseek login) and is
> part of the single charts-stack compose project, because the whole charts
> stack depends on it.

## How it interacts with the charts stack

```
charts-download/-upgrade/-songrequest  --REST-API-->  slskd
                                                        |
                                        downloads into  /downloads
                                                        |
                     = MUSIC_ROOT/Charts (host)  = /charts (charts services)
                                                        |
                                        charts-import picks up the file
```

The key point: slskd's **`/downloads`** and the charts services' **`/charts`**
point to the **same host folder** (`MUSIC_ROOT/Charts`). Only that way does
charts-import find the finished downloads. `MUSIC_ROOT` must therefore be
identical for slskd and all charts services.

## Setup

> slskd is part of the **one** charts-stack project. There is no separate slskd
> `.env` any more – all values live in the central `.env` in the charts-stack
> root (`MUSIC_ROOT`, `PUID`/`PGID`, `SLSKD_CONFIG_DIR`, `TZ`). Run all commands
> below from the **charts-stack root**.

> **Important – ordering:** the `SLSKD_API_KEY` is chosen by YOU (any long
> random string) and must appear in TWO places: in the central `.env` AND in
> the `slskd.yml`. Because the `slskd` service in the `compose.yml` requires a
> non-empty `SLSKD_API_KEY` (`:?` guard), you MUST write it into the `.env`
> first – otherwise slskd will not start at all. So: set the key first (step 1),
> then start (step 2).

### 1. Choose the key + write it into the central `.env`

```bash
cp .env.example .env            # central .env in the charts-stack root (if not there yet)
openssl rand -hex 32            # generates a random key - copy the output
```

Enter the generated key as `SLSKD_API_KEY=<your-key>` in the **central `.env`**.
charts-download, charts-upgrade, charts-songrequest and the dashboard read it
from there. `SLSKD_HOST` defaults to `http://slskd:5030` (the container name on
the project network) – that works automatically in the one-project setup.

### 2. Start slskd (creates the slskd.yml)

```bash
docker compose up -d slskd
docker compose logs -f slskd
```

On the first start slskd creates a `slskd.yml` under `SLSKD_CONFIG_DIR` (default
`./slskd/slskd-config`).

### 3. Soulseek login + the SAME API key in the slskd.yml

Open `slskd.yml` (in `SLSKD_CONFIG_DIR`) and set two things:

**a) Soulseek account** – the username and password are **chosen by you and
self-registered inside slskd on the first login** (there is no website sign-up;
the slskd docs describe the process):

```yaml
soulseek:
  username: <your-soulseek-user>
  password: <your-soulseek-password>
```

**b) API key with role `readwrite`** – **exactly the same** key as in the `.env`
(from step 1):

```yaml
web:
  authentication:
    api_keys:
      charts:
        key: <the-same-key-as-in-the-.env>
        role: readwrite
```

Then restart slskd (from the charts-stack root):

```bash
docker compose restart slskd
```

### 4. Start the rest

```bash
docker compose up -d --build
```

## Ports

| Port | Purpose |
|------|-------|
| `5030` | Web UI + REST API (on the LAN / project network) |
| `50300` | Soulseek peer port (incoming connections) |

## Network

All services run in **one** compose project and automatically share its project
network – the charts services reach slskd via the container name
`http://slskd:5030`. You do **not** need to create a network by hand.

## Data & paths

| Host path | Container | Purpose |
|-----------|-----------|-------|
| `SLSKD_CONFIG_DIR` | `/app` | slskd.yml + database (persistent) |
| `MUSIC_ROOT/Charts` | `/downloads` | download target (= `/charts` of the charts services) |
| `MUSIC_ROOT/SeedShare` | `/seedmusic` | optional seed folder (sharing on the network) |

## Version

The image is pinned to `slskd/slskd:0.26.0` (instead of `:latest`) for
reproducible deployments. Check the slskd changelog before updating.
