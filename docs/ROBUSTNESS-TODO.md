# Robustheit / Fehlersichtbarkeit – offene Folgeaufgaben (Planungsnotiz)

Planungsnotizen, kein umgesetztes Feature. Festgehalten, damit die beim
charts-recommend-Robustheits-Umbau bewusst aufgeschobenen Punkte nicht im
Chat-Verlauf verschwinden.

Kontext: charts-recommend wurde robuster gemacht (konfigurierbares Timeout +
Retry, Transport-Fehler sichtbar als `status=error` + Pushover **nur bei
Statuswechsel**, Dashboard-Kachel rot bei Fehler). Zwei Punkte blieben bewusst
offen.

## 1. Partial-Success-Pushover nicht wechsel-gated (LOW)

Aus dem Code-Review der charts-recommend-Robustheit:

- Der „nur bei Statuswechsel"-Schutz greift sauber beim **Komplettausfall**
  (alle Quellen weg → genau eine Alarm-Meldung, dann Ruhe, genau eine
  Erholungs-Meldung).
- Beim **Teilerfolg** (eine Quelle hängt, die andere liefert) ist die
  Alarm-Meldung **nicht** wechsel-gated → könnte theoretisch alle 24 h erneut
  feuern, solange eine Quelle hängt und die andere liefert.

**Aktuell irrelevant**, weil nur ListenBrainz aktiv ist (eine Quelle). **Wird
relevant, sobald Last.fm dazukommt** (`LASTFM_ENABLED=1`). Dann sollte die
Partial-Success-Alarmierung ebenfalls über den Status-Wechsel gegated werden,
analog zur bestehenden Komplettausfall-Logik.

→ **Fix mitnehmen, wenn Last.fm aktiviert wird.**

## 2. Stack-weite Fehlersichtbarkeit für charts-sync + charts-download

Dieselbe „verschluckt Fehler still"-Logik wie bei charts-recommend existiert
auch in **charts-sync** und **charts-download** (ein Deezer-/Quell-Ausfall bzw.
ein fehlgeschlagener Download flippt den Dienst heute **nicht** auf
`status=error`). Beim recommend-Umbau wurde das **bewusst NICHT mitgefixt**.

**Begründung (wichtig – nicht einfach 1:1 kopieren):** Das sind
**Multi-Quellen-Dienste**. Dass *eine* von vielen Deezer-Playlists / Last.fm-Tags
/ Soulseek-Downloads mal nicht antwortet, ist dort **Normalbetrieb**, kein
Alarm. Würde man „jeder Quellfehler = status=error + Pushover" einbauen,
klingelt das Handy ständig wegen Lappalien → Alarm-Müdigkeit, und echte
Meldungen gehen unter.

**Sinnvolle, LEISERE Robustheit für diese Dienste** (eigener Umbau, nicht das
recommend-Muster):

- Retry/Timeout auf die einzelnen Quell-Calls (C-artig, wie bei recommend).
- Einen Fehler nur melden, wenn **ein ganzer Lauf** scheitert ODER eine Quelle
  **über mehrere Läufe in Folge** dauerhaft tot ist – **nicht** beim ersten
  Zucken.
- `NOTIFY_ONLY_ON_CHANGE`-Muster (im Stack vorhanden) konsequent nutzen.

→ **Eigenes Thema**, bewusst getrennt vom recommend-Fix, um die Fehlalarm-Falle
zu vermeiden.
