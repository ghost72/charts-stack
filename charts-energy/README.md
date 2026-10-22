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

## Zwei Integrations-Modi pro Kanal

- **OWN (eigene Playlist)** – z. B. Pop/Hits/Love: charts-energy schreibt eine
  eigene `energy-<slug>.m3u` + `missing-energy-<slug>.txt`, genau wie hitparade.
- **FEED (in bestehendes Thema)** – z. B. Mundart, 80er/90er: charts-energy
  schreibt **keine** eigene Playlist, sondern eine **Übergabe-Datei pro Thema**
  (`.charts-energy-feed-<theme>.json`). [`charts-sync`](../charts-sync) liest
  diese als zusätzlichen Feed eines bestehenden Themas – so bleibt es bei
  **genau einer** `mundart.m3u` (von charts-sync geschrieben). Energy-Tracks
  werden dort gegen die Deezer/Last.fm-Quellen entdoppelt und **nach** ihnen
  einsortiert (Positions-Priorität bleibt bei den kuratierten Charts).

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
| `ENERGY_CHANNELS` | *(leer)* | Kanalliste als **JSON** (einzeilig). Jeder Eintrag: `slug`, optional `title`, `enabled`, `mode` (`own`/`feed`), für `own` optional `playlist`/`missing`, für `feed` das `theme`. |
| `ENERGY_CHANNELS_FILE` | *(leer)* | Pfad zu einer mehrzeiligen JSON-Datei – hat Vorrang vor `ENERGY_CHANNELS`. |
| `ENERGY_POLL_SECONDS` | `90` | Poll-Intervall pro Kanal (60–120 s empfohlen). |
| `ENERGY_MATCH_INTERVAL_MINUTES` | `30` | Wie oft gematcht + geschrieben wird (teurer Library-Scan, daher vom Poll entkoppelt). |
| `ENERGY_TRACK_RETENTION_DAYS` | `30` | Spins älter als X Tage fallen aus dem Puffer. |
| `ENERGY_MAX_TRACKS_PER_CHANNEL` | `500` | Obergrenze pro Kanal (neueste behalten). |
| `MATCH_THRESHOLD` | `87` | Match-Schwelle (wie charts-sync). |
| `ARTIST_MIN_SCORE` | `60` | Künstler-Mindest-Score. |
| `IGNORE_COMPILATIONS` | `1` | Compilation-Tracks beim Match ignorieren. |
| `PUSHOVER_SOUND` | `falling` | Benachrichtigungston für diesen Dienst. Leer = Account-Default. |

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
ENERGY_CHANNELS=[{"slug":"energypop","title":"Energy Pop 🎶","mode":"own","enabled":true}]
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

### Optional: Dekaden-Thema (80er/90er) als EINE Playlist

Die 80er/90er-Kanäle laufen im **FEED**-Modus auf ein gemeinsames Thema. Das
Repo liefert **keine** Dekaden-Playlist mit – du musst das Thema selbst in
`charts-sync`s `CHARTS` anlegen. Producer- und Consumer-Seite müssen beim
`theme` **byte-identisch** sein; der folgende Block ist als **ein
Copy-Paste-Paar** gedacht:

```jsonc
// 1) Producer: charts-energy ENERGY_CHANNELS (.env)
//    energy80s + energy90s auf dasselbe Thema "achtziger-neunziger".
ENERGY_CHANNELS=[{"slug":"energy80s","mode":"feed","theme":"achtziger-neunziger","enabled":true},{"slug":"energy90s","mode":"feed","theme":"achtziger-neunziger","enabled":true}]

// 2) Consumer: charts-sync CHARTS – eine neue Dekaden-Playlist, die den
//    Energy-Feed desselben Themas konsumiert.
{
  "sources": [
    { "service": "energy", "theme": "achtziger-neunziger", "enabled": true }
  ],
  "name": "achtziger-neunziger.m3u",
  "title": "80er & 90er \uD83D\uDCFB",
  "missing": "missing-achtziger-neunziger.txt"
}
```

> ⚠️ **Lockstep:** Den `theme`-Wert in beiden Zeilen **gemeinsam** ändern oder
> auf keiner. charts-energy kann die Gegenseite nicht prüfen – ein Tippfehler
> auf einer Seite sieht aus wie „Energy hat nichts beigetragen", nicht wie ein
> Konfigurationsfehler. Deshalb das Copy-Paste-Paar.

## Monitoring

Verpasste ICY-Lesevorgänge sind transient (eine WARN-Zeile, weiter geht's). Nur
wenn der **Match-/Output-Lauf** selbst wirft, meldet der Dienst `status=error`
(Dashboard-Kachel rot + Pushover). Deaktiviert = `idle` (graue Kachel).

> 🇬🇧 English version: [README.en.md](README.en.md)
