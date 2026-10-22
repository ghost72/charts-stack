# charts-config

Snippet-/Konfig-Weboberfläche für den charts-stack. Eine passwortgeschützte
Web-UI (Standard-Port `5058`), die beim Zusammenstellen der Chart- und
Energy-Quellen hilft: Sie zeigt **fertige Snippets** zum Hineinkopieren in
`.env`/`charts.json` – sie ändert selbst **nichts**.

Schwester-Dienst des [`charts-dashboard`](../charts-dashboard): charts-config
wurde aus dem Dashboard herausgelöst. Der Quellen-Finder und der
Energy-Kanal-Browser, die früher im Dashboard lagen, leben jetzt hier; das
Dashboard ist ein reiner Status-Monitor.

---

## Was es zeigt

Zwei reine **Lese-Werkzeuge** auf einer Seite – beide bauen nur Snippets, keines
schreibt `.env` oder `charts.json`:

### Quellen-Finder (neue Chart-Quellen finden)

Hilft beim Zusammenstellen der `charts-sync`-`CHARTS`-Liste – man muss die IDs
nicht mehr von Hand aus Weblinks fummeln:

- **Deezer-Playlist-Suche:** Suchbegriff eingeben → passende Playlists mit ID
  und einem **fertigen JSON-Snippet** zum Kopieren
  (`{"service":"deezer","type":"playlist","source":"<id>",…}`).
- **Deezer-Künstler-Suche:** Künstlernamen eingeben (z. B. `Gölä`, `Polo Hofer`)
  → passende Künstler mit ID und Fan-Zahl (hilft, gleichnamige Künstler zu
  unterscheiden) sowie ein **fertiges Snippet** für die Top-Tracks des Künstlers
  (`{"service":"deezer","type":"artist","source":"<id>",…}`).
- **Deezer-Genre-Tabelle:** die stabilen Genre-IDs als Liste (1×/Tag gecacht,
  Refresh beim ersten Aufruf nach Ablauf – siehe `DASHBOARD_GENRE_CACHE_TTL`).
  Fällt der Live-Call aus, greift eine eingebaute statische Fallback-Liste.
- **Last.fm-Tag-Prüfung:** einen Tag (z. B. `country`) eingeben → prüft via
  `tag.getTopTracks`, ob er brauchbar ist, und zeigt das passende Snippet
  (`{"service":"lastfm","method":"tag.getTopTracks","tag":"<tag>",…}`). Dieser
  Teil ist **nur mit `LASTFM_API_KEY`** aktiv; ohne Key ist er ausgegraut.

Jedes Snippet lässt sich kopieren und direkt in die `CHARTS`-Variable der
`.env`/`compose.yml` (bzw. `charts.json`) einsetzen. Volle Referenz:
[`CHARTS-SOURCES.md`](../CHARTS-SOURCES.md).

### Energy-Kanäle (NRJ) durchsuchen

Ein zweites, ebenfalls **reines Lese-Panel**: eine Liste der bestätigten
Energy-(NRJ-)Webradio-Kanäle, damit man beim Zusammenstellen von
`ENERGY_CHANNELS` nicht die Slugs aus dem README suchen muss. Je Kanal werden
Anzeigename, Slug, Genre, ein **Status-Badge** (aktiv + laufender Modus bzw.
inaktiv) und ein **fertiges JSON-Snippet** für das `ENERGY_CHANNELS`-Array
gezeigt. charts-config ändert **nie** `.env` oder `charts.json` – man fügt von
Hand ein und startet den Dienst selbst neu (nur **Text-Hinweise**, kein
Neustart-Knopf).

Der Status stammt aus der laufenden `charts-energy`-Konfiguration (die der
Dienst in seine Status-JSON schreibt und die charts-config read-only liest);
eine fehlende/alte Statusdatei zeigt alle Kanäle einfach „inaktiv" (fail-open).
Je Standard-Modus ist eine zusätzliche Verdrahtung nötig:

- **own** → die (im Snippet angezeigte) `missing-energy-<name>.txt` an
  `MISSING_FILES` anhängen, damit `charts-download` die fehlenden Songs lädt.
- **feed** → `{"service":"energy","theme":"<theme>"}` zur Playlist dieses Themas
  in `charts-sync/charts.json` hinzufügen (Thema bytegleich, z. B. `mundart`).
