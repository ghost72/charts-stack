> 🇩🇪 Deutsche Version. English: [README.en.md](README.en.md)

# slskd (Download-Engine für den charts-Stack)

[slskd](https://github.com/slskd/slskd) ist ein moderner Soulseek-Client mit
Web-UI und REST-API. Der charts-Stack nutzt ihn zum Suchen und Herunterladen von
Songs: **charts-download**, **charts-upgrade** und **charts-songrequest** sprechen
über die REST-API mit slskd.

> Hinweis: slskd ist ein eigenständiger Dienst (eigenes Soulseek-Login nötig) und
> wird hier als separates compose-Projekt geführt, weil der ganze charts-Stack
> davon abhängt.

## Wie es mit dem charts-Stack zusammenspielt

```
charts-download/-upgrade/-songrequest  --REST-API-->  slskd
                                                        |
                                           lädt nach  /downloads
                                                        |
                        = MUSIC_ROOT/Charts (Host)  = /charts (charts-Dienste)
                                                        |
                                              charts-import holt die Datei
```

Der entscheidende Punkt: slskds **`/downloads`** und das **`/charts`** der
charts-Dienste zeigen auf **denselben Host-Ordner** (`MUSIC_ROOT/Charts`). Nur so
findet charts-import die fertigen Downloads. `MUSIC_ROOT` muss daher bei slskd
und allen charts-Diensten identisch sein.

## Setup

> slskd ist Teil des **einen** charts-stack-Projekts. Es gibt keine eigene
> slskd-`.env` mehr – alle Werte stehen in der zentralen `.env` im
> charts-stack-Root (`MUSIC_ROOT`, `PUID`/`PGID`, `SLSKD_CONFIG_DIR`, `TZ`).
> Alle Befehle unten vom **charts-stack-Root** aus ausführen.

> **Wichtig – Reihenfolge:** Der `SLSKD_API_KEY` wird von dir SELBST gewählt
> (ein beliebiger langer Zufalls-String) und muss an ZWEI Stellen stehen: in der
> zentralen `.env` UND in der `slskd.yml`. Weil der `slskd`-Dienst in der
> `compose.yml` einen nicht-leeren `SLSKD_API_KEY` voraussetzt (`:?`-Guard),
> MUSST du ihn zuerst in die `.env` schreiben – sonst startet slskd gar nicht.
> Darum: erst Key festlegen (Schritt 1), dann starten (Schritt 2).

### 1. Key festlegen + in die zentrale `.env` schreiben

```bash
cp .env.example .env            # zentrale .env im charts-stack-Root (falls noch nicht da)
openssl rand -hex 32            # erzeugt einen Zufalls-Key - Ausgabe kopieren
```

Den erzeugten Key als `SLSKD_API_KEY=<dein-key>` in die **zentrale `.env`**
eintragen. charts-download, charts-upgrade, charts-songrequest und das Dashboard
lesen ihn von dort. `SLSKD_HOST` steht standardmäßig auf `http://slskd:5030`
(Containername im Projekt-Netz) – das passt im Ein-Projekt-Setup automatisch.

### 2. slskd starten (erzeugt die slskd.yml)

```bash
docker compose up -d slskd
docker compose logs -f slskd
```

Beim ersten Start legt slskd unter `SLSKD_CONFIG_DIR` (Default
`./slskd/slskd-config`) eine `slskd.yml` an.

### 3. Soulseek-Login + DENSELBEN API-Key in die slskd.yml

`slskd.yml` (in `SLSKD_CONFIG_DIR`) öffnen und zwei Dinge eintragen:

**a) Soulseek-Account** (kostenlos auf dem Soulseek-Netz; die slskd-Doku
beschreibt die Registrierung):

```yaml
soulseek:
  username: <dein-soulseek-benutzer>
  password: <dein-soulseek-passwort>
```

**b) API-Key mit Rolle `readwrite`** – **exakt derselbe** Key wie in der `.env`
(aus Schritt 1):

```yaml
web:
  authentication:
    api_keys:
      charts:
        key: <derselbe-key-wie-in-der-.env>
        role: readwrite
```

Danach slskd neu starten (vom charts-stack-Root):

```bash
docker compose restart slskd
```

### 4. Rest starten

```bash
docker compose up -d --build
```

## Ports

| Port | Zweck |
|------|-------|
| `5030` | Web-UI + REST-API (im LAN / Projekt-Netz) |
| `50300` | Soulseek-Peer-Port (eingehende Verbindungen) |

## Netz

Alle 7 Dienste laufen in **einem** compose-Projekt und teilen sich automatisch
dessen Projekt-Netz – die charts-Dienste erreichen slskd über den Containernamen
`http://slskd:5030`. Du musst **kein** Netz von Hand anlegen.

## Daten & Pfade

| Host-Pfad | Container | Zweck |
|-----------|-----------|-------|
| `SLSKD_CONFIG_DIR` | `/app` | slskd.yml + Datenbank (persistent) |
| `MUSIC_ROOT/Charts` | `/downloads` | Download-Ziel (= `/charts` der charts-Dienste) |
| `MUSIC_ROOT/SeedShare` | `/seedmusic` | optionaler Seed-Ordner (Teilen im Netz) |

## Version

Image ist auf `slskd/slskd:0.26.0` gepinnt (statt `:latest`), für
reproduzierbare Deployments. Vor einem Update den slskd-Changelog prüfen.
