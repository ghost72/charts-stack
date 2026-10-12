# charts-watchdog

Stiller Wächter für den charts-stack: überwacht alle Container und schickt eine
Pushover-Meldung, wenn einer ausfällt – und eine Entwarnung, wenn er sich wieder
erholt. Kein UI, kein eigenes Image (nutzt `docker:cli` + ein kleines
Shell-Skript).

Gegenstück zu [`charts-dashboard`](../charts-dashboard) (Status-UI zum
Nachschauen); der Watchdog meldet sich **aktiv**, wenn etwas nicht stimmt.

---

## Was gemeldet wird

Pro Container, nur bei Zustands**wechsel**:

- **unhealthy** – der Healthcheck schlägt an (z. B. slskd nicht mehr beim
  Soulseek-Server eingeloggt).
- **Neustart-Schleife** (`restarting`) – ein Container kommt nicht hoch.
- **gestoppt** (`exited`/`dead`) – unerwartet beendet und bleibt es.
- **Erholung** – zurück auf „läuft", Entwarnung.

## Externe/abhängige Dienste

Zusätzlich zu den Containern prüft der Watchdog periodisch die **Erreichbarkeit
der externen Dienste**, von denen der Stack abhängt – mit denselben
Erreichbarkeits-Kriterien wie das [`charts-dashboard`](../charts-dashboard):

- **slskd** – `/api/v0/server` meldet eingeloggt (`isLoggedIn`).
- **Lidarr** – `/api/v1/system/status` antwortet (nur wenn `LIDARR_URL` +
  `LIDARR_API_KEY` gesetzt sind, sonst nicht überwacht).
- **ListenBrainz** – `validate-token` antwortet überhaupt (auch HTTP 400 gilt
  als erreichbar).
- **Deezer** – Chart-Endpoint liefert HTTP 200 ohne Fehlerobjekt.
- **Last.fm** – Chart-Endpoint liefert HTTP 200 ohne Fehler (nur mit
  `LASTFM_API_KEY`, sonst nicht überwacht).
- **iTunes** – Such-Endpoint erreichbar; eine **Drosselung** (HTTP 429/403) gilt
  bewusst als erreichbar, **nicht** als Ausfall.

Fällt ein Dienst länger als `WATCH_EXTERNAL_DEBOUNCE_SECONDS` (Default `600` =
**10 min**) **durchgehend** aus, kommt **eine** Pushover-Meldung – und eine
Entwarnung, sobald er sich wieder erholt. Kurze transiente Timeouts lösen
**nie** sofort eine Meldung aus (gleiche Entprell-/Cooldown-Logik wie bei den
Containern, nur mit eigenem Zustands-Namespace, damit sich der externe
slskd-Check nicht mit dem slskd-**Container** in die Quere kommt).

**Pushover** wird mitgeprüft, aber **bewusst nicht per Pushover gemeldet** – eine
Pushover-Störung per Pushover melden zu wollen wäre zirkulär. Ein Ausfall oder
ungültige Credentials landen daher **nur im Container-Log**, nie als Push.

## Kein Melde-Spam

Pro Problem-Episode gibt es garantiert **eine** Problem-Meldung und **eine**
Entwarnung:

- **Entprellung:** ein Problem wird erst gemeldet, wenn es `WATCH_DEBOUNCE_POLLS`
  Polls am Stück anhält (gegen kurze Start-/Neustart-Zucker beim Deploy).
- **Erholung erst stabil:** die Entwarnung kommt erst nach `WATCH_RECOVER_POLLS`
  OK-Polls am Stück. Eine flippernde App (ok/problem/ok/…) erreicht die Schwelle
  nie → keine Entwarnung → auch keine erneute Problem-Meldung.
- **Cooldown:** nach einer Problem-Meldung bleibt es für `WATCH_COOLDOWN_SECONDS`
  still für diese App – egal wie oft sie flippert.

## Konfiguration (zentrale `.env`)

