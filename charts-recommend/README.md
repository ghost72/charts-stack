> 🇩🇪 Deutsche Version. English: [README.en.md](README.en.md)

# charts-recommend

**Multi-User-Nachlade-Dienst:** lässt die **gemeinsame Library** aus dem
**öffentlichen Hörgeschmack mehrerer ListenBrainz-User** wachsen. Der Dienst
schreibt **keine Playlists** mehr – was noch nicht in der Library liegt, wird
über die bestehende charts-Kette automatisch nachgeladen. Die Personalisierung
(wer welche Songs sieht/hört) macht der Client (Navidrome/Substreamer) pro
Account selbst.

Das ist bewusst **multiuser-tauglich**: statt einer einzelnen Person ihre
Geschmacks-Playlists aufzuzwingen, fliessen die Lieblingssongs aller gelisteten
User in dieselbe Library – genau wie charts-sync/charts-hitparade neue Songs
hinzufügen.

Zwei Datenquellen (beide liefern Künstler + Titel **direkt**, kein
MBID-Resolving):

1. **ListenBrainz stats** (`stats/user/<user>/recordings`) – die meistgehörten
   Recordings eines Users. Sofort verfügbar, sobald gescrobbelt wird.
2. **ListenBrainz createdfor** (Weekly Jams / Weekly Exploration) – echte
   „Discovery", kann aber in den ersten 2–4 Wochen leer sein.

Optional zusätzlich **Last.fm** (globale Zusatz-Quelle des Betreibers, nicht pro
User): „ähnliche Songs" (`track.getSimilar`) oder Top-Tracks
(`user.getTopTracks`).

> Der CF-Empfehlungs-Endpoint (`cf/recommendation/user/<user>/recording`) wird
> **bewusst nicht** verwendet: er liefert nur rohe `recording_mbid` + Score,
> deren Namensauflösung MusicBrainz (1 req/s) oder einen fragilen
> Player-Endpoint bräuchte.

## Voraussetzung: nur ein öffentlicher Username

Zum Lesen eines Users brauchst du **nur dessen öffentlichen ListenBrainz-
Benutzernamen** – kein Token, kein Passwort. ListenBrainz-Profile sind öffentlich
lesbar.

- **Kein Passwort nötig.** Der ListenBrainz-**Hauptaccount** (z.B. `yourusername`)
  wird nur noch für seinen **Token** gebraucht – und zwar ausschliesslich zum
  **Anheben des Rate-Limits** beim Einlesen der gelisteten User. Das
  **Passwort** von ListenBrainz wird **nicht** mehr benötigt und steht in
  **keiner** `.env`.
- Der Token ist **optional**, aber empfohlen, wenn du **mehrere** User abfragst
  (höheres Rate-Limit).

Damit **überhaupt** Empfehlungen entstehen, braucht ein User natürlich
Hörverlauf bei ListenBrainz:

1. **ListenBrainz-Account** (über MusicBrainz-Login, kostenlos).
2. **Scrobbling → ListenBrainz aktivieren.** In Navidrome: Profil →
   „Scrobble to ListenBrainz" + persönlichen User-Token einfügen. Jeder
   Navidrome-User nutzt seinen **eigenen** Token in **seinem** Profil – dieser
   Dienst liest nur die **öffentlichen** Profile aller gelisteten User.
3. **createdfor braucht Zeit:** Weekly Jams/Exploration erzeugt ListenBrainz erst
   nach **2–4 Wochen** Hörverlauf. Die **stats**-Quelle hat dagegen sofort
   Daten.

Verifizieren, dass Scrobbles ankommen: `https://listenbrainz.org/user/<username>/`.

## Wie es funktioniert

```
ListenBrainz-User A ─┐
ListenBrainz-User B ─┼─► charts-recommend ─► missing-recommend.txt ─►
Last.fm (optional)  ─┘                        charts-download ─► slskd ─►
                                              charts-import ─► Navidrome
```

1. Baut einen Library-Index (identisches Matching wie charts-sync: tolerant,
   Künstler-Mindestscore, Compilation-Handling).
2. Iteriert über **alle** konfigurierten ListenBrainz-User × aktive Quellen:
   - **stats:** `GET /1/stats/user/<user>/recordings?range=…&count=…` → liefert
     `artist_name` + `track_name` direkt.
   - **createdfor:** `GET /1/user/<user>/playlists/createdfor` → wählt die
     Playlists per Titel (Weekly Jams / Weekly Exploration) → `GET /1/playlist/
     <mbid>` liefert die Tracks (Titel + Künstler) im JSPF-Format.
