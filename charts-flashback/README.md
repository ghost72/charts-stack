> 🇩🇪 Deutsche Version. English: [README.en.md](README.en.md)

# charts-flashback

Nachlade-Dienst, der die Musik-Rotation von **FLASHBACK FM** anzapft und deine
Library damit wachsen lässt. Er liest die „zuletzt gespielt"-Liste des
**Live-Hauptstreams** (spielt alle Dekaden gemischt) über die öffentliche
GraphQL-API des Senders, sammelt die Songs über die Zeit und lädt fehlende über
die bestehende charts-Kette nach.

> **Nur der Hauptstream** (`flashbackfm.ffm`) führt eine abrufbare
> „zuletzt gespielt"-Historie. Die thematischen Dekaden-/Künstler-Channels
> existieren zwar, liefern über die API aber keine Historie (`played=null`) –
> deshalb wird der Hauptstream abgefragt, der ohnehin alle Dekaden mischt.

**Download-only:** charts-flashback schreibt **keine** Playlists. Die
Dekaden-Anzeige übernehmen die **Navidrome Smart Playlists (NSP)**, die die
Library nach Jahr filtern. Damit das funktioniert, bekommt jeder nachgeladene
Song ein **Jahr-Tag** (siehe „Jahr-Hybrid").

> Gleiches Prinzip wie `charts-recommend`: Beschaffung gehört in den Stack,
> die Anzeige/Personalisierung in den Client (Substreamer) bzw. in die NSP.

## Wie es funktioniert

```
FLASHBACK FM (GraphQL, zuletzt gespielt)
      │  pro Lauf (Hauptstream)
      ▼
charts-flashback ─► missing-flashback.txt ─► charts-download ─► slskd
      │  (Jahr: Titel-Suffix (JJJJ),               │
      │   MusicBrainz nur Fallback)                 │
      └─ State (schon gesehen)              charts-import ─► Library (mit Jahr-Tag)
         + Jahr-Map (.charts-flashback-years.json)  │
                                            Navidrome Smart Playlists (Dekaden)
```

1. Pro konfiguriertem Channel die „zuletzt gespielt"-Liste holen (GraphQL).
2. Nur **neue** Songs (nicht im State) verarbeiten – die Radio-Rotation
   wiederholt sich, der State verhindert Doppelarbeit.
3. Für jeden neuen Song die Schreibweise kanonisieren (Deezer, s. u.) und ein
   Erscheinungsjahr bestimmen (Titel-Suffix bzw. MusicBrainz-Fallback, s. u.).
4. Gegen die Library matchen; was fehlt, kommt **ohne Jahr** in
   `missing-flashback.txt` (sauberes „artist - title"-Format wie bei den anderen
   Diensten). Das Jahr wandert **separat** in die Jahr-Map
   `.charts-flashback-years.json` – **nicht** in den Datei-/Ordnernamen.
5. `charts-download` lädt nach, `charts-import` liest die Jahr-Map und setzt
   beim Einsortieren das `date`/`originaldate`-Tag → der Song erscheint in der
   richtigen Dekaden-NSP.

## Das Jahr (für die Dekaden-NSP)

FLASHBACK gibt das Erscheinungsjahr **direkt im Titel** als Suffix an, z. B.
`THE WILD BOYS (1984)`. Das ist die **verlässliche** Jahr-Quelle (der Sender
selbst). charts-flashback spaltet das `(JJJJ)` ab → sauberer Titel + Jahr; das
Jahr wird beim Import als `date`/`originaldate`-Tag gesetzt, damit der Song in
der richtigen Dekaden-NSP erscheint.

**MusicBrainz** dient nur noch als **Fallback** für die seltenen Titel **ohne**
`(JJJJ)`-Suffix (frühestes Studio-Erstrelease, mit Rate-Limit + Cache; über
`MB_ENABLED=0` abschaltbar). Steht ein Jahr im Titel, wird MusicBrainz gar nicht
erst angefragt – schneller und zuverlässiger.

> Das Jahr wird **nie** im Datei-/Ordnernamen abgelegt. Es geht ausschließlich
> über die Jahr-Map `.charts-flashback-years.json` an `charts-import`, der es als
> `date`/`originaldate`-Tag setzt.

## Kanonische Schreibweise (Deezer)

FLASHBACK FM liefert Artist und Titel in **GROSSBUCHSTABEN** (z. B.
`U2 / WHERE THE STREETS HAVE NO NAME`). Diese Rohwerte würden ungefiltert in die
missing-Liste und damit in Ordner-/Dateinamen der Library wandern. Deshalb löst
charts-flashback zu jedem Song die **offizielle Mixed-Case-Schreibweise** über
die offene Deezer-Such-API auf (kein API-Key), konsistent zu `charts-sync`:

- Plain-Query `q="<artist> <title>"` (die strukturierte Advanced-Query liefert
  bei Deezer konsequent 0 Treffer).
- Validierung über dieselbe `normalize`/`similarity`-Logik wie das
  Library-Matching; ein Treffer gilt nur ab Ähnlichkeit `DEEZER_MATCH_MIN`
  (Artist **und** Titel).
- Gewählt wird der **beste** belastbare Treffer (höchste Summe), nicht blind der
  erste – schützt vor Live-/Remix-/Edit-Varianten weiter oben.
- Cache `.charts-flashback-deezer-cache.json` (inklusive Misses), damit
  wiederkehrende Songs die API nicht erneut belasten.
- **fail-open:** Bei abgeschaltetem Schalter, keinem belastbaren Treffer oder
  einem Netz-/HTTP-/JSON-Fehler bleibt der Rohwert stehen (lieber GROSS als
  falsch geraten).

Die Kanonisierung läuft **vor** Key/Match/missing/Jahr-Map, damit der in
`missing-flashback.txt` geschriebene Name den späteren Ordner-/Dateinamen
bestimmt.

## Konfiguration (zentrale `.env`)

| Variable | Default | Bedeutung |
|----------|---------|-----------|
| `FLASHBACK_ENABLED` | `true` | An/Aus-Schalter (`true`/`false`). `false` = Dienst pausieren: Container läuft weiter, kein Abruf – Status **`idle`** (grau im Dashboard, **kein** Fehler). Toleranter Parser (`true`/`1`/`yes`/`on` bzw. `false`/`0`/`no`/`off`). |
| `FLASHBACK_HASH` | *(Beispiel in .env.example)* | Persisted-Query-Hash der LastPlayed-Query. Öffentlich, **kein Secret**. Bei aktiviertem Dienst, aber leerem Hash → Status **`idle`** („kein FLASHBACK_HASH gesetzt – nichts zu tun"). |
| `FLASHBACK_CHANNELS` | `flashbackfm.ffm` | Streams (streamName), kommagetrennt. Nur der Hauptstream führt eine Historie. |
| `FLASHBACK_INTERVAL_HOURS` | `2` | Lauf-Intervall. |
| `FLASHBACK_LIMIT` | `50` | Max. neue Songs pro Lauf. |
| `FLASHBACK_PLAYED_LIMIT` | `50` | Wie viele „zuletzt gespielt" je Channel abgefragt werden. |
| `MB_ENABLED` | `1` | MusicBrainz-Jahr-Auflösung an/aus. |
| `MB_USER_AGENT` | `charts-flashback/1.0 (self-hosted)` | MusicBrainz verlangt einen aussagekräftigen User-Agent. |
| `DEEZER_CANONICAL_ENABLED` | `1` | Kanonische Schreibweise via Deezer an/aus. |
| `DEEZER_MATCH_MIN` | `80` | Mindest-Ähnlichkeit (Artist und Titel, 0–100), damit ein Deezer-Treffer als kanonische Quelle gilt. |
| `PUSHOVER_SOUND` | `falling` | Benachrichtigungston für diesen Dienst (z.B. der `status=error`-Alarm). Gültige Pushover-Töne: `pushover, magic, cosmic, intermission, bike, bugle, cashregister, classical, falling, gamelan, incoming, mechanical, pianobar, siren, spacealarm, tugboat, alien, climb, persistent, echo, updown, none` (= still), `vibrate`. Leer = Account-Default. Über `PUSHOVER_SOUND_FLASHBACK` in der `compose.yml` überschreibbar. |

Hinweis: Die `DEEZER_*`-Variablen sind in der `compose.yml` **nicht** gesetzt →
es greift der Code-Default (kanonische Schreibweise standardmäßig **an**).

### „Pausiert" vs. „läuft leer" – Status `idle`

`FLASHBACK_ENABLED=false` und ein aktiver Dienst **ohne** `FLASHBACK_HASH` sind
beide „bewusst nichts tun" und erscheinen deshalb als Status **`idle`** (grau, nie
rot – weder Dashboard noch Watchdog alarmieren):

1. `FLASHBACK_ENABLED=false` → Status `idle`, Log „deaktiviert".
2. `FLASHBACK_ENABLED=true`, aber `FLASHBACK_HASH` leer → Status `idle` mit
   „kein FLASHBACK_HASH gesetzt – nichts zu tun" (früher `ok`/„nichts zu tun";
   `idle` ist ehrlicher, da gar kein Abruf lief).