- **library** → die gemeinsame `missing-energy-library.txt` an `MISSING_FILES`
  anhängen (alle `library`-Kanäle teilen sich diese eine Datei).

Details zu den Modi: [`charts-energy/README.md`](../charts-energy/README.md).

## Woher die Daten kommen

- **Deezer / Last.fm:** direkte Abfrage der öffentlichen HTTP-APIs (WAN). Ohne
  `LASTFM_API_KEY` ist nur der Last.fm-Teil ausgegraut – der Rest läuft.
- **Energy-Live-Status:** aus der Status-JSON, die `charts-energy` nach
  `Charts/.charts-status/` schreibt – von charts-config **read-only** gemountet.
- **Kein Docker-Zugriff, kein Neustart-Knopf:** anders als das Dashboard braucht
  charts-config die Docker-SDK **nicht**. Es liest nur Deezer/Last.fm und die
  Status-JSON – es startet oder stoppt keinen Container.

## Login

charts-config hat einen **eigenen** Login, getrennt vom Dashboard/songrequest
(es zeigt interne Snippets):

- `CONFIG_USERNAME` (Default `admin`) + `CONFIG_PASSWORD`.
- Ist `CONFIG_PASSWORD` leer, fällt es auf die gemeinsamen
  `APP_USERS`/`APP_PASSWORD` zurück.

Ohne irgendein Passwort startet der Dienst **nicht** (fail-closed) – die Seite
kann von außen erreichbar sein.

## Konfiguration (zentrale `.env`)

| Variable | Default | Zweck |
| --- | --- | --- |
| `CONFIG_PORT` | `5058` | Host-Port der Web-UI |
| `CONFIG_USERNAME` | `admin` | Benutzername für charts-config |
| `CONFIG_PASSWORD` | – | Passwort (leer = Fallback auf `APP_USERS`/`APP_PASSWORD`; fehlt beides, startet der Dienst nicht) |
| `SESSION_SECRET` | – | Zufallsstring für Session-Cookies; leer = erneuter Login nach Neustart |
| `LASTFM_API_KEY` | – | optional, für die Last.fm-Tag-Prüfung (leer = Last.fm-Box ausgegraut) |
| `DASHBOARD_GENRE_CACHE_TTL` | `86400` | Cache-TTL der Deezer-Genre-Tabelle im Quellen-Finder (Sekunden; Refresh beim ersten Aufruf nach Ablauf) |
| `CHARTS_LANG` | – (leer = Englisch) | Sprache für UI **und** Logs (`de`/`fr` verfügbar) – stackweiter Schalter, kein eigener UI-Umschalter |

## Zugriff

Im LAN direkt über `http://<host>:5058`. Für den Zugriff von außen hinter einen
Reverse Proxy legen (z. B. `config.deine-domain.tld` → `http://<host>:5058`,
kein WebSocket nötig) und **HTTPS** verwenden – das Login nutzt ein Session-Cookie.

## Sicherheit

- Läuft als **unprivilegierter User** (`PUID:PGID`), **nicht** als root.
- **Kein** Docker-Zugriff: charts-config spricht nur mit Deezer/Last.fm (WAN) und
  liest die charts-energy-Status-JSON. Kein Socket-Mount, kein Proxy, kein
  Container-Start/-Stop.
- Mountet den Charts-Ordner **read-only** (nur für die Status-JSONs).

## Installation

charts-config ist Teil des charts-stack und wird **nicht** einzeln installiert,
sondern über das gemeinsame Stack-Projekt (eine `compose.yml` + eine zentrale
`.env` im `charts-stack`-Ordner). Siehe das Haupt-README für die vollständige
Installation. Nur diesen Dienst neu bauen/starten:
`docker compose up -d --build charts-config` aus dem `charts-stack`-Ordner.

## Sprache

charts-config folgt dem stackweiten `CHARTS_LANG` (leer = Englisch; `de`/`fr`
verfügbar). Ein Schalter für den ganzen Stack, kein eigener UI-Umschalter –
Sprache ändern = `.env` anpassen + Container neu starten (kein Rebuild, die
Sprachdateien kommen read-only aus dem `/lang`-Mount).

> 🇬🇧 English version: [README.en.md](README.en.md)
