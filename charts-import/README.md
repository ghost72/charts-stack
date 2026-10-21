> 🇩🇪 Deutsche Version. English: [README.en.md](README.en.md)

# charts-import

Sortiert die von **slskd** heruntergeladenen Chart-Songs automatisch in die
**Navidrome-Library** ein und räumt den Download-Ordner nach einer gewissen
Zeit wieder auf.

Das Schwester-Projekt zu [`charts-sync`](../charts-sync): Während `charts-sync`
die fehlenden Chart-Songs ermittelt (`missing.txt`) und die Playlist pflegt,
kümmert sich `charts-import` um die Dateien, die anschliessend per slskd
geladen wurden. `charts-sync` fasst bewusst **keine** Audiodateien an, deshalb
ist das Einsortieren + Aufräumen ein eigener, unabhängiger Dienst.

---

## Charts-Automatisierung – Gesamtübersicht

`charts-import` ist Teil einer Kette aus drei Diensten, die zusammen den
Chart-Workflow vollständig automatisieren. Jeder läuft als eigener Container
mit eigenem Intervall und eigener `.env`. Die Kopplung erfolgt ausschließlich
über gemeinsame Ordner (`/volume2/music/Charts`, `/volume2/music/Music`).
Ergänzt wird die Familie durch [`charts-songrequest`](../charts-songrequest),
eine optionale Web-UI zum **manuellen** Anfordern einzelner Songs. Deren
Downloads holt `charts-import` über das Manifest automatisch in die Library
(Label `manuell`).

