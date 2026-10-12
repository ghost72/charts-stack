> 🇩🇪 Deutsche Version. English: [README.en.md](README.en.md)

# charts-songrequest

Kleine **Web-UI**, um einzelne Songs manuell anzufordern: Künstler + Titel
eingeben → der Song wird in slskd gesucht, das beste **MP3 ≥ 256 kbps** geladen
und automatisch in die Library einsortiert. Kein Datei-Verschieben, kein
Terminal – ideal auch von unterwegs (hinter dem Reverse Proxy).

Vierter Dienst der `charts-*`-Familie neben
[`charts-sync`](../charts-sync), [`charts-download`](../charts-download) und
[`charts-import`](../charts-import).

---

## Wie es funktioniert

```
Web-UI (Künstler + Titel)
      │
      ▼
charts-songrequest ─► sucht via slskd (gleiche Filter wie charts-download:
      │                MP3 ≥ 256, keine Remixe/Live/Compilations)
      │             ─► stösst besten Treffer als Download an
      │             ─► schreibt Manifest-Eintrag (source="manuell")
      ▼
slskd ───────────────► lädt nach /volume2/music/Charts/
      ▼
charts-import ───────► holt die Datei (Manifest-Treffer!) automatisch in die
                       Library, taggt + Cover, meldet "1 aus manuell"
```

Der Trick: charts-songrequest schreibt in **dasselbe Manifest**, das auch
`charts-download` schreibt und `charts-import` liest. Dadurch greift die
komplette bestehende Import-Pipeline (Tags, Cover, Lidarr-Unmonitor, Navidrome)
ganz ohne Zusatzlogik. Weil `REQUIRE_MANIFEST=1` in charts-import gilt, wird der
manuelle Song **gezielt** importiert (nicht als wahlloser Downloads-Ordner-Fund).

### Automatischer Qualitätsvergleich
charts-songrequest sucht **immer** in slskd und vergleicht die beste gefundene
Bitrate mit der ggf. schon vorhandenen Version in der Library. Drei Fälle:

- **Nicht vorhanden** → laden (normaler Neuzugang).
- **Vorhanden, aber schlechtere Qualität** → die bessere Version wird geladen
  und **ersetzt** die vorhandene (Upgrade). UI: „⬆️ Bessere Version wird
  geladen (320 statt 256 kbps)".
- **Vorhanden in gleicher/besserer Qualität** → **nichts** wird geladen. Die UI
  zeigt die tatsächliche Backend-Meldung: entweder „ℹ️ Schon in guter Qualität
  vorhanden (X kbps)" ODER – wenn die vorhandene Version selbst unter
  `MIN_BITRATE` liegt und im Netz gerade nichts Besseres auftaucht – ehrlich
  „ℹ️ Vorhanden mit X kbps, aktuell keine bessere Version gefunden".

Dafür wird die Navidrome-Library **read-only** gemountet (`/music/Music:ro`);
die Bitrate der vorhandenen Datei liest `mutagen`. Die Song-Erkennung ist
identisch zu charts-import (normalisierter Titel, case-insensitiver Artist).

### Retry über mehrere Anbieter
Soulseek ist P2P: Der beste Treffer kann von einem Anbieter (Endnutzer, der die
Datei teilt) stammen, der gerade offline ist oder den Upload ablehnt. Darum
sucht charts-songrequest nicht nur den einen besten Treffer, sondern hält alle
passenden Kandidaten (nach Qualität sortiert) bereit und stößt sie **der Reihe
nach** an, bis ein Anbieter den Download wirklich annimmt. Beim Upgrade werden
dabei nur Kandidaten berücksichtigt, die echt besser sind als die vorhandene
Version. Erst wenn kein einziger Anbieter liefert, meldet die UI einen Fehler.

**Upgrade-Mechanik:** Bei einem Upgrade schreibt charts-songrequest ein
`"replace": true` in den Manifest-Eintrag. charts-import ersetzt daraufhin die
vorhandene Datei **nur wenn** die neue Bitrate wirklich höher ist – die alte
Version wandert dabei in einen Papierkorb (`MUSIC_DIR/.charts-trash/<Datum>/`,
reversibel), die neue landet an derselben Stelle (keine Dublette).

