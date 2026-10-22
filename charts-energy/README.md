# charts-energy

Pollt die Schweizer **NRJ / energy.ch**-Webradio-Kanäle auf ihren aktuell
gespielten Song (über die Icecast-**ICY**-In-Stream-Metadaten auf den
Infomaniak-Stream-Hosts), sammelt die beobachteten Spins, matcht sie gegen deine
Library und speist die fehlenden in die bestehende
[`charts-download`](../charts-download)-Pipeline ein – dieselbe Mechanik wie
[`charts-hitparade`](../charts-hitparade), nur eine andere Quelle.

Es gibt **keine** nutzbare JSON-API; der ICY-Metadatenblock ist der einzige Weg
(Details: `.agents/tasks/energy-api-feasibility.md`). Der Dienst wird
**standardmässig deaktiviert** ausgeliefert (`ENERGY_ENABLED=0`, leere
Kanalliste), damit ein frischer Clone nicht ungefragt Icecast anzapft.

---

## Drei Integrations-Modi pro Kanal

- **OWN (eigene Playlist)** – z. B. Pop/Hits/Love: charts-energy schreibt eine
  eigene `energy-<slug>.m3u` + `missing-energy-<slug>.txt`, genau wie hitparade.
  Der Playlist-Name **muss** im reservierten Namensraum `energy-*.m3u` liegen
  (Namen werden kleingeschrieben/normalisiert; jeder Nicht-`energy-`-Name wird
  abgelehnt). So kollidiert ein OWN-Kanal nie mit einer kuratierten
  charts-sync-Playlist. Zwei Schreiber zielen nie auf dieselbe `.m3u` (und auch
  nie auf dieselbe Fehlliste). Um in eine bestehende/gemeinsame Playlist zu
  schreiben, immer `feed` nutzen, nie `own`.
- **FEED (in bestehendes Thema)** – z. B. Mundart: charts-energy schreibt
  **keine** eigene Playlist, sondern eine **Übergabe-Datei pro Thema**
  (`.charts-energy-feed-<theme>.json`). [`charts-sync`](../charts-sync) liest
  diese als zusätzlichen Feed eines bestehenden Themas – so bleibt es bei
  **genau einer** `mundart.m3u` (von charts-sync geschrieben). Energy-Tracks
  werden dort gegen die Deezer/Last.fm-Quellen entdoppelt und **nach** ihnen
  einsortiert (Positions-Priorität bleibt bei den kuratierten Charts).
- **LIBRARY (nur nachladen, keine Playlist)** – z. B. Dekaden (80er/90er):
  charts-energy schreibt **keine** `.m3u`, sondern füllt wie
  [`charts-flashback`](../charts-flashback) nur die **gemeinsame Library** auf:
  pro Lauf eine gemeinsame Fehlliste (`missing-energy-library.txt`, von
  charts-download nachgeladen) und – für jeden **fehlenden** Song – ein
  Erscheinungsjahr in die **gemeinsame Jahr-Map**
  (`.charts-flashback-years.json`, von charts-import gelesen). Das Jahr taucht
  **nie** im Pfad/Dateinamen auf, nur als Tag – so landet der Song über die
  **Navidrome Smart Playlists (NSP)** in der richtigen Dekade. Die **Dekade**
  ist zuverlässig (über den Slug `energy80s` → 1980er + Clamp), das exakte Jahr
  ist best effort über MusicBrainz.

## Wie die Daten geholt werden (kein Headless-Browser, keine API)

Pro Kanal wird der Infomaniak-Stream mit dem Header `Icy-MetaData: 1` geöffnet,
**ein** Metadatenblock gelesen und die Verbindung sofort geschlossen. Der
`StreamTitle` wird am **U+02D7 `˗`** (MODIFIER LETTER MINUS SIGN, **nicht** am
ASCII-Bindestrich) in Artist/Titel getrennt; nur Einträge mit `type=music`
werden behalten (Werbung/Jingles raus). Entdoppelt wird pro Spin über
`startedAt`+`plId`. Die ICY-Lese nutzt die Stdlib (`http.client`/`ssl`, normale
TLS-Prüfung) – **keine** neue Abhängigkeit.

Ein verpasster/leerer Block (zwischen zwei Songs, kein Metadaten-Host) ist ein
stiller `None` – ein transienter Lesefehler eine einmalige WARN-Zeile, nie ein
Dienst-Fehler. Höflich: 60–120 s pro Kanal, Verbindung sofort zu.