Ein gesetzter, aber **toter** Hash bleibt dagegen ein echter `error` (siehe unten).

Hinweis: Die thematischen Channels (`flashbackfm-80er`, `queen`, …) liefern über
die API **keine** „zuletzt gespielt"-Historie (`played=null`) und sind daher hier
nicht sinnvoll. Der Hauptstream `flashbackfm.ffm` deckt alle Dekaden ab.

### Den Hash aktualisieren

Der `FLASHBACK_HASH` kann sich ändern, wenn FLASHBACK FM seine Web-App neu baut.
Dann meldet der Dienst `status=error` (Dashboard-Kachel rot + Pushover). So holst
du den neuen Hash:

1. flashbackfm.ch im Browser öffnen, Entwickler-Tools → Netzwerk-Tab.
2. Filter auf `played` setzen, einen Channel abspielen/wechseln.
3. Den Request anklicken; die URL endet auf `.../LastPlayed/<HASH>?variables=…`.
4. `<HASH>` kopieren, in der `.env` als `FLASHBACK_HASH` setzen, Dienst neu
   starten (`docker compose up -d charts-flashback`).

## Verzahnung mit charts-download

Damit die Songs wirklich nachgeladen werden, muss `/charts/missing-flashback.txt`
in der `MISSING_FILES`-Variable von charts-download stehen (in der
`.env.example` bereits so vorbereitet).

