# charts-hitparade

Holt wöchentlich (sonntags) die **offizielle Schweizer Hitparade** von
[hitparade.ch](https://hitparade.ch) – die Single-Top-100 –, matcht sie gegen
deine Library und schreibt eine Playlist `hitparade.m3u` + eine Fehlliste
`missing-hitparade.txt`, die [`charts-download`](../charts-download) nachlädt.

Fünfte Chart-Quelle neben [`charts-sync`](../charts-sync) (Deezer/Last.fm) –
dieselbe Mechanik, nur eine andere Quelle: die offizielle IFPI-Schweiz-Chart.

---

## Was es macht

- **Sonntag 20:00** (konfigurierbar): holt die aktuelle Top-100 von
  hitparade.ch.
- Matcht jeden Song gegen die Library (gleiche Logik wie charts-sync:
  Fuzzy-Match, `IGNORE_COMPILATIONS`).
- **Treffer** → `hitparade.m3u` (Navidrome-Playlist).
- **Fehlende** → `missing-hitparade.txt` → charts-download lädt die findbaren
  nach.

Die Playlist ist eine **Momentaufnahme der aktuellen Woche** (genau die 100
Songs der Woche, jeden Sonntag neu geschrieben). Herausgefallene Songs
verschwinden aus der Playlist, die heruntergeladenen Dateien bleiben aber in der
Library (auffindbar über Navidrome „Kürzlich hinzugefügt" und beim Künstler).

## Wie die Daten geholt werden (kein Headless-Browser)

hitparade.ch liefert die Top-100 als **HTML** aus – vorausgesetzt, man sendet
einen echten Browser-`User-Agent` (ohne UA kommt nur ein leerer Platzhalter).
Jeder Chart-Eintrag steht als `<div class="chart_title"><a ...><b>ARTIST</b><br>
TITEL</a></div>` im HTML; Artist/Titel werden daraus gelesen. Ein simpler
HTTP-Abruf reicht, **kein** Headless-Browser, **keine** API. Die Seite ist
ISO-8859-1-kodiert (Umlaute/Akzente werden korrekt dekodiert).

### Warum nur die Top-100 (`singles`) und nicht die CH-Artist-Charts

hitparade.ch hat auch eine „CH-Artists"-Liste (`singles-ch`). Diese wird aber
**per JavaScript/AJAX nachgeladen** – im HTML stehen dort 0 Songs. Mit der
reinen HTTP-Methode ist sie nicht abrufbar (es bräuchte einen Headless-Browser,
der den schlanken Stack unnötig schwer machen würde). Das ist verkraftbar: Die
Schweizer Acts, die gerade erfolgreich sind, laufen ohnehin in der Top-100 mit.
`HITPARADE_CHARTS=singles-ch` ist daher nicht empfohlen (würde dauerhaft als
Fehler gemeldet, weil 0 Songs).

## Monitoring

Liefert hitparade.ch **0 Songs** (Seite umgebaut/blockiert) oder ist nicht
erreichbar, meldet der Dienst `status=error` → die Dashboard-Kachel wird
rot/orange und es kommt eine Pushover-Meldung. So fällt ein kaputter Abruf
sofort auf, statt still eine leere Playlist zu schreiben.

## Konfiguration (zentrale `.env`)

| Variable | Default | Zweck |
| --- | --- | --- |
| `HITPARADE_ENABLED` | `true` | An/Aus-Schalter (`true`/`false`). `false` = Dienst pausieren: Container läuft weiter, kein Abruf, keine Playlist – Status **`idle`** (grau im Dashboard, **kein** Fehler). Toleranter Parser (`true`/`1`/`yes`/`on` bzw. `false`/`0`/`no`/`off`). |
| `HITPARADE_CHARTS` | `singles` | welche Charts (Komma-getrennt) |
| `HITPARADE_WEEKDAY` | `6` | Wochentag (0=Mo … 6=So) |
| `HITPARADE_HOUR` / `HITPARADE_MINUTE` | `20` / `0` | Uhrzeit des Laufs |
| `MATCH_THRESHOLD` | `87` | Match-Schwelle (wie charts-sync) |
| `ARTIST_MIN_SCORE` | `60` | Künstler-Mindest-Score |
| `IGNORE_COMPILATIONS` | `1` | Compilation-Tracks beim Match ignorieren |
| `PUSHOVER_SOUND` | `falling` | Benachrichtigungston für diesen Dienst. Gültige Pushover-Töne: `pushover, magic, cosmic, intermission, bike, bugle, cashregister, classical, falling, gamelan, incoming, mechanical, pianobar, siren, spacealarm, tugboat, alien, climb, persistent, echo, updown, none` (= still), `vibrate`. Leer = Account-Default. Über `PUSHOVER_SOUND_HITPARADE` in der `compose.yml` überschreibbar. |

**Wichtig:** `missing-hitparade.txt` muss in der `MISSING_FILES`-Variable der
`.env` stehen, damit charts-download die fehlenden Songs nachlädt (in
`.env.example` bereits vorausgefüllt).

> 🇬🇧 English version: [README.en.md](README.en.md)