## Kanäle entdecken

```bash
docker compose run --rm charts-energy python -m app.discover          # Liste
docker compose run --rm charts-energy python -m app.discover --probe energypop
```

`--list` zeigt die bestätigten Slugs (Anzeige | Slug | Genre); `--probe <slug>`
prüft einen geratenen Slug (HTTP 200 + `icy-name`-Header ⇒ echter Kanal).

## Konfiguration (zentrale `.env`)

| Variable | Default | Zweck |
| --- | --- | --- |
| `ENERGY_ENABLED` | `0` (**aus**) | An/Aus-Schalter. `false` = Dienst pausieren: Container läuft weiter, kein Polling/Playlist – Status **`idle`** (grau im Dashboard, **kein** Fehler). Toleranter Parser (`true`/`1`/`yes`/`on` bzw. `false`/`0`/`no`/`off`). |
| `ENERGY_CHANNELS` | *(leer)* | Kanalliste als **JSON** (einzeilig). Jeder Eintrag: `slug`, optional `title`, `enabled`, `mode` (`own`/`feed`/`library`), für `own` optional `playlist`/`missing` (Namensraum `energy-*.m3u`), für `feed` das `theme`, für `library` optional `decade` (Override des Slug-Jahrzehnts). |
| `ENERGY_CHANNELS_FILE` | *(leer)* | Pfad zu einer mehrzeiligen JSON-Datei – hat Vorrang vor `ENERGY_CHANNELS`. |
| `ENERGY_POLL_SECONDS` | `90` | Poll-Intervall pro Kanal (60–120 s empfohlen). |
| `ENERGY_MATCH_INTERVAL_MINUTES` | `30` | Wie oft gematcht + geschrieben wird (teurer Library-Scan, daher vom Poll entkoppelt). |
| `ENERGY_TRACK_RETENTION_DAYS` | `30` | Spins älter als X Tage fallen aus dem Puffer. |
| `ENERGY_MAX_TRACKS_PER_CHANNEL` | `500` | Obergrenze pro Kanal (neueste behalten). |
| `MATCH_THRESHOLD` | `87` | Match-Schwelle (wie charts-sync). |
| `ARTIST_MIN_SCORE` | `60` | Künstler-Mindest-Score. |
| `IGNORE_COMPILATIONS` | `1` | Compilation-Tracks beim Match ignorieren. |
| `MB_ENABLED` | `1` | **library-Modus:** MusicBrainz-Jahresauflösung an/aus (für die Dekaden-NSPs). |
| `MB_USER_AGENT_ENERGY` | `charts-energy/1.0 (self-hosted)` | User-Agent für die MusicBrainz-Abfrage (MB verlangt einen aussagekräftigen UA). |
| `MB_RATE_SLEEP` | `1.1` | Pause (s) vor jeder MB-Abfrage (~1 req/s, höflich zu MusicBrainz). |
| `MB_SCORE_MIN` | `90` | Minimaler MB-Recording-Score, damit ein Treffer zählt. |
| `ENERGY_LIBRARY_MISSING_FILE` | `missing-energy-library.txt` | **library-Modus:** EINE gemeinsame Fehlliste aller library-Kanäle. In `MISSING_FILES` ergänzen. |
| `PUSHOVER_SOUND` | `falling` | Benachrichtigungston für diesen Dienst. Leer = Account-Default. |

> Pro `library`-Kanal optional ein `decade`-Override im JSON (z. B.
> `"decade":"1980"`); ohne wird das Jahrzehnt aus dem Slug `energy80s` →
> `1980` abgeleitet. Ein Nicht-Dekaden-Slug (z. B. `energypop`) ergibt „kein
> Jahrzehnt": dann nur das MusicBrainz-Jahr, sonst gar kein Jahr-Tag.

Der Zustand (`.charts-energy-state.json`) und die FEED-Übergabedateien
(`.charts-energy-feed-<theme>.json`) liegen im `CHARTS_DIR`.

## Einschalten

`ENERGY_ENABLED=1` setzen und eine Kanalliste eintragen. Zwei Beispiele:

**Mundart-Auffüllung (FEED) – füllt die bestehende `mundart.m3u` auf:**

```dotenv
ENERGY_CHANNELS=[{"slug":"energymadeinswitzerland","mode":"feed","theme":"mundart","enabled":true}]
```

