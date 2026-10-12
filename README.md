# charts-stack

**[Deutsch](#deutsch) · [English](#english)**

Ein selbst-gehosteter Verbund kleiner Docker-Dienste, der aus Online-Charts und
deinem Hörverhalten automatisch Musik-Playlists für **Navidrome** baut und
fehlende Songs über **Soulseek (slskd)** nachlädt. Eine „Spotify-artige"
Erfahrung für die eigene Musiksammlung – ohne Abo, komplett lokal.

> ⚠️ **Rechtlicher Hinweis:** Dieses Projekt lädt Musik über das Soulseek-P2P-
> Netz. Lade nur Inhalte herunter, zu denen du berechtigt bist. Du bist selbst
> für die Einhaltung des Urheberrechts in deinem Land verantwortlich.

---

## Deutsch

### Was macht der Stack?

Mehrere eigenständige Dienste plus slskd als Download-Engine. Die Verarbeitungs-
Dienste sind **nur über gemeinsame Ordner** gekoppelt (ein Musik- und ein
Charts-Ordner) – kein Dienst ruft einen anderen direkt auf. Der ganze Stack ist
**ein** compose-Projekt mit **einer** zentralen `.env`.

| Dienst | Aufgabe | Intervall |
| --- | --- | --- |
| **charts-sync** | Holt Deezer-/Last.fm-Charts, matcht gegen deine Library, schreibt `.m3u`-Playlists + `missing-*.txt` | 12 h |
| **charts-hitparade** | Holt wöchentlich die offizielle Schweizer Hitparade (Top-100), schreibt `hitparade.m3u` + `missing-hitparade.txt` | So 20:00 |
| **charts-flashback** | Sammelt die Rotation vom FLASHBACK-FM-Hauptstream (alle Dekaden gemischt) und lässt die Library wachsen (Nachladen, keine Playlist; Jahr-Tag für die Dekaden-NSP) | 2 h |
| **charts-download** | Liest `missing-*.txt`, sucht/lädt via slskd, schreibt das **Manifest** | 60 min |
| **charts-import** | Sortiert fertige Downloads in die Library ein (Tags, Cover, ReplayGain, optional Lidarr-Unmonitor) | 30 min |
| **charts-digest** | Schickt täglich eine Pushover-Zusammenfassung der neu in die Library gekommenen Songs (nach Quelle gruppiert) | täglich 09:00 |
| **charts-recommend** | Lässt die gemeinsame Library aus dem öffentlichen Hörgeschmack mehrerer ListenBrainz-User wachsen (Nachladen, keine Playlist) | 24 h |
| **charts-upgrade** | Ersetzt Songs mit niedriger Bitrate durch bessere Versionen | on-demand |
| **charts-songrequest** | Web-UI (FastAPI) zum manuellen Anfordern einzelner Songs (Mehrbenutzer-Login) | on-demand |
| **charts-watchdog** | Überwacht alle Container und meldet per Pushover, wenn einer ausfällt oder sich erholt | 30 s |
| **charts-dashboard** | Web-UI (FastAPI) mit Status aller Dienste, Verbindungen und Lauf-Statistik | Dienst |
| **slskd** | Soulseek-Client, die Download-Engine (eigenes Soulseek-Login) | Dienst |

### Das Bindeglied: zwei gemeinsame Ordner + das Manifest

Alle Dienste teilen sich **einen Host-Ordner** (`MUSIC_ROOT`, z. B. `/volume2/music`)
mit zwei Unterordnern:

- **`MUSIC_ROOT/Music`** – deine Navidrome-Library (gelesen + Playlists geschrieben).
- **`MUSIC_ROOT/Charts`** – slskd-Downloads, die `missing-*.txt`, der State und
  das **Manifest** (`.charts-manifest.json`).

Das **Manifest** ist der Klebstoff: charts-download/-songrequest schreiben pro
geladener Datei die sauberen Metadaten hinein; charts-import liest es und taggt/
einsortiert die Datei korrekt. slskd lädt nach `Charts/` (= sein `/downloads`),
genau dort sucht charts-import.

### Voraussetzungen

- Docker + Docker Compose (getestet auf Synology DSM 7, läuft aber überall).
- **Navidrome** als Musik-Server (die `.m3u`-Playlists landen in dessen Library).
- Ein **Soulseek-Account** (kostenlos) für slskd.
- Optional: **Last.fm**-API-Key (Genre-Filter + Empfehlungen), **ListenBrainz**-
  Account (Empfehlungen), **Lidarr** (Unmonitor), **Pushover** (Benachrichtigungen).

> **Pushover einrichten (optional).** Benachrichtigungen sind komplett optional –
> ohne `PUSHOVER_TOKEN`/`PUSHOVER_USER` laufen alle Dienste still weiter (nur
> Log). Wenn du sie willst, brauchst du **zwei** Werte aus deinem Pushover-Account
> ([pushover.net](https://pushover.net)):
> 1. `PUSHOVER_USER` – dein persönlicher **User-Key** (nach dem Login oben im
>    Dashboard sichtbar).
> 2. `PUSHOVER_TOKEN` – das **API-Token einer eigenen Application**, die du
>    einmalig unter „Create an Application/API Token" anlegst.
>
> Der **Absendername** der Push-Nachricht ist der **Name dieser Application** –
> änderbar **nur im Pushover-Web** (Application bearbeiten), nicht über eine
> Variable in diesem Repo. Benennst du die App um, kommen neue Nachrichten unter
> dem neuen Namen; bereits zugestellte behalten den alten.

### Installation (ein Projekt, eine `.env`)

Der ganze Stack ist **ein** Projekt: eine `compose.yml` mit allen Diensten,
eine zentrale `.env`. Du musst nur einen Ordner kopieren und eine Datei
ausfüllen.

```bash
# 1. Diesen charts-stack-Ordner auf deinen Server kopieren.

# 2. slskd zuerst einrichten (API-Key + Soulseek-Login) – siehe slskd/README.md.
#    Dabei entsteht der API-Key, den du gleich in die .env eintraegst.

# 3. Eine zentrale .env anlegen und ausfuellen:
cp .env.example .env
#    PFLICHT (sonst startet mind. ein Dienst NICHT):
#      MUSIC_ROOT, SLSKD_API_KEY, PUID/PGID,
#      SONGREQUEST_USERS   (charts-songrequest crasht sonst - Sicherheits-Gate),
#      DASHBOARD_PASSWORD  (charts-dashboard crasht sonst - Sicherheits-Gate).
#    Optional: RECOMMEND_LISTENBRAINZ_USERS (charts-recommend), LISTENBRAINZ_TOKEN.

# 4. Die drei Daten-Unterordner VOR dem Start mit den richtigen Rechten anlegen
#    (sonst legt Docker sie beim Bind-Mount als root an -> die Dienste laufen
#    unter PUID:PGID und koennen nicht schreiben):
mkdir -p "$MUSIC_ROOT"/{Music,Charts,SeedShare}
chown -R 1027:100 "$MUSIC_ROOT"   # eigene PUID:PGID einsetzen (hier Synology-Default)

# 5. Starten – alle Dienste zusammen:
docker compose up -d --build
```

**Mit dem Synology Container Manager:** Projekt → Erstellen → Pfad auf diesen
`charts-stack`-Ordner → er findet die `compose.yml` und die `.env` automatisch.
Nach **Code-Änderungen** die betroffenen Dienste neu **bauen** (im Container
Manager: Projekt → Aktion → Erstellen; per CLI: `docker compose up -d --build
<dienst>`). `charts-watchdog` ist ein reines Shell-Skript ohne eigenes Image –
dort reicht ein Neustart (`docker compose up -d --force-recreate charts-watchdog`),
kein Rebuild. Reine `.env`-Änderungen brauchen nie einen Rebuild, nur ein
`docker compose up -d`.

Das war's. Die Dienste reden über das automatisch angelegte Projekt-Netz
miteinander (z. B. charts-download → `http://slskd:5030`); ein eigenes Netz musst
du nicht anlegen. Alle Variablen haben sinnvolle Defaults – in der `.env` setzt
du nur deine eigenen/Pflicht-Werte.

### Navidrome anbinden

charts-stack erzeugt nur die `.m3u`-Playlists und sortiert die Musik in
`MUSIC_ROOT/Music` ein – abgespielt wird über **Navidrome** (separater Dienst,
nicht Teil dieses Stacks). Damit es zusammenspielt:

- Navidrome muss **denselben** `MUSIC_ROOT/Music`-Ordner als Musik-Library
  einbinden, den charts-import befüllt.
- In Navidrome den **Playlist-Scan** aktivieren (Einstellung
  `ND_PLAYLISTSPATH` bzw. „automatisch `.m3u` importieren"), damit die von
  charts-sync geschriebenen Playlists (`charts.m3u`, `mundart.m3u`) in
  Navidrome erscheinen.
- Navidrome scannt periodisch; neue Songs/Playlists tauchen nach dem nächsten
  Scan auf.

**Was wann läuft:** slskd ist Dauerdienst; charts-sync (12 h) erzeugt Playlists +
`missing-*.txt`; charts-download (60 min) lädt die fehlenden Songs; charts-import
(30 min) sortiert sie ein; charts-recommend (24 h), charts-upgrade und
charts-songrequest sind optional. charts-dashboard (Status-UI) und
charts-watchdog (Pushover-Alarme) laufen optional mit; der kleine
`charts-docker-proxy` ist Infrastruktur, läuft automatisch über die
`compose.yml` mit und braucht **keine** Konfiguration (er gibt den Status-Diensten
nur lesenden Docker-Zugriff).

**Einzelne Dienste steuern:** vom charts-stack-Ordner aus, z. B.
`docker compose restart slskd` oder `docker compose up -d charts-sync`.

### Die MISSING_FILES-Kopplung (recommend → download)

Damit **charts-download** auch die Nachlade-Liste von **charts-recommend**
nachlädt, muss deren **eine** kombinierte `missing-recommend.txt` in der
`MISSING_FILES`-Variable der zentralen `.env` stehen. Das ist in der
`.env.example` bereits vorausgefüllt:

```
MISSING_FILES=/charts/missing.txt;/charts/missing-mundart.txt;/charts/missing-flashback.txt;/charts/missing-hitparade.txt;/charts/missing-recommend.txt
```

Nicht vorhandene Dateien sind harmlos – es gibt nur eine Log-Warnung.

> **Dekaden (80er/90er …):** Diese laufen **nicht mehr** über eine eigene
> charts-sync-Playlist, sondern über **charts-flashback** (füllt die Library aus
> der FLASHBACK-FM-Rotation) + **Navidrome Smart Playlists (NSP)**, die die
> Library nach Jahr filtern. charts-sync lädt nur noch die internationalen +
> Schweizer Charts und Mundart. Grund: die NSP greifen die *ganze* Library ab
> (breiter + bessere Qualität als eine einzelne Deezer-Playlist).

### Eigene Chart-Quellen (Deezer / Last.fm)

In `charts-sync` definiert die Variable `CHARTS` (JSON) die Playlists und ihre
Quellen. Jede Quelle kann ein optionales, abwärtskompatibles `service`-Feld
(`deezer`/`lastfm`) tragen. Die mitgelieferten IDs sind **Beispiele** – ersetze
sie durch deine eigenen. Die vollständige Feld- und ID-Referenz samt fertiger
Beispiele (Country, 80er/90er …) steht in **[`CHARTS-SOURCES.md`](CHARTS-SOURCES.md)**.
Neue Quellen-IDs findest du bequem über den **Quellen-Finder** im
[charts-dashboard](charts-dashboard) (Deezer-Playlist-Suche, Genre-Tabelle,
Last.fm-Tag-Prüfung). Details in `charts-sync/README.md`.

**An/Aus pro Dienst:** `charts-hitparade` und `charts-flashback` haben je einen
Schalter `HITPARADE_ENABLED` / `FLASHBACK_ENABLED` (`true`/`false`, Default
`true`). `false` pausiert den Dienst (Container bleibt, kein Fehler) – im
Dashboard als grauer **`idle`**-Status statt rot.

### Monitoring: Dashboard & Watchdog

Zwei Dienste behalten den Stack im Auge:

- **charts-dashboard** – eine Web-Oberfläche (Standard-Port `5057`), die auf einen
  Blick zeigt: laufen alle Container, ist slskd beim Soulseek-Server eingeloggt,
  ist Lidarr erreichbar, und was hat jeder Dienst zuletzt verarbeitet (exakte
  Lauf-Statistik). Passwortgeschützt mit **eigenem** Login (Benutzer `admin` +
  `DASHBOARD_PASSWORD`). Details in `charts-dashboard/README.md`.
- **charts-watchdog** – kein UI, sondern ein stiller Wächter: er überwacht alle
  Container und schickt **eine** Pushover-Meldung, wenn einer ausfällt (und eine
  Entwarnung bei Erholung). Details in `charts-watchdog/README.md`.

Beide greifen **nicht** direkt auf den Docker-Socket zu, sondern **read-only**
über einen kleinen `charts-docker-proxy` (nur lesende Abfragen erlaubt, kein
Container-Start/-Stop). Dashboard und Watchdog laufen dadurch als
unprivilegierter User, nicht als root. Die Lauf-Statistik stammt aus kleinen
JSON-Dateien, die jeder Dienst in `Charts/.charts-status/` schreibt.

### Secrets & Portabilität

- **Keine Secrets im Repo.** Alle Keys/Passwörter kommen aus der `.env` (per
  `.gitignore` ausgeschlossen); versioniert ist nur `.env.example`.
- Alle Host-Pfade laufen über `MUSIC_ROOT`, Benutzer über `PUID`/`PGID` – ein
  Umzug auf ein anderes System braucht nur `.env`-Anpassungen, keine compose-Edits.

### Sprache (Logs, Pushover, Dashboard)

Alle sichtbaren Texte – Log-Meldungen, Pushover-Benachrichtigungen und die
Dashboard-Oberfläche – lassen sich über **eine** Variable umschalten:

```
CHARTS_LANG=        # leer = Englisch (Default) | de = Deutsch | fr = Français
```

- Die Variable steht in der zentralen `.env` und gilt für **alle** Dienste
  (jeder Container bekommt sie plus den gemeinsamen `./lang`-Ordner read-only
  gemountet). Nach einer Änderung die Dienste neu starten
  (`docker compose up -d`), bei reiner `.env`-Änderung reicht ein Restart.
- Die Übersetzungen liegen **zentral** in `lang/en.json`, `lang/de.json` und
  `lang/fr.json` – eine Datei je Sprache, geteilt über alle Dienste (wie die
  `.env`). Englisch ist Default und Fallback: fehlt ein Text in der gewählten
  Sprache, erscheint automatisch die englische Fassung; fehlt auch die, der
  Schlüsselname. Ein unbekanntes `CHARTS_LANG` (Tippfehler) fällt still auf
  Englisch zurück – kein Dienst stürzt deswegen ab.
- **Code-Kommentare werden bewusst nicht übersetzt** (sie richten sich an
  Entwickler, nicht an Nutzer).

#### Eine Sprache beitragen

1. Kopiere `lang/en.json` nach `lang/<code>.json` (z. B. `lang/it.json` für
   Italienisch – `<code>` ist ein beliebiger Kürzel, das du dann in
   `CHARTS_LANG` setzt).
2. Übersetze **die Werte** (die Texte rechts), **nicht die Schlüssel** (links).
3. Platzhalter unverändert lassen: `{name}`-Felder (Python-Dienste) bzw. `%s`
   (nur die `watchdog.*`-Schlüssel) müssen in Zahl und – bei `{name}` – Namen
   gleich bleiben wie in `en.json`. Keine geschweiften Klammern zusätzlich
   einbauen.
4. `CHARTS_LANG=<code>` setzen, Dienste neu starten. Fehlt ein Schlüssel in
   deiner Datei, erscheint dort die englische Fassung – du kannst also auch
   unvollständig anfangen.

Ein Test (`charts-sync/tests/test_i18n.py`) stellt sicher, dass alle
mitgelieferten Sprachdateien dieselben Schlüssel und Platzhalter haben.

### Lizenz

MIT – siehe [LICENSE](LICENSE).

---

## English

### What does the stack do?

Several independent services plus slskd as the download engine. The processing
services are coupled **only through shared folders** (one music, one charts
folder) – no service calls another directly. The whole stack is **one** compose
project with **one** central `.env`.

| Service | Job | Interval |
| --- | --- | --- |
| **charts-sync** | Fetches Deezer/Last.fm charts, matches against your library, writes `.m3u` playlists + `missing-*.txt` | 12 h |
| **charts-hitparade** | Fetches the official Swiss charts (Top 100) weekly, writes `hitparade.m3u` + `missing-hitparade.txt` | Sun 20:00 |
| **charts-flashback** | Collects the FLASHBACK FM main stream rotation (all decades mixed) and grows the library (downloads, no playlist; year tag for the decade NSPs) | 2 h |
| **charts-download** | Reads `missing-*.txt`, searches/downloads via slskd, writes the **manifest** | 60 min |
| **charts-import** | Imports finished downloads into the library (tags, cover, ReplayGain, optional Lidarr unmonitor) | 30 min |
| **charts-digest** | Sends a daily Pushover summary of songs newly added to the library (grouped by source) | daily 09:00 |
| **charts-recommend** | Grows the shared library from the public listening taste of several ListenBrainz users (downloads, no playlist) | 24 h |
| **charts-upgrade** | Replaces low-bitrate songs with better versions | on-demand |
| **charts-songrequest** | Web UI (FastAPI) to request individual songs manually (multi-user login) | on-demand |
| **charts-watchdog** | Watches all containers and notifies via Pushover when one fails or recovers | 30 s |
| **charts-dashboard** | Web UI (FastAPI) showing status of all services, connections and run stats | service |
| **slskd** | Soulseek client, the download engine (needs its own Soulseek login) | service |

### The glue: two shared folders + the manifest

All services share **one host folder** (`MUSIC_ROOT`, e.g. `/volume2/music`) with
two subfolders:

- **`MUSIC_ROOT/Music`** – your Navidrome library (read + playlists written).
- **`MUSIC_ROOT/Charts`** – slskd downloads, the `missing-*.txt`, state, and the
  **manifest** (`.charts-manifest.json`).

The **manifest** is the glue: charts-download/-songrequest write clean metadata
per downloaded file; charts-import reads it and tags/imports the file correctly.
slskd downloads into `Charts/` (its `/downloads`), which is exactly where
charts-import looks.

### Requirements

- Docker + Docker Compose (tested on Synology DSM 7, but runs anywhere).
- **Navidrome** as the music server (the `.m3u` playlists land in its library).
- A **Soulseek account** (free) for slskd.
- Optional: **Last.fm** API key (genre filter + recommendations), **ListenBrainz**
  account (recommendations), **Lidarr** (unmonitor), **Pushover** (notifications).

### Installation (one project, one `.env`)

The whole stack is **one** project: a single `compose.yml` with all services
and one central `.env`. You only copy one folder and fill in one file.

```bash
# 1. Copy this charts-stack folder to your server.

# 2. Set up slskd first (API key + Soulseek login) – see slskd/README.md.
#    This produces the API key you enter into the .env next.

# 3. Create and fill in one central .env:
cp .env.example .env
#    REQUIRED (otherwise at least one service will NOT start):
#      MUSIC_ROOT, SLSKD_API_KEY, PUID/PGID,
#      SONGREQUEST_USERS   (charts-songrequest crashes otherwise - security gate),
#      DASHBOARD_PASSWORD  (charts-dashboard crashes otherwise - security gate).
#    Optional: RECOMMEND_LISTENBRAINZ_USERS (charts-recommend), LISTENBRAINZ_TOKEN.

# 4. Create the three data subfolders BEFORE the first start with the right
#    ownership (otherwise Docker creates them as root on bind-mount and the
#    services, running as PUID:PGID, cannot write):
mkdir -p "$MUSIC_ROOT"/{Music,Charts,SeedShare}
chown -R 1027:100 "$MUSIC_ROOT"   # use your own PUID:PGID (Synology default here)

# 5. Start – all services together:
docker compose up -d --build
```

**With the Synology Container Manager:** Project → Create → point the path at
this `charts-stack` folder → it finds `compose.yml` and `.env` automatically.
After **code changes**, **rebuild** the affected services (Container Manager:
Project → Action → Build; CLI: `docker compose up -d --build <service>`).
`charts-watchdog` is a pure shell script with no image of its own – there a
restart is enough (`docker compose up -d --force-recreate charts-watchdog`), no
rebuild. Pure `.env` changes never need a rebuild, just `docker compose up -d`.

### Connecting Navidrome

charts-stack only produces the `.m3u` playlists and sorts the music into
`MUSIC_ROOT/Music` – playback happens through **Navidrome** (a separate service,
not part of this stack). To make them work together:

- Navidrome must mount the **same** `MUSIC_ROOT/Music` folder as its music
  library that charts-import fills.
- Enable **playlist scanning** in Navidrome (setting `ND_PLAYLISTSPATH` / "import
  `.m3u` automatically") so the playlists written by charts-sync (`charts.m3u`,
  `mundart.m3u`) show up in Navidrome.
- Navidrome scans periodically; new songs/playlists appear after the next scan.

That's it. The services talk to each other over the automatically created
project network (e.g. charts-download → `http://slskd:5030`); you don't need to
create a network. All variables have sensible defaults – in the `.env` you only
set your own/required values.

**What runs when:** slskd is a permanent service; charts-sync (12 h) generates
playlists + `missing-*.txt`; charts-download (60 min) downloads the missing
songs; charts-import (30 min) imports them; charts-recommend (24 h),
charts-upgrade and charts-songrequest are optional. charts-dashboard (status UI)
and charts-watchdog (Pushover alerts) run optionally alongside; the small
`charts-docker-proxy` is infrastructure, runs automatically via the `compose.yml`
and needs **no** configuration (it only grants the status services read-only
Docker access).

**Controlling individual services:** from the charts-stack folder, e.g.
`docker compose restart slskd` or `docker compose up -d charts-sync`.

### The MISSING_FILES wiring (recommend → download)

For **charts-download** to also fetch the download list from
**charts-recommend**, its **one** combined `missing-recommend.txt` must be in the
`MISSING_FILES` variable of the central `.env`. It is pre-filled in
`.env.example`:

```
MISSING_FILES=/charts/missing.txt;/charts/missing-mundart.txt;/charts/missing-flashback.txt;/charts/missing-hitparade.txt;/charts/missing-recommend.txt
```

Without this, charts-recommend still writes the lists but charts-download ignores
them. (Missing files are harmless – just a log warning.)

### Your own chart sources (Deezer / Last.fm)

In `charts-sync` the `CHARTS` variable (JSON) defines the playlists and their
sources. Each source may carry an optional, backward-compatible `service` field
(`deezer`/`lastfm`). The bundled IDs are **examples** – replace them with your
own. The full field and ID reference plus ready-made examples (country, 80s/90s,
…) is in **[`CHARTS-SOURCES.en.md`](CHARTS-SOURCES.en.md)**. New source IDs are
easy to find via the **source finder** in the
[charts-dashboard](charts-dashboard) (Deezer playlist search, genre table,
Last.fm tag check). See `charts-sync/README.md`.

**On/off per service:** `charts-hitparade` and `charts-flashback` each have a
switch `HITPARADE_ENABLED` / `FLASHBACK_ENABLED` (`true`/`false`, default
`true`). `false` pauses the service (container stays, no error) – shown in the
dashboard as a grey **`idle`** status instead of red.

### Monitoring: dashboard & watchdog

Two services keep an eye on the stack:

- **charts-dashboard** – a web UI (default port `5057`) that shows at a glance:
  are all containers running, is slskd logged in to the Soulseek server, is
  Lidarr reachable, and what each service processed last (exact run stats).
  Password-protected with its **own** login (user `admin` + `DASHBOARD_PASSWORD`).
  See `charts-dashboard/README.md`.
- **charts-watchdog** – no UI, a silent guard: it watches all containers and
  sends **one** Pushover message when one fails (and an all-clear on recovery).
  See `charts-watchdog/README.md`.

Neither accesses the Docker socket directly; both go **read-only** through a
small `charts-docker-proxy` (read requests only, no container start/stop).
Dashboard and watchdog therefore run as an unprivileged user, not as root. The
run stats come from small JSON files each service writes into
`Charts/.charts-status/`.

### Secrets & portability

- **No secrets in the repo.** All keys/passwords come from `.env` (gitignored);
  only `.env.example` is versioned.
- All host paths go through `MUSIC_ROOT`, users through `PUID`/`PGID` – moving to
  another system only needs `.env` changes, no compose edits.

### Language (logs, Pushover, dashboard)

Every user-facing text – log messages, Pushover notifications and the dashboard
UI – is switchable through **one** variable:

```
CHARTS_LANG=        # empty = English (default) | de = German | fr = French
```

- The variable lives in the central `.env` and applies to **all** services
  (each container receives it plus the shared `./lang` folder mounted
  read-only). After a change restart the services (`docker compose up -d`); a
  pure `.env` change only needs a restart.
- Translations live **centrally** in `lang/en.json`, `lang/de.json` and
  `lang/fr.json` – one file per language, shared across all services (like the
  `.env`). English is the default and fallback: a text missing in the chosen
  language falls back to English, and if that is missing too, to the key name.
  An unknown `CHARTS_LANG` (typo) silently falls back to English – no service
  crashes over it.
- **Code comments are intentionally not translated** (they target developers,
  not users).

#### Contributing a language

1. Copy `lang/en.json` to `lang/<code>.json` (e.g. `lang/it.json` for Italian –
   `<code>` is any short code you then set in `CHARTS_LANG`).
2. Translate **the values** (the text on the right), **not the keys** (left).
3. Leave placeholders unchanged: `{name}` fields (Python services) and `%s`
   (the `watchdog.*` keys only) must match `en.json` in count and – for
   `{name}` – in name. Do not add extra curly braces.
4. Set `CHARTS_LANG=<code>`, restart the services. Any key missing from your
   file shows the English text, so you can start incomplete.

A test (`charts-sync/tests/test_i18n.py`) ensures all shipped language files
carry the same keys and placeholders.

### License

MIT – see [LICENSE](LICENSE).