## Container

| Container | Zweck |
|-----------|-------|
| `charts-flashback` | Scheduler-Loop: Rotation holen, Jahr auflösen, matchen, missing schreiben. Kein Webserver. |

Nutzt bewusst dieselben (kopierten) Bausteine wie die Schwester-Dienste
(`library.py`/`notify.py`/`status.py`/`manifest.py`) – keine Laufzeit-Abhängigkeit.

## Daten & Pfade

| Host-Pfad | Zweck |
|-----------|-------|
| `/volume2/music/Music` | Library (gescannt, **read-only** gemountet) |
| `/volume2/music/Charts` | `missing-flashback.txt`, Jahr-Map, State, Caches |

State-, Map- und Cache-Dateien (im Charts-Ordner):
`.charts-flashback-state.json` (schon gesehene Songs),
`.charts-flashback-years.json` (Jahr-Map, von charts-import gelesen),
`.charts-flashback-mbcache.json` (MusicBrainz-Jahr-Cache),
`.charts-flashback-deezer-cache.json` (Deezer-Casing-Cache).

## Betrieb

```bash
docker compose up -d --build charts-flashback   # bauen + starten
docker logs -f charts-flashback                 # ersten Lauf beobachten
```

## Einmalige Migration / Altlasten

Die 7 vor der Deezer-Kanonisierung bereits **GROSS** in der Library liegenden
flashback-Songs räumt das idempotente Skript
`scripts/migrate_flashback_casing.py` nach (holt die Zielschreibweise zur
Laufzeit über dieselbe `deezer.resolve_canonical`-Funktion; `--dry-run`
unterstützt).

> Der echte Rename muss auf dem **NAS-Host** laufen (als `root`, mit
> `PYTHONPATH` auf das charts-flashback-Verzeichnis), weil der Container die
> Library **read-only** mountet (`/music/Music:ro`) – ein echter Lauf im
> Container scheitert mit „Read-only file system". Das `--dry-run` läuft im
> Container problemlos.

Für die zugehörigen vergifteten Manifest-Einträge (alte GROSS/`{JJJJ}`-Titel)
gibt es in `charts-import` das Cleanup-Skript
`scripts/cleanup_flashback_legacy.py`.

## Rechtlicher Hinweis

Wie der ganze Stack lädt auch dieser Dienst Musik über Soulseek nach. Lade nur
Inhalte herunter, zu denen du berechtigt bist. FLASHBACK FM dient hier nur als
Ideengeber (welche Songs laufen) – die Beschaffung läuft über die bestehende
charts-Kette.
