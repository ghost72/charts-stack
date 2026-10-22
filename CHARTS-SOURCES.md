> 🇩🇪 Deutsche Version. English: [CHARTS-SOURCES.en.md](CHARTS-SOURCES.en.md)

# CHARTS-Quellen – Schema & Beispiele

Dieses Dokument beschreibt die `CHARTS`-Konfiguration von
[`charts-sync`](charts-sync): wie du **beliebig viele Playlists** definierst und
jede aus **einer oder mehreren Quellen** (Deezer und/oder Last.fm) zusammensetzt.
Alles ist über eine einzige Umgebungsvariable konfigurierbar – kein Code-Umbau,
keine festen Playlist-Namen.

---

## 1. Einleitung

`CHARTS` ist eine Umgebungsvariable in deiner `.env` (bzw. in der `environment:`
des `charts-sync`-Containers). Ihr Wert ist **ein JSON-Array** aus
Playlist-Objekten. Jedes Objekt beschreibt eine `.m3u`-Playlist und die Quellen,
aus denen sie befüllt wird.

- Eine Playlist kann **mehrere Quellen bündeln**; die Treffer werden zu **einer**
  Playlist zusammengeführt und über Künstler+Titel **dedupliziert** (gemergt).
- Der optionale Genre-Filter (`GENRE_FILTER=1`) hält Fremd-Genres aus den
  Pop-Charts (Details in der [charts-sync-README](charts-sync/README.md)).
- `charts-sync` matcht die Chart-Tracks gegen deine **bereits vorhandene**
  Library. Fehlende Songs landen in `missing-*.txt` und werden von
  `charts-download` nachgeladen.

> **Wichtig:** In der `.env` muss `CHARTS=` auf **einer einzigen Zeile** stehen.
> Die mehrzeiligen Beispiele unten sind nur zum Lesen; zum Übernehmen gibt es zu
> jedem Beispiel eine kompakte Einzeile.

### Alternative: Playlist-Definition aus einer Datei (`CHARTS_FILE`)

Wenn du mehrere Playlists mit je mehreren Quellen pflegst, ist die einzeilige
`CHARTS`-Variable mühsam. Deshalb kann `charts-sync` die Definition alternativ
aus einer **lesbaren, mehrzeiligen JSON-Datei** laden:

1. Lege `charts-sync/charts.json` an (Vorlage: `charts-sync/charts.json.example`,
   enthält fertige Pop-/Country-/Schlager-Beispiele).
2. Setze in der `.env`: `CHARTS_FILE=/config/charts.json`

Die Datei wird per `compose.yml` read-only nach `/config/charts.json` gemountet.
**Priorität:** `CHARTS_FILE` (wenn gesetzt, Datei existiert und valides JSON) >
`CHARTS`-Env > eingebaute Defaults. Das Schema ist **identisch** zur `CHARTS`-Env
(dasselbe JSON-Array), nur eben schön formatiert und mehrzeilig. Ist die Datei
kaputt (ungültiges JSON), fällt `charts-sync` auf die Default-Playlists und loggt
eine klare Fehlermeldung — es wird **nicht** stillschweigend die `CHARTS`-Env
genutzt (damit du den Fehler bemerkst).

> **Nach jeder Änderung** den Container neu starten, damit er die neue Config
> liest:
> ```bash
> docker compose up -d charts-sync   # oder: docker restart charts-sync
> ```