3. **Führt alle Ergebnisse zusammen und dedupliziert** (gleicher Song, den
   mehrere User mögen, wird nur **einmal** nachgeladen).
4. Matcht die zusammengeführte Liste **einmal** gegen die Library:
   - **vorhanden** → nichts zu tun (nur zählen),
   - **fehlt** → in **eine** kombinierte `missing-recommend.txt` (Format wie
     charts-sync).
5. Pushover-Meldung („X neue Songs zum Nachladen aus dem Hörgeschmack von N
   Usern").

Für `PUSHOVER_DEVICE` sind mehrere Geräte kommagetrennt möglich (z.B.
`iPhone-Max,Tablet-Anna`) — es wird pro Gerät eine eigene Nachricht
gesendet. Alle Geräte gehören zum selben Pushover-Account. Leeres Feld =
Zustellung an alle Geräte des Accounts. Ungültige/deaktivierte Gerätenamen
werden vor dem Senden automatisch entfernt — sonst stellt Pushover einen
unbekannten Namen ersatzweise an **alle** Geräte zu (Doppel-Push). Die dafür
geladene Geräteliste wird `PUSHOVER_DEVICES_TTL` Sekunden gecacht (Default
`3600`).

`PUSHOVER_SOUND` steuert den Benachrichtigungston dieses Dienstes
(Code-Default `falling`). Gültige Pushover-Töne: `pushover, magic, cosmic,
intermission, bike, bugle, cashregister, classical, falling, gamelan, incoming,
mechanical, pianobar, siren, spacealarm, tugboat, alien, climb, persistent,
echo, updown, none` (= still), `vibrate`. Leer = Account-Default (kein
`sound`-Feld). Über `PUSHOVER_SOUND_RECOMMEND` in der `compose.yml`
überschreibbar.

## Nachladen: Verzahnung mit charts-download

Wenn `WRITE_MISSING=1` (Default), landen die fehlenden Songs in **einer**
kombinierten Datei:

- `/charts/missing-recommend.txt`

Damit charts-download sie tatsächlich nachlädt, **muss diese eine Datei in dessen
`MISSING_FILES` stehen**. In der zentralen `.env` ist das vorausgefüllt, z.B.:

```
MISSING_FILES=/charts/missing.txt;/charts/missing-mundart.txt;/charts/missing-80er-90er.txt;/charts/missing-hitparade.txt;/charts/missing-recommend.txt
```

Danach charts-download neu starten. Der Rest läuft automatisch: laden →
einsortieren → die Songs wachsen in die gemeinsame Library.

> Möchtest du **kein** automatisches Nachladen (nur zeigen, was schon da ist),
> setze `WRITE_MISSING=0`.

> **Altlasten aufräumen:** alte `missing-recommend-jams.txt`,
> `missing-recommend-exploration.txt`, `missing-recommend-lastfm.txt` sowie alte
> `recommend-*.m3u`-Dateien in `/charts` bzw. `MUSIC_DIR` dürfen manuell gelöscht
> werden – der Dienst schreibt/liest sie nicht mehr. Vorhandene `.m3u` blieben
> sonst in Navidrome als verwaiste Playlists stehen.

## Setup

### 1. Konfiguration

```bash
cp .env.example .env
# RECOMMEND_LISTENBRAINZ_USERS setzen (Komma-Liste öffentlicher Usernamen).
# LISTENBRAINZ_TOKEN optional (nur Rate-Limit). Last.fm optional.
```

| Variable | Default | Bedeutung |
|----------|---------|-----------|
| `RECOMMEND_LISTENBRAINZ_USERS` | – | Komma-Liste öffentlicher ListenBrainz-Usernamen (kein Secret). Leer = Feature aus. |
| `LISTENBRAINZ_USER` | – | Backward-Compat: alter Einzelwert, Fallback wenn die Liste leer ist |
| `RECOMMEND_SOURCES` | `stats,createdfor` | aktive Quellen (`stats` und/oder `createdfor`) |
| `RECOMMEND_STATS_RANGE` | `month` | Zeitraum der stats-Quelle (`week`…`all_time`) |
| `LISTENBRAINZ_TOKEN` | – | optional, nur zum Anheben des Rate-Limits – **KEIN Passwort** |
| `LISTENBRAINZ_KINDS` | `jams,exploration` | welche createdfor-Playlists |
| `LASTFM_ENABLED` | `0` | Last.fm als optionale Zusatz-Quelle |
| `LASTFM_MODE` | `similar` | `similar` oder `top` |
| `RECOMMEND_LIMIT` | `100` | max. Tracks pro User und Quelle |
| `WRITE_MISSING` | `1` | fehlende Songs nachladen |
| `INTERVAL_HOURS` | `24` | Lauf-Intervall |
| `HTTP_TIMEOUT` | `30` | Timeout pro HTTP-Request (Sekunden) |
| `HTTP_RETRIES` | `3` | Versuche je Request bei Transport-Fehlern |
| `NOTIFY_ONLY_ON_CHANGE` | `1` | Fehler-Alarm nur bei Zustandswechsel |

> In der compose/`.env` heissen die drei letzten Werte
> `RECOMMEND_HTTP_TIMEOUT`, `RECOMMEND_HTTP_RETRIES` und
> `RECOMMEND_NOTIFY_ONLY_ON_CHANGE`.

## Robustheit & Fehler-Sichtbarkeit

Schlägt der Abruf eines Users/einer Quelle fehl (Timeout, HTTP- oder
Verbindungsfehler), wird jeder Request bis `HTTP_RETRIES`-mal mit kurzem Backoff
wiederholt. Erst wenn **alle** Abrufe (alle User × alle Quellen) nach den
Wiederholungen scheitern, meldet der Dienst `status="error"` – das Dashboard
färbt die Kachel rot und zeigt die Fehlermeldung, und es geht ein
**Pushover-Alarm** raus. Liefert mindestens eine User/Quelle noch Ergebnisse,
bleibt der Lauf `ok`, nennt die gestörte Quelle aber in der Meldung.

Wichtig: ein **leeres, aber erfolgreiches** Ergebnis (createdfor noch nicht
erzeugt, oder ein frischer User liefert HTTP 404) ist **kein** Fehler und bleibt
`ok` – nur ein echter Transport-Fehler löst Alarm aus. Ist **kein** User
konfiguriert (leere Liste, Feature aus), läuft der Dienst idle weiter („nichts
zu tun"), ebenfalls kein Fehler. Mit `NOTIFY_ONLY_ON_CHANGE=1` (Default) kommt
der Alarm nur beim ersten fehlgeschlagenen Lauf (kein täglicher Spam) und eine
Entwarnung, sobald der Abruf wieder klappt. Ohne Pushover-Config bleibt alles
still (nur Log + Dashboard).

### 2. Deploy (auf `your-nas`)

Code nach `/path/to/charts-recommend/` kopieren, dann:

```bash
sudo docker compose up -d --build
```

### 3. charts-download verdrahten

`MISSING_FILES` in der zentralen `.env` um die eine `missing-recommend.txt`
ergänzen (siehe oben) und charts-download neu starten.

### 4. Beobachten

```bash
sudo docker compose logs -f charts-recommend
```

Solange ListenBrainz für die createdfor-Quelle noch nichts generiert hat, meldet
der Dienst „noch keine createdfor-Playlists" – die **stats**-Quelle liefert
trotzdem Daten, sobald gescrobbelt wurde. Zum sofortigen Testen kannst du
zusätzlich **Last.fm** aktivieren (`LASTFM_ENABLED=1` + API-Key, ggf.
`LASTFM_SEEDS` oder `LASTFM_USER`).

## Container

| Container | Zweck |
|-----------|-------|
| `charts-recommend` | Scheduler-Loop: Empfehlungen mehrerer User holen, zusammenführen, matchen, Nachlade-Liste schreiben. Kein Webserver. |

Nutzt bewusst dieselben (kopierten) Bausteine wie die Schwester-Dienste
(`library.py`/`notify.py` aus charts-sync) – keine Laufzeit-Abhängigkeit
zwischen den Containern.

## Daten & Pfade

| Host-Pfad | Zweck |
|-----------|-------|
| `/volume2/music/Music` | Library – nur **gescannte** Quelle (read-only), es werden keine Playlists mehr geschrieben |
| `/volume2/music/Charts` | geteilter Ordner: `missing-recommend.txt` |

## Betrieb

```bash
sudo docker compose logs -f charts-recommend   # Logs
sudo docker compose up -d --build              # neu bauen/starten
sudo docker compose down                       # stoppen
```
