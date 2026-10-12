> 🇩🇪 Deutsche Version. English: [README.en.md](README.en.md)

# charts-download

Lädt die fehlenden Chart-Songs automatisch über die **slskd-API** herunter.
Liest die von [`charts-sync`](../charts-sync) geschriebene `missing.txt`, sucht
jeden Song in slskd, wählt das beste **MP3 ≥ 256 kbps** und stößt den Download
an, damit du sie nicht mehr einzeln von Hand suchen musst.

Das dritte Schwester-Projekt neben `charts-sync` (findet die Lücken) und
[`charts-import`](../charts-import) (sortiert Downloads ein + räumt auf).
`charts-download` ersetzt nur den **manuellen Such-/Download-Schritt** in slskd,
nicht charts-import.

---

## Charts-Automatisierung – Gesamtübersicht

`charts-download` ist Teil einer Kette aus drei Diensten, die zusammen den
Chart-Workflow vollständig automatisieren. Jeder läuft als eigener Container
mit eigenem Intervall und eigener `.env`. Die Kopplung erfolgt ausschließlich
über gemeinsame Ordner (`/volume2/music/Charts`, `/volume2/music/Music`).
Ergänzt wird die Familie durch [`charts-songrequest`](../charts-songrequest),
eine optionale Web-UI zum **manuellen** Anfordern einzelner Songs (sucht/lädt
via slskd wie dieser Dienst, Import über das Manifest).

