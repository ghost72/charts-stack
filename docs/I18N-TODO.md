# i18n – Mehrsprachige Log- & Pushover-Meldungen (Planungsnotiz)

> **i18n** = „internationalization" (i + 18 Buchstaben + n). Software so bauen,
> dass ihre Texte in mehreren Sprachen ausgegeben werden können, ohne den Code
> zu ändern. Verwandt: **l10n** = „localization" (die konkrete Übersetzung).

Dies ist eine **Planungsnotiz**, kein umgesetztes Feature. Festgehalten, damit das
Konzept nicht verloren geht, wenn charts-stack öffentlich wird und
Übersetzungs-Wünsche aufkommen.

## Problem / Ausgangslage

Aktuell sind **alle** Log-Meldungen und Pushover-Nachrichten in den
Verarbeitungs-Diensten fest auf **Deutsch** im Python-Code hartcodiert, z. B.:

```python
logger.info("Scanne Library: %s", music_dir)
logger.info("%d von %d Chart-Tracks in Library gefunden", found, total)
```

Für ein internationales Open-Source-Projekt ist das eine Hürde: Nicht-deutsche
Nutzer verstehen die Logs/Meldungen nicht, und es gibt keinen Weg, die Sprache
umzustellen.

## Ziel-Konzept: Sprachdateien + Auto-Erkennung

Angelehnt an etablierte Projekte (Jellyfin/Navidrome): Texte raus aus dem Code,
rein in **Sprachdateien**, die die Community übersetzen kann – ohne Code-Änderung.

- **Sprachdateien** pro Sprache, z. B. als JSON im Ordner `lang/`:
  ```
  lang/en.json   (Default / Fallback)
  lang/de.json
  lang/fr.json   (später, Community)
  ...
  ```
  Inhalt = Schlüssel → Text mit Platzhaltern:
  ```json
  {
    "library.scan": "Scanning library: {dir}",
    "charts.found": "{found} of {total} chart tracks found in library"
  }
  ```

- **Im Code** nur noch Schlüssel statt Texte:
  ```python
  log.info(t("library.scan", dir=music_dir))
  log.info(t("charts.found", found=found, total=total))
  ```

- **Sprachauswahl NICHT hartcodiert.** Ein kleiner i18n-Loader:
  1. liest beim Start, welche `lang/*.json` existieren (Prefix = Sprachcode),
  2. wählt die Datei anhand der Env-Variable `LANG` (z. B. `LANG=fr`),
  3. fällt auf **Englisch** zurück, wenn `LANG` nicht gesetzt ist oder die Datei
     fehlt, und auf den Schlüssel-Namen, wenn ein Text in der Datei fehlt.

  → Neue Sprache hinzufügen = nur eine neue `lang/<code>.json` dazulegen und
  `LANG=<code>` setzen. Kein Code-Eingriff, kein Rebuild der Logik.

- **Default = Englisch.** `en.json` ist die Referenz-/Fallback-Sprache; `de.json`
  ist die aktuelle (zu extrahierende) deutsche Variante.

- **Env-Schalter** in der zentralen `.env`:
  ```
  # Sprache der Log-/Pushover-Meldungen. Vorhandene lang/<code>.json nutzen.
  # Leer/unbekannt -> en (Default).
  LANG=de
  ```

## Umfang (ehrliche Aufwandsschätzung)

Das ist ein **großes Refactoring über alle 6 Dienste**, kein Nebenbei-Task:

1. i18n-Loader-Modul bauen (lang-Dateien finden, laden, `t(key, **kwargs)`,
   Fallback-Logik). Idealerweise als geteiltes Modul, das jeder Dienst einbindet.
2. In **jedem** Dienst alle `logger.*()`- und Pushover-Texte durch `t("key", ...)`
   ersetzen (viele Dutzend Meldungen pro Dienst).
3. Alle Texte nach `lang/en.json` + `lang/de.json` extrahieren.
4. `LANG`-Variable in compose + `.env.example` ergänzen.
5. **Jeden Dienst einzeln testen** (dass alle Meldungen noch korrekt rendern,
   Platzhalter stimmen, Fallback greift).

Risiko: Jede geänderte Logzeile ist eine potenzielle Fehlerquelle. Die Logik der
Dienste bleibt unberührt – aber die schiere Menge der Textstellen macht es
aufwändig und testintensiv.

## Empfohlenes Vorgehen (wenn es so weit ist)

- **Nicht** alle 6 Dienste auf einmal. **Einen Pilot-Dienst** (z. B. das kleine
  `charts-recommend`) komplett umstellen, Muster + Loader etablieren, testen.
- Dann die übrigen Dienste nach demselben Muster, einzeln, mit Test je Dienst.
- Erst angehen, wenn der Stack produktiv **stabil** läuft (nicht während eines
  frischen Deployments).