> Hinweis: Der Swiss-Kanal heisst **„Energy Made In Switzerland"**
> (`energymadeinswitzerland`) – es gibt keinen Kanal namens „Mundart". Er ist
> etwas breiter als reine Mundart (auch Schweizer Acts in anderen Sprachen) und
> eignet sich daher gut zum **Auffüllen** der Deezer-Mundart-Liste.

**Pop als eigene Playlist (OWN):**

```dotenv
ENERGY_CHANNELS=[{"slug":"energypop","title":"Energy Pop 🎶","mode":"own","playlist":"energy-pop.m3u","enabled":true}]
```

Bei einem **OWN**-Kanal die erzeugte Fehlliste in `MISSING_FILES` ergänzen (eine
Zeile in der `.env`), damit charts-download sie nachlädt:

```dotenv
MISSING_FILES=/charts/missing.txt;/charts/missing-energy-pop.txt
```

> Der ausgelieferte `MISSING_FILES`-Default bleibt `"/charts/missing.txt"`.
> FEED-Kanäle erzeugen **keine** eigene `missing-energy-*`-Datei – ihre
> fehlenden Songs landen in der Fehlliste des Themas (z. B. `missing-mundart.txt`,
> von charts-sync geschrieben).

### Dekaden (80er/90er) – nur nachladen via LIBRARY

Dekaden brauchen **keine** eigene Playlist mehr: die Dekaden-Kanäle laufen im
**LIBRARY**-Modus, füllen die gemeinsame Library auf und bekommen pro Song ein
Erscheinungsjahr (als Tag, **nicht** im Datei-/Ordnernamen). Die Dekaden-Anzeige
übernehmen die **Navidrome Smart Playlists**, die nach Jahr filtern. Dafür nur
die eine gemeinsame library-Fehlliste in `MISSING_FILES` ergänzen:

```dotenv
# Dekaden (nur nachladen -> NSP, keine Playlist).
ENERGY_CHANNELS=[{"slug":"energy80s","mode":"library","enabled":true},{"slug":"energy90s","mode":"library","enabled":true}]
MISSING_FILES=/charts/missing.txt;/charts/missing-energy-library.txt
```

> Die **Dekade** ist zuverlässig (aus dem Slug + Clamp), das exakte Jahr ist
> best effort über MusicBrainz – für die Dekaden-NSP ist genau das, was zählt.
> Ein `decade`-Override pro Kanal ist möglich (z. B. `"decade":"1980"`).

### Kombinierte Config (mehrere Kanäle, alle Modi auf einmal)

Die Modi lassen sich frei mischen – alles in **einer** `ENERGY_CHANNELS`-Zeile.
Ein realistisches Beispiel: Mundart **und** Hits je als `feed` in die
bestehenden Playlists `mundart.m3u`/`charts.m3u`, die Dekaden als `library`:

```dotenv
ENERGY_ENABLED=1
ENERGY_CHANNELS=[{"slug":"energymadeinswitzerland","mode":"feed","theme":"mundart","enabled":true},{"slug":"energyhits","mode":"feed","theme":"charts","enabled":true},{"slug":"energy80s","mode":"library","enabled":true},{"slug":"energy90s","mode":"library","enabled":true}]
# library-Kanäle brauchen die gemeinsame Fehlliste in MISSING_FILES:
MISSING_FILES=/charts/missing.txt;/charts/missing-mundart.txt;/charts/missing-energy-library.txt
```

Für jedes `feed`-Thema muss die **Gegenseite** in `charts-sync/charts.json`
denselben `theme`-Wert tragen (siehe `charts-sync/charts.json.example`): beim
`mundart.m3u`-Block `{ "service": "energy", "theme": "mundart" }`, beim
`charts.m3u`-Block `{ "service": "energy", "theme": "charts" }`. Producer
(`.env`) und Consumer (`charts.json`) müssen beim `theme` **byte-identisch**
sein – ein Tippfehler sieht aus wie „Energy hat nichts beigetragen", nicht wie
ein Fehler.

## Monitoring

Verpasste ICY-Lesevorgänge sind transient (eine WARN-Zeile, weiter geht's). Nur
wenn der **Match-/Output-Lauf** selbst wirft, meldet der Dienst `status=error`
(Dashboard-Kachel rot + Pushover). Deaktiviert = `idle` (graue Kachel).

> 🇬🇧 English version: [README.en.md](README.en.md)
