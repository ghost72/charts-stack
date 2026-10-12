> 🇬🇧 English version. Deutsche Version: [README.md](README.md)

# slskd (download engine for the charts stack)

[slskd](https://github.com/slskd/slskd) is a modern Soulseek client with a
web UI and a REST API. The charts stack uses it to search for and download
songs: **charts-download**, **charts-upgrade** and **charts-songrequest** talk
to slskd over the REST API.

> Note: slskd is a standalone service (it needs its own Soulseek login) and is
> run here as a separate compose project because the whole charts stack depends
> on it.

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

### 1. Configuration

```bash
cp .env.example .env
# MUSIC_ROOT, PUID/PGID, CONFIG_DIR, TZ anpassen (siehe Kommentare in .env.example).
```

### 2. First start (creates the slskd.yml)

```bash
docker compose up -d
docker compose logs -f slskd
```

On the first start slskd creates a `slskd.yml` under `CONFIG_DIR`.

### 3. Soulseek login + API key in the slskd.yml

Open `slskd.yml` (in `CONFIG_DIR`) and set two things:

**a) Soulseek account** (free on the Soulseek network; the slskd docs describe
the registration):

```yaml
soulseek:
  username: <dein-soulseek-benutzer>
  password: <dein-soulseek-passwort>
```

**b) API key with role `readwrite`** (that's what the charts services need):

```yaml
web:
  authentication:
    api_keys:
      charts:
        key: <langer-zufaelliger-key>   # z.B. openssl rand -hex 32
        role: readwrite
```

Then restart slskd:

```bash
docker compose restart slskd
```

### 4. Carry the API key over into the charts services

Enter the chosen key as `SLSKD_API_KEY` in the `.env` of **charts-download**,
**charts-upgrade** and **charts-songrequest**. There, `SLSKD_HOST` defaults to
`http://slskd:5030` (if everything runs in the same Docker network) or
`http://<NAS-IP>:5030`.

## Ports

| Port | Purpose |
|------|-------|
| `5030` | Web UI + REST API (on the LAN / Docker network) |
| `50300` | Soulseek peer port (incoming connections) |

## Shared Docker network (recommended)

So that the charts services can reach slskd via `http://slskd:5030` (container
name instead of IP), they should all be on the same Docker network. Either put
all services into ONE compose project, or create an external network:

```bash
docker network create charts-net
```

and reference it in every compose.yml (slskd + charts services):

```yaml
services:
  <dienst>:
    networks: [charts-net]
networks:
  charts-net:
    external: true
```

Alternatively, without a shared network: set `SLSKD_HOST=http://<NAS-IP>:5030`.

## Data & paths

| Host path | Container | Purpose |
|-----------|-----------|-------|
| `CONFIG_DIR` | `/app` | slskd.yml + database (persistent) |
| `MUSIC_ROOT/Charts` | `/downloads` | Download target (= `/charts` of the charts services) |
| `MUSIC_ROOT/SeedShare` | `/seedmusic` | Optional seed folder (sharing on the network) |

## Version

The image is pinned to `slskd/slskd:0.26.0` (instead of `:latest`) for
reproducible deployments. Check the slskd changelog before updating.