### Pushover-Meldung erst bei Verfügbarkeit
charts-songrequest selbst sendet **keine** Pushover-Meldung mehr beim Anfordern
(der Download-Start steht ohnehin direkt in der Web-UI). Stattdessen bekommt der
anfordernde Benutzer **eine** Meldung an **sein eigenes Gerät**, sobald der Song
tatsächlich in der Library ist – gesendet von `charts-import` nach dem Import
(„🎵 Dein Song ist da!" mit Künstler, Titel, Album, Quelle).

Dafür wird pro Benutzer ein Pushover-Gerät hinterlegt (`USER_PUSHOVER_DEVICES`,
Format `user:Gerät`), das auf den **Login-Benutzernamen** matcht. Beim Anfordern
schreibt charts-songrequest dieses Gerät als `notify_device` ins Manifest;
charts-import liest es und meldet die Verfügbarkeit an genau dieses Gerät.

**Datenschutz/Sicherheit:** Ist ein Benutzer **nicht** in `USER_PUSHOVER_DEVICES`
gelistet (oder die Variable leer), wird **keine** Meldung gesendet – nie an alle
Geräte des Accounts. Es gibt bewusst keine Geräte-Auswahlliste in der UI.

---

## Zugriffsschutz

Die Seite ist über den Reverse Proxy von aussen erreichbar, daher ist ein Login
**Pflicht**. Es gibt **Mehrbenutzer-Support**:

- `SONGREQUEST_USERS="user1:pass1,user2:pass2"` – beliebig viele Benutzer
  (Komma-getrennt; Passwort darf kein `:` oder `,` enthalten). Dies ist der
  einzige Login-Weg (reiner Mehrbenutzer-Login).

Mindestens ein Benutzer muss definiert sein,
sonst startet der Container nicht. Der angemeldete Benutzer erscheint im Log und
bestimmt (über `USER_PUSHOVER_DEVICES`) das Zielgerät der Verfügbarkeits-Meldung.
Login per Cookie-Session;
`SESSION_SECRET` setzen, damit Sessions Neustarts überleben.

---

## Installation auf dem Synology NAS

charts-songrequest ist Teil des charts-stack und wird **nicht** einzeln
installiert, sondern über das gemeinsame Stack-Projekt (eine `compose.yml` +
eine zentrale `.env` im `charts-stack`-Ordner). Vollständige Installation siehe
Haupt-README. Nur diesen Dienst neu starten/bauen:
`docker compose up -d --build charts-songrequest` aus dem `charts-stack`-Ordner.

1. **Dateien aufs NAS kopieren** (`app/`, `Dockerfile`, `compose.yml`,
   `requirements.txt`, `.env`).

2. **`.env` anlegen:**
   ```bash
   cp .env.example .env
   # SLSKD_API_KEY (wie charts-download), SONGREQUEST_USERS und SESSION_SECRET setzen
   #   openssl rand -hex 32   → für SESSION_SECRET
   ```

3. **Bauen und starten:**
   ```bash
   sudo docker compose up -d --build
   sudo docker compose logs -f
   ```

4. **Zugriff – LAN vs. Internet:** Standardmäßig läuft die App nur im lokalen
   Netz unter `http://<NAS-IP>:5056`. Das reicht, wenn du Songs nur von zu
   Hause anforderst.

   **Für den Zugriff von unterwegs (aus dem Internet)** braucht es zusätzlich:
   - einen **Reverse Proxy** mit HTTPS, der eine Subdomain (z. B.
     `songs.example.com`) auf `http://<NAS-IP>:5056` weiterleitet (kein
     WebSocket nötig), mit einem Let's-Encrypt-Zertifikat für die Subdomain;
   - eine **Portfreigabe/DNS**, damit die Subdomain von außen erreichbar ist
     (Router-Portforwarding auf den Reverse Proxy bzw. DynDNS).

   > ⚠️ **Sicherheit:** Sobald die App über das Internet erreichbar ist, kann
   > sie jeder aufrufen. Deshalb ist `SONGREQUEST_USERS` **Pflicht** (die App
   > startet ohne nicht) und HTTPS zwingend. Setze starke Passwörter. Wer keinen
   > Zugriff von außen braucht, lässt den Reverse Proxy weg und nutzt nur das LAN.

5. Im Browser die URL öffnen → mit Benutzer/Passwort aus `SONGREQUEST_USERS` anmelden → Song anfordern.

---

## Konfiguration (`.env`)

| Variable            | Default | Bedeutung |
|---------------------|---------|-----------|
| `SLSKD_HOST`        | `http://slskd:5030` | slskd-URL (Containername im selben Docker-Netz). |
| `SLSKD_API_KEY`     | *(leer, Pflicht)* | readwrite-Key aus slskd.yml (derselbe wie charts-download ok). |
| `SONGREQUEST_USERS` | *(leer, Pflicht)* | Mehrbenutzer-Login: `user1:pass1,user2:pass2`. Einziger Login-Weg. |
| `SESSION_SECRET`    | *(leer)* | Zufallsstring für Session-Cookies. Leer = nach Neustart neu anmelden. |
| `MIN_BITRATE`       | `256` | Minimale MP3-Bitrate. |
| `MATCH_THRESHOLD`   | `80` | Ähnlichkeit 0-100 zwischen „Artist Titel" und Dateiname. |
| `REQUIRE_FREE_SLOT` | `1` | Nur Anbieter mit freiem Upload-Slot. |
| `EXCLUDE_KEYWORDS`  | *(Standardliste)* | Nur Originalversionen (keine Remixe/Live/Lyrics …). `off`/`none` = aus. |
| `REJECT_COMPILATIONS`| `1` | Sampler/Compilations ablehnen (echte Single/Album-Version bevorzugen). |
| `MANIFEST_FILE`     | `/charts/.charts-manifest.json` | **Muss dieselbe Datei sein wie bei charts-import/-download.** |
| `MANUAL_SOURCE_LABEL`| `manuell` | Label in der charts-import-Meldung. |
| `SONGREQUEST_PORT`  | `5056` | Web-UI-Port. |
| `PUID` / `PGID`     | `1027` / `100` | User/Gruppe (youruser:users). |
| `PUSHOVER_*`        | *(leer)* | Pushover-Account (Token/User). charts-songrequest sendet selbst nichts mehr; die Verfügbarkeits-Meldung kommt von charts-import. |
| `USER_PUSHOVER_DEVICES` | *(leer)* | Pushover-Gerät pro Benutzer (`user1:Gerät1,user2:Gerät2`), matcht auf den Login-Namen. Nur gelistete User bekommen die Verfügbarkeits-Meldung an ihr Gerät. Nicht gelistet = keine Meldung. |
| `PUSHOVER_DEVICE`   | *(leer)* | Optional: Ziel-Gerät. Mehrere Geräte kommagetrennt möglich (z.B. `iPhone-Max,Tablet-Anna`) — es wird pro Gerät eine eigene Nachricht gesendet. Alle Geräte gehören zum selben Pushover-Account. Leeres Feld = Zustellung an alle Geräte des Accounts. Ungültige/deaktivierte Gerätenamen werden vor dem Senden automatisch entfernt — sonst stellt Pushover einen unbekannten Namen ersatzweise an **alle** Geräte zu (Doppel-Push). |
| `PUSHOVER_SOUND` | `magic` | Benachrichtigungston (Code-Default). **Hinweis:** aktuell wirkungslos — charts-songrequest sendet selbst **keine** Pushover-Meldung; die Verfügbarkeits-Meldung („🎵 Dein Song ist da!") kommt von **charts-import** und nutzt dort `PUSHOVER_SOUND_MANUAL` (= `magic`). Die Variable bleibt der Einheitlichkeit halber vorhanden und greift erst, falls songrequest später selbst sendet. Gültige Pushover-Töne: `pushover, magic, cosmic, intermission, bike, bugle, cashregister, classical, falling, gamelan, incoming, mechanical, pianobar, siren, spacealarm, tugboat, alien, climb, persistent, echo, updown, none` (= still), `vibrate`. Über `PUSHOVER_SOUND_SONGREQUEST` in der `compose.yml` überschreibbar. |
| `PUSHOVER_DEVICES_TTL` | `3600` | Optional: Cache-Dauer (Sekunden) für die zum Filtern geladene Geräteliste des Accounts. |

---

## Endpoints

| Pfad            | Zweck |
|-----------------|-------|
| `GET /`         | Web-UI (nach Login). |
| `GET /login`    | Login-Seite. |
| `POST /login`   | Anmeldung (Passwort). |
| `GET /logout`   | Abmelden. |
| `POST /api/request` | Song anfordern (Form: `artist`, `title`). |
| `GET /health`   | Health-Check. |

---

## Grenzen

- slskd erlaubt **kein** eigenes Download-Zielverzeichnis pro Download
  (offener Feature-Request upstream). Deshalb dieser Weg über das Manifest
  statt eines separaten Ordners.
- Findet slskd keine passende Version (MP3 ≥ Bitrate, kein Remix/Sampler),
  meldet die UI „kein Treffer" – dann Schreibweise prüfen oder später erneut.
- Ergebnisqualität hängt vom Soulseek-Netz ab (wie bei charts-download).
