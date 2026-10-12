> 🇩🇪 Deutsche Version. English: [README.en.md](README.en.md)

# charts-sync

Hält in Navidrome automatisch eine **Charts-Playlist** mit den aktuellen
Chart-Hits aktuell – auf Basis deiner **bereits vorhandenen** Musiksammlung.
Standardmäßig werden dafür die **globalen Top 100** und die **Schweizer Top
100** von Deezer geholt und zu **einer** deduplizierten Playlist
zusammengeführt (**Aktuelle Hits & Charts 🔥🎶**).

Der Service holt periodisch die aktuellen Charts von **Deezer** (offene API,
kein Account, kein API-Key), gleicht sie gegen deine Musik-Library ab und
schreibt die `.m3u`-Playlist dorthin, wo Navidrome sie sieht
(`/volume2/music/Music`). Navidrome liest sie automatisch als Playlist ein.
Songs, die (noch) nicht in der Library liegen, werden in einer `missing.txt`
protokolliert.

Es lassen sich beliebig viele Playlists definieren, und jede kann mehrere
Chart-Quellen bündeln (siehe [Konfiguration](#konfiguration)).

---

## Charts-Automatisierung – Gesamtübersicht

`charts-sync` ist Teil einer Kette aus drei Diensten, die zusammen den
Chart-Workflow vollständig automatisieren. Jeder läuft als eigener Container
mit eigenem Intervall und eigener `.env`. Die Kopplung erfolgt ausschließlich
über gemeinsame Ordner (`/volume2/music/Charts`, `/volume2/music/Music`).
Ergänzt wird die Familie durch [`charts-songrequest`](../charts-songrequest),
eine optionale Web-UI zum **manuellen** Anfordern einzelner Songs (nutzt
dieselbe Pipeline über das Manifest).

Schwester-Projekte: [`charts-download`](../charts-download) · [`charts-import`](../charts-import) · [`charts-songrequest`](../charts-songrequest)

### Flow mit Default-Zeiten

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Charts-Automatisierung                          │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ① charts-sync                              (alle 12 Stunden)      │
│     │  Holt aktuelle Deezer-Charts (API)                           │
│     │  Gleicht ab gegen /volume2/music/Music (Library)             │
│     │  Schreibt charts.m3u / mundart.m3u                          │
│     │  Schreibt missing*.txt (fehlende Songs je Playlist)          │
│     │  Pushover: missing-Liste aufs Handy                          │
│     ▼                                                               │
│  ② charts-download                          (alle 60 Minuten)      │
│     │  Liest missing.txt                                           │
│     │  Prueft fehlgeschlagene Downloads → raeumt auf, gibt frei    │
│     │  Sucht pro Song via slskd-API (max. 10/Lauf)                 │
│     │  Waehlt bestes MP3 >= 256 kbps (Singles vor Compilations)    │
│     │  Stoesst Download in slskd an                                │
│     ▼                                                               │
│  slskd                                      (laeuft dauerhaft)     │
│     │  Laedt die Songs von anderen Soulseek-Nutzern               │
│     │  Songs landen in /volume2/music/Charts/                      │
│     │  Teilt ~500 Chart-Downloads + ~400 SeedShare-Songs           │
│     ▼                                                               │
│  ③ charts-import                            (alle 30 Minuten)      │
│     │  Scannt /volume2/music/Charts/ nach fertigen Downloads       │
│     │  Wartet MIN_AGE_SECONDS (120s) ab (keine halben Dateien)     │
│     │  Liest Tags → kopiert nach Music/<Artist>/<Album>/<Track>    │
│     │  Raeumt auf: KEEP_LATEST=500 (neueste behalten, Rest weg)   │
│     │  Pushover: importierte Songs melden                          │
│     ▼                                                               │
│  Navidrome                                  (scannt periodisch)    │
│     │  Sieht die neuen Songs beim naechsten Scan                   │
│     ▼                                                               │
│  ① charts-sync (Resync-Trigger, ~1 Min)                            │
│     charts-import meldet "neue Songs da" → charts-sync laeuft       │
│     sofort → Song faellt aus missing.txt, kommt in die Playlist    │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

Neue **Chart-Einträge** erkennt charts-sync über sein reguläres Intervall
(alle 12 h). **Nachgeladene Songs** landen dagegen fast sofort in der Playlist:
charts-import setzt nach dem Einsortieren ein Signal, das charts-sync innerhalb
von ~1 Minute zu einem Lauf veranlasst (Resync-Trigger).

### Dienste im Überblick

| Dienst | Aufgabe | Intervall | Schlüssel-Einstellungen |
|--------|---------|-----------|------------------------|
| **charts-sync** | Findet Lücken, schreibt Playlist + missing.txt, meldet aufs Handy | 12 h | `SYNC_INTERVAL_HOURS`, `MATCH_THRESHOLD`, `NOTIFY_MISSING_LIMIT` |
| **charts-download** | Lädt fehlende Songs via slskd-API, Retry bei Fehlschlägen | 60 min | `MAX_PER_RUN=10`, `MIN_BITRATE=256`, `DRY_RUN`, `SLSKD_API_KEY` |
| **charts-import** | Sortiert Downloads in Library ein, hält Share stabil | 30 min | `KEEP_LATEST=500`, `COPY_MODE=1`, `MIN_AGE_SECONDS=120` |
| **charts-recommend** | Lässt die Library aus dem Hörgeschmack mehrerer ListenBrainz-User wachsen (Nachladen, keine Playlist) | 24 h | `RECOMMEND_LISTENBRAINZ_USERS`, `LASTFM_ENABLED`, `WRITE_MISSING` |
| **charts-upgrade** | Ersetzt Songs mit zu niedriger Bitrate durch bessere Versionen | on-demand | `MIN_BITRATE`, `SLSKD_API_KEY` |
| **charts-songrequest** | Web-UI zum manuellen Anfordern einzelner Songs | on-demand | `SONGREQUEST_USERS`, `SLSKD_API_KEY` |

### Verzeichnisse auf dem NAS

| Pfad | Zweck |
|------|-------|
| `/volume2/music/Charts/` | slskd-Downloads + missing.txt + State-Dateien |
| `/volume2/music/Music/` | Navidrome-Library (Ziel des Imports) |
| `/volume2/music/SeedShare/` | Dauerhafter Seed-Ordner (Leecher-Schutz) |
| `/path/to/charts-stack/` | Stack-Projektordner (eine `compose.yml` + eine zentrale `.env` für alle Dienste) |

---

## Inhaltsverzeichnis

- [Überblick](#überblick)
- [Funktionsweise](#funktionsweise)
- [Projektstruktur](#projektstruktur)
- [Architektur & Module](#architektur--module)
- [Matching im Detail](#matching-im-detail)
- [Installation auf dem Synology NAS](#installation-auf-dem-synology-nas)
- [Navidrome: Playlist einlesen](#navidrome-playlist-einlesen)
- [Konfiguration (`.env`)](#konfiguration-env)
- [Verzeichnis-Layout](#verzeichnis-layout)
- [Betrieb & Wartung](#betrieb--wartung)
- [Ausgabedateien](#ausgabedateien)
- [Troubleshooting](#troubleshooting)
- [FAQ](#faq)

---

## Überblick

| Eigenschaft            | Wert |
|------------------------|------|
| Chart-Quelle           | Deezer (offene REST-API, ohne Account/Key) + optional Last.fm (API-Key) |
| Playlists              | Default: 2 (`charts.m3u` = Global + Schweiz + DE + UK, `mundart.m3u`); frei konfigurierbar. Dekaden (80er/90er usw.) laufen jetzt über charts-flashback. |
| Basis-Image            | `python:3.12-slim` |
| Abhängigkeiten         | `httpx`, `mutagen` |
| Betriebsmodus          | Endlosschleife mit konfigurierbarem Intervall |
| Schreibt               | pro Playlist eine `.m3u` (in `Music/`) + `missing`-Datei (in `Charts/`) |
| Läuft als              | `youruser:users` (PUID 1027 / PGID 100), wie Navidrome |
| Lädt Audiodateien?     | **Nein** (nur Metadaten) |
| Benachrichtigungen     | Pushover (optional, bei Änderung der Treffer oder der fehlenden Songs) |

---

## Funktionsweise

```
Deezer Global Top 100  ┐
Deezer Schweiz (alle)  ┘─► Merge + Dedup ─► charts.m3u    (Metadaten)
Deezer Swiss Mundart ─────────────────────► mundart.m3u
        │
        ▼
  Library-Abgleich  ◄── /volume2/music/Music  (deine vorhandene Sammlung)
        │
        ├──► /volume2/music/Music/<name>.m3u          (Navidrome-Playlist je Liste)
        └──► /volume2/music/Charts/missing*.txt       (fehlende Songs je Liste)
```

Ein Sync-Lauf:

1. **Library indexieren** – `/volume2/music/Music` einmal rekursiv scannen
   (der teure Teil, für alle Playlists wiederverwendet).
2. Für **jede** konfigurierte Playlist:
   - **Charts holen** – die Feeds der Playlist von Deezer laden und zu einer
     deduplizierten Liste mergen (gleiche Songs zählen nur einmal).
   - **Matchen** – jeden Chart-Track gegen den Index abgleichen.
   - **Playlist schreiben** – Treffer als `.m3u`.
   - **Fehlliste schreiben** – nicht gefundene Songs als `missing`-Datei.
3. **Benachrichtigen** – optionale Pushover-Meldung, standardmäßig nur bei
   Änderung gegenüber dem letzten Lauf.

Danach wartet der Service `SYNC_INTERVAL_HOURS` Stunden und wiederholt den Lauf.

---

## Projektstruktur

```
charts-sync/
├── app/
│   ├── __init__.py       # Paket-Marker
│   ├── config.py         # Konfiguration aus Umgebungsvariablen
│   ├── deezer.py         # Deezer-Chart-Client + Feed-Dispatcher + Genre-Filter
│   ├── lastfm.py         # Last.fm-Client (chart/geo/tag/user + Genre-Tags)
│   ├── library.py        # Library-Scan + Matching (mutagen + difflib)
│   ├── playlist.py       # Schreibt die .m3u-Playlists und missing-Dateien
│   ├── notify.py         # Optionale Pushover-Benachrichtigung
│   └── main.py           # Scheduler-Loop, verbindet alle Module
├── Dockerfile            # python:3.12-slim, startet app.main
├── compose.yml           # Service-Definition für das NAS
├── requirements.txt      # httpx, mutagen
├── .env.example          # Vorlage für die Konfiguration
├── .gitignore
└── README.md             # diese Datei
```

---

## Architektur & Module

Der Service ist bewusst in kleine, testbare Module aufgeteilt. Jedes Modul hat
eine klar abgegrenzte Aufgabe.

### `config.py`

Liest sämtliche Einstellungen aus Umgebungsvariablen. Parst die `CHARTS`-Liste
(JSON) in `ChartSource`-Objekte mit `Feed`-Objekten je Quelle (Deezer oder
Last.fm, mit `enabled`-Schalter); ist `CHARTS` nicht gesetzt, greifen die
Default-Playlists (charts.m3u = Global + Schweiz, mundart.m3u).
Liest je Playlist außerdem das optionale `exclude_artists` (Künstler-Ausschluss
pro Playlist). Enthält außerdem die Genre-Filter-Einstellungen (`GENRE_FILTER`, `EXCLUDE_TAGS`
…), Helfer für robustes Parsen und die Liste der als Musik erkannten
Dateiendungen (`AUDIO_EXTENSIONS`).

### `deezer.py`

Kapselt den Zugriff auf die Deezer-API **und** ist der Feed-Dispatcher.
`fetch_chart(chart_source)` lädt alle **aktiven** Feeds einer Playlist – je Feed
Deezer (`/chart/<id>/tracks` bzw. `/playlist/<id>/tracks`) oder, bei
`type: lastfm`, delegiert an `lastfm.py` – merged sie dedupliziert und wendet
optional den Genre-Filter an. Liefert `ChartTrack`-Objekte (Position, Künstler,
Titel). API-Fehler (Deezer meldet sie teils mit HTTP 200 und `error`-Feld)
werden erkannt; ein fehlgeschlagener Einzel-Feed bricht den Lauf nicht ab.

### `lastfm.py`

Client für die Last.fm-Web-API (AudioScrobbler 2.0). `fetch_feed(...)` bedient
`chart/geo/tag/user.getTopTracks` und liefert dieselben `ChartTrack`-Objekte wie
der Deezer-Client (quellenunabhängiges Matching). `get_top_tags(...)` holt die
Genre-Tags eines Songs für den Genre-Filter (fail-open bei Fehlern). Braucht
`LASTFM_API_KEY`.

### `library.py`

Das Herzstück des Abgleichs. Zwei Verantwortlichkeiten:

- **Indexieren** (`build_index`): scannt `MUSIC_DIR` rekursiv, liest pro Datei
  Künstler/Titel bevorzugt aus den Tags (`mutagen`), sonst aus dem Pfad.
- **Matchen** (`Library.find_match`): findet für einen Chart-Track den besten
  Library-Treffer über einen unscharfen Textvergleich (Details unten).

Der Ordner `CHARTS_DIR` wird beim Scan ausgespart, damit dort abgelegte Dateien
nicht als Quelle auftauchen.

### `playlist.py`

Schreibt die beiden Ausgabedateien **atomar** (erst in eine `.tmp`-Datei, dann
umbenennen), damit Navidrome nie eine halb geschriebene Datei sieht:

- `write_playlist(matched, file_name, display_title)` erzeugt eine erweiterte
  M3U (`#EXTM3U`, `#PLAYLIST:`, `#EXTINF:`) mit Pfaden **relativ** zum
  Musik-Wurzelverzeichnis – so, wie Navidrome sie erwartet.
- `write_missing(missing, file_name)` schreibt die kommentierte Fehlliste.

### Playlist-Reihenfolge (`PLAYLIST_ORDER`)

Steuert, in welcher Reihenfolge die Songs in die `.m3u` geschrieben werden:

- `chart` (Default): nach **Chart-Position** (Platz 1 zuoberst) – so wie die
  Charts wirklich aussehen.
- `newest-first`: **zuletzt neu** in die Playlist gekommene Songs zuoberst,
  danach die älteren. Gedacht für Player wie Substreamer, wo man beim Öffnen
  sofort die Neuzugänge oben sehen will, statt immer dieselben Chart-Platz-1-
  Songs. Da die Playlist aus mehreren Quellen (Deezer + Last.fm) gespeist wird
  und ständig wechselt, ist das oft praktischer.

Für `newest-first` merkt sich charts-sync pro Song das Datum des **ersten
Auftauchens** (`first_seen` je Song im State `.charts-sync-state.json`).
Bekannte Songs behalten ihr Datum, neue bekommen den aktuellen Lauf-Zeitpunkt –
so wandern Neuzugänge nach oben und die Reihenfolge bleibt über Läufe hinweg
stabil. Bei gleichem Datum (z. B. beim allerersten Lauf) entscheidet die
Chart-Position. Fällt ein Song aus den Charts und kommt später zurück, gilt er
zu Recht wieder als neu (der `first_seen`-Eintrag wird für nicht mehr
enthaltene Songs verworfen).

### `notify.py`

Optionaler Pushover-Versand. Wird nur aktiv, wenn `PUSHOVER_TOKEN` und
`PUSHOVER_USER` gesetzt sind, andernfalls stiller No-op. Fehler beim Versand
werden geloggt, brechen den Lauf aber nicht ab.

### `main.py`

Der Einstiegspunkt (`python -m app.main`). `run_sync` indexiert die Library
einmal und verarbeitet dann jede Chart-Quelle einzeln (`_sync_source`). Führt
Dedup durch (derselbe Song nur einmal pro Playlist) und verwaltet die
Änderungserkennung über eine State-Datei (`.charts-sync-state.json`), damit
Pushover nur bei echten Änderungen meldet. Als Änderung gelten sowohl neue oder
weggefallene Playlist-Treffer als auch Änderungen an der Liste der fehlenden
Songs. Der Loop wiederholt den Lauf im konfigurierten Intervall und fängt
Fehler einzelner Läufe/Quellen ab, damit der Dienst dauerhaft läuft.
Zusätzlich prüft der Loop alle `RESYNC_CHECK_SECONDS` (Standard 60 s), ob
`charts-import` ein **Resync-Signal** (`.resync-requested` im `CHARTS_DIR`)
hinterlegt hat, und löst dann sofort einen Lauf aus. So landen frisch
nachgeladene Songs fast unmittelbar in der Playlist, ohne dass auf das reguläre
12h-Intervall gewartet werden muss. Der Check ist nur eine Datei-Existenzabfrage
(praktisch kostenlos); ein echter Lauf passiert nur, wenn das Signal da ist.

---

## Matching im Detail

Ziel ist, Deezer-Metadaten robust gegen die realen Datei-/Tag-Namen deiner
Library abzugleichen – trotz Schreibvarianten und Zusätzen.

**Schritt 1 – Normalisierung** (`normalize`): vor jedem Vergleich werden
Künstler und Titel vereinheitlicht:

- Kleinschreibung
- Akzente entfernen (`é` → `e`)
- Zusätze entfernen: `(feat. …)`, `(ft. …)`, `(with …)`, `(Remastered)`,
  `(… Version)`, `(… Edit)`, `(Official …)`, `(Audio)`, `(Video)` u. a.
- `&` und `and` vereinheitlicht
- alles Nicht-Alphanumerische zu Leerzeichen

**Schritt 2 – Ähnlichkeit** (`similarity`): Vergleich zweier normalisierter
Strings via `difflib.SequenceMatcher`, Ergebnis als Wert 0–100.

**Schritt 3 – Künstler-Mindestschwelle**: Der Künstler-Score muss mindestens
`ARTIST_MIN_SCORE` (Default `60`) erreichen, sonst ist es **kein** Treffer,
egal wie gut der Titel passt. Das verhindert, dass ein gleicher Titel eines
anderen Künstlers fälschlich matcht (z. B. würde Chart „BLACKPINK - Jump"
sonst „Van Halen - Jump" treffen). Künstler **und** Titel müssen also passen.

**Schritt 4 – Gewichteter Score**: sind Künstler- und Titel-Tag vorhanden,
zählt der **Titel zu 60 %** und der **Künstler zu 40 %**. Fehlen Tags, wird
gegen einen kombinierten „Künstler + Titel"-String verglichen.

**Schritt 5 – Schwelle**: liegt der beste Score ≥ `MATCH_THRESHOLD` (Default
`87`), gilt der Track als vorhanden und wandert in die Playlist. Sonst landet er
in der `missing-*.txt` der jeweiligen Quelle.

**Tag-Lesung vs. Pfad-Fallback**: Künstler/Titel kommen bevorzugt aus den
Datei-Tags. Fehlen sie, rät der Service aus dem Pfad nach dem Muster
`.../<Artist>/<Album>/<NN - Title>.<ext>` und entfernt führende Tracknummern
sowie ein evtl. `Artist - Title` im Dateinamen.

**Schritt 6 – Compilations ausblenden** (optional, Default an): Ist
`IGNORE_COMPILATIONS=1`, werden Library-Tracks, die aus einer Compilation oder
einem Sampler stammen, beim Abgleich **übersprungen**. Erkannt wird das anhand
von Markern im Pfad, im Album- oder AlbumArtist-Tag (`Various Artists`,
`Bravo Hits`, `Top 100`, `Greatest Hits`, `Sampler` u. a.); das zuverlässigste
Signal ist der AlbumArtist `Various Artists`. So gilt ein Chart-Song, der bei
dir nur als Teil einer Compilation vorliegt, als **fehlend** und wird von
`charts-download` als saubere Single-/Album-Version nachgeladen. Beim
Index-Aufbau protokolliert das Log, wie viele Tracks als Compilation erkannt
und ignoriert werden. Mit `IGNORE_COMPILATIONS=0` zählt auch die
Compilation-Version als vorhanden.

---

## Installation auf dem Synology NAS

charts-sync wird **nicht** einzeln installiert. Es ist Teil des **charts-stack**,
der als EIN Projekt läuft: eine `compose.yml` + eine zentrale `.env` im
`charts-stack`-Ordner (z. B. `/path/to/charts-stack/`). Die
vollständige Installation (Ordner kopieren, slskd einrichten, `.env` ausfüllen,
Container Manager / `docker compose up -d --build`) steht in der
**[Haupt-README](../README.md)**.

Nur diesen Dienst gezielt bauen/starten – aus dem `charts-stack`-Ordner:

```bash
docker compose up -d --build charts-sync
```

Logs dieses Dienstes:

```bash
docker compose logs -f charts-sync
```

Beim ersten Lauf siehst du pro Chart-Song `TREFFER` oder `FEHLT`, gefolgt von
einer Zusammenfassung wie `X von Y Chart-Tracks in Library gefunden`.

---

## Navidrome: Playlist einlesen

Navidrome importiert `.m3u`-Dateien aus der Musik-Library automatisch als
Playlists. Zwei Punkte stellen sicher, dass es klappt:

- **Auto-Import aktiv:** In Navidrome ist `ND_PLAYLISTSPATH` standardmäßig auf
  die Library gesetzt, der Charts-Ordner liegt darin. Falls du den Pfad
  eingeschränkt hast, ergänze `Charts` bzw. setze `ND_PLAYLISTSPATH` so, dass
  `/music/Charts` erfasst wird.
- **Scan:** Navidrome erkennt die Playlist beim nächsten Library-Scan. Mit
  `ND_SCANSCHEDULE` (z. B. `1h`) oder einem manuellen „Scan" im Navidrome-UI
  wird sie sichtbar.

Die Playlist erscheint in Navidrome unter ihrem Titel (Default **Aktuelle Hits
& Charts 🔥🎶**).

> **Wichtig – gleiche Library:** Eine M3U-Playlist kann in Navidrome nur Songs
> referenzieren, die in **derselben Library** liegen wie die Playlist-Datei.
> Liegen deine Songs in der Library `/Music`, muss die `.m3u` ebenfalls dort
> liegen (nicht in einer separaten `/Charts`-Library). Deshalb steht
> `PLAYLIST_DIR=/music/Music` und `MUSIC_DIR=/music/Music`, damit die
> Song-Pfade relativ zu diesem Root stimmen. Die `missing-*.txt` dürfen dagegen
> in `/Charts` liegen, sie werden von Navidrome nicht aufgelöst.

### Warum `.m3u` und nicht `.nsp`?

Navidrome kennt zwei Playlist-Formate: `.m3u` (feste Songliste) und `.nsp`
(Smart Playlist, **regelbasiert**). Für Charts brauchen wir eine feste,
handverlesene Liste konkreter Songs, das ist genau der `.m3u`-Anwendungsfall.
`.nsp` beschreibt dagegen Kriterien („alle Songs mit Rating ≥ 4") und kann eine
feste Titelliste nicht sauber abbilden. Deshalb schreibt `charts-sync` eine
`.m3u`. Navidrome importiert beide Formate gleichwertig automatisch.

---

## Konfiguration

Die **Chart-Quellen** (welche Playlists entstehen) werden in der
`compose.yml` über die Variable `CHARTS` als JSON-Liste definiert. Alle
übrigen Einstellungen kommen aus der `.env` bzw. den Compose-Defaults.

### Chart-Quellen (`CHARTS`)

`CHARTS` ist eine JSON-Liste. Jeder Eintrag erzeugt genau **eine** Playlist und
kann **mehrere** Deezer-Quellen (`sources`) bündeln, die zu einer
deduplizierten Liste zusammengeführt werden. Standard sind **zwei** Playlists:

> 📖 **Vollständige Feld- und ID-Referenz + fertige Beispiele** (Country,
> 80er/90er, …): [`CHARTS-SOURCES.md`](../CHARTS-SOURCES.md) im Repo-Root
> ([English](../CHARTS-SOURCES.en.md)). Neue Quellen-IDs findest du bequem im
> **„Quellen-Finder"** unten im [charts-dashboard](../charts-dashboard).
>
> **Config aus Datei statt Env (empfohlen bei vielen Playlists):** Statt der
> einzeiligen `CHARTS`-Variable kann die Definition aus einer lesbaren,
> mehrzeiligen Datei geladen werden – `CHARTS_FILE=/config/charts.json` setzen
> und `charts.json` anlegen (Vorlage: [`charts.json.example`](charts.json.example)
> mit Pop/Country/Schlager). Priorität: `CHARTS_FILE` > `CHARTS` > Defaults.
>
> **Startup-Validierung:** charts-sync prüft die `CHARTS`-Liste beim Start.
> Kaputtes JSON oder unbrauchbare Quellen (z. B. unbekannte Last.fm-Methode)
> werden **im Log benannt** und die betroffene Quelle stillgelegt – der Dienst
> **läuft trotzdem weiter** (fällt notfalls auf die Code-Defaults zurück).

```json
[
  {
    "sources": [
      {"type": "chart",    "source": "0",          "limit": 100},
      {"type": "playlist", "source": "1313617925", "limit": "all"}
    ],
    "name": "charts.m3u",
    "title": "Aktuelle Hits & Charts 🔥🎶",
    "missing": "missing.txt"
  },
  {
    "sources": [
      {"type": "playlist", "source": "658561775", "limit": "all"}
    ],
    "name": "mundart.m3u",
    "title": "Schweizer Mundart 🇨🇭",
    "missing": "missing-mundart.txt",
    "exclude_artists": ["Nemo", "Trauffer"]
  }
]
```

| Feld              | Bedeutung |
|-------------------|-----------|
| `sources`         | Liste der zu mergenden Quellen (Deezer **und/oder** Last.fm). |
| `sources[].service`| Optional, empfohlen: `deezer` oder `lastfm` – macht die Quelle eindeutig. **Abwärtskompatibel:** fehlt `service`, wird er aus `type`/`method` hergeleitet (Alt-Config ohne `service` verhält sich exakt wie bisher). Explizites `service` schlägt die Heuristik (bei Widerspruch gibt es eine Warnung). |
| `sources[].type`  | Deezer: `chart` (Chart-ID, `0` = global) oder `playlist` (Playlist-ID). Last.fm: `lastfm`. |
| `sources[].source`| Deezer: die ID als String. |
| `sources[].limit` | Optional; max. Tracks **für genau diese Quelle** (`"all"` oder Zahl). Fehlt es, gilt das Eintrags-`limit`, sonst `CHART_LIMIT`. |
| `sources[].enabled`| Optional (Default `true`). `false` = Quelle wird übersprungen, ohne sie zu löschen. So kann man z. B. alle Deezer-Feeds aus- und nur Last.fm einschalten (oder umgekehrt). |
| `sources[].method`| Nur Last.fm: `chart.getTopTracks`, `geo.getTopTracks`, `tag.getTopTracks`, `user.getTopTracks`. |
| `sources[].country` / `.tag` / `.user` / `.period` | Nur Last.fm: der zur Methode passende Parameter (z. B. `"country": "Switzerland"`, `"tag": "80s"`, `"user": "deinname"`, `"period": "3month"`). |
| `name`            | Dateiname der `.m3u` (landet in `PLAYLIST_DIR`). |
| `title`           | Anzeigename in Navidrome (Emojis erlaubt, `{date}` = aktuelles Datum). |
| `missing`         | Dateiname der Fehlliste (landet in `CHARTS_DIR`). |
| `limit`           | Optional; Eintrags-Standard für Quellen ohne eigenes `limit`. |
| `exclude_artists` | Optional; Künstler, die aus **genau dieser** Playlist ferngehalten werden (Liste oder kommagetrennter String). Siehe unten. |

**Per-Quelle-Limit:** Jede Quelle kann ihr eigenes `limit` haben. So wird die
globale Chart auf die echten Top 100 begrenzt (der Longtail auf Platz 100–300
ist überwiegend international und in der Schweiz unbekannt), während die
Schweiz- und die Mundart-Playlist komplett (`"all"`) geholt werden.

**Merge-Verhalten:** Die Feeds werden in Reihenfolge geladen und zu einer Liste
zusammengeführt. Ein Song, der in mehreren Feeds vorkommt, wird nur **einmal**
übernommen (das erste Vorkommen gewinnt). Die Positionen werden nach dem Merge
fortlaufend neu vergeben.

Rechenbeispiel `charts.m3u` (Global Top 100 + Schweiz komplett):

- Global liefert ~100, die Schweiz-Chart ~99 Roh-Einträge.
- **Doppelte Songs werden entfernt.** Die Schweiz-Chart liegt zu ~98 % ohnehin
  in der globalen Top 300 und zu ~64 % in der globalen Top 100.
- Ergebnis: **real ~135 eindeutige Songs** – die weltweiten Top-Hits plus die
  schweiz-typischen, ohne den globalen Longtail.

`mundart.m3u` enthält die ~140 Songs der Deezer-Playlist „Swiss Mundart Hits".
Hinweis: Viele reine Mundart-Titel sind über slskd nicht in ausreichender
Qualität (≥256 kbps) auffindbar und landen daher zunächst in
`missing-mundart.txt`; bei jedem Lauf wird erneut versucht, sie zu finden.

Die Dekaden (80er/90er usw.) laufen **nicht mehr** über charts-sync. Statt einer
`80er-90er.m3u` füllt der Nachlade-Dienst **charts-flashback** (FLASHBACK FM) die
Library; die Dekaden-Anzeige übernehmen deine Navidrome Smart Playlists (NSP),
die nach dem Jahr-Tag filtern.

Zusätzlich landen in jeder Playlist nur Songs, die du **tatsächlich in der
Library** hast; alle übrigen stehen in der jeweiligen `missing`-Datei.

**Vereinfachtes Schema:** Ein Eintrag mit nur einer Quelle darf statt `sources`
auch direkt `type` + `source` verwenden (abwärtskompatibel).

**Weitere/getrennte Playlists**: einfach weitere `CHARTS`-Einträge anlegen,
jeder mit eigenem `sources`, `name`, `title` und `missing`. Die passende
Deezer-Playlist-ID steht im Weblink (`https://www.deezer.com/…/playlist/<ID>`);
offizielle Länder-Charts findest du über den Account „Deezer Charts". Bekannte
IDs: `1313617925` = „Top Switzerland", `658561775` = „Swiss Mundart Hits".

### Last.fm als zweite Quelle (optional)

Neben Deezer kann charts-sync Chart-/Tag-/Länder-Listen von **Last.fm** holen
(hörer-/scrobble-basiert statt streaming-basiert). Dafür brauchst du einen
kostenlosen API-Key von <https://www.last.fm/api/account/create> und trägst ihn
als `LASTFM_API_KEY` in die `.env` ein. Last.fm liefert – wie Deezer – nur
Metadaten (Künstler + Titel), keine Audiodaten.

Ein Last.fm-Feed sieht so aus (Beispiele):

```json
{"type": "lastfm", "method": "chart.getTopTracks", "limit": 100, "enabled": true}
{"type": "lastfm", "method": "geo.getTopTracks", "country": "Switzerland", "limit": 100, "enabled": true}
{"type": "lastfm", "method": "tag.getTopTracks", "tag": "80s", "limit": 150, "enabled": true}
{"type": "lastfm", "method": "tag.getTopTracks", "tag": "90s", "limit": 150, "enabled": true}
{"type": "lastfm", "method": "user.getTopTracks", "user": "deinname", "period": "3month", "limit": 100, "enabled": true}
```

- `chart.getTopTracks` – globale Top-Tracks auf Last.fm.
- `geo.getTopTracks` + `country` – länderbezogen (z. B. `Switzerland`).
- `tag.getTopTracks` + `tag` – meistgetaggte Tracks eines Tags (z. B. `80s`,
  `90s`, `pop`). Gut für dekaden-/genrebezogene Listen.
- `user.getTopTracks` + `user` (+ optional `period`: `7day`, `1month`,
  `3month`, `6month`, `12month`, `overall`) – **deine** meistgehörten Tracks.

**An/Aus pro Quelle:** In den Default-Charts sind Last.fm-Feeds bereits als
Vorlage hinterlegt, aber mit `"enabled": false`. Zum Ausprobieren einfach auf
`true` setzen. Umgekehrt kannst du die Deezer-Feeds auf `false` setzen und nur
Last.fm laufen lassen. Deezer- und Last.fm-Feeds in derselben Playlist werden
ganz normal gemergt und über Künstler+Titel dedupliziert.

### Genre-Filter (optional, nur Pop – kein Country/Schlager)

Weder Deezer noch Last.fm liefern eine saubere „nur Pop"-Chart; die offiziellen
Charts sind genre-gemischt (Pop, HipHop, Country, Schlager …). Der Filter ist
**opt-in** (Default `GENRE_FILTER=0`). Mit `GENRE_FILTER=1` prüft charts-sync
pro Chart-Track die **Last.fm-Genre-Tags** und wirft Tracks raus, deren
dominanter Tag auf der Ausschlussliste steht (`EXCLUDE_TAGS`, Default u. a.
`country`, `schlager`, `volksmusik`, `folk`, `classical`; bewusst **ohne** hard
rock/metal, damit Rock-Klassiker bleiben).

- **Pro Playlist steuerbar:** Das optionale Feld `genre_filter` (`true`/`false`)
  in einem CHARTS-Eintrag sticht den globalen `GENRE_FILTER` aus; fehlt es, gilt
  der globale Default (Vorrang: explizit pro Playlist > global). So kann man den
  Filter z. B. nur für dynamische Chart-Playlists einschalten und Künstler-/
  Mundart-Playlists unangetastet lassen. Schema/Beispiel siehe CHARTS-SOURCES.md.
- Braucht `LASTFM_API_KEY` (auch wenn die Quellen selbst von Deezer kommen).
- **Fail-open:** Liefert Last.fm für einen Song keine Tags, bleibt er drin –
  lieber ein Fremd-Genre zu viel als ein Pop-Hit fälschlich raus.
- Ein Track fliegt raus, wenn einer seiner stärksten `EXCLUDE_TAG_TOP_N` Tags
  in `EXCLUDE_TAGS` steht **und** dessen count `>= EXCLUDE_TAG_MIN_COUNT` ist
  (Last.fm-Tag-Counts gehen 0–100).
- Kostet einen zusätzlichen API-Call pro Track (mit Pause
  `GENRE_FILTER_SLEEP_MS`), macht den Lauf also etwas langsamer.

Beispiel: „Shaboozey – A Bar Song (Tipsy)" hat den dominanten Tag `country`
(count 100) → wird gefiltert. „Sabrina Carpenter – Espresso" hat `pop` (100) →
bleibt drin.

**Matching der Tags:** wortbasiert. Der Ausschluss-Tag `country` trifft daher
auch `contemporary country`, `modern country`, `country rap` usw. Mehrwort-Tags
(z. B. `hard rock`) werden als Phrase geprüft. `hard rock` und `metal` stehen
bewusst **nicht** in der Default-Liste, damit Rock-Klassiker (AC/DC, Bon Jovi,
Guns N' Roses, Europe) in den Charts bleiben.

**Prüfen, was gefiltert wurde.** Jeder Ausschluss wird geloggt. So siehst du
nach einem Lauf, welche Songs (mit welchem Tag) rausgeworfen wurden:

```bash
# Alle Ausschluss-Entscheidungen des letzten Laufs (dedupliziert):
sudo docker logs charts-sync 2>&1 | grep "Genre-Filter:" | sort -u
# Nur die Bilanz je Playlist ("X von Y ausgeschlossen"):
sudo docker logs charts-sync 2>&1 | grep "ausgeschlossen"
```

Zusätzlich steht die Zahl der gefilterten Songs in der Pushover-Meldung
(„🚫 X Fremd-Genre gefiltert"). Fliegt ein Song fälschlich raus (oder rutscht
Country durch), passe `EXCLUDE_TAGS` / `EXCLUDE_TAG_MIN_COUNT` an.

### Künstler-Ausschluss pro Playlist (`exclude_artists`)

Manche Künstler lassen sich per Genre **nicht** sauber aussortieren, weil die
APIs sie z. B. als „Pop" führen – etwa Schweizer Mundart-Schlager wie
**Trauffer**. Dafür gibt es pro Playlist das optionale Feld `exclude_artists`
direkt im `CHARTS`-JSON. Es wirkt **nur auf diese eine Playlist**: Ein Künstler,
den du aus der Mundart-Playlist verbannst, bleibt in einer künftigen Folk- oder
Schlager-Playlist erhalten.

```json
{
  "sources": [{"type": "playlist", "source": "658561775", "limit": "all"}],
  "name": "mundart.m3u",
  "title": "Schweizer Mundart 🇨🇭",
  "missing": "missing-mundart.txt",
  "exclude_artists": ["Nemo", "Trauffer"]
}
```

- Schreibweise als **Liste** (`["Nemo", "Trauffer"]`) oder als kommagetrennter
  **String** (`"Nemo, Trauffer"`).
- Groß-/Kleinschreibung egal. Trifft den ganzen Namen **oder** einen
  feat.-Partner: `"Trauffer"` filtert auch `Trauffer feat. Beatrice Egli`.
- **Kein API-Call, kein Last.fm-Key nötig** – der Abgleich passiert lokal beim
  Merge und wirkt sofort beim nächsten Sync.
- Unabhängig vom Genre-Filter: greift auch bei `GENRE_FILTER=0`.
- Änderung nur an der `compose.yml` (nicht am Code) → `docker compose up -d`
  genügt, **kein** Rebuild.

Jeder Ausschluss wird geloggt:

```bash
# Welche Songs wurden per exclude_artists entfernt:
sudo docker logs charts-sync 2>&1 | grep "Artist-Ausschluss"
# Bilanz je Playlist:
sudo docker logs charts-sync 2>&1 | grep "per exclude_artists entfernt"
```

Hinweis: `exclude_artists` verhindert, dass ein Künstler **neu** in die
Playlist geschrieben wird. Bereits vorhandene Einträge verschwinden beim
nächsten Sync automatisch, weil die `.m3u` bei jedem Lauf neu geschrieben wird
(die Musikdateien in der Library bleiben unberührt).

### Weitere Einstellungen (`.env`)

| Variable               | Default         | Bedeutung |
|------------------------|-----------------|-----------|
| `CHART_LIMIT`          | `all`           | Rückfall-Limit pro Quelle, wenn diese kein eigenes `limit` hat (`all` oder Zahl). |
| `LASTFM_API_KEY`       | *(leer)*        | Last.fm-API-Key (von last.fm/api/account/create). Nur nötig für `lastfm`-Feeds oder den Genre-Filter. |
| `GENRE_FILTER`         | `0`             | **Opt-in.** `1` = Fremd-Genres (Country/Schlager/Folk …) über Last.fm-Tags aus den Charts filtern. Braucht `LASTFM_API_KEY`. Pro Playlist per `genre_filter`-Feld überschreibbar (siehe CHARTS-SOURCES.md). |
| `EXCLUDE_TAGS`         | *(Liste)*       | Auszuschließende Tags (kommagetrennt). Leer = Default (`country, schlager, volksmusik, folk, americana, bluegrass, classical`). Bewusst **ohne** hard rock/metal, damit Rock-Klassiker (AC/DC, Bon Jovi …) in den Charts bleiben. |
| `EXCLUDE_TAG_TOP_N`    | `3`             | Wie viele der stärksten Tags eines Songs geprüft werden. |
| `EXCLUDE_TAG_MIN_COUNT`| `60`            | Mindest-count (0–100) eines Tags, damit er als „dominant" zählt. 60 hält Troll-/Nischen-Tags draußen. |
| `GENRE_FILTER_SLEEP_MS`| `200`           | Pause zwischen den Genre-Abfragen (ms), schont die Last.fm-API. |
| `MUSIC_DIR`            | `/music/Music`  | Navidrome-Library-Root. Song-Pfade werden relativ dazu geschrieben. |
| `PLAYLIST_DIR`         | = `MUSIC_DIR`   | Zielordner der `.m3u`-Playlists (muss Navidrome scannen). |
| `CHARTS_DIR`           | `/music/Charts` | Ablage der `missing-*.txt` und der State-Datei. |
| `SYNC_INTERVAL_HOURS`  | `12`            | Stunden zwischen zwei Läufen. |
| `RUN_ON_START`         | `1`             | Beim Start sofort synchronisieren. |
| `RESYNC_CHECK_SECONDS` | `60`            | Wie oft geprüft wird, ob charts-import einen Resync ausgelöst hat (nur Datei-Check, günstig). |
| `MATCH_THRESHOLD`      | `87`            | Ähnlichkeitsschwelle 0–100 (höher = strenger). |
| `ARTIST_MIN_SCORE`     | `60`            | Mindest-Ähnlichkeit des Künstlers, sonst kein Treffer (verhindert falsche Titel-Gleichnamen wie „Van Halen - Jump"). |
| `IGNORE_COMPILATIONS`  | `1`             | Compilation-Tracks (Various Artists, Bravo Hits, Top 100 …) beim Abgleich ignorieren, damit die Einzelversion nachgeladen wird. `0` = mitzählen. |
| `PUID` / `PGID`        | `1027` / `100`  | User/Gruppe (youruser:users, wie Navidrome). |
| `TZ`                   | `Europe/Zurich` | Zeitzone. |
| `PUSHOVER_TOKEN`       | *(leer)*        | Optional: Pushover-App-Token. |
| `PUSHOVER_USER`        | *(leer)*        | Optional: Pushover-User-Key. |
| `PUSHOVER_DEVICE`      | *(leer)*        | Optional: Ziel-Gerät. Mehrere Geräte kommagetrennt möglich (z.B. `iPhone-Max,Tablet-Anna`) — es wird pro Gerät eine eigene Nachricht gesendet. Alle Geräte gehören zum selben Pushover-Account. Leeres Feld = Zustellung an alle Geräte des Accounts. Ungültige/deaktivierte Gerätenamen werden vor dem Senden automatisch entfernt — sonst stellt Pushover einen unbekannten Namen ersatzweise an **alle** Geräte zu (Doppel-Push). |
| `PUSHOVER_SOUND`       | `falling`       | Benachrichtigungston für diesen Dienst. Gültige Pushover-Töne: `pushover, magic, cosmic, intermission, bike, bugle, cashregister, classical, falling, gamelan, incoming, mechanical, pianobar, siren, spacealarm, tugboat, alien, climb, persistent, echo, updown, none` (= still), `vibrate`. Leer = Account-Default (kein `sound`-Feld). Pro Dienst in der `compose.yml` über `PUSHOVER_SOUND_SYNC` überschreibbar. |
| `PUSHOVER_DEVICES_TTL` | `3600`          | Optional: Cache-Dauer (Sekunden) für die zum Filtern geladene Geräteliste des Accounts. |
| `NOTIFY_ONLY_ON_CHANGE`| `1`             | Nur benachrichtigen, wenn sich etwas geändert hat: Playlist-Treffer **oder** die Liste der fehlenden Songs. `0` = nach jedem Lauf. |
| `NOTIFY_MISSING_LIMIT` | `40`            | Max. Anzahl fehlender Songs in der Pushover-Meldung (aufs Handy, ohne NAS-Login). `0` = nur die Anzahl. |

### Global, Schweiz und Mundart

- **Global** (`chart:0`, Limit `100`): weltweite Deezer-Top-Tracks, sehr
  international (US/UK/K-Pop/Latin). Auf die echten Top 100 begrenzt, weil der
  Longtail (Platz 100–300) überwiegend Songs enthält, die in der Schweiz kaum
  jemand hört.
- **Schweiz** (`playlist:1313617925`, Limit `all`): offizielle Deezer-Playlist
  „Top Switzerland", näher an der Schweizer Hitparade.
- **Mundart** (`playlist:658561775`, Limit `all`): kuratierte Sammlung
  „Swiss Mundart Hits" (Bligg, Trauffer, Gölä, Patent Ochsner …). Landet in
  einer **separaten** `mundart.m3u`, damit die Hits-Playlist fokussiert bleibt.

> **Dekaden (80er/90er usw.)** laufen nicht mehr über charts-sync. Sie kommen
> jetzt vom Nachlade-Dienst **charts-flashback** (FLASHBACK FM), der die Library
> füllt; angezeigt werden sie über deine Navidrome Smart Playlists (NSP), die
> nach dem Jahr-Tag filtern.

Die globale und die Schweizer Chart überschneiden sich stark: ~98 % der
Schweizer Titel stehen auch in der globalen Top 300, ~64 % in der globalen
Top 100. Deshalb werden Global (Top 100) und Schweiz (komplett) zu **einer**
`charts.m3u` gemergt – die internationalen Top-Hits plus die schweiz-typischen,
ohne den globalen Longtail und ohne Duplikate.

Zusätzlich fließen die offiziellen Deezer-Länder-Charts **Top Germany**
(`1111143121`) und **Top UK** (`1111142221`, je auf 100 begrenzt) ein. Grund:
Der Global-Chart ist stark US-/Latin-/K-Pop-lastig; DE und UK bringen den west-/
mitteleuropäischen Pop, der den Alltagsgeschmack hierzulande besser trifft.
Beide stammen vom offiziellen „Deezer Charts"-Account und werden täglich
aktualisiert. Durch den Merge/Dedup wächst `charts.m3u` dadurch moderat (die
großen Hits überschneiden sich ohnehin mit Global/CH).

---

## Verzeichnis-Layout

### Auf dem Host (NAS)

| Pfad                            | Zweck |
|---------------------------------|-------|
| `/path/to/charts-stack` | Stack-Projektverzeichnis (Compose/Build, alle Dienste). |
| `/volume2/music/Music`          | Musik-Library + Ablage der `.m3u`-Playlists. |
| `/volume2/music/Charts`         | Ablage der `missing-*.txt` + State-Datei. |

### Im Container

| Host (NAS)        | Container   | Zweck |
|-------------------|-------------|-------|
| `/volume2/music`  | `/music`    | ganze Musik-Freigabe (Lesen + Schreiben in Unterordner). |

Konkret: `MUSIC_DIR`/`PLAYLIST_DIR` = `/music/Music`, `CHARTS_DIR` =
`/music/Charts`. Der Service liest die Library und schreibt Playlists nach
`Music/` sowie Fehllisten/State nach `Charts/`. Er läuft als `youruser:users`
(PUID 1027 / PGID 100), damit die Dateien denselben Eigentümer wie die Musik
haben.

---

## Betrieb & Wartung

**Status / Logs:**

```bash
sudo docker compose logs -f          # live mitlesen
sudo docker compose logs --tail=50   # letzte 50 Zeilen
```

**Einen Lauf sofort auslösen** (Neustart mit `RUN_ON_START=1`):

```bash
sudo docker compose restart
```

**Konfiguration ändern:** `.env` anpassen, dann:

```bash
sudo docker compose up -d
```

**Nach Code-Änderungen neu bauen:**

```bash
sudo docker compose up -d --build
```

**Stoppen / entfernen:**

```bash
sudo docker compose down
```

---

## Ausgabedateien

Pro Chart-Quelle entsteht eine `.m3u` (in `PLAYLIST_DIR`, also `Music/`) und
eine `missing-*.txt` (in `CHARTS_DIR`). Zusätzlich liegt in `CHARTS_DIR` eine
versteckte State-Datei (`.charts-sync-state.json`) für die Änderungserkennung.

**Beispiel-Ausgabe (Default-Konfiguration, 2 Playlists):**

```
/volume2/music/Music/charts.m3u
/volume2/music/Music/mundart.m3u
/volume2/music/Charts/missing.txt
/volume2/music/Charts/missing-mundart.txt
/volume2/music/Charts/.charts-sync-state.json
```

### `.m3u` (`charts.m3u`)

Erweiterte M3U mit den vorhandenen Chart-Songs, sortiert nach Position:

```
#EXTM3U
#PLAYLIST:Aktuelle Hits & Charts 🔥🎶
#EXTINF:-1,Tame Impala - Dracula (with JENNIE)
Various Artists/Bravo Hits 132 (2026)/CD 01/....mp3
...
```

Die Pfade sind relativ zum Musik-Wurzelverzeichnis (`Music/`), damit Navidrome
sie zuordnen kann.

### `missing.txt`

Kommentierte Liste der Chart-Songs, die nicht in der Library gefunden wurden:

```
# Fehlende Chart-Songs (Stand 02.09.2026 11:37)
# ...
  3. Ella Langley - Choosin' Texas
  4. Ella Langley - Be Her
  5. Ariana Grande - hate that i made you love me
```

Diese Titel legal beschaffen und in die Library legen – beim nächsten Lauf
landen sie automatisch in der jeweiligen Playlist.

---

## Troubleshooting

**Die Playlist erscheint nicht / ist leer in Navidrome.**
Häufigste Ursache: Die `.m3u` liegt in einer **anderen Library** als die Songs.
Eine M3U in der `/Charts`-Library kann keine Songs aus der `/Music`-Library
referenzieren → sie bleibt leer. Lösung: Playlist in `/Music` ablegen
(`PLAYLIST_DIR=/music/Music`). Prüfen, ob die `.m3u` unter
`/volume2/music/Music/` liegt (`sudo docker compose logs` zeigt „Playlist
geschrieben: …") und danach in Navidrome einen **Full Scan** auslösen.

**Songs, die ich habe, tauchen in `missing.txt` auf.**
Das Matching ist zu streng oder die Tags/Dateinamen weichen stark ab.
`MATCH_THRESHOLD` schrittweise senken (z. B. `85`, `82`) und Logs beobachten.
Die Log-Zeile `FEHLT … (bester Score N)` zeigt, wie nah der beste Treffer war.

**Falsche Songs landen in der Playlist.**
`MATCH_THRESHOLD` erhöhen (z. B. `90`, `92`). Wenn es sich um einen **gleichen
Titel eines anderen Künstlers** handelt (z. B. „Van Halen - Jump" statt
„BLACKPINK - Jump"), hilft vor allem `ARTIST_MIN_SCORE` hochsetzen (z. B. `75`);
dann muss der Künstler stärker übereinstimmen.

**Ein Song, den ich nur in einer Compilation habe, wird trotzdem neu geladen.**
Das ist so gewollt: Bei `IGNORE_COMPILATIONS=1` (Standard) werden
Compilation-Tracks beim Abgleich ignoriert, damit `charts-download` die saubere
Single-/Album-Version holt. Wenn du das nicht willst und die Compilation-Version
als vorhanden gelten soll, setze `IGNORE_COMPILATIONS=0`.

**Ein regulärer Song wird fälschlich als Compilation erkannt.**
Die Erkennung ist eine Heuristik über Pfad-/Tag-Marker. Passiert das bei einem
Album mit einem der Schlüsselwörter im Namen, kannst du entweder
`IGNORE_COMPILATIONS=0` setzen (dann wird gar nicht gefiltert) oder das
Album/den AlbumArtist-Tag anpassen.

**„Konnte Charts nicht laden".**
Meist ein temporäres Netzwerkproblem oder Deezer-Ratelimit. Der Service
versucht es beim nächsten Intervall erneut. Erreichbarkeit testen:

```bash
curl -s "https://api.deezer.com/chart/0/tracks?limit=3"
```

**Container startet, aber Library-Index ist leer.**
Prüfen, ob `/volume2/music` korrekt gemountet ist und Audiodateien enthält
(unterstützte Endungen: mp3, flac, m4a, aac, ogg, opus, wma, wav, alac).

**Zeitstempel stimmen nicht.**
`TZ` in der `.env` setzen (z. B. `Europe/Zurich`).

---

## FAQ

**Brauche ich einen Deezer-Account oder API-Key?**
Nein. Die verwendeten Chart-/Playlist-Endpunkte sind öffentlich.

**Werden Songs heruntergeladen?**
Nein. Der Service verarbeitet nur Metadaten und deine vorhandene Musik. Fehlende
Titel musst du selbst auf legalem Weg beschaffen.

**Wie oft aktualisiert sich die Playlist?**
Standardmäßig alle 12 Stunden (`SYNC_INTERVAL_HOURS`). Beim Start läuft der Sync
sofort, wenn `RUN_ON_START=1`. Deezers Charts selbst aktualisieren sich in der
Regel wöchentlich; ein Intervall von 12–24 h ist daher völlig ausreichend.

**Wann kommt eine Pushover-Benachrichtigung?**
Bei `NOTIFY_ONLY_ON_CHANGE=1` (Standard) immer dann, wenn sich gegenüber dem
letzten Lauf etwas geändert hat, und zwar in **einem von beiden** Bereichen:
- die Playlist-Treffer (ein Song neu gefunden oder weggefallen), oder
- die Liste der fehlenden Songs (neue Chart-Einträge, die dir noch fehlen).

Dadurch bekommst du auch dann eine Meldung mit der aktualisierten missing-Liste,
wenn sich deine Trefferliste gar nicht geändert hat. Die Meldung nennt die neu
hinzugekommenen Songs und listet die fehlenden Songs auf. Mit
`NOTIFY_ONLY_ON_CHANGE=0` kommt nach jedem Lauf eine Meldung, unabhängig von
Änderungen.

**Wie bekomme ich die Liste der fehlenden Songs aufs Handy?**
Die Pushover-Meldung führt die fehlenden Chart-Songs direkt auf (nummeriert,
Künstler - Titel), damit du sie unterwegs in slskd suchen kannst, ohne dich am
NAS einzuloggen. Über `NOTIFY_MISSING_LIMIT` steuerst du, wie viele Titel
gelistet werden (Standard 40). Da Pushover eine Nachricht auf 1024 Zeichen
begrenzt, wird bei sehr vielen fehlenden Songs gekürzt und ein Hinweis auf die
vollständige `missing`-Datei angehängt. `NOTIFY_MISSING_LIMIT=0` listet keine
Titel, sondern nur die Anzahl.

**Kann ich mehrere Chart-Playlists betreiben?**
Ja – das ist der Standard. Einfach weitere Einträge in die `CHARTS`-Liste der
`compose.yml` aufnehmen (jeweils eigener `name`, `title`, `missing`).
