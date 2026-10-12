> 🇩🇪 Deutsche Version. English: [README.en.md](README.en.md)

# charts-upgrade

Räumt den **Altbestand** der Navidrome-Library qualitativ auf. Findet Songs mit
schlechter Qualität und lädt eine echte bessere Version nach – ersetzt aber
**nur**, wenn die neue Datei nachweislich besser ist.

Zwei Arten von „schlecht" werden erkannt:

1. **Niedrige Bitrate** – die Datei ist ehrlich unter `BAD_BITRATE` (z.B. ein
   alter 128-kbps-Rip).
2. **Upsampling / gefälschte Bitrate** – die Datei *behauptet* z.B. 320 kbps,
   hat real aber nur den Frequenzumfang einer 128er (hochtranskodiert). Das ist
   der häufigere, unsichtbare Fall: Der Tag lügt.

## Wie das Upsampling erkannt wird (Frequenz-Cutoff)

Ein MP3-Encoder wirft oberhalb einer bitratenabhängigen Grenze alles weg
(Lowpass). Faustregel:

| Bitrate | echter Cutoff |
|---------|---------------|
| 128 kbps | ~16 kHz |
| 192 kbps | ~19 kHz |
| 256 kbps | ~19–20 kHz |
| 320 kbps | ~20–20.5 kHz |

Behauptet eine Datei 320 kbps, hat aber real nur bis ~16 kHz Energie, ist sie
mit hoher Wahrscheinlichkeit upgesampelt. charts-upgrade misst den echten Cutoff
mit **ffmpeg** (`aspectralstats`/`rolloff`, Median über alle Frames) und
vergleicht ihn mit der Bitraten-Erwartung.

**Konservativ by design** – Ziel ist, möglichst KEINE legitimen Songs fälschlich
zu treffen:

- Kann der Cutoff nicht gemessen werden (ffmpeg-Problem) → Datei gilt als **ok**,
  wird nie angefasst.
- Nur wenn der Cutoff **deutlich** unter der Erwartung liegt (Toleranz
  `CUTOFF_TOLERANCE_KHZ`, Standard 2 kHz), gilt die Datei als upgesampelt.
- Alte/akustische Aufnahmen mit von Natur aus wenig Höhen sind ein bekannter
  Grenzfall – die Toleranz reduziert Fehlalarme. Im Zweifel lieber nicht
  anfassen.

## Sicherer Ersatz (Download → messen → nur bei echtem Upgrade ersetzen)

Soulseek ist voll von fake-320ern – die neue Datei könnte genauso upgesampelt
sein. Deshalb wird nie blind getauscht:

1. Ersatz bei slskd suchen (`REJECT_COMPILATIONS=1`, keine Sampler; nur MP3 ≥
   `MIN_BITRATE`).
2. Beste Version laden, auf Abschluss warten.
3. **Echten Cutoff der geladenen Datei messen** und mit der vorhandenen
   vergleichen (`quality_key`: echter Cutoff dominiert, Bitrate nur
   Tie-Breaker).
4. **Nur wenn wirklich besser** → Manifest-Eintrag mit `replace=true`;
   charts-import ersetzt die vorhandene Datei (alte in den Papierkorb
   `.charts-trash/`, reversibel).
5. **Nicht besser** → Anbieter für genau diesen Song vormerken
   (Blockliste, Verfall nach 30 Tagen) und nicht übernehmen. So wird nicht
   endlos derselbe Müll vom selben Anbieter geladen. Andere Songs des Anbieters
   bleiben erlaubt.

Wenn nach dem Ausfiltern nichts Besseres übrig ist, bleibt die **vorhandene
Datei** – es wird nie eine schlechtere eingespielt.

## Compilations sind tabu

Kuratierte Compilations (Bravo Hits, Kuschelrock, …) sind vollständige Alben und
werden **nie** track-weise durch Soulseek-Singles ersetzt. Der Scanner
überspringt Compilation-Ordner komplett – erkannt über die Pfad-Heuristik
(`various`, `best of`, `hits`, …) **plus** Künstler-Streuung im Album-Ordner
(viele verschiedene Artists = Sampler). Steuerbar über `SKIP_COMPILATIONS`.

## Stellung in der Kette

```
charts-sync  → charts-download → slskd → charts-import → Navidrome
                                              ▲
charts-upgrade (Altbestand) ──────────────────┘
   scannt Library, lädt Ersatz + schreibt replace-Manifest;
   charts-import führt den Tausch aus (bei echtem Cutoff-Upgrade)
```

charts-upgrade nutzt bewusst dieselben (kopierten) Bausteine wie die
Schwester-Dienste: `matching.py`, `slsk.py`, `manifest.py`, `notify.py`,
`quality.py` – keine Laufzeit-Abhängigkeit zwischen den Containern.

> **Der Upsampling-Check läuft auch im laufenden Betrieb:** charts-import prüft
> jede frisch heruntergeladene Datei mit demselben `quality.py` und lehnt
> fake-320er ab (Quarantäne + Neusuche). So kommt ab sofort auch über
> charts-download / charts-songrequest kein upgesampelter Müll mehr rein –
> charts-upgrade kümmert sich um den **Altbestand**.

## Container

| Container | Zweck |
|-----------|-------|
| `charts-upgrade` | Scheduler-Loop: scannt die Library, lädt/prüft/ersetzt. Kein Webserver. |