| Variable | Default | Zweck |
| --- | --- | --- |
| `WATCH_CONTAINERS` | alle Stack-Container | Leerzeichen-getrennte Liste |
| `WATCH_INTERVAL_SECONDS` | `30` | Poll-Intervall |
| `WATCH_DEBOUNCE_POLLS` | `2` | Polls bis zur Problem-Meldung |
| `WATCH_RECOVER_POLLS` | `3` | OK-Polls bis zur Entwarnung |
| `WATCH_COOLDOWN_SECONDS` | `1800` | Ruhe nach einer Meldung (pro App) |
| `WATCH_EXTERNAL_DEBOUNCE_SECONDS` | `600` | Sekunden durchgehend nicht erreichbar vor der Meldung (externe Dienste, Default 10 min) |
| `SLSKD_HOST` / `SLSKD_API_KEY` | `http://slskd:5030` / – | slskd-Check (`/api/v0/server`) |
| `LIDARR_URL` / `LIDARR_API_KEY` | *(leer)* | optional – nur mit beiden wird Lidarr überwacht |
| `LASTFM_API_KEY` | *(leer)* | optional – nur mit Key wird Last.fm überwacht |
| `PUSHOVER_TOKEN` / `PUSHOVER_USER` | – | ohne diese nur Log, keine Meldung |
| `PUSHOVER_DEVICE` | *(leer)* | Optional: Ziel-Gerät. Mehrere Geräte kommagetrennt möglich (z.B. `iPhone-Max,Tablet-Anna`) — es wird pro Gerät eine eigene Nachricht gesendet. Alle Geräte gehören zum selben Pushover-Account. Leeres Feld = Zustellung an alle Geräte des Accounts. Ungültige/deaktivierte Gerätenamen werden vor dem Senden automatisch entfernt — sonst stellt Pushover einen unbekannten Namen ersatzweise an **alle** Geräte zu (Doppel-Push). |
| `PUSHOVER_SOUND` | `siren` | Benachrichtigungston für die Watchdog-Alarme. Gültige Pushover-Töne: `pushover, magic, cosmic, intermission, bike, bugle, cashregister, classical, falling, gamelan, incoming, mechanical, pianobar, siren, spacealarm, tugboat, alien, climb, persistent, echo, updown, none` (= still), `vibrate`. Leer = Account-Default. Über `PUSHOVER_SOUND_WATCHDOG` in der `compose.yml` überschreibbar. |
| `PUSHOVER_DEVICES_TTL` | `3600` | Optional: Cache-Dauer (Sekunden) für die zum Filtern geladene Geräteliste des Accounts. |

## Wie es funktioniert

Das Skript `watch.sh` fragt pro Container Status + Health ab. Es ändert **nichts**
an anderen Containern – es liest nur und schickt bei Bedarf eine Pushover-
Nachricht.

Der Zugriff auf die Docker-Engine läuft **nicht** direkt über den Socket, sondern
**read-only** über den `charts-docker-proxy`
(`DOCKER_HOST=tcp://charts-docker-proxy:2375`). Der Watchdog läuft dadurch als
**unprivilegierter User** (youruser), nicht als root – nur der kleine Proxy berührt
den Socket, und der lässt ausschließlich lesende Abfragen durch.

## Sprache

Die nach außen sichtbaren Watchdog-Texte (Pushover-Meldungen, Zustands-
beschreibungen, Log-Zeilen) folgen `CHARTS_LANG` – leer = Englisch, `de`/`fr`
verfügbar – aus dem zentralen `charts-stack/lang/`-Satz (dieselben Dateien, die
auch die Python-Dienste nutzen; read-only nach `/lang` gemountet). Fehlt die
Sprachdatei, degradiert der Watchdog sauber auf Englisch bzw. den Key-Namen und
läuft weiter.

Bewusst **nicht** übersetzt bleiben die internen Zustandswörter
(`running-healthy`, `ok`, `problem`, `transient` …) und Eigennamen der externen
Dienste (slskd, Lidarr, ListenBrainz, Deezer, Last.fm, iTunes) – sie sind
technische Marker bzw. Produktnamen, keine Anzeigetexte.

> 🇬🇧 English version: [README.en.md](README.en.md)