Ist `CHARTS` nicht gesetzt oder kaputt (ungültiges JSON), nutzt `charts-sync`
die eingebauten Default-Playlists und loggt eine deutliche Fehlermeldung mit
Verweis auf dieses Dokument (siehe [Troubleshooting](#7-troubleshooting)).

---

## 2. Vollständiges JSON-Schema

### Playlist-Objekt (ChartSource)

| Feld | Pflicht | Typ | Bedeutung |
|------|---------|-----|-----------|
| `name` | ja | String | Dateiname der `.m3u` (landet in `PLAYLIST_DIR`) |
| `title` | nein | String | Anzeigename in Navidrome (Emojis + `{date}` erlaubt); Default = `name` |
| `missing` | nein | String | Dateiname der Fehlliste (in `CHARTS_DIR`); Default `missing-<i>.txt` |
| `limit` | nein | `"all"` oder Zahl | Standard-Limit für Quellen ohne eigenes `limit`; Default = `CHART_LIMIT` |
| `sources` | ja* | Liste von Quell-Objekten | *oder eine Einzelquelle direkt am Objekt (abwärtskompatibel) |
| `exclude_artists` | nein | Liste oder CSV-String | Künstler, die **nur** aus dieser Playlist fliegen |
| `genre_filter` | nein | `true`/`false` | Pro-Playlist-Override des globalen `GENRE_FILTER`; fehlt das Feld = globaler Default |

### Quell-Objekt (Feed) – gemeinsame Felder

| Feld | Pflicht | Typ | Bedeutung |
|------|---------|-----|-----------|
| `service` | nein (empfohlen) | `"deezer"` / `"lastfm"` | Datenquelle; fehlt das Feld, wird es aus `type` abgeleitet (`type:"lastfm"` → Last.fm, sonst Deezer) |
| `limit` | nein | `"all"` oder Zahl | max. Tracks **dieser** Quelle |
| `enabled` | nein | bool | `false` = Quelle stilllegen, ohne sie zu löschen; Default `true` |

### `service` × `type`/`method` → Pflichtfelder

| service | Unterart | zusätzl. Pflichtfeld(er) | Minimal-Quelle |
|---------|----------|--------------------------|----------------|
| `deezer` | `type:"chart"` | `source` (Chart-ID, `0` = global) | `{"service":"deezer","type":"chart","source":"0"}` |
| `deezer` | `type:"playlist"` | `source` (Playlist-ID) | `{"service":"deezer","type":"playlist","source":"1313617925"}` |
| `deezer` | `type:"artist"` | `source` (Künstler-ID) | `{"service":"deezer","type":"artist","source":"16529"}` |
| `lastfm` | `method:"chart.getTopTracks"` | – | `{"service":"lastfm","method":"chart.getTopTracks"}` |
| `lastfm` | `method:"geo.getTopTracks"` | `country` (engl. Ländername) | `{"service":"lastfm","method":"geo.getTopTracks","country":"Switzerland"}` |
| `lastfm` | `method:"tag.getTopTracks"` | `tag` | `{"service":"lastfm","method":"tag.getTopTracks","tag":"pop"}` |
| `lastfm` | `method:"user.getTopTracks"` | `user` (+ optional `period`) | `{"service":"lastfm","method":"user.getTopTracks","user":"rj","period":"3month"}` |

> **Hinweis:** Die Last.fm-`method` ist **case-insensitiv** – der Parser wandelt
> sie intern in Kleinbuchstaben um. `geo.getTopTracks` und `geo.gettoptracks`
> sind also gleichwertig.

Für Last.fm-Quellen wird ein kostenloser `LASTFM_API_KEY`
(https://www.last.fm/api/account/create) benötigt. Ein versehentlich an einer
Last.fm-Quelle gesetztes Deezer-`source` wird **ignoriert** (nicht an die API
durchgereicht).

> **`type:"artist"`** holt die Top-Tracks EINES Künstlers
> (`/artist/<id>/top`, von Deezer intern bei ~100 gedeckelt). Praktisch für
> gezielte Künstler-Quellen, die keine Playlist/Chart gut abdeckt – z.B.
> Schweizer Mundart (Gölä, Polo Hofer, Patent Ochsner). Die `source` ist die
> **Künstler-ID**, nicht die Playlist-ID (siehe
> [Abschnitt 5](#5-idstags-finden-ohne-charts-config) zum Finden).

> **`genre_filter`** schaltet den Genre-Filter **pro Playlist** – `true`/`false`
> stechen den globalen `GENRE_FILTER` (Default aus, opt-in) aus; fehlt das Feld,
> gilt der globale Default. Sinnvoll nur bei **dynamischen** Chart-Quellen
> (unbekannte Songs); Künstler-/Mundart-Playlists lässt man meist unangetastet.
> Beispiel – nur für diese Pop-Chart-Playlist filtern (kein `CHARTS=`-Einzeiler,
> daher ohne `runnable`-Marker):
>
> ```json
> {"name":"charts.m3u","title":"Aktuelle Hits & Charts 🔥🎶","genre_filter":true,"sources":[{"service":"deezer","type":"chart","source":"0","limit":100,"enabled":true}]}
> ```

---

## 3. Deezer-Genre-IDs (Momentaufnahme + Live-Weg)

Die folgende Tabelle ist eine **Momentaufnahme** der bekannten, stabilen
Deezer-Genre-IDs (Stand dieses Dokuments):

| ID | Genre | ID | Genre |
|----|-------|----|-------|
| `0` | All | `98` | Classical |
| `132` | Pop | `84` | Country |
| `116` | Rap/Hip Hop | `129` | Jazz |
| `152` | Rock | `173` | Films/Games |
| `113` | Dance | `464` | Metal |
| `165` | R&B | `169` | Soul & Funk |
| `85` | Alternative | `2` | African Music |
| `106` | Electro | `12` | Brazilian Music |
| `466` | Folk | `16` | Indian Music |
| `153` | Blues | | |

**Live-Weg:** Die aktuelle, vollständige Liste gibt es jederzeit unter
`https://api.deezer.com/genre` (JSON, `data[].id` / `data[].name`). Im
charts-config-Panel „Quellen-Finder" erscheint dieselbe Liste als durchsuchbare
Tabelle.

**Aktualität der charts-config-Tabelle:** Die Genre-Tabelle in charts-config
aktualisiert sich **nicht** per Cronjob, sondern beim ersten Seitenaufruf **nach
Ablauf der 24-h-TTL** (`DASHBOARD_GENRE_CACHE_TTL`). Ist charts-config länger
nicht geöffnet, kann die Tabelle älter als 24 h sein, bis der nächste Aufruf sie
frischt – für den „täglich aktuell"-Anspruch genügt das.

> **Wichtig:** Diese IDs sind **Genre-Katalog-IDs** (`/genre`), **nicht**
> automatisch gültige `chart`-IDs. Für Länder-/Genre-spezifische Playlists ist
> der zuverlässigste Weg eine **Playlist-ID** (`type:"playlist"`) einer
> offiziellen Deezer-Länder-/Genre-Playlist. Deshalb empfiehlt dieses Dokument
> für „Pop/Country/…" primär den **Playlist-Weg** (Playlist-Suche in charts-config
> oder `https://api.deezer.com/search/playlist?q=…`).

---

## 4. Die vier Last.fm-Methoden

### `chart.getTopTracks`
Globale Top-Tracks. **Keine** Zusatzparameter.
```json
{"service":"lastfm","method":"chart.getTopTracks"}
```

### `geo.getTopTracks`
Top-Tracks eines Landes. Pflichtparameter `country` = ISO-3166-1-Ländername auf
**Englisch** (z.B. `Switzerland`, `Germany`, `United States`).
```json
{"service":"lastfm","method":"geo.getTopTracks","country":"Switzerland"}
```

### `tag.getTopTracks`
Top-Tracks zu einem **Tag/Genre**. Pflichtparameter `tag` (z.B. `pop`, `rock`,
`country`, `80s`, `swiss`). Tags sind frei vergeben, die Qualität variiert –
Brauchbarkeit am besten über den charts-config-Tag-Finder prüfen.
```json
{"service":"lastfm","method":"tag.getTopTracks","tag":"pop"}
```

### `user.getTopTracks`
Top-Tracks eines öffentlichen Last.fm-Users. Pflichtparameter `user`, optional
`period` (`overall`, `7day`, `1month`, `3month`, `6month`, `12month`).
```json
{"service":"lastfm","method":"user.getTopTracks","user":"rj","period":"3month"}
```

---

## 5. IDs/Tags finden (ohne charts-config)

Falls du das charts-config-Panel „Quellen-Finder" nicht nutzt, kommst du so an die
Werte:

- **Deezer-Playlist-ID:** Playlist in der Deezer-Web-App öffnen; die ID steht in
  der URL (`https://www.deezer.com/.../playlist/<ID>`). Oder per API:
  `https://api.deezer.com/search/playlist?q=pop`.
- **Deezer-Künstler-ID** (für `type:"artist"`): Künstler in der Deezer-Web-App
  öffnen; die ID steht in der URL (`https://www.deezer.com/.../artist/<ID>`).
  Oder per API: `https://api.deezer.com/search/artist?q=Gölä` (das Feld `id` des
  gesuchten Treffers; `tracklist` zeigt die passende `/artist/<id>/top`-URL).
- **Deezer-Genre-Liste:** `https://api.deezer.com/genre` (JSON mit
  `data[].id` / `data[].name`).
- **Last.fm-Tag:** im Browser unter `https://www.last.fm/tag/<tag>` prüfen; per
  API `https://ws.audioscrobbler.com/2.0/?method=tag.getTopTracks&tag=<tag>&api_key=<KEY>&format=json`.
- **Last.fm-Country:** englische Ländernamen verwenden; mit demselben
  `geo.getTopTracks`-Aufruf validieren.

> Im charts-config-Panel „Quellen-Finder" geht das bequemer und liefert direkt das
> fertige `.env`-Snippet (inkl. Tag-/Country-Validierung).

---

## 6. Copy-paste-Beispiele

Jedes Beispiel gibt es **mehrzeilig** (zum Lesen) und als **kompakte Einzeile**
(zum Übernehmen in die `.env`). Die kompakte Zeile unter dem Marker
`<!-- runnable:… -->` ist genau die, die in der `.env` nach `CHARTS=` kommt.

> **Erinnerung:** In der `.env` muss `CHARTS=` auf **einer** Zeile stehen.

### Rubrik A — sofort lauffähig (echte IDs / keine ID nötig)

Diese Beispiele nutzen **ausschließlich** verifizierte, real existierende
Deezer-IDs (`0` = globale Chart, `1313617925` = Top Switzerland,
`658561775` = Swiss Mundart Hits) **oder** ID-lose Last.fm-Methoden. Sie laufen
sofort.

#### A1 – Minimal / Default (Deezer global + Top Switzerland)

```json
[
  {
    "sources": [
      {"service": "deezer", "type": "chart", "source": "0", "limit": 100, "enabled": true},
      {"service": "deezer", "type": "playlist", "source": "1313617925", "limit": "all", "enabled": true}
    ],
    "name": "charts.m3u",
    "title": "Aktuelle Hits & Charts 🔥🎶",
    "missing": "missing.txt"
  }
]
```

Kompakte Einzeile (`CHARTS=` auf EINER Zeile):

<!-- runnable:minimal -->
```json
[{"sources":[{"service":"deezer","type":"chart","source":"0","limit":100,"enabled":true},{"service":"deezer","type":"playlist","source":"1313617925","limit":"all","enabled":true}],"name":"charts.m3u","title":"Aktuelle Hits & Charts 🔥🎶","missing":"missing.txt"}]
```

#### A2 – Mundart (Deezer-Playlist + `exclude_artists`)

```json
[
  {
    "sources": [
      {"service": "deezer", "type": "playlist", "source": "658561775", "limit": "all", "enabled": true}
    ],
    "name": "mundart.m3u",
    "title": "Schweizer Mundart 🇨🇭",
    "missing": "missing-mundart.txt",
    "exclude_artists": ["Nemo", "Trauffer"]
  }
]
```

Kompakte Einzeile (`CHARTS=` auf EINER Zeile):

<!-- runnable:mundart -->
```json
[{"sources":[{"service":"deezer","type":"playlist","source":"658561775","limit":"all","enabled":true}],"name":"mundart.m3u","title":"Schweizer Mundart 🇨🇭","missing":"missing-mundart.txt","exclude_artists":["Nemo","Trauffer"]}]
```

#### A3 – Country-Fan OHNE ID-Suche (nur Last.fm)

Eigene Playlist `country.m3u` aus zwei Last.fm-Tags – braucht **keine**
Deezer-Playlist-ID, läuft sofort (nur `LASTFM_API_KEY` nötig).

```json
[
  {
    "name": "country.m3u",
    "title": "Country 🤠",
    "sources": [
      {"service": "lastfm", "method": "tag.getTopTracks", "tag": "country", "limit": 100, "enabled": true},
      {"service": "lastfm", "method": "tag.getTopTracks", "tag": "classic country", "limit": 100, "enabled": true}
    ]
  }
]
```

Kompakte Einzeile (`CHARTS=` auf EINER Zeile):

<!-- runnable:country_lastfm -->
```json
[{"name":"country.m3u","title":"Country 🤠","sources":[{"service":"lastfm","method":"tag.getTopTracks","tag":"country","limit":100,"enabled":true},{"service":"lastfm","method":"tag.getTopTracks","tag":"classic country","limit":100,"enabled":true}]}]
```

#### A4 – Pop-Fan OHNE ID-Suche (Deezer global + Last.fm-Tag)

```json
[
  {
    "name": "pop.m3u",
    "title": "Pop 🎤",
    "sources": [
      {"service": "deezer", "type": "chart", "source": "0", "limit": 100, "enabled": true},
      {"service": "lastfm", "method": "tag.getTopTracks", "tag": "pop", "limit": 100, "enabled": true}
    ]
  }
]
```

Kompakte Einzeile (`CHARTS=` auf EINER Zeile):

<!-- runnable:pop_lastfm -->
```json
[{"name":"pop.m3u","title":"Pop 🎤","sources":[{"service":"deezer","type":"chart","source":"0","limit":100,"enabled":true},{"service":"lastfm","method":"tag.getTopTracks","tag":"pop","limit":100,"enabled":true}]}]
```

#### A5 – Mix mit stillgelegter Quelle (`enabled: false`)

Zeigt, wie eine Quelle vorübergehend ausgeschaltet wird, ohne sie zu löschen
(hier die Top-Switzerland-Playlist).

```json
[
  {
    "name": "charts.m3u",
    "title": "Aktuelle Hits & Charts 🔥🎶",
    "sources": [
      {"service": "deezer", "type": "chart", "source": "0", "limit": 100, "enabled": true},
      {"service": "deezer", "type": "playlist", "source": "1313617925", "limit": "all", "enabled": false}
    ]
  }
]
```

Kompakte Einzeile (`CHARTS=` auf EINER Zeile):

<!-- runnable:with_disabled_source -->
```json
[{"name":"charts.m3u","title":"Aktuelle Hits & Charts 🔥🎶","sources":[{"service":"deezer","type":"chart","source":"0","limit":100,"enabled":true},{"service":"deezer","type":"playlist","source":"1313617925","limit":"all","enabled":false}]}]
```

#### A6 – Mundart aus Künstler-Top-Tracks (`type:"artist"`)

Eine Mundart-Playlist, die statt (nur) einer kuratierten Deezer-Playlist gezielt
die Top-Tracks mehrerer Schweizer Künstler bündelt. Alle IDs sind verifiziert
(Deezer-Künstler-IDs): Gölä `16529`, Polo Hofer `449929`, Patent Ochsner `71287`,
Trauffer `187153`, Hecht `4459005`, Bligg `99572`. Zusätzlich die größte
Mundart-Playlist „Schwiizer MundArt" (`14877041603`). Alles wird zu einer
Playlist gemergt und dedupliziert.

```json
[
  {
    "name": "mundart.m3u",
    "title": "Schweizer Mundart 🇨🇭",
    "missing": "missing-mundart.txt",
    "sources": [
      {"service": "deezer", "type": "playlist", "source": "14877041603", "limit": "all", "enabled": true},
      {"service": "deezer", "type": "artist", "source": "16529", "limit": 50, "enabled": true},
      {"service": "deezer", "type": "artist", "source": "449929", "limit": 50, "enabled": true},
      {"service": "deezer", "type": "artist", "source": "71287", "limit": 50, "enabled": true},
      {"service": "deezer", "type": "artist", "source": "187153", "limit": 50, "enabled": true},
      {"service": "deezer", "type": "artist", "source": "4459005", "limit": 50, "enabled": true},
      {"service": "deezer", "type": "artist", "source": "99572", "limit": 50, "enabled": true}
    ]
  }
]
```

Kompakte Einzeile (`CHARTS=` auf EINER Zeile):

<!-- runnable:mundart_artists -->
```json
[{"name":"mundart.m3u","title":"Schweizer Mundart 🇨🇭","missing":"missing-mundart.txt","sources":[{"service":"deezer","type":"playlist","source":"14877041603","limit":"all","enabled":true},{"service":"deezer","type":"artist","source":"16529","limit":50,"enabled":true},{"service":"deezer","type":"artist","source":"449929","limit":50,"enabled":true},{"service":"deezer","type":"artist","source":"71287","limit":50,"enabled":true},{"service":"deezer","type":"artist","source":"187153","limit":50,"enabled":true},{"service":"deezer","type":"artist","source":"4459005","limit":50,"enabled":true},{"service":"deezer","type":"artist","source":"99572","limit":50,"enabled":true}]}]
```

### Rubrik B — Vorlage mit Platzhalter (ID erst holen)

Diese Vorlagen sind **nicht direkt lauffähig**: Jedes `<id>` muss zuerst über den
charts-config-Finder (Deezer-Playlist-Suche) oder
`https://api.deezer.com/search/playlist?q=…` durch eine echte ID ersetzt werden.
Sie stehen **bewusst nur mehrzeilig** als Vorlage – nicht als kompakte
copy-paste-Zeile – damit niemand versehentlich ein `<id>` live schaltet.

#### B1 – Pop-Fan mit kuratierter Deezer-Playlist

```json
[
  {
    "name": "pop.m3u",
    "title": "Pop 🎤",
    "sources": [
      {"service": "deezer", "type": "playlist", "source": "<id einer Deezer-Pop-Playlist>", "limit": "all", "enabled": true}
    ]
  }
]
```

#### B2 – Country-Fan mit kuratierter Deezer-Playlist

```json
[
  {
    "name": "country.m3u",
    "title": "Country 🤠",
    "sources": [
      {"service": "deezer", "type": "playlist", "source": "<id einer Deezer-Country-Playlist>", "limit": "all", "enabled": true}
    ]
  }
]
```

#### B3 – Voll-dynamisch, mehrere Playlists (empfohlener Stil)

`service` ist durchgehend gesetzt; gemischt aus echten Rubrik-A-IDs und
`<id>`-Vorlagen.

```json
[
  {
    "name": "charts.m3u",
    "title": "Aktuelle Hits & Charts 🔥🎶",
    "sources": [
      {"service": "deezer", "type": "chart", "source": "0", "limit": 100, "enabled": true},
      {"service": "deezer", "type": "playlist", "source": "1313617925", "limit": "all", "enabled": true},
      {"service": "lastfm", "method": "geo.getTopTracks", "country": "Switzerland", "limit": 100, "enabled": true}
    ]
  },
  {
    "name": "country.m3u",
    "title": "Country 🤠",
    "sources": [
      {"service": "lastfm", "method": "tag.getTopTracks", "tag": "country", "limit": 100, "enabled": true},
      {"service": "deezer", "type": "playlist", "source": "<id einer Deezer-Country-Playlist>", "limit": "all", "enabled": true}
    ]
  }
]
```

---

## 7. Troubleshooting

`charts-sync` validiert die `CHARTS`-Config beim Start und loggt deutlich, ohne
jemals zu crashen. Logs ansehen:

```bash
docker logs charts-sync
```

Typische Zeilen und ihre Bedeutung:

- `CHARTS geladen: N Playlist(s), M aktive Quelle(n).` – alles in Ordnung; `M`
  zählt die Quellen **nach** eventuellen `enabled:false`-Abschaltungen.
- `CHARTS geladen: … , K Warnung(en). Details oben; …` – es gab `K` Warnungen,
  die unmittelbar davor einzeln geloggt wurden.
- `CHARTS: Playlist '…', Quelle #i: unbekannter service '…'` – wird als
  Deezer behandelt.
- `CHARTS: Playlist '…', Quelle #i: unbekannte Last.fm-method '…'` bzw.
  `… braucht Parameter '…'` – diese Quelle wird **deaktiviert** (`enabled=False`).
- `CHARTS: Playlist '…', Quelle #i: unbekannter Deezer-type '…'` – fällt auf
  `chart` zurück.
- `CHARTS ist kein gueltiges JSON an Position … (…)` – die ganze `CHARTS`-Variable
  ist kaputt; `charts-sync` nutzt die eingebauten Default-Playlists, bis du den
  JSON-Fehler behebst (meist eine `.env`, in der `CHARTS=` nicht auf **einer**
  Zeile steht, oder ein fehlendes Komma/Anführungszeichen).

Fehlerhafte Einzelquellen werden deaktiviert bzw. auf einen sinnvollen Default
gemappt – der Rest der Playlist läuft weiter.