Schwester-Projekte: [`charts-sync`](../charts-sync) · [`charts-import`](../charts-import) · [`charts-songrequest`](../charts-songrequest)

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
│     │  Pushover: importierte Songs melden (mit Playlist-Herkunft)  │
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
| **charts-recommend** | Personalisierte Playlists (ListenBrainz/Last.fm) + missing | 24 h | `LISTENBRAINZ_USER`, `LASTFM_ENABLED`, `WRITE_MISSING` |
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
- [Gesamt-Workflow](#gesamt-workflow)
- [Funktionsweise](#funktionsweise)
- [Projektstruktur](#projektstruktur)
- [Architektur & Module](#architektur--module)
- [Voraussetzung: API-Key in slskd anlegen](#voraussetzung-api-key-in-slskd-anlegen)
- [Installation auf dem Synology NAS](#installation-auf-dem-synology-nas)
- [Konfiguration (`.env`)](#konfiguration-env)
- [DRY_RUN: erst testen, dann laden](#dry_run-erst-testen-dann-laden)
- [Auswahl-Logik der Downloads](#auswahl-logik-der-downloads)
- [Künstler global ausschließen](#künstler-global-ausschließen)
- [Betrieb & Wartung](#betrieb--wartung)
- [Troubleshooting](#troubleshooting)
- [FAQ](#faq)

---

## Überblick

| Eigenschaft            | Wert |
|------------------------|------|
| Quelle                 | `missing.txt` von charts-sync (`/volume2/music/Charts/missing.txt`) |
| Ziel                   | slskd-Downloads (`/volume2/music/Charts/`), via slskd-API angestoßen |
| slskd-Anbindung        | [`slskd-api`](https://pypi.org/project/slskd-api/) (REST-API, API-Key) |
| Basis-Image            | `python:3.12-slim` |
| Abhängigkeiten         | `httpx`, `slskd-api` |
| Format                 | **nur MP3 ≥ `MIN_BITRATE`** (Standard 256 kbps) |
| Priorisierung          | Singles/Alben vor Compilations (Compilations optional ganz ablehnen: `REJECT_COMPILATIONS`) |
| Menge pro Lauf         | max. `MAX_PER_RUN` (Standard 10) |
| Standardmodus          | **`DRY_RUN=1`** – lädt nichts, meldet nur, was geladen *würde* |
| Betriebsmodus          | Endlosschleife mit Intervall (Minuten) |
| Läuft als              | `youruser:users` (PUID 1027 / PGID 100) |
| Benachrichtigungen     | Pushover (optional) |

---

## Gesamt-Workflow

`charts-download` schließt die Lücke zwischen „Song fehlt" und „Song ist geladen":

```
Deezer-Charts
     │
     ▼
charts-sync ─────► missing.txt          (welche Chart-Songs fehlen)
     │             /volume2/music/Charts/missing.txt
     ▼
charts-download ─► liest missing.txt, sucht via slskd-API,
     │             wählt bestes MP3 ≥ 256 und stößt Download an
     ▼
slskd ───────────► lädt nach /volume2/music/Charts/
     │
     ▼
charts-import ───► kopiert nach /volume2/music/Music/<Artist>/<Album>/
     │             und hält den Charts-Ordner auf KEEP_LATEST Dateien
     ▼
Navidrome ───────► sieht die neuen Songs
     │
     ▼
charts-sync ─────► matcht sie in die Playlist, missing.txt wird kürzer
```

Alle Dienste in diesem Fluss (charts-sync, charts-download, slskd, charts-import)
laufen unabhängig in ihrem eigenen Intervall und sind nur über die gemeinsamen
Ordner (`Charts`, `Music`) gekoppelt. `charts-songrequest` klinkt sich bei
Bedarf zusätzlich ein (manuelle Einzel-Requests über dieselbe Pipeline).

---

## Funktionsweise

Ein Lauf (`run_once`):

1. **fehlgeschlagene prüfen** – den Status bereits angefragter Downloads in slskd
   abfragen. Fehlgeschlagene (`Aborted`, `Errored`, `TimedOut`, `Rejected`,
   `Cancelled`) werden aus dem State entfernt und in slskd weggeräumt, damit
   sie in diesem Lauf erneut gesucht werden. Laufende Downloads (`InProgress`,
   `Queued`) werden in Ruhe gelassen.
2. **missing lesen** – eine oder mehrere `missing.txt` parsen (`MISSING_FILES`),
   nach Chart-Position sortieren, Duplikate (gleicher Song in mehreren Charts)
   zusammenfassen.
3. **verbinden** – slskd-Client über API-Key aufbauen und Verbindung testen.
4. **pro Song** (bis `MAX_PER_RUN` erreicht):
   - bereits angefragte Songs überspringen (State-Datei),
   - kürzlich erfolglos gesuchte Songs überspringen (Cooldown, siehe
     `SEARCH_COOLDOWN_HOURS`) – **ohne** einen `MAX_PER_RUN`-Slot zu
     verbrauchen, damit dauerhaft nicht findbare Songs (z. B. viele
     Mundart-Titel) nicht jeden Lauf blockieren,
     > **Cooldown-Rücknahme bei „slskd noch nicht suchfähig":** Liefern in
     > **einem** Lauf **alle** durchsuchten Songs 0 Treffer und wurde nichts
     > angestoßen, wertet charts-download das als „slskd eingeloggt, aber noch
     > nicht suchfähig" (Timing direkt nach Rebuild/Neustart) und **nimmt die
     > soeben gesetzten Cooldowns wieder zurück** – so bleibt der Rückstand nicht
     > bis zu `SEARCH_COOLDOWN_HOURS` brach, der nächste Lauf versucht es regulär
     > erneut.
   - in slskd suchen (zweistufig): zuerst mit „Artist Titel"; liefert das
     **0 Treffer** (Soulseek matcht Suchwörter gegen Dateipfade – steht der
     Künstler nicht im Ordner, fällt sonst alles weg), wird als **Fallback nur
     nach dem Titel** gesucht. Beim Titel-Fallback **muss** der Künstler im
     Dateipfad vorkommen (Schutz vor Coverversionen),
   - auf Abschluss der Suche warten,
   - aus allen Ergebnissen das beste **MP3 ≥ `MIN_BITRATE`** wählen, dabei
     Singles/Alben vor Compilations (siehe
     [Auswahl-Logik](#auswahl-logik-der-downloads)),
   - **`DRY_RUN=1`**: nur loggen/melden, was geladen *würde*;
     **`DRY_RUN=0`**: Download per slskd-API anstoßen, im State vermerken und
     die sauberen Chart-Metadaten (Artist/Titel) ins **Manifest** schreiben.
5. **melden** – optionale Pushover-Zusammenfassung. Zeigt auch die Herkunft der
   Songs nach Playlist (z. B. „3 aus charts, 2 aus mundart"), abgeleitet aus
   der jeweiligen `missing`-Datei; jede Zeile ist mit `[playlist]` präfixt.

**Manifest für saubere Tags.** slskd-Downloads haben oft schlechte oder fehlende
Tags. Damit `charts-import` sie nicht als „Unknown Album" einsortiert, schreibt
`charts-download` beim Download die echten Chart-Metadaten in
`MANIFEST_FILE` (`/charts/.charts-manifest.json`): pro Datei den echten
Künstler und Titel. `charts-import` liest dieses Manifest und taggt die Datei
korrekt. So werden die Songs sauber einsortiert und von `charts-sync`
zuverlässig gematcht.

Danach wartet der Dienst `INTERVAL_MINUTES` Minuten und wiederholt den Lauf.

---

## Projektstruktur

```
charts-download/
├── app/
│   ├── __init__.py       # Paket-Marker
│   ├── config.py         # Konfiguration aus Umgebungsvariablen
│   ├── matching.py       # Normalisierung + Ähnlichkeit (wie charts-sync)
│   ├── missing.py        # Parser für die missing.txt von charts-sync
│   ├── slsk.py           # slskd-API: Suche, Ergebnisauswahl, Download
│   ├── state.py          # merkt sich angefragte Songs (keine Doppel-Downloads)
│   ├── manifest.py       # schreibt Chart-Metadaten für charts-import
│   ├── notify.py         # optionale Pushover-Benachrichtigung
│   └── main.py           # Scheduler-Loop, verbindet alle Module
├── Dockerfile            # python:3.12-slim, startet app.main
├── compose.yml           # Service-Definition für das NAS
├── requirements.txt      # httpx, slskd-api
├── .env.example          # Vorlage für die Konfiguration
├── .gitignore
└── README.md             # diese Datei
```

---

## Architektur & Module

### `config.py`
Liest alle Einstellungen aus Umgebungsvariablen: slskd-Zugang (`SLSKD_HOST`,
`SLSKD_API_KEY`), Quelldatei(en), Auswahl-Kriterien (`MIN_BITRATE`,
`MATCH_THRESHOLD`), Mengensteuerung (`MAX_PER_RUN`, `DRY_RUN`), Scheduler und
Pushover.

### `matching.py`
Dieselbe `normalize`/`similarity`-Logik wie in charts-sync, plus `match_score`:
bewertet, wie gut ein slskd-Dateiname zu „Artist Titel" passt. Da slskd nur
Dateipfade liefert (keine sauberen Tags), zählt neben der reinen String-Ähnlich-
keit vor allem die **Wort-Abdeckung** (kommen alle Suchwörter im Dateinamen
vor?). Das schützt vor zufälligen Treffern. Zusätzlich erkennt `is_compilation`
anhand von Schlüsselwörtern im Pfad, ob ein Treffer aus einer Compilation/
einem Sampler stammt (für die Priorisierung in der Auswahl).

### `missing.py`
Parst das Format von charts-sync (`  12. Artist - Title`), ignoriert die
Header-Kommentare und Leerzeilen. `load_all` fasst mehrere Dateien zusammen und
dedupliziert nach normalisiertem „Artist|Titel".

### `slsk.py`
Die slskd-Anbindung über die Library `slskd-api`:
- `connect()` baut den Client (API-Key) auf und testet die Verbindung.
- `find_candidates()` sucht zweistufig: erst „Artist Titel", bei 0 Treffern als
  Fallback nur den Titel (dann muss der Künstler im Pfad stehen,
  `artist_in_path`, Schutz vor Coverversionen). Liefert **alle** passenden
  Treffer nach Qualität sortiert (bestes zuerst), damit der Aufrufer sie beim
  Retry der Reihe nach abhaken kann. Wirft `SlskdError` bei Verbindungsproblemen
  (damit ein slskd-Ausfall nicht als „nicht findbar" gilt). `find_download()`
  ist ein dünner Wrapper, der nur den besten Treffer zurückgibt.
- `_rank_candidates()` filtert auf **MP3 ≥ `MIN_BITRATE`**, freie Upload-Slots
  und `MATCH_THRESHOLD`, verwirft Ausschluss-Keywords und DJ-Pool-Edits
  (`looks_like_dj_edit`: Dateiname endet auf BPM-Zahl), sortiert dann nach
  Score, Bitrate und Upload-Speed.
  Singles/Alben werden vor Compilations priorisiert.
- `enqueue()` stößt den Download an. **Retry über mehrere Anbieter:** Weil
  Soulseek P2P ist, kann der beste Treffer von einem Anbieter (Endnutzer)
  stammen, der gerade offline ist / den Upload ablehnt. Pro Song werden die
  Kandidaten daher der Reihe nach angestoßen, bis einer den Download annimmt;
  erst wenn keiner liefert, gilt der Song als (vorerst) fehlgeschlagen.
- `check_download_states()` prüft den Status der angefragten Downloads in slskd
  und erkennt fehlgeschlagene (`Aborted`, `Errored`, `TimedOut` usw.).
- `cleanup_failed()` entfernt fehlgeschlagene Downloads in slskd.

### `state.py`
Persistiert in `.charts-download-state.json`, welche Songs bereits angefragt
wurden (inkl. slskd-Username + Dateiname für die Status-Nachverfolgung), damit
nichts doppelt geladen wird. Fehlgeschlagene Downloads werden automatisch aus
dem State entfernt und erneut versucht. Im Dry-Run wird **nicht** markiert,
damit ein späterer Echtlauf dieselben Songs erneut betrachtet.

### `manifest.py`
Schreibt beim Download die sauberen Chart-Metadaten (echter Künstler + Titel)
in `MANIFEST_FILE`, mit dem Datei-Basisnamen als Schlüssel. `charts-import`
liest dieses Manifest und taggt die Datei korrekt, statt die oft schlechten
slskd-Tags zu übernehmen. Das verhindert „Unknown Album"-Einträge und sorgt
dafür, dass `charts-sync` die Songs zuverlässig matcht.

### `notify.py`
Optionaler Pushover-Versand, nur aktiv wenn `PUSHOVER_TOKEN` und `PUSHOVER_USER`
gesetzt sind. Identisch zu den Schwester-Diensten.

### `main.py`
Einstiegspunkt (`python -m app.main`). Führt `run_once` aus (missing lesen,
verbinden, pro Song suchen/auswählen/laden mit `MAX_PER_RUN`-Grenze und
Dry-Run-Weiche) und wiederholt im Intervall. Fehler werden abgefangen, damit
der Dienst dauerhaft läuft.

---

## Voraussetzung: API-Key in slskd anlegen

`charts-download` braucht einen API-Key mit Schreibrechten. In der `slskd.yml`
(auf dem NAS unter `/path/to/arr-stack/slskd/slskd.yml`) einen Key
mit Rolle `readwrite` eintragen:

```yaml
web:
  authentication:
    api_keys:
      charts_download:
        key: <ein zufaelliger String, 16-255 Zeichen>
        role: readwrite
        cidr: 0.0.0.0/0,::/0
```

Den `key`-Wert generierst du selbst (z. B. ein langer Zufallsstring). Danach
slskd neu laden/neu starten. Diesen Key trägst du in der `.env` als
`SLSKD_API_KEY` ein.

> Sicherheitshinweis: `role: readwrite` reicht (Suchen + Downloads).
> `administrator` ist nicht nötig. Mit `cidr` kannst du den Zugriff auf das
> Docker-/LAN-Netz einschränken.

---

## Installation auf dem Synology NAS

charts-download wird **nicht** einzeln installiert. Es ist Teil des
**charts-stack**, der als EIN Projekt läuft: eine `compose.yml` + eine zentrale
`.env` im `charts-stack`-Ordner (z. B. `/path/to/charts-stack/`).
Die vollständige Installation (inkl. slskd-Einrichtung und `.env`) steht in der
**[Haupt-README](../README.md)**. Für charts-download zusätzlich wichtig:

- **API-Key in slskd anlegen** (siehe oben) und als `SLSKD_API_KEY` in die
  zentrale `.env` eintragen.
- Zum ersten Testen `DRY_RUN=1` lassen (siehe Abschnitt
  [DRY_RUN](#dry_run-erst-testen-dann-laden)).

Nur diesen Dienst gezielt bauen/starten – aus dem `charts-stack`-Ordner:

```bash
docker compose up -d --build charts-download
docker compose logs -f charts-download
```

Im ersten (Dry-Run-)Lauf siehst du pro Song `[DRY_RUN] wuerde laden: …` und
am Ende `Lauf fertig (DRY_RUN): X Songs wuerden geladen.`

---

## Konfiguration (`.env`)

| Variable            | Default | Bedeutung |
|---------------------|---------|-----------|
| `SLSKD_HOST`        | `http://slskd:5030` | slskd-URL. Default = Containername im gemeinsamen Docker-Netz; sonst Host-IP (`http://<NAS-IP>:5030`). |
| `SLSKD_API_KEY`     | *(leer, Pflicht)* | API-Key aus der slskd.yml (`readwrite`). |
| `SLSKD_URL_BASE`    | `/` | Nur falls slskd unter einem Unterpfad läuft. |
| `MISSING_FILES`     | `/charts/missing.txt;/charts/missing-mundart.txt;/charts/missing-flashback.txt;/charts/missing-hitparade.txt;/charts/missing-recommend.txt` | Quelldatei(en), eine pro Dienst/Playlist, mehrere per `;` trennen. |
| `MIN_BITRATE`       | `256` | Minimale MP3-Bitrate (kbps). Andere Formate werden ignoriert. |
| `MATCH_THRESHOLD`   | `80` | Ähnlichkeit 0-100 zwischen „Artist Titel" und Dateiname. |
| `SEARCH_COOLDOWN_HOURS` | `24` | Erfolglos gesuchte Songs so lange überspringen (ohne `MAX_PER_RUN`-Slot zu verbrauchen). Verhindert, dass dauerhaft nicht findbare Songs jeden Lauf blockieren. `0` = aus. |
| `EXCLUDE_KEYWORDS`  | *(Standardliste)* | Dateien mit diesen Wörtern im Namen werden verworfen (nur Originalversionen). Leer = Standardliste, `off`/`none` = Filter aus, eigene kommagetrennte Liste ersetzt die Standardliste. |
| `EXCLUDE_ARTISTS`   | *(leer)* | Global auszuschließende Künstler (kommagetrennt). Songs dieser Künstler werden **nie** geladen – egal aus welcher Chart-Quelle. Ergänzung zum playlist-spezifischen `exclude_artists` in charts-sync. Siehe [Künstler global ausschließen](#künstler-global-ausschließen). |
| `REJECT_COMPILATIONS` | `0` | `1` = Compilation-/Sampler-Treffer **komplett ablehnen** (Song bleibt „missing", bis eine echte Single/Album-Version auftaucht). `0` = Compilation laden, wenn es nichts Besseres gibt. Siehe [Auswahl-Logik](#auswahl-logik-der-downloads). |
| `REQUIRE_FREE_SLOT` | `1` | Nur Anbieter mit freiem Upload-Slot. |
| `MAX_PER_RUN`       | `10` | Max. Songs pro Lauf. |
| `DRY_RUN`           | `1` | `1` = nur testen (nichts laden), `0` = wirklich laden. |
| `SEARCH_TIMEOUT_MS` | `15000` | Timeout einer slskd-Suche. |
| `SEARCH_WAIT_SECONDS`| `25` | Max. Wartezeit auf Suchabschluss. |
| `INTERVAL_MINUTES`  | `60` | Minuten zwischen zwei Läufen. |
| `RUN_ON_START`      | `1` | Beim Start sofort einen Lauf machen. |
| `STATE_FILE`        | `/charts/.charts-download-state.json` | Merkt angefragte Songs. |
| `MANIFEST_FILE`     | `/charts/.charts-manifest.json` | Chart-Metadaten für charts-import (saubere Tags). |
| `PUID` / `PGID`     | `1027` / `100` | User/Gruppe (youruser:users). |
| `TZ`                | `Europe/Zurich` | Zeitzone. |
| `PUSHOVER_*`        | *(leer)* | Optionale Pushover-Zugangsdaten. |
| `PUSHOVER_DEVICE`   | *(leer)* | Optional: Ziel-Gerät. Mehrere Geräte kommagetrennt möglich (z.B. `iPhone-Max,Tablet-Anna`) — es wird pro Gerät eine eigene Nachricht gesendet. Alle Geräte gehören zum selben Pushover-Account. Leeres Feld = Zustellung an alle Geräte des Accounts. Ungültige/deaktivierte Gerätenamen werden vor dem Senden automatisch entfernt — sonst stellt Pushover einen unbekannten Namen ersatzweise an **alle** Geräte zu (Doppel-Push). |
| `PUSHOVER_SOUND` | `falling` | Benachrichtigungston für diesen Dienst. Gültige Pushover-Töne: `pushover, magic, cosmic, intermission, bike, bugle, cashregister, classical, falling, gamelan, incoming, mechanical, pianobar, siren, spacealarm, tugboat, alien, climb, persistent, echo, updown, none` (= still), `vibrate`. Leer = Account-Default. Über `PUSHOVER_SOUND_DOWNLOAD` in der `compose.yml` überschreibbar. |
| `PUSHOVER_DEVICES_TTL` | `3600` | Optional: Cache-Dauer (Sekunden) für die zum Filtern geladene Geräteliste des Accounts. |

---

## DRY_RUN: erst testen, dann laden

`charts-download` startet bewusst im **Testmodus** (`DRY_RUN=1`). In diesem Modus
wird **nichts heruntergeladen**. Der Dienst sucht ganz normal, wählt Treffer aus
und schreibt ins Log/Pushover, was er laden *würde*, inklusive Bitrate und
Match-Score. So prüfst du, ob die Trefferqualität stimmt, bevor echt geladen
wird.

Empfohlenes Vorgehen:

1. Mit `DRY_RUN=1` starten und ein, zwei Läufe im Log ansehen.
2. Prüfen, ob die geplanten Downloads plausibel sind (richtiger Song, sinnvolle
   Bitrate, Score deutlich über der Schwelle).
3. Ist die Qualität gut, in der `.env` `DRY_RUN=0` setzen und neu starten.
4. Bleiben Fehltreffer, `MATCH_THRESHOLD` erhöhen (strenger) und erneut testen.

Wichtig: Im Dry-Run werden Songs **nicht** im State vermerkt. Ein späterer
Echtlauf betrachtet dieselben Songs also erneut, nichts geht verloren.

---

## Auswahl-Logik der Downloads

Aus allen Suchergebnissen eines Songs wird genau ein Treffer gewählt. Eine
Datei kommt nur infrage, wenn **alle** Bedingungen erfüllt sind:

- Format ist **MP3** und Bitrate **≥ `MIN_BITRATE`** (Standard 256),
- Datei ist nicht gesperrt (`isLocked`),
- der Dateiname enthält **kein Ausschluss-Keyword** (`EXCLUDE_KEYWORDS`, siehe
  unten) – so werden nur Originalversionen geladen,
- der Anbieter hat einen freien Upload-Slot (bei `REQUIRE_FREE_SLOT=1`),
- der Match-Score (Dateiname gegen „Artist Titel") ist **≥ `MATCH_THRESHOLD`**.

**Ausschluss-Keywords (nur Originalversionen).** Dateien, deren Name eines der
Keywords enthält, werden komplett verworfen – unabhängig vom Score. Zusätzlich
werden **DJ-Pool-Edits** verworfen, deren Dateiname auf eine BPM-Zahl endet
(z. B. „… (Uptempo Edit) 124.mp3"); vierstellige Jahreszahlen wie „(2024)" sind
davon nicht betroffen. Die eingebaute Standardliste deckt u. a. ab: `lyrics`,
`lyric video`,
`official video/audio`, `remix`, `mashup`, `bootleg`, `acapella`,
`instrumental`, `karaoke`, `live`, `cover`, `unplugged`, `sped up`, `slowed`,
`reverb`, `nightcore`, `8d`, `snippet`, `teaser`, `extended/club/radio mix`,
`break in`, sowie DJ-Pool-Marker wie `intro clean`, `uptempo`, `transition`,
`hyper remix`. So werden Lyrics-Videos, DJ-Edits, Live- und Trend-Versionen
gar nicht erst geladen. (Mehr-Wort-Marker verhindern Fehltreffer bei echten
Titeln wie „Clean Bandit" oder „Dirty Diana".) Über `EXCLUDE_KEYWORDS` in der `.env` kannst du die
Liste ersetzen (`off`/`none` schaltet den Filter aus). Die Erkennung prüft
ganze Wörter, sodass z. B. „The Living End" (`living` ≠ `live`) oder
„Coverdale" (≠ `cover`) korrekt durchgelassen werden.

Unter den verbleibenden Kandidaten wird in dieser Reihenfolge sortiert:

1. **Singles/Alben vor Compilations** – Treffer, deren Pfad auf eine
   Compilation/Sampler hindeutet (z. B. `VA -`, `Various`, `Varios`, `Top 100`,
   `Charts`, `Hits`, `Best Of`, `Mastermix`, `New Music Friday`, `Week 34`),
   werden **nachrangig** behandelt.
   - Bei `REJECT_COMPILATIONS=0` (Default): Sie werden **nicht verworfen** –
     gibt es keinen Single-/Album-Treffer, wird die Compilation genommen (im
     Log: „nur Compilation-Treffer verfuegbar").
   - Bei `REJECT_COMPILATIONS=1`: Compilation-Treffer werden **komplett
     verworfen**. Der Song bleibt dann „missing", bis eine echte Single-/
     Album-Version auftaucht – so landen keine Sampler-Tracks in der Library.
2. höchster **Match-Score**,
3. höchste **Bitrate**,
4. schnellster **Upload-Speed**.

So bekommst du bevorzugt die Single- oder Album-Version, in der besten Qualität
und am schnellsten verfügbar. Ob eine Compilation im Notfall akzeptiert wird
(`REJECT_COMPILATIONS=0`) oder der Song lieber fehlend bleibt
(`REJECT_COMPILATIONS=1`), steuerst du selbst.

> Hinweis: slskd liefert nur Dateipfade, keine echten Metadaten. Die
> Compilation-Erkennung ist daher eine Heuristik anhand von Schlüsselwörtern im
> Pfad, kein sicheres Signal. Sie kann einzelne Fälle falsch einordnen, ändert
> aber nur die Priorität, nie ob ein Song grundsätzlich geladen wird.

---

## Künstler global ausschließen

Manche Künstler willst du **nie** in der Library haben – auch nicht, wenn sie in
einer Chart-Quelle auftauchen. Typischer Fall: Schweizer Mundart-Schlager wie
**Trauffer**, den die Musik-APIs fälschlich als „Pop" führen.

Dafür gibt es `EXCLUDE_ARTISTS` (kommagetrennt) in der `.env`:

```bash
EXCLUDE_ARTISTS=Trauffer,Nemo
```

Songs dieser Künstler werden bereits beim Einlesen der `missing`-Listen
aussortiert – sie werden **nie gesucht, nie geladen, nie einsortiert**, egal aus
welcher Chart-Quelle. Der Vergleich ist unabhängig von Groß-/Kleinschreibung und
Akzenten und trifft auch `feat.`-Kombinationen (z. B. „X feat. Trauffer").

**Abgrenzung zum playlist-spezifischen Ausschluss in charts-sync.** Es gibt zwei
Ausschluss-Ebenen, bewusst getrennt:

| Ebene | Ort | Wirkung |
|-------|-----|---------|
| `exclude_artists` (pro Playlist) | charts-sync (`CHARTS`-JSON) | Künstler erscheint **nicht in dieser Playlist**, kann aber in anderen Playlists und in der Library sein. |
| `EXCLUDE_ARTISTS` (global) | charts-download (`.env`) | Künstler wird **gar nicht geladen** und landet nie in der Library. |

Für einen Künstler, den du komplett loswerden willst, setzt du ihn in **beide**:
in charts-sync (raus aus den Playlists) und in charts-download (kein Download).

```bash
# Welche Songs wurden per EXCLUDE_ARTISTS nicht geladen:
sudo docker logs charts-download 2>&1 | grep "Artist-Ausschluss"
```

---

## Betrieb & Wartung

**Status / Logs:**

```bash
sudo docker compose logs -f
sudo docker compose logs --tail=50
```

**Einen Lauf sofort auslösen** (Neustart mit `RUN_ON_START=1`):

```bash
sudo docker compose restart
```

**Konfiguration ändern:** `.env` anpassen, dann `sudo docker compose up -d`.

**State zurücksetzen** (alle Songs wieder als „nicht angefragt" behandeln):

```bash
rm /volume2/music/Charts/.charts-download-state.json
```

**Stoppen / entfernen:** `sudo docker compose down`.

---

## Troubleshooting

**`slskd nicht erreichbar` / Verbindungsfehler.**
`SLSKD_HOST` und `SLSKD_API_KEY` prüfen. Der Key muss in der slskd.yml unter
`web.authentication.api_keys` mit Rolle `readwrite` stehen und slskd danach neu
geladen worden sein. Testweise die slskd-Web-UI unter `SLSKD_HOST` öffnen.

**`kein passendes MP3 >= 256 gefunden`.**
Für den Song gibt es keine MP3 in ausreichender Qualität mit freiem Slot, oder
der Match-Score lag unter der Schwelle (auch nach dem Titel-Fallback). Der Song
wird für `SEARCH_COOLDOWN_HOURS` übersprungen und danach erneut versucht.
Optionen: `MATCH_THRESHOLD` senken
(toleranter), `REQUIRE_FREE_SLOT=0` (auch Anbieter ohne freien Slot), oder den
Song bleibt für den nächsten Lauf liegen.

**Es werden falsche Songs geplant/geladen.**
`MATCH_THRESHOLD` erhöhen (strenger). Erst im Dry-Run gegenprüfen.

**Es passiert nichts / 0 Songs.**
Prüfen, ob `MISSING_FILES` auf die richtige Datei zeigt und diese Einträge
enthält. Der Pfad ist der **Container-Pfad** (`/charts/missing.txt`), nicht der
NAS-Pfad.

**Downloads starten, aber nichts landet in der Library.**
Das ist Aufgabe von charts-import, nicht von charts-download. charts-download
stößt nur den Download in slskd an; das Einsortieren macht charts-import.

---

## FAQ

**Ersetzt charts-download das charts-import-Projekt?**
Nein. charts-download ersetzt nur das **manuelle Suchen/Laden** in slskd.
charts-import sortiert die geladenen Dateien weiterhin in die Library ein und
räumt den Charts-Ordner auf.

**Werden Compilations bevorzugt oder vermieden?**
Immer vermieden. Standardmäßig (`REJECT_COMPILATIONS=0`) werden Compilation-/
Sampler-Treffer nur nach hinten sortiert und nur als Notlösung geladen, wenn es
keine Single-/Album-Version gibt. Mit `REJECT_COMPILATIONS=1` werden sie
**komplett abgelehnt** – der Song bleibt dann „missing", bis eine echte
Single-/Album-Version auftaucht (empfohlen, wenn du nur saubere Singles in der
Library willst). Die Erkennung ist eine Heuristik anhand von Schlüsselwörtern im
Pfad (`VA -`, `Various`, `Varios`, `Charts`, `Hits`, `Best Of`, `Mastermix`,
`Week`, `New Music Friday` usw.), da slskd keine Metadaten liefert.

**Wie schließe ich einen Künstler komplett aus?**
Siehe [Künstler global ausschließen](#künstler-global-ausschließen).

**Es werden Lyrics-Videos, Remixe oder Live-Versionen geladen. Wie verhindere ich das?**
Das ist standardmäßig bereits verhindert: Dateien mit Keywords wie `lyrics`,
`remix`, `live`, `acapella`, `sped up` usw. im Namen werden komplett verworfen
(`EXCLUDE_KEYWORDS`). Es werden nur Originalversionen geladen. Fehlt trotzdem
mal ein Muster, ergänze es in `EXCLUDE_KEYWORDS`. Ein Song, für den es nur
solche Versionen gibt, bleibt dann in der `missing.txt` (bewusst: lieber kein
Song als eine schlechte Version).

**Warum nur MP3 und kein FLAC?**
So konfiguriert (`MIN_BITRATE=256`, nur MP3). Wer FLAC will, müsste die
Auswahl-Logik in `slsk.py` erweitern; aktuell werden Nicht-MP3-Dateien bewusst
ignoriert.

**Warum maximal 10 pro Lauf?**
Bewusst konservativ (`MAX_PER_RUN`), damit nicht auf einen Schlag Dutzende
Downloads starten und du die Trefferqualität in Ruhe beobachten kannst. Der Rest
folgt in den nächsten Läufen. Wert nach Belieben erhöhen.

**Lädt es denselben Song doppelt?**
Nein. Angefragte Songs werden in der State-Datei vermerkt und übersprungen (nur
im Echtlauf, nicht im Dry-Run).

**Was passiert, wenn ein Download in slskd fehlschlägt (Aborted/Errored)?**
Zu Beginn jedes Laufs prüft charts-download automatisch den Status aller
angefragten Downloads. Fehlgeschlagene (`Aborted`, `Errored`, `TimedOut`,
`Rejected`, `Cancelled`) werden aus dem State freigegeben **und** in slskd
weggeräumt (der rote Eintrag verschwindet). Im selben Lauf wird der Song dann
erneut gesucht und von einem anderen Anbieter geladen. Laufende Downloads
(`InProgress`, `Queued`) bleiben unangetastet.

**Ist das legal?**
`charts-download` steuert nur slskd. Für die rechtliche Bewertung des Downloads
urheberrechtlich geschützter Musik bist du selbst verantwortlich (siehe Hinweise
in der slskd-Doku und der charts-sync-README).
