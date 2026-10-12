> 🇩🇪 Deutsche Version. English: [README.en.md](README.en.md)

# charts-digest

Schickt einmal täglich eine **Pushover-Zusammenfassung** der Songs, die in den
letzten 24 h neu in die Library einsortiert wurden – gruppiert nach Quelle
(Aktuelle Charts, Schweizer Mundart, FLASHBACK FM, manuell angefordert …).

Reiner Melde-/Report-Dienst der `charts-*`-Familie (wie
[`charts-watchdog`](../charts-watchdog) und [`charts-dashboard`](../charts-dashboard)) –
er fasst **keine** Audiodateien an und lädt nichts nach.

## Wie es funktioniert

```
charts-import  ──schreibt pro einsortiertem Song──►  .charts-imported-log.jsonl
                                                              │
charts-digest  ──liest Log, filtert auf 24 h, gruppiert──────┘
      │
      ▼
  Pushover: "🎉 N neue Songs in deiner Library" (nach Quelle)
```

1. `charts-import` schreibt bei **jedem** einsortierten Song eine Zeile ins
   Import-Log (`.charts-imported-log.jsonl` im Charts-Ordner):
   `{ts, source, artist, title}`.
2. `charts-digest` wartet bis zum Zielzeitpunkt (Default **09:00**), liest das
   Log, filtert auf das Rückblick-Fenster (Default **24 h** = Vortag) und
   gruppiert die Songs nach Quelle.
3. Es sendet eine formatierte Pushover-Meldung mit Emoji je Quelle. Gab es
   nichts Neues, bleibt es standardmäßig still (kein „0 Songs"-Spam).
4. Danach wird das Log kompakt neu geschrieben (Einträge älter als
   `LOG_RETENTION_DAYS` werden verworfen), damit es nicht unbegrenzt wächst.

**Vertrauenswürdige Datengrundlage:** Der Dienst rät **nicht** über
Datei-Zeitstempel (mtime) oder Titel – er nutzt ausschließlich das, was
`charts-import` beim Import tatsächlich protokolliert hat (korrekte Quelle,
kanonischer Name). Ein nachträgliches Retaggen/Cover-Nachrüsten verfälscht die
Zusammenfassung also nicht.

## Manuell auslösen (jederzeit)

Unabhängig vom täglichen Zeitplan lässt sich die Zusammenfassung sofort
anstoßen (sendet auch bei 0 neuen Songs eine ehrliche Rückmeldung):

```bash
docker exec charts-digest python -m app.main --now
```

## Konfiguration (zentrale `.env`)

| Variable | Default | Bedeutung |
|----------|---------|-----------|
| `DIGEST_HOUR` | `9` | Uhrzeit (Stunde, lokale Zeit) der täglichen Meldung. |
| `DIGEST_MINUTE` | `0` | Minute der täglichen Meldung. |
| `DIGEST_WINDOW_HOURS` | `24` | Rückblick-Fenster in Stunden (24 = seit gestern zur selben Zeit). |
| `DIGEST_MAX_PER_SOURCE` | `100` | Sicherheitsnetz je Quelle; der echte Deckel ist Pushovers 1024-Zeichen-Limit (lange Titel werden am Ende gekürzt). Rest als „… und N weitere“. |
| `DIGEST_NOTIFY_WHEN_EMPTY` | `0` | Auch bei 0 neuen Songs eine Meldung senden (`1`) oder still bleiben (`0`). |
| `DIGEST_LOG_RETENTION_DAYS` | `7` | Import-Log-Einträge älter als so viele Tage werden beim Lauf verworfen. |
| `DIGEST_RUN_ON_START` | `0` | Beim Container-Start sofort eine Zusammenfassung senden (nur zum Testen). |
| `PUSHOVER_TOKEN` / `PUSHOVER_USER` | – | Pushover-Zugang (ohne bleibt der Dienst still, nur Log). |
| `PUSHOVER_DEVICE` | – | Optionales Zielgerät (kommagetrennt möglich). |
| `PUSHOVER_SOUND` | `magic` | Benachrichtigungston für die Tages-Zusammenfassung. Gültige Pushover-Töne: `pushover, magic, cosmic, intermission, bike, bugle, cashregister, classical, falling, gamelan, incoming, mechanical, pianobar, siren, spacealarm, tugboat, alien, climb, persistent, echo, updown, none` (= still), `vibrate`. Leer = Account-Default. Über `PUSHOVER_SOUND_DIGEST` in der `compose.yml` überschreibbar. |

> **Pushover einrichten (optional).** Benachrichtigungen sind komplett optional –
> ohne `PUSHOVER_TOKEN`/`PUSHOVER_USER` bleibt der Dienst still (nur Log). Du
> brauchst **zwei** Werte aus deinem Pushover-Account
> ([pushover.net](https://pushover.net)):
> 1. `PUSHOVER_USER` – dein persönlicher **User-Key** (nach dem Login oben im
>    Dashboard sichtbar).
> 2. `PUSHOVER_TOKEN` – das **API-Token einer eigenen Application**, die du
>    einmalig unter „Create an Application/API Token" anlegst.
>
> Der **Absendername** der Nachricht ist der **Name dieser Application** –
> änderbar nur im Pushover-Web (Application bearbeiten), nicht über eine Variable
> hier. Benennst du die App um, kommen neue Nachrichten unter dem neuen Namen.

## Container

| Container | Zweck |
|-----------|-------|
| `charts-digest` | Scheduler-Loop: wartet bis zur Zielzeit, sendet die Zusammenfassung. Kein Webserver. |

Nutzt dieselben kopierten Bausteine wie die Schwester-Dienste
(`notify.py`/`status.py`) – keine Laufzeit-Abhängigkeit.

## Daten & Pfade

| Host-Pfad | Zweck |
|-----------|-------|
| `/volume2/music/Charts` | Import-Log (`.charts-imported-log.jsonl`) + Status (`.charts-status/`) |

Es wird **kein** Music-Ordner gemountet – der Dienst liest nur das Log.

## Betrieb

```bash
docker compose up -d --build charts-digest   # bauen + starten
docker logs -f charts-digest                  # Scheduler/Läufe beobachten
docker exec charts-digest python -m app.main --now   # sofort senden
```

Der Dienst erscheint auf dem **charts-dashboard** in der Überwachungs-Zone
(Status, letzter Lauf, nächster geplanter Lauf, kumulierte Zusammenfassungen).

## Rechtlicher Hinweis

Reiner Benachrichtigungsdienst – lädt oder verändert keine Inhalte. Siehe die
Haupt-[README](../README.md) für den rechtlichen Rahmen des gesamten Stacks.