Schwester-Projekte: [`charts-sync`](../charts-sync) · [`charts-download`](../charts-download) · [`charts-songrequest`](../charts-songrequest)

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
- [Installation auf dem Synology NAS](#installation-auf-dem-synology-nas)
- [Konfiguration (`.env`)](#konfiguration-env)
- [Verzeichnis-Layout](#verzeichnis-layout)
- [Kopieren vs. Verschieben & Aufräumen](#kopieren-vs-verschieben--aufräumen)
- [Betrieb & Wartung](#betrieb--wartung)
- [Troubleshooting](#troubleshooting)
- [Ausbaupfad: Automatisierte Suche mit Soularr](#ausbaupfad-automatisierte-suche-mit-soularr)
- [FAQ](#faq)

---

## Überblick

| Eigenschaft            | Wert |
|------------------------|------|
| Quelle                 | slskd-Download-Ordner (`/volume2/music/Charts`) |
| Ziel                   | Navidrome-Library (`/volume2/music/Music/<Artist>/<Album>/<Track>`) |
| Basis-Image            | `python:3.12-slim` |
| Abhängigkeiten         | `httpx`, `mutagen` |
| Betriebsmodus          | Endlosschleife mit konfigurierbarem Intervall (Minuten) |
| Import-Modus           | Kopieren (Standard, Original bleibt zum Seeden) oder Verschieben |
| Aufräumen              | Anzahl-basiert (`KEEP_LATEST`: die N neuesten behalten, Standard 500; `all` = nie löschen) oder alter-basiert (`RETENTION_DAYS`) |
| Läuft als              | `youruser:users` (PUID 1027 / PGID 100), wie Navidrome |
| Benachrichtigungen     | Pushover (optional, wenn Songs importiert wurden) |

---

## Gesamt-Workflow

`charts-import` ist ein Baustein einer Kette aus mehreren Diensten:

```
Deezer-Charts
     │
     ▼
charts-sync ───► missing.txt        (welche Chart-Songs fehlen in der Library)
     │           /volume2/music/Charts/missing.txt
     ▼
slskd ─────────► lädt fehlende Songs nach /volume2/music/Charts/
     │
     ▼
charts-import ─► kopiert nach /volume2/music/Music/<Artist>/<Album>/<Track>
     │           und hält den Charts-Ordner auf KEEP_LATEST Dateien
     ▼
Navidrome ─────► sieht die neuen Songs beim nächsten Scan
     │
     ▼
charts-sync ───► matcht sie beim nächsten Lauf in die Playlist
```

So schliesst sich der Kreis: Ein Song, der heute in der `missing.txt` steht,
wird via slskd geladen, von `charts-import` einsortiert und landet beim
nächsten `charts-sync`-Lauf automatisch in der Charts-Playlist.

---

## Funktionsweise

Ein Lauf besteht aus zwei Schritten:

1. **Import** (`importer.py`)
   - `DOWNLOADS_DIR` rekursiv nach Audiodateien scannen.
   - Nur **stabile** Dateien nehmen (seit `MIN_AGE_SECONDS` nicht mehr
     verändert), damit keine halbfertigen Downloads einsortiert werden.
   - **Manifest-Pflicht (`REQUIRE_MANIFEST=1`, Default):** Nur Dateien mit
     einem Eintrag im charts-download-Manifest werden importiert – also nur
     Songs, die charts-download **gezielt** geladen hat. Der Downloads-Ordner
     enthält oft ganze Sampler-/Album-Ordner, die beim Song-Download von
     Soulseek mitkamen; ohne diese Sperre würde daraus wahllos importiert (z. B.
     Compilation-Versionen), und zwar bei **jedem** Lauf erneut, solange die
     Quelldateien im geteilten Ordner liegen. Mit `REQUIRE_MANIFEST=0` wird
     alles importiert, was zuordenbar ist (z. B. für manuell abgelegte Dateien).
   - Aus den Tags den Zielpfad `Music/<Artist>/<Album>/<Track>.<ext>` bilden.
   - Datei **kopieren** (`COPY_MODE=1`) oder **verschieben** (`COPY_MODE=0`).
   - **Duplikat-Schutz (Ordner-lokal):** Liegt im Ziel-Album-Ordner bereits
     eine Datei mit demselben Track-Namen (unabhängig von Dateiname,
     `(n)`-Suffix und Endung), wird übersprungen. So entstehen keine
     `(2)/(3)`-Duplikate, auch wenn derselbe Song von mehreren Anbietern mit
     unterschiedlichen Dateinamen geladen wurde. Der Lauf ist dadurch
     **idempotent**.
   - **Duplikat-Schutz (albumübergreifend):** Zusätzlich wird geprüft, ob
     derselbe Song bereits in einem **anderen** Album-Ordner desselben
     Künstlers liegt. Das deckt den häufigen Fall ab, dass eine andere Quelle
     (z. B. Lidarr) den Song schon als Album-Track führt (`Into You (2016)/
     Ariana Grande - Into You - 01 - Into You.mp3`), während charts-download ihn
     als Single laden würde (`Into You/Into You.mp3`). Ohne diese Prüfung
     entstünden zwei Album-Ordner (Dublette) für denselben Song. Verglichen wird
     über den normalisierten **Songtitel**, damit die abweichende Datei-/Album-
     Benennung keine Rolle spielt.
   - **Qualitäts-Upgrade (`replace`):** Trägt der Manifest-Eintrag
     `"replace": true` (von `charts-songrequest` bei „bessere Version laden"
     gesetzt) **und** ist die neue Datei höher-bitratig als die vorhandene, wird
     die vorhandene **ersetzt** statt übersprungen: die alte Version wandert in
     einen Papierkorb (`MUSIC_DIR/.charts-trash/<YYYYMMDD>/`, reversibel), die
     neue landet an derselben Stelle (keine Dublette). Ist die neue **nicht**
     besser, bleibt alles wie es ist (kein sinnloser Tausch). Der Papierkorb
     wird automatisch geleert (Einträge älter als `TRASH_RETENTION_DAYS`, Standard
     30 Tage) – kein manuelles Aufräumen nötig.

   - **Türsteher:** bevor eine Datei einsortiert wird, prüft charts-import, ob
     sie brauchbar ist. Abgelehnt wird bei einem Ausschluss-Keyword im Namen
     (Lyrics/Remix/Live/Acapella …) oder wenn kein brauchbarer Artist
     ermittelbar ist (`Unknown Artist`). Abgelehnte Dateien werden nach
     `_rejected` verschoben (Quarantäne, nicht gelöscht) und der Song im
     charts-download-State freigegeben, damit er neu (möglichst besser) geladen
     wird. So landet kein Müll in der Library, egal woher die Datei kommt.
     Abschaltbar über `REJECT_ENABLED=0`.
   - **Cover einbetten:** für einsortierte Songs (Manifest-Treffer) wird das
     passende Cover über die konfigurierten Quellen (iTunes→Deezer) gesucht und
     direkt in die MP3 eingebettet (als ID3-Tag, **keine** separate `cover.jpg`). Bei
     `OVERWRITE_COVER=1` wird ein bereits vorhandenes Cover ersetzt (wichtig bei
     Songs aus Compilations, die das Sampler-Cover mitbringen). Das Cover wird
     außerdem **quadratisch** gemacht (mittig zugeschnitten), damit es beim
     Abspielen nicht verzerrt aussieht. Abschaltbar über `EMBED_COVERS=0`.

2. **Cleanup** (`cleanup.py`)
   - Standard: die neuesten `KEEP_LATEST` Downloads behalten, den Rest löschen.
   - Alternativ (`KEEP_LATEST=0`): Downloads älter als `RETENTION_DAYS` löschen.
   - Danach leere Unterordner entfernen.

Ist die **Lidarr-Integration** konfiguriert (`LIDARR_URL` + `LIDARR_API_KEY`),
werden die neu importierten Künstler anschließend in Lidarr auf *unmonitored*
gesetzt (bzw. direkt unmonitored neu angelegt). So kennt und taggt Lidarr die
Chart-Singles, lädt aber **nicht** deren kompletten Katalog herunter. Damit
kann Lidarrs normales Monitoring für echte Alben/Compilations aktiviert bleiben.
Am besten in Lidarr zusätzlich „Watch Root Folders for file changes" abschalten,
sonst grabbt Lidarr evtl. schon, bevor charts-import reagieren kann. Fehler der
Lidarr-Anbindung werden nur geloggt und beeinträchtigen den Import nicht.

Wurden neue Songs importiert, folgt optional eine **Pushover-Meldung** (mit der
Liste der einsortierten Titel sowie einer Aufschlüsselung nach Playlist, z. B.
„3 aus charts, 2 aus mundart" – abgeleitet aus dem Manifest) und ein
**Resync-Signal** an `charts-sync`
(`.resync-requested` im Charts-Ordner). Dieses Signal veranlasst `charts-sync`,
die Playlist innerhalb ~1 Minute zu aktualisieren, statt aufs reguläre
12h-Intervall zu warten.

Zusätzlich zur Sammelmeldung sendet charts-import für jeden **manuell über
charts-songrequest angeforderten** Song eine eigene Verfügbarkeits-Meldung
(„🎵 Dein Song ist da!") an **das Gerät des anfordernden Benutzers**. Das
Zielgerät steht als `notify_device` im Manifest-Eintrag (von charts-songrequest
aus `USER_PUSHOVER_DEVICES` gesetzt). Fehlt das Feld (normale Chart-Downloads,
oder User ohne hinterlegtes Gerät), gibt es keine solche Einzelmeldung – sie
wird nie an alle Geräte gesendet.

Danach wartet der Dienst `INTERVAL_MINUTES` Minuten und wiederholt den Lauf.

---

## Projektstruktur

```
charts-import/
├── app/
│   ├── __init__.py       # Paket-Marker
│   ├── config.py         # Konfiguration aus Umgebungsvariablen
│   ├── tags.py           # Manifest + Tag-Lesung/-Schreiben + Zielpfad-Bildung
│   ├── importer.py       # Scan + Kopieren/Verschieben + Türsteher
│   ├── cover.py          # Cover suchen (iTunes→Deezer), einbetten, quadratisch
│   ├── replaygain.py     # Lautstärke messen (ffmpeg) + ReplayGain-Tag schreiben
│   ├── cleanup.py        # Aufräumen (KEEP_LATEST oder RETENTION_DAYS)
│   ├── lidarr.py         # Chart-Künstler in Lidarr auf unmonitored halten
│   ├── backfill_covers.py    # Einmal-Skript: Cover nachrüsten / --fix-broken
│   ├── backfill_replaygain.py# Einmal-Skript: ReplayGain nachrüsten
│   ├── notify.py         # Optionale Pushover-Benachrichtigung
│   └── main.py           # Scheduler-Loop, verbindet alle Module
├── scripts/
│   ├── cleanup_flashback_legacy.py  # Einmal-Skript: alte flashback-Altlasten entfernen
│   └── strip_provenance_tags.py     # Einmal-Skript: Herkunfts-/Encoder-Tags aus Chart-Songs entfernen
├── Dockerfile            # python:3.12-slim + ffmpeg, startet app.main
├── compose.yml           # Service-Definition für das NAS
├── requirements.txt      # httpx, mutagen, Pillow
├── .env.example          # Vorlage für die Konfiguration
├── .gitignore
└── README.md             # diese Datei
```

---

## Architektur & Module

### `config.py`

Liest alle Einstellungen aus Umgebungsvariablen (Pfade, Import-Modus,
Aufbewahrungsdauer, Intervall, Pushover). Enthält die Liste der als Musik
erkannten Dateiendungen (`AUDIO_EXTENSIONS`), identisch zu `charts-sync`.

### `tags.py`

Ermittelt Künstler/Album/Titel in dieser Reihenfolge:

1. **Manifest von charts-download** (`MANIFEST_FILE`) – bevorzugt. Enthält die
   echten Chart-Metadaten (Künstler/Titel). Bei einem Treffer werden diese
   Werte verwendet **und** per `mutagen` in die Datei-Tags geschrieben
   (`artist`, `albumartist`, `title`, `album`), damit Navidrome und `charts-sync`
   korrekte Werte sehen. Das Album wird dabei auf den Titel gesetzt (Chart-Songs
   sind Singles), sodass nichts unter „Unknown Album" landet.
   **Jahr-Tag:** `charts-import` liest zusätzlich die Jahr-Map
   `.charts-flashback-years.json` (im Charts-Ordner, von `charts-flashback`
   geschrieben; Key = lowercase `artist|title`, `_flashback_year`). Findet sich
   dort ein Jahr, setzt `charts-import` die Tags `date`/`originaldate` – für die
   Dekaden-Smart-Playlists in Navidrome. Fehlt der Eintrag (alle anderen
   Dienste), bleiben die Datum-Tags unangetastet – kein Überschreiben
   vorhandener Jahre. Das Jahr steht so **nur** im Tag, **nie** im
   Datei-/Ordnernamen.
2. **Datei-Tags** (`mutagen`, `albumartist` bevorzugt).
3. **Fallback** auf den Dateinamen bzw. `Unknown Artist` / `Unknown Album`.

Für den Zielpfad werden die Bestandteile von dateisystem-/SMB-kritischen Zeichen
bereinigt (z. B. `/` → `_`); die **Tags** behalten aber den Originalwert (z. B.
`HUNTR/X`), damit `charts-sync` sauber matcht. Ergebnis:
`Music/<Artist>/<Album>/<Track>.<ext>`.

**Kanonische Künstler-Schreibweise:** Existiert der Künstler bereits als Ordner
in der Library (egal in welcher Groß-/Kleinschreibung), nimmt `charts-import`
dessen **echten** Ordnernamen für Pfad **und** Tag (`canonical_artist`). Das
verhindert Dubletten wie `rod stewart` neben `Rod Stewart` auf case-sensitiven
Dateisystemen (ext4) und lässt die etablierte Schreibweise gewinnen, egal was
Manifest/Tags sagen. Greift für **alle** Dienste, die charts-import nutzt
(songrequest, sync, download, flashback).

**Kanonische Schreibweise via Deezer (letzte Instanz):** Ist der Manifest-Name
eines Chart-Songs noch **GROSS**/verdächtig (z. B. ein durchgerutschter
FLASHBACK-Rohwert wie `OMD / ENOLA GAY`), löst `charts-import` die offizielle
Mixed-Case-Schreibweise **vor** dem Bilden von Pfad/Tags über die offene
Deezer-Such-API auf (`app/deezer.py`, `resolve_canonical`). Das ist die
**zentrale letzte Instanz**, damit nie ein GROSS-Name in die Library gelangt —
auch wenn `charts-flashback` ihn beim Schreiben der missing-Liste nicht sicher
auflösen konnte. Die Treffer-Validierung ist konservativ (asymmetrische
Ähnlichkeits-Schwelle `DEEZER_MATCH_MIN`/`_STRONG`/`_RELAX` + Klammer-Zusatz-
Strip): lieber GROSS lassen als falsch raten. Deezer wird nur bei GROSS-Verdacht
gefragt (saubere Namen bleiben unberührt, kein API-Overhead); das Ergebnis wird
gecacht (`.charts-flashback-deezer-cache.json`, gemeinsam mit flashback).

> **Unlösbare Fälle:** Findet Deezer keinen sicheren Treffer (Alias-Künstler wie
> OMD = „Orchestral Manoeuvres in the Dark", oder ein von FLASHBACK schlicht
> falsch benannter Titel), wird der Song **trotzdem importiert** (keine Lücke in
> der Library) und am Lauf-Ende per **Pushover-Sammelmeldung** zur manuellen
> Prüfung/Umbenennung gemeldet (`✏️ charts-import: N Name(n) prüfen`) — nie wird
> geraten.

**Herkunfts-/Encoder-Tags entfernen:** Beim Einsortieren entfernt `charts-import`
Herkunfts-Spuren aus den Tags (`strip_provenance_tags`): den Kommentar-Frame
(`COMM` – trägt bei Soulseek-Downloads fast immer Uploader-/Release-Group-
Signaturen wie „eXenz", „PMEDIA", `www.t.me/…` oder den fre:ac-Werbetext),
`TENC`/`TSSE` (Encoder/Rip-Tool), die URL-Frames `WXXX`/`WCOM`/`WOAF`/`WPUB`,
`TOWN` (File-Owner) sowie benutzerdefinierte `TXXX`-Freitext-Frames (z. B.
`TXXX:LOG`). So landet keine Quellen-Spur in der Library.
**Geschützt (nie angefasst):** `TXXX:MusicBrainz*` und `TXXX:*replaygain*`
(Metadaten-IDs + Lautstärke-Normalisierung) sowie alle Standard-Tags
(Titel/Album/Artist/Datum/Track/Cover). Verlustfrei (nur Metadaten, kein
Re-Encoding).

### `importer.py`

Der eigentliche Import. Scannt den Download-Ordner, prüft die Stabilität jeder
Datei, bildet den Zielpfad und kopiert bzw. verschiebt. Erkennt bereits
vorhandene Dateien über einen Grössenvergleich (günstig und für Dedup
ausreichend) und vergibt bei echten Namenskollisionen ein Zähler-Suffix
(`Track (2).mp3`).

### `cleanup.py`

Räumt den Download-Ordner auf und entfernt danach leere Unterordner. Zwei
Strategien, `KEEP_LATEST` hat Vorrang: entweder die N neuesten Downloads
behalten (`KEEP_LATEST`) oder Downloads älter als `RETENTION_DAYS` löschen. Sind
beide `0`, passiert nichts (Aufräumen deaktiviert).

### `replaygain.py`

Misst die Lautheit einer MP3 mit ffmpeg (EBU R128) und schreibt den Track-Gain
als ReplayGain-2.0-Tag (`REPLAYGAIN_TRACK_GAIN`/`_PEAK`). Der Audio-Stream
bleibt unverändert (verlustfrei); ein ReplayGain-fähiger Player regelt die
Wiedergabe-Lautstärke. Zielpegel über `REPLAYGAIN_TARGET_LUFS` (Default −14 LUFS,
Streaming-Niveau). Braucht ffmpeg im Image. Fällt still
aus, wenn ffmpeg fehlt. **Hinweis:** Damit es hörbar wird, muss im Abspieler
(Navidrome-Web bzw. App) der ReplayGain-Modus aktiviert sein.

### `lidarr.py`

Optionale Lidarr-Integration (nur aktiv mit `LIDARR_URL` + `LIDARR_API_KEY`).
Setzt nach jedem Import die neu importierten Chart-Künstler in Lidarr auf
*unmonitored* (bzw. legt sie direkt unmonitored an), damit Lidarr sie kennt und
taggt, aber nicht ihren kompletten Katalog herunterlädt. Fehler werden nur
geloggt und beeinträchtigen den Import nicht.

### `backfill_covers.py`

Einmal-Skript (kein Teil des Scheduler-Loops). Rüstet Cover für die bestehende
Library nach. Ohne Flag nur fehlende Cover; mit `--fix-broken` werden zusätzlich
rechteckige Cover (CD-Scans) der **Chart-Songs** (Playlist-Einträge in
`PLAYLIST_DIR`) durch das quadratische Cover ersetzt. Mit
`--force-refresh` wird bei **allen** Chart-Songs das Cover neu geholt (Quelle laut
`COVER_SOURCES`, i. d. R. iTunes-Single-Artwork), auch bei bereits quadratischen
(nötig für Kassetten-/CD-Hüllen-Scans sowie um alte, schlechtere Cover wie eine
Demo-EP durch das dedizierte Single-Cover zu ersetzen). `--dry-run`
zeigt nur an, was passieren würde. Siehe „Betrieb & Wartung".

### `backfill_replaygain.py`

Einmal-Skript. Rüstet ReplayGain-Tags für die bestehenden **Chart-Songs** nach
(nur Playlist-Einträge, `PLAYLIST_DIR`). `--dry-run` zählt nur, `--overwrite`
berechnet auch vorhandene neu. Siehe „Betrieb & Wartung".

### `backfill.py` (automatisch, im Scheduler-Loop)

Anders als die beiden manuellen `backfill_*`-Einmal-Skripte läuft dieses Modul
**automatisch** mit: Der Import ist „fail-open" — ist beim Einsortieren eine
externe Quelle kurz nicht erreichbar (iTunes/Deezer für Cover, ffmpeg für
ReplayGain), wird der Song **trotzdem** importiert, dann aber ohne Cover/RG. Da
charts-import bereits vorhandene Songs nie wieder anfasst, bliebe diese Lücke
sonst dauerhaft. `backfill.py` holt sie periodisch nach (`BACKFILL_INTERVAL_HOURS`,
Default 24 h), nur über die **Chart-Songs aus dem Manifest** — die übrige
(Lidarr-)Library bleibt unangetastet. Es bettet fehlende Cover ein und berechnet
fehlendes ReplayGain; vorhandene Cover/Tags werden nie überschrieben.
Zusätzlich gleicht der Backfill **GROSS geschriebene flashback-Namen** gegen die
offizielle Deezer-Schreibweise ab (`BACKFILL_RECANON=1`, Default an): Nur bei
einem **sicheren** Deezer-Treffer wird umbenannt (Library-Ordner/Datei + Tags +
Manifest), unsichere Fälle (Alias-Künstler, von Deezer offiziell groß geführte
Namen wie ABBA/R.E.M.) bleiben unangetastet — nie geraten. Dafür wird vorher der
Deezer-Cache der Kandidaten invalidiert, damit alte „eingefrorene" Misses neu
aufgelöst werden. Es nutzt dieselbe Logik wie das manuelle
`scripts/recanonicalize_flashback.py` (ausgelagert nach `app/recanon.py`); das
manuelle Skript bleibt für gezielte Einzelläufe bestehen.

### `notify.py`

Optionaler Pushover-Versand, nur aktiv wenn `PUSHOVER_TOKEN` und
`PUSHOVER_USER` gesetzt sind (sonst stiller No-op). Identisch zu `charts-sync`
und `jellyfin-webhook`.

### `main.py`

Einstiegspunkt (`python -m app.main`). `run_once()` führt Import + Cleanup aus,
loggt eine Zusammenfassung und schickt bei neuen Songs eine Pushover-Meldung.
Der Loop wiederholt das im konfigurierten Intervall und fängt Fehler ab, damit
der Dienst dauerhaft läuft.

---

## Installation auf dem Synology NAS

charts-import wird **nicht** einzeln installiert. Es ist Teil des **charts-stack**,
der als EIN Projekt läuft: eine `compose.yml` + eine zentrale `.env` im
`charts-stack`-Ordner (z. B. `/path/to/charts-stack/`). Die
vollständige Installation steht in der **[Haupt-README](../README.md)**.

Nur diesen Dienst gezielt bauen/starten – aus dem `charts-stack`-Ordner:

```bash
docker compose up -d --build charts-import
```

Logs dieses Dienstes:

```bash
docker compose logs -f charts-import
```

Beim ersten Lauf siehst du pro Song `Kopiert: … -> …` bzw. `Verschoben: …`,
gefolgt von einer Zusammenfassung wie
`Lauf fertig: X Songs importiert, Y alte Downloads entfernt.`

---

## Konfiguration (`.env`)

| Variable          | Default          | Bedeutung |
|-------------------|------------------|-----------|
| `COPY_MODE`       | `1`              | `1` = kopieren (Original bleibt zum Seeden liegen), `0` = verschieben. |
| `REQUIRE_MANIFEST`| `1`              | `1` = nur gezielt von charts-download geladene Songs (Manifest-Eintrag) importieren – hält Sampler-/Album-Tracks aus dem geteilten Downloads-Ordner draußen. `0` = alles importieren, was zuordenbar ist (z. B. für manuell abgelegte Dateien). |
| `KEEP_LATEST`     | `500`            | Die N neuesten Downloads behalten, Rest löschen. Gilt für **alle** Downloads gemeinsam (alle Playlists im selben Ordner), nicht pro Playlist. Hält den Share stabil bei ~N Dateien (Leecher-Schutz). `all` = nie löschen, `0` = deaktiviert. Hat Vorrang vor `RETENTION_DAYS`. |
| `RETENTION_DAYS`  | `0`              | Fallback (nur wenn `KEEP_LATEST=0`): Downloads älter als so viele Tage löschen. `0` = nie löschen. |
| `TRASH_RETENTION_DAYS` | `30`        | Upgrade-Papierkorb (`MUSIC_DIR/.charts-trash/`) automatisch leeren: ersetzte Versionen älter als so viele Tage werden gelöscht. `0` = nie (manuell leeren). |
| `MIN_AGE_SECONDS` | `120`            | Datei erst einsortieren, wenn sie so lange unverändert ist. |
| `INTERVAL_MINUTES`| `30`             | Minuten zwischen zwei Läufen. |
| `HITPARADE_SOURCE_LABEL` | `hitparade` | Quell-Label, dessen **Mitgliedschaft** in der `+`-verknüpften Source eines Imports (z. B. `charts+hitparade`) ihn als hitparade-relevant markiert. Nur dann legt charts-import zusätzlich das Signal `.hitparade-resync-requested` an, damit `charts-hitparade` `hitparade.m3u` sofort neu rendert (statt erst am nächsten Sonntag). Verglichen wird pro `+`-Teil, nicht als Teilstring. |
| `RUN_ON_START`    | `1`              | Beim Start sofort einen Lauf machen. |
| `REJECT_ENABLED`  | `1`              | Türsteher aktiv: verdächtige/unbrauchbare Dateien nach `_rejected` verschieben statt einsortieren. `0` = aus. |
| `REJECT_UNKNOWN_ARTIST` | `1`        | Ablehnen, wenn kein brauchbarer Artist ermittelbar ist. Album wird nicht geprüft (Singles). |
| `REJECT_KEYWORDS` | *(Standardliste)*| Ablehnen bei Keyword im Dateinamen (Lyrics/Remix/Live …). Leer = Standardliste, `off`/`none` = aus, eigene Liste ersetzt. |
| `MIN_DURATION_SECONDS` | `90`        | Kürzere Dateien gelten als abgebrochene Downloads → abgelehnt und neu gesucht. `0` = aus. |
| `REPLAYGAIN`      | `1`              | Lautstärke beim Import normalisieren (ReplayGain-Tag, verlustfrei). Braucht ffmpeg. Der Player muss ReplayGain aktiviert haben. `0` = aus. |
| `REPLAYGAIN_TARGET_LUFS` | `-14`     | Ziel-Lautheit. `-18` = ReplayGain-Referenz (leiser), `-16` = Apple Music, `-14` = Spotify/YouTube (lauter). Größer = lauter. |
| `EMBED_COVERS`    | `1`              | Cover über die konfigurierten Quellen (`COVER_SOURCES`, Default iTunes→Deezer) suchen und in die MP3 einbetten (ID3-Tag, keine separate Datei). `0` = aus. |
| `COVER_SOURCES`   | `itunes,deezer`  | Cover-Quellen und Reihenfolge. iTunes hat die besten Single-Artworks (immer quadratisch), Deezer ist Fallback. `deezer` = nur Deezer. |
| `ITUNES_COVER_SIZE` | `1000`         | Kantenlänge der iTunes-Cover in px. |
| `ITUNES_MAX_RETRIES` | `3`           | Bei iTunes-Drosselung (HTTP 429/403) so oft erneut versuchen, bevor auf Deezer ausgewichen wird. |
| `ITUNES_RETRY_BASE_MS` | `800`       | Basis-Wartezeit vor dem ersten iTunes-Retry (danach verdoppelt). |
| `OVERWRITE_COVER` | `1`              | Bei Chart-Songs ein bereits eingebettetes Cover durch das neu gesuchte **Single**-Cover ersetzen (wichtig bei Songs aus Compilations/Samplern). Ersetzt nur, wenn eine Quelle ein Cover liefert. `0` = vorhandenes Cover nie überschreiben. |
| `BACKFILL_INTERVAL_HOURS` | `24`     | **Automatischer** Metadaten-Backfill: holt im Scheduler-Loop periodisch fehlende Cover/ReplayGain für die Chart-Songs (Manifest) nach — für Songs, die beim Import ohne Cover/RG blieben, weil eine Quelle kurz nicht erreichbar war. `0` = aus. |
| `BACKFILL_SLEEP_MS` | `400`          | Pause zwischen den Cover-API-Calls im Backfill (schont iTunes/Deezer-Rate-Limits). |
| `BACKFILL_LIMIT`  | `0`              | Max. nachgebesserte Dateien pro Backfill-Lauf (`0` = alle). Deckel gegen sehr lange Läufe. |
| `BACKFILL_RECANON`| `1`              | Im Backfill GROSS-flashback-Namen gegen die offizielle Deezer-Schreibweise abgleichen und bei sicherem Treffer korrigieren (umbenennen + Tags + Manifest). `0` = aus. |
| `PUID` / `PGID`   | `1027` / `100`   | User/Gruppe (youruser:users, wie Navidrome). |
| `TZ`              | `Europe/Zurich`  | Zeitzone. |
| `PUSHOVER_TOKEN`  | *(leer)*         | Optional: Pushover-App-Token. |
| `PUSHOVER_USER`   | *(leer)*         | Optional: Pushover-User-Key. |
| `PUSHOVER_DEVICE` | *(leer)*         | Optional: Ziel-Gerät. Mehrere Geräte kommagetrennt möglich (z.B. `iPhone-Max,Tablet-Anna`) — es wird pro Gerät eine eigene Nachricht gesendet. Alle Geräte gehören zum selben Pushover-Account. Leeres Feld = Zustellung an alle Geräte des Accounts. Ungültige/deaktivierte Gerätenamen werden vor dem Senden automatisch entfernt — sonst stellt Pushover einen unbekannten Namen ersatzweise an **alle** Geräte zu (Doppel-Push). |
| `PUSHOVER_SOUND` | `incoming`         | Benachrichtigungston für die normale Import-Sammelmeldung (sowie rejected/flagged). Gültige Pushover-Töne: `pushover, magic, cosmic, intermission, bike, bugle, cashregister, classical, falling, gamelan, incoming, mechanical, pianobar, siren, spacealarm, tugboat, alien, climb, persistent, echo, updown, none` (= still), `vibrate`. Leer = Account-Default. Über `PUSHOVER_SOUND_IMPORT` in der `compose.yml` überschreibbar. |
| `PUSHOVER_SOUND_MANUAL` | `magic`     | Eigener Ton **nur** für die manuell via charts-songrequest angeforderte Verfügbarkeits-Meldung („🎵 Dein Song ist da!"). So hört der User am Ton, dass **sein** Song da ist, nicht nur ein Routine-Import. Gleiche gültige Töne wie `PUSHOVER_SOUND`; leer = Account-Default. Über `PUSHOVER_SOUND_MANUAL` in der `compose.yml` überschreibbar. |
| `PUSHOVER_DEVICES_TTL` | `3600`      | Optional: Cache-Dauer (Sekunden) für die zum Filtern geladene Geräteliste des Accounts. |
| `LIDARR_URL`      | *(leer)*         | Optional: Lidarr-Basis-URL (z. B. `http://<NAS-IP>:8686`). Leer = Integration aus. |
| `LIDARR_API_KEY`  | *(leer)*         | Lidarr-API-Key (Settings → General → Security). |
| `LIDARR_UNMONITOR`| `1`              | Importierte Chart-Künstler in Lidarr auf *unmonitored* setzen (kein Katalog-Download). |
| `LIDARR_ADD_MISSING` | `1`           | Noch nicht vorhandene Künstler in Lidarr neu anlegen (direkt unmonitored). `0` = nur vorhandene unmonitoren. |
| `LIDARR_QUALITY_PROFILE_ID` | `4`    | Quality-Profil-ID für die Neuanlage. |
| `LIDARR_METADATA_PROFILE_ID`| `1`    | Metadata-Profil-ID für die Neuanlage. |
| `LIDARR_ROOT_FOLDER` | `/music/Music` | Root-Folder-Pfad für die Neuanlage. |

Die Pfade im Container (`DOWNLOADS_DIR=/music/Charts`, `MUSIC_DIR=/music/Music`)
sind in der `compose.yml` gesetzt und müssen normalerweise nicht
geändert werden. Wichtig: `DOWNLOADS_DIR` muss **derselbe** Ordner sein, in den
slskd seine fertigen Downloads legt (`directories.downloads` in der
`slskd.yml`).

---

## Verzeichnis-Layout

### Auf dem Host (NAS)

| Pfad                            | Zweck |
|---------------------------------|-------|
| `/path/to/charts-stack` | Stack-Projektverzeichnis (Compose/Build, alle Dienste). |
| `/volume2/music/Charts`         | slskd-Downloads (Quelle für den Import). |
| `/volume2/music/Music`          | Navidrome-Library (Ziel des Imports). |

### Im Container

| Host (NAS)        | Container   | Zweck |
|-------------------|-------------|-------|
| `/volume2/music`  | `/music`    | ganze Musik-Freigabe (Lesen aus `Charts`, Schreiben nach `Music`). |

Konkret: `DOWNLOADS_DIR=/music/Charts`, `MUSIC_DIR=/music/Music`. Der Dienst
läuft als `youruser:users` (PUID 1027 / PGID 100), damit die einsortierten Dateien
denselben Eigentümer wie die übrige Musik haben und Navidrome sie lesen kann.

---

## Kopieren vs. Verschieben & Aufräumen

**Warum kopieren (Standard)?**
Soulseek erwartet, dass geladene Dateien eine Weile weiter zum Teilen
bereitstehen. Beim **Kopieren** (`COPY_MODE=1`) bleibt das Original im
Charts-Ordner (= slskd-Share) liegen, dein Song ist aber sofort auch in der
Library. So teilst du frisch Geladenes noch eine Weile, ohne deine gesamte
Sammlung freizugeben.

**Warum anzahl-basiert aufräumen (`KEEP_LATEST`)?**
Soulseek/slskd kennt **keinen** Seed-Zähler wie BitTorrent („lösche nach X
Uploads" geht technisch nicht). Wichtiger ist ohnehin die **Anzahl** der
geteilten Dateien (siehe Leecher-Abschnitt). Deshalb ist die Standard-Strategie
**anzahl-basiert**: `charts-import` behält immer die `KEEP_LATEST` neuesten
Downloads (Standard 500) und löscht nur den Überschuss.

Der Vorteil gegenüber zeitbasiertem Löschen: Der Share bleibt **stabil bei ~500
Dateien** (bei `KEEP_LATEST=500`), egal wie oft du manuell neue Songs suchst. Bei zeitbasiertem Löschen
(`RETENTION_DAYS`) würde der Share bei längeren Such-Pausen leerlaufen und dich
zum Leecher machen. `KEEP_LATEST` entkoppelt das Aufräumen komplett von deinem
Such-Rhythmus.

**Warum schützt das vor dem Leecher-Status?**
Andere Soulseek-Nutzer bewerten dich danach, **wie viele Dateien** du teilst,
nicht danach, wie viel real hochgeladen wird. Wer nur eine Handvoll Dateien
anbietet, gilt als „Leecher" und wird ausgebremst oder geblockt (slskd hat dafür
sogar eine eigene `leechers`-Gruppe). Mit `KEEP_LATEST=500` teilst du dauerhaft
rund 500 Dateien, sobald sich genug Downloads angesammelt haben. Das reicht
meist, um nicht als Leecher zu gelten. Reicht es nicht, teile zusätzlich einen
kleinen, festen Seed-Ordner (siehe unten).

**Alternative: alter-basiert (`RETENTION_DAYS`).**
Wer lieber zeitbasiert aufräumt, setzt `KEEP_LATEST=0` und `RETENTION_DAYS` auf
die gewünschte Anzahl Tage. Downloads werden dann gelöscht, sobald sie älter
sind. Nur sinnvoll, wenn regelmäßig neue Downloads reinkommen (z. B. bei
automatischer Suche), sonst läuft der Share leer.

**Upload einschränken, ohne als Leecher zu gelten.**
Nicht die Dateimenge reduzieren, sondern die **Bandbreite** begrenzen. In der
`slskd.yml` unter `transfers.upload` `slots` und `speed_limit` moderat setzen
(z. B. 3 Slots, 300 KiB/s). So bleibt dein Upstream frei, du teilst aber
weiterhin genug Dateien.

**Optionaler fester Seed-Ordner.**
Wer sicher nicht als Leecher gelten will, teilt zusätzlich einen kleinen,
dauerhaften Ordner mit z. B. 100–300 eigenen Tracks (nicht die ganze Library).
In slskd als zweites Share (`/seedmusic`) hinzufügen. So bist du ein „fairer"
Nutzer, ohne die komplette Sammlung freizugeben.

**Wann verschieben statt kopieren?**
Wenn du überhaupt nicht (weiter-)teilen willst: `COPY_MODE=0`. Dann wird die
Datei sofort aus dem slskd-Share in die Library verschoben und gar nicht mehr
angeboten. (Beachte: dauerhaftes Nicht-Teilen bremst dich im Soulseek-Netz als
„Leecher" aus.)

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

**Cover für bestehende Songs nachrüsten (einmalig):**
Der Import bettet Cover nur für neu einsortierte Songs ein. Um die **bestehende**
Library nachzubessern, gibt es ein Einmal-Skript, das alle MP3s ohne Cover
findet und über die konfigurierten Quellen (iTunes→Deezer) nachrüstet:

```bash
# 1. Erst zaehlen/auflisten (aendert nichts):
sudo docker compose exec charts-import python -m app.backfill_covers --dry-run
# 2. Dann tatsaechlich nachruesten:
sudo docker compose exec charts-import python -m app.backfill_covers
```

**Fehlerhafte (rechteckige) Cover reparieren.** Manche slskd-Dateien bringen
verzerrte Cover mit (z. B. CD-Scans mit Vorder- und Rückseite nebeneinander).
Mit `--fix-broken` ersetzt der Backfill **rechteckige** Cover durch das
quadratische Single-Cover. Wichtig: `--fix-broken` betrachtet **nur die
Chart-Songs** (die in den `.m3u`-Playlists gelisteten Dateien, `PLAYLIST_DIR`),
**nicht** die ganze Library, damit deine übrige (Lidarr-verwaltete) Sammlung mit
ihren Album-/Compilation-Covern unangetastet bleibt. Quadratische Cover bleiben
ohnehin unangetastet, und ersetzt wird nur, wenn eine Quelle ein Cover liefert:

```bash
# Erst auflisten, was repariert wuerde (aendert nichts):
sudo docker compose exec charts-import python -m app.backfill_covers --fix-broken --dry-run
# Dann reparieren:
sudo docker compose exec charts-import python -m app.backfill_covers --fix-broken
```

**Kassetten-/CD-Scan-Cover ersetzen (quadratisch, aber falsch).** Manche
Quelldateien haben als „Cover" den Scan einer Kassetten- oder CD-Hülle (mit
Trackliste, Seite A/B). Beispiel: „Kylie Minogue – I Should Be So Lucky" zeigte
einen Kassettenhüllen-Scan. Solche Bilder sind **quadratisch** und werden von
`--fix-broken` daher **nicht** erkannt. Mit `--force-refresh` wird bei **allen**
Chart-Songs das Cover neu geholt (Quelle laut `COVER_SOURCES`, i. d. R.
iTunes-Single-Artwork; nur Playlist-Songs, nicht die ganze Library). Das ersetzt
auch alte, schlechtere Cover (z. B. eine Demo-EP durch das echte Single-Cover):

```bash
# Vorschau (ändert nichts):
sudo docker compose exec charts-import python -m app.backfill_covers --force-refresh --dry-run
# Ersetzen:
sudo docker compose exec charts-import python -m app.backfill_covers --force-refresh
```

**Alle je geladenen Chart-Songs erfassen (`--all-charts`).** `--force-refresh`
allein betrachtet nur die aktuell in den Playlists gelisteten Songs. Um **alle
je von charts-download geladenen** Chart-Songs zu erfassen – auch solche, die
inzwischen aus den Charts gefallen sind, aber noch in der Library liegen –
kombinierst du es mit `--all-charts`. Der Umfang kommt dann aus dem **Manifest**
(`MANIFEST_FILE`), das ausschließlich von charts-download geschrieben wird.

> **Wichtig – Lidarr-Sammlung bleibt unangetastet:** `--all-charts` fasst
> **nur** Dateien an, die im Manifest stehen (= von slskd geladene Chart-Songs).
> Deine übrige, von Lidarr verwaltete Sammlung steht nie im Manifest und wird
> daher **nie** verändert. Trotzdem immer erst mit `--dry-run` prüfen.

```bash
# Vorschau: ALLE je geladenen Chart-Cover, die neu geholt wuerden (aendert nichts):
sudo docker compose exec charts-import python -m app.backfill_covers --all-charts --force-refresh --dry-run
# Ersetzen (nur Manifest-Songs, keine Lidarr-Sammlung):
sudo docker compose exec charts-import python -m app.backfill_covers --all-charts --force-refresh
```

Für einen **einzelnen** Song geht es auch gezielt, ohne alle anzufassen:

```bash
sudo docker compose exec charts-import python -c "
from app import cover
cover.embed_cover('/music/Music/Kylie Minogue/Kylie (1988)/Kylie Minogue - Kylie - 01 - I Should Be So Lucky.mp3', 'Kylie Minogue', 'I Should Be So Lucky', overwrite=True)
"
```

**ReplayGain für bestehende Chart-Songs nachrüsten.** Der Import normalisiert
neue Songs automatisch (`REPLAYGAIN=1`). Für die bereits vorhandenen Chart-Songs
gibt es ein Einmal-Skript (nur Playlist-Songs, nicht die ganze Library):

```bash
# Erst zählen, was fehlt (ändert nichts):
sudo docker compose exec charts-import python -m app.backfill_replaygain --dry-run
# Dann berechnen + schreiben (nur Songs ohne ReplayGain-Tag):
sudo docker compose exec charts-import python -m app.backfill_replaygain
# Alle Chart-Songs neu berechnen (z. B. nach Änderung von REPLAYGAIN_TARGET_LUFS):
sudo docker compose exec charts-import python -m app.backfill_replaygain --overwrite
```

Damit die Normalisierung hörbar wird, im Abspieler den ReplayGain-Modus
aktivieren (Navidrome-Web: Einstellungen → ReplayGain; Apps wie Symfonium haben
eine eigene ReplayGain-Option).

Steuerung über Umgebungsvariablen: `BACKFILL_SLEEP_MS` (Pause zwischen
API-Calls, Default 400 ms, schont die APIs) und `BACKFILL_LIMIT` (max. Anzahl
pro Lauf, 0 = alle). Bereits vorhandene Cover werden nie überschrieben, Songs
ohne lesbare Tags übersprungen. Neu nachgerüstete Cover werden ebenfalls
quadratisch zugeschnitten. Läuft über die ganze `MUSIC_DIR`, kann bei
grosser Library also eine Weile dauern.

**flashback-Altlasten aufräumen (einmalig).** Vor der Deezer-Kanonisierung
schrieb `charts-flashback` Artist/Titel in GROSSBUCHSTABEN und mit `{JJJJ}` im
Namen; die entsprechenden Manifest-Einträge wirken nach (charts-import bildet
Pfad/Tag aus dem Manifest, nicht aus dem Dateinamen). Das idempotente Skript
`scripts/cleanup_flashback_legacy.py` entfernt diese vergifteten
`src=flashback`-Alt-Einträge (GROSS- bzw. `{JJJJ}`-Titel) aus dem Manifest und
räumt die zugehörigen `{JJJJ}`-Library-Dubletten weg. Die Jahr-Map
(`.charts-flashback-years.json`) bleibt unangetastet. Auf dem **NAS-Host**
ausführen (Library beschreibbar), zuerst mit `--dry-run`:

```bash
python charts-import/scripts/cleanup_flashback_legacy.py --dry-run
python charts-import/scripts/cleanup_flashback_legacy.py
```

**Herkunfts-/Encoder-Tags aus der bestehenden Library entfernen (einmalig).**
Neue Importe werden automatisch bereinigt (siehe `tags.py`); für den **bereits**
vorhandenen Bestand gibt es ein Einmal-Skript mit **derselben** Lösch-Logik. Es
läuft über die **ganze Library** und entfernt `COMM`/`TENC`/`TSSE`/URL-Frames +
ungeschützte `TXXX` (z. B. Uploader-Signaturen, `TXXX:LOG`, der fre:ac-Kommentar)
– **schützt** aber `TXXX:MusicBrainz*` und `TXXX:*replaygain*` sowie alle
Standard-Tags. Läuft im Container (dort ist `/music` beschreibbar), **immer zuerst
mit `--dry-run`** (zeigt mit `--verbose` die betroffenen Frames; mit `--artist
"<Name>"` auf einen Künstler-Ordner begrenzen zum Testen):

```bash
sudo docker compose exec charts-import python /app/scripts/strip_provenance_tags.py --dry-run
sudo docker compose exec charts-import python /app/scripts/strip_provenance_tags.py
```

Danach einen Navidrome-Scan auslösen, damit die bereinigten Tags sichtbar werden.

**Nach Code-Änderungen neu bauen:**

```bash
sudo docker compose up -d --build
```

**Stoppen / entfernen:**

```bash
sudo docker compose down
```

---

## Troubleshooting

**Songs landen unter `Unknown Artist/Unknown Album`.**
Wenn der Download von charts-download kam, sollte das durch das Manifest
verhindert sein (saubere Tags werden gesetzt). Passiert es trotzdem, fehlt der
Manifest-Eintrag (z. B. Datei manuell in den Charts-Ordner gelegt) und die Datei
hat keine lesbaren Tags. Dann Tags nachpflegen (z. B. mit MusicBrainz Picard)
und die Datei erneut in den Charts-Ordner legen, oder den
Song von einer besser getaggten Quelle laden. slskd-Downloads sind i. d. R.
getaggt, ganz alte Rips manchmal nicht.

**Ein gerade laufender Download wird schon einsortiert.**
Sollte durch `MIN_AGE_SECONDS` verhindert werden. Wert erhöhen (z. B. `300`),
falls deine Downloads langsam sind.

**Der Song ist in der Library, taucht aber nicht in Navidrome auf.**
Navidrome muss erst scannen. Warte auf den nächsten `ND_SCANSCHEDULE` oder löse
im Navidrome-UI einen Scan aus. Erst danach kann `charts-sync` ihn in die
Playlist matchen.

**Dateien gehören dem falschen Benutzer.**
`PUID`/`PGID` prüfen (auf dem NAS mit `id youruser`). Sie müssen mit Navidrome und
slskd übereinstimmen, sonst kann Navidrome die Dateien evtl. nicht lesen.

**Cleanup löscht nichts.**
Bei `KEEP_LATEST` wird erst gelöscht, wenn mehr als `KEEP_LATEST` Dateien im
Ordner liegen. Bei der alter-basierten Variante `RETENTION_DAYS` prüfen (`0` =
deaktiviert). Sind beide `0`, ist das Aufräumen abgeschaltet. Sortiert/gelöscht
wird nach dem Änderungsdatum (`mtime`) der Datei.

---

## Ausbaupfad: Automatisierte Suche mit Soularr

Der manuelle Weg ist: `missing.txt` öffnen, Songs in der slskd-Weboberfläche
suchen und laden. `charts-import` übernimmt danach das Einsortieren. Für die
**automatische Suche** gibt es [Soularr](https://github.com/mrusse/soularr).

**Was ist Soularr?**
Soularr ist die Brücke zwischen **Lidarr** (dem „*arr"-Tool für Musik, analog zu
Sonarr für Serien / Radarr für Filme) und **slskd**. Es liest die „Wanted"-Liste
aus Lidarr, sucht die Titel automatisch in slskd, lädt sie und meldet sie an
Lidarr zurück. Es ist **für Musik** gedacht, nicht für Hörbücher.

**Wichtige Einschränkung für diesen Charts-Workflow:**
Soularr kann **keine `missing.txt` direkt verarbeiten**. Es braucht zwingend
**Lidarr** als Quelle der Wunschliste. Damit Soularr die Chart-Lücken schliesst,
müsstest du:

1. **Lidarr** aufsetzen (eigener Container).
2. Die fehlenden Künstler/Alben in Lidarr als „Monitored / Wanted" markieren.
3. **slskd** als Download-Client in Lidarr / über Soularr anbinden.
4. **Soularr** periodisch laufen lassen; es lädt die Wanted-Einträge via slskd.

Lidarr ist zudem **album-orientiert**, während Charts einzelne **Singles** sind.
Das passt konzeptionell nur mittelmässig – für einzelne Chart-Songs ist der
manuelle slskd-Weg oft direkter.

**Skizze eines Soularr-Setups** (nur wenn du den Lidarr-Weg gehen willst):

```yaml
# Ergaenzung zur bestehenden slskd/Lidarr-Landschaft. Nur Skizze!
services:
  soularr:
    image: mrusse08/soularr:latest
    container_name: soularr
    restart: unless-stopped
    environment:
      - TZ=Europe/Zurich
      - SCRIPT_INTERVAL=300        # Sekunden zwischen den Laeufen
    volumes:
      # Soularr-Konfiguration (config.ini + Zeitstempel)
      - /path/to/arr-stack/soularr:/data
      # Muss denselben Download-Ordner sehen wie slskd
      - /volume2/music/Charts:/downloads
```

Die eigentliche Konfiguration passiert in `config.ini` (im `/data`-Volume):
dort trägst du die **Lidarr-URL + API-Key**, die **slskd-URL + API-Key** und den
gemeinsamen `download_dir` ein. Den slskd-API-Key legst du vorher in der
`slskd.yml` an (`web.authentication.api_keys`). Details im
[Soularr-README](https://github.com/mrusse/soularr).

> **Empfehlung:** Für deinen reinen Charts-Anwendungsfall reicht die Kette
> `charts-sync` → manuelle slskd-Suche → `charts-import`. Soularr lohnt sich
> erst, wenn du ohnehin **Lidarr** für eine grössere, album-basierte
> Musikverwaltung einsetzt.

*Hinweis: Inhalte zu Soularr wurden aus Gründen der Lizenzkonformität
zusammengefasst und umformuliert.*

---

## FAQ

**Was passiert mit abgelehnten Dateien (Türsteher)?**
Sie werden nach `_rejected` im Charts-Ordner verschoben (nicht gelöscht), damit
du bei Bedarf reinschauen kannst. charts-import fasst diesen Ordner nicht mehr
an. Der zugehörige Song wird im charts-download-State freigegeben, sodass
charts-download beim nächsten Lauf eine andere (hoffentlich bessere) Version
sucht. Findet sich keine saubere Version, bleibt der Song in der missing.txt.
Wenn du den `_rejected`-Ordner nicht mehr brauchst, kannst du ihn einfach
löschen.

**Werden viele Dateien abgelehnt?**
Normalerweise nein. charts-download filtert Lyrics/Remix/FLAC schon beim
Download weg, daher kommt beim Türsteher selten noch etwas an. Er ist vor allem
das Sicherheitsnetz für Altbestände und manuell abgelegte Dateien.

**Ein legitimer Song wird fälschlich abgelehnt.**
Prüfe den `_rejected`-Ordner. Bei einem Fehlalarm durch ein Keyword kannst du
`REJECT_KEYWORDS` anpassen oder mit `REJECT_ENABLED=0` den Türsteher ganz
abschalten. Bei „kein brauchbarer Artist" hilft es, die Datei-Tags zu pflegen.

**Woher kommen die Cover?**
charts-import sucht für jeden einsortierten Song (mit sauberen Chart-Metadaten)
das passende Cover über die konfigurierten Quellen (`COVER_SOURCES`, Default
**iTunes → Deezer**) und bettet es direkt in die MP3 ein (ID3-Tag, keine
separate Bilddatei). **iTunes/Apple** ist die Primärquelle, weil es den größten
Katalog mit dedizierten **Single-Artworks** in hoher Auflösung hat (immer
quadratisch); **Deezer** springt ein, wenn iTunes einen Song nicht kennt.
Die Trefferauswahl bevorzugt die echte Single bzw. das Original-Album und
sortiert Fremd-Versionen aus (Remix, Sped Up, Slowed, Live, Instrumental,
Piano-/Cover-Bands, Compilations/„Now That's What I Call"/KIDZ BOP sowie
Demo-EPs). Damit bekommt z. B. „Dominic Fike – Babydoll" das dedizierte
„Babydoll / White Keys"-Single-Cover statt des Demo-EP-Covers.

Das Cover wird über Künstler+Titel gesucht, liefert also das **Single-/
Original-Album-Cover**, auch wenn die Datei aus einer Compilation stammt. Mit
`OVERWRITE_COVER=1` wird ein bereits eingebettetes (z. B. Sampler-)Cover ersetzt.
iTunes-Cover sind bereits quadratisch; ein evtl. rechteckiges Deezer-Cover wird
mittig zugeschnitten (mit Pillow). Navidrome liest eingebettete Cover mit
höchster Priorität. Findet keine Quelle ein Cover, bleibt der Song ohne Artwork
(kein Fehler). Abschaltbar über `EMBED_COVERS=0`. Hinweis: Nur MP3 wird
unterstützt; FLAC-/andere Dateien werden übersprungen.

**Werden meine Original-Downloads gelöscht?**
Nur beim Aufräumen. Mit `KEEP_LATEST` (Standard) werden die ältesten Downloads
gelöscht, sobald mehr als `KEEP_LATEST` im Ordner liegen; die neuesten bleiben.
Im Kopier-Modus (Standard) bleibt bis dahin sowohl die Kopie in der Library als
auch das Original im Charts-Ordner bestehen.

**Was passiert bei doppelten Songs?**
Ist am Zielort bereits eine Datei gleicher Grösse, wird übersprungen. Bei einer
echten Namenskollision mit anderer Grösse wird ein Zähler-Suffix angehängt.

**Bekomme ich mit, was importiert wurde?**
Ja, wenn Pushover konfiguriert ist (`PUSHOVER_TOKEN` + `PUSHOVER_USER`). Nach
jedem Lauf mit neuen Songs kommt eine Meldung mit der Liste der einsortierten
Titel (bis zu 10, Rest als „… und X weitere"), je Titel mit `[playlist]`-Präfix
und einer Aufschlüsselung, wie viele Songs aus welcher Playlist stammen.

**Muss `charts-import` auf `charts-sync` warten?**
Nein. Beide laufen unabhängig in ihrem eigenen Intervall. Die Kette entsteht
über die gemeinsamen Ordner (`Charts`, `Music`), nicht über direkte
Kommunikation.