- Reihenfolge nach Veröffentlichung: zuerst `en.json` sauber (Default), dann
  `de.json`, weitere Sprachen als Community-Beiträge.

## Status — UMGESETZT (abgeschlossen)

Das Konzept ist umgesetzt. Zwei Abweichungen gegenüber der ursprünglichen
Planungsnotiz oben, bewusst entschieden:

- **Env-Variable heißt `CHARTS_LANG`, nicht `LANG`.** `LANG` ist eine
  Standard-Unix-Locale-Variable und hätte mit dem OS/Python-Locale kollidiert.
- **Sprachdateien liegen ZENTRAL** in `charts-stack/lang/{en,de,fr}.json`
  (eine Datei je Sprache, geteilt über alle Dienste, read-only nach `/lang`
  gemountet) – NICHT pro Dienst. Analog zur zentralen `.env`. Jeder Dienst hat
  eine byte-identische Kopie des Loaders (`app/i18n.py` bzw. die sh-Variante im
  watchdog), aber alle lesen denselben zentralen Sprachsatz.
- **Pilot war charts-sync** (nicht charts-recommend) – zentraler Dienst mit
  genug Textvielfalt, um das Muster zu tragen.

- [x] i18n-Loader-Modul (`app/i18n.py`, stdlib-only, `t()`, Fallback
      gewählt→en→Key, crash-sicher; Shell-Variante `t()`/`_lookup` im watchdog)
- [x] `lang/en.json` + `lang/de.json` + **`lang/fr.json`** extrahiert
      (en=Default/Fallback, de byte-genau wie der frühere Hardcode, fr neu);
      zentral, 701 Schlüssel je Sprache, Parität per Test abgesichert
- [x] charts-sync (Pilot) umgestellt + getestet
- [x] charts-download / -import / -upgrade / -songrequest / -recommend /
      -flashback / -hitparade / -digest umgestellt (je Review APPROVED)
- [x] charts-watchdog (Shell-Skript: Pushover-/Log-Texte, eigener sh-i18n) +
      charts-dashboard (Backend-Meldungen + UI-Texte in index.html, Server
      injiziert die Sprache + die `dashboard.ui.*`-Teilmenge) umgestellt
- [x] **`CHARTS_LANG`**-Schalter in `compose.yml` (alle Dienste) + `.env.example`
- [x] READMEs um den `CHARTS_LANG`-Schalter + „eigene Sprache beitragen"-Abschnitt
      ergänzt (`charts-stack/README.md` DE+EN, je Dienst-README)

Offene, bewusst nicht blockierende Punkte:
- Weitere Sprachen (Community-Beiträge) kommen durch Ablegen einer neuen
  `lang/<code>.json` dazu – kein Code-Eingriff nötig.
- charts-songrequest hat eine statische Login-HTML-Seite, deren Gerüst noch
  deutsch ist (nur die Fehlerzeile ist übersetzt) – als Folgemöglichkeit über
  denselben Dashboard-Frontend-Mechanismus notiert, bewusst nicht Teil dieser
  Runde.
- charts-watchdog: BusyBox-echter Container-Test (statt nur dash) steht noch
  aus; beim Deploy im echten Image gegenprüfen.

---

# Zusätzliches TODO: Doku-Links im Dashboard (zusammen mit i18n angehen)

Idee: In jeder Dienst-Box im charts-dashboard ein dezentes Link-Icon (z. B. „📖"),
das in einem **neuen Tab** die gerenderte README des Dienstes auf **GitHub**
öffnet (nicht die rohe `.md` lokal – die wäre unformatiert und liegt nicht im
Dashboard-Container).

Warum zusammen mit i18n / erst wenn das Repo öffentlich ist:
- Sinnvoll erst, wenn das Repo auf GitHub veröffentlicht ist (dann sind die
  READMEs dort gerendert + erreichbar).
- Die Links sind sprachabhängig (README.md vs. README.en.md) → passt thematisch
  zum i18n-Schritt, dann in einem Rutsch sauber lösen.

Umsetzung (wenn es so weit ist):
- Basis-URL als Env, z. B. `DASHBOARD_DOCS_BASE=https://github.com/<user>/charts-stack/tree/main`.
- Pro Dienst im Frontend: `${DOCS_BASE}/<dienst>/README.md` (bzw. `.en.md` je
  nach gewählter Sprache), `target="_blank" rel="noopener"`.
- Leer lassen -> keine Links anzeigen (Default, solange kein öffentliches Repo).

## Status (Doku-Links)

- [ ] `DASHBOARD_DOCS_BASE`-Env (leer = aus)
- [ ] Link-Icon je Dienst-Box (neuer Tab, sprachabhängig)
- [ ] im README dokumentiert
