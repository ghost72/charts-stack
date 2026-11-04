# charts-hitparade

Holt wöchentlich (sonntags) die **offizielle Schweizer Hitparade** von
[hitparade.ch](https://hitparade.ch) – die Single-Top-100 –, matcht sie gegen
deine Library und schreibt eine Playlist `hitparade.m3u` + eine Fehlliste
`missing-hitparade.txt`, die [`charts-download`](../charts-download) nachlädt.

Fünfte Chart-Quelle neben [`charts-sync`](../charts-sync) (Deezer/Last.fm) –
dieselbe Mechanik, nur eine andere Quelle: die offizielle IFPI-Schweiz-Chart.

---

## Was es macht

- **Sonntag 18:00** (konfigurierbar über `HITPARADE_WEEKDAY`/`HITPARADE_HOUR`):
  holt die aktuelle Top-100 von hitparade.ch.
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

Liefert hitparade.ch **0 Songs** (Seite umgebaut/blockiert), meldet der Dienst
`status=error` → die Dashboard-Kachel wird rot/orange. Ist hitparade.ch
vorübergehend **nicht erreichbar** (5xx/Timeout), meldet der Lauf `idle` (kein
Scraper-Defekt, erholt sich von selbst). In **beiden** Fehlerfällen kommt eine
Pushover-Meldung, damit ein verpasster Lauf auffällt — der Watchdog sieht nur
den Container-Zustand, nicht einen fehlgeschlagenen Lauf in einem weiterhin
laufenden Container.

Ein fehlgeschlagener Lauf verbraucht den Tages-Slot **nicht**: Das wöchentliche
Run-Gate wird nur bei echtem Erfolg gestempelt. Ein Lauf, der an einem
vorübergehenden Abruffehler scheitert (auch ein `RUN_ON_START`-Lauf direkt nach
einem Container-Neustart), wird noch am selben Tag beim nächsten Scheduler-Check
erneut versucht, statt eine ganze Woche zu warten.

## Aufbau der Pushover-Meldung

Unter der Kopfzeile zeigt eine **Übersichtszeile** jeden Chart mit seinen Zahlen (`Chart ✅n ⬇n`), damit spätere Charts sichtbar bleiben, auch wenn der erste eine lange Fehlt-Liste hat. Die Fehler-Meldung wird ebenfalls auf die escapte Länge gekürzt. Der Text hält immer Pushovers 1024-Zeichen-Limit ein (so gemessen, wie Pushover ihn erhält, HTML-escaped). Ist die Liste zu lang, wird nur zwischen ganzen Einträgen gekürzt und mit genau einer Zeile „… und N weitere“ abgeschlossen; Kopfzeile und Übersichtszeile werden nie abgeschnitten.

## Konfiguration (zentrale `.env`)

| Variable | Default | Zweck |
| --- | --- | --- |
| `HITPARADE_ENABLED` | `true` | An/Aus-Schalter (`true`/`false`). `false` = Dienst pausieren: Container läuft weiter, kein Abruf, keine Playlist – Status **`idle`** (grau im Dashboard, **kein** Fehler). Toleranter Parser (`true`/`1`/`yes`/`on` bzw. `false`/`0`/`no`/`off`). |
| `HITPARADE_CHARTS` | `singles` | welche Charts (Komma-getrennt) |
| `HITPARADE_WEEKDAY` | `6` | Wochentag (0=Mo … 6=So) |
| `HITPARADE_HOUR` / `HITPARADE_MINUTE` | `20` / `0` | Uhrzeit des Laufs |
| `HITPARADE_RESYNC_CHECK_SECONDS` | `60` | Wie oft der Loop zwischen den Sonntagsläufen prüft, ob `charts-import` einen **Resync** ausgelöst hat (nur Datei-Check, günstig). Liegt ein Signal vor und wurde ein hitparade-relevanter Song importiert, wird `hitparade.m3u` sofort aus der gecachten Hitparade neu gerendert (ohne hitparade.ch erneut abzurufen; der Sonntagslauf bleibt unverändert). Signal-Datei `.hitparade-resync-requested` und Chart-Cache `.charts-hitparade-chart.json` liegen im `CHARTS_DIR`. |
| `MATCH_THRESHOLD` | `87` | Match-Schwelle (wie charts-sync) |
| `ARTIST_MIN_SCORE` | `60` | Künstler-Mindest-Score |
| `IGNORE_COMPILATIONS` | `1` | Compilation-Tracks beim Match ignorieren |
| `PUSHOVER_SOUND` | `falling` | Benachrichtigungston für diesen Dienst. Gültige Pushover-Töne: `pushover, magic, cosmic, intermission, bike, bugle, cashregister, classical, falling, gamelan, incoming, mechanical, pianobar, siren, spacealarm, tugboat, alien, climb, persistent, echo, updown, none` (= still), `vibrate`. Leer = Account-Default. Über `PUSHOVER_SOUND_HITPARADE` in der `compose.yml` überschreibbar. |

**Wichtig:** `missing-hitparade.txt` muss in der `MISSING_FILES`-Variable der
`.env` stehen, damit charts-download die fehlenden Songs nachlädt (in
`.env.example` bereits vorausgefüllt).

## Presence-Guard (kein Nachladen von Songs, die nur in Compilations überleben)

Bevor ein Song in `missing-hitparade.txt` geschrieben wird, prüft ein
**Presence-Guard** (`presence.py`, byte-identisch über sync / energy /
flashback / recommend / hitparade), ob der Song bereits irgendwo unter
`Music/<Artist>/` existiert – unabhängig vom Album-Ordner und **ohne** den
Compilation-Filter. Falls ja, landet der Song weiterhin in der Playlist (über
den bestehenden Pfad), wird aber nicht erneut geladen. Zusätzlich liefert
`_looks_like_compilation` jetzt für jeden Lidarr `(JJJJ)`-Album-Ordner einen
**Kurzschluss auf `False`**, sodass ein Song, dessen einzige verbliebene Kopie
in einem kuratierten `Greatest Hits (2008)/`-Album liegt, nicht mehr für einen
Sampler gehalten und als Single nachgeladen wird.

> Ein Repo-Root-Paritätstest (`tests/test_shared_helper_parity.py`) erzwingt nun,
> dass diese duplizierten Helfer (`presence.py`, `_title_key`, `_artist_key`,
> `_looks_like_compilation`, …) über alle Dienste hinweg verhaltensgleich
> bleiben – driftet eine Kopie künftig weg, schlägt der Test fehl.

> 🇬🇧 English version: [README.en.md](README.en.md)