Braucht **ffmpeg** im Image (für die Cutoff-Messung) – ist im Dockerfile
enthalten.

## Setup

### 1. Konfiguration

```bash
cp .env.example .env
# mindestens SLSKD_API_KEY setzen. Pushover optional.
```

Für `PUSHOVER_DEVICE` sind mehrere Geräte kommagetrennt möglich (z.B.
`iPhone-Max,Tablet-Anna`) — es wird pro Gerät eine eigene Nachricht
gesendet. Alle Geräte gehören zum selben Pushover-Account. Leeres Feld =
Zustellung an alle Geräte des Accounts. Ungültige/deaktivierte Gerätenamen
werden vor dem Senden automatisch entfernt — sonst stellt Pushover einen
unbekannten Namen ersatzweise an **alle** Geräte zu (Doppel-Push). Die dafür
geladene Geräteliste wird `PUSHOVER_DEVICES_TTL` Sekunden gecacht (Default
`3600`).

`PUSHOVER_SOUND` steuert den Benachrichtigungston dieses Dienstes
(Code-Default `cosmic`). Gültige Pushover-Töne: `pushover, magic, cosmic,
intermission, bike, bugle, cashregister, classical, falling, gamelan, incoming,
mechanical, pianobar, siren, spacealarm, tugboat, alien, climb, persistent,
echo, updown, none` (= still), `vibrate`. Leer = Account-Default (kein
`sound`-Feld). Über `PUSHOVER_SOUND_UPGRADE` in der `compose.yml`
überschreibbar.

Wichtige Schalter (siehe `.env.example` für alle):

| Variable | Default | Bedeutung |
|----------|---------|-----------|
| `BAD_BITRATE` | `256` | Songs mit echter Bitrate darunter gelten als schlecht |
| `CHECK_UPSAMPLED` | `1` | Upsampling per Frequenz-Cutoff erkennen |
| `CUTOFF_TOLERANCE_KHZ` | `2.0` | wie weit der Cutoff unter der Erwartung liegen darf |
| `SKIP_COMPILATIONS` | `1` | Compilation-Ordner nie upgraden |
| `MAX_PER_RUN` | `10` | max. bestätigte Upgrades pro Lauf |
| `MAX_SCAN_PER_RUN` | `200` | max. Dateien, die pro Lauf mit ffmpeg analysiert werden (CPU-Schutz) |
| `INTERVAL_MINUTES` | `1440` | Lauf-Intervall (Default 24h) |
| `DRY_RUN` | `1` | **nur analysieren/melden, nichts laden** – so starten! |

### 2. Deploy (auf `your-nas`)

Code nach `/path/to/charts-upgrade/` kopieren (NVMe), dann:

```bash
sudo docker compose up -d --build
```

oder über den Container Manager (Projekt auf den Ordner mit `compose.yml`
zeigen lassen).

### 3. Erst im Testlauf beobachten (DRY_RUN)

Mit `DRY_RUN=1` (Default) lädt der Dienst **nichts**. Er scannt, meldet per
Log/Pushover, welche Songs er als schlecht/upgesampelt einstuft, und was er
laden würde:

```bash
sudo docker compose logs -f charts-upgrade
```

**Prüfe die gemeldeten Fälle an echten Beispielen aus deiner Library** (z.B. mit
Spek das Spektrogramm ansehen). Passt die Erkennung, `CUTOFF_TOLERANCE_KHZ` ggf.
feinjustieren (größer = nachsichtiger). Erst dann `DRY_RUN=0` setzen.

### 4. Scharf schalten

```bash
# in .env: DRY_RUN=0
sudo docker compose up -d
```

## CPU-Hinweis (DS1621+)

Die Cutoff-Messung dekodiert jede geprüfte Datei einmal mit ffmpeg. Auf der
DS1621+ (4 Kerne, kein HW-Transcoding) ist der **Erst-Durchlauf** über die ganze
Library spürbar. Deshalb:

- Der Analyse-Cache im State (`.charts-upgrade-state.json`, Schlüssel
  `seen`) merkt sich pro Datei `mtime:size` – jede Datei wird **nur einmal**
  dekodiert. Spätere Läufe prüfen nur neue/geänderte Dateien.
- `MAX_SCAN_PER_RUN` deckelt die Analyse pro Lauf. Der Dienst arbeitet sich über
  mehrere Läufe schonend durch den Bestand, statt alles auf einmal zu rechnen.
- Läuft standardmäßig nur alle 24h. Für einen schnelleren Erst-Scan das
  Intervall/`MAX_SCAN_PER_RUN` temporär (nachts) hochsetzen.

## Daten & Pfade

| Host-Pfad | Zweck |
|-----------|-------|
| `/volume2/music/Music` | Navidrome-Library (read-only gescannt) |
| `/volume2/music/Charts` | geteilter Ordner: State, Manifest, slskd-Downloads |
| `/volume2/music/Music/.charts-trash/` | Papierkorb ersetzter Versionen (charts-import, reversibel) |

## Betrieb

```bash
# Logs
sudo docker compose logs -f charts-upgrade

# Neu bauen/starten nach Code-Änderung
sudo docker compose up -d --build

# Stoppen
sudo docker compose down
```
