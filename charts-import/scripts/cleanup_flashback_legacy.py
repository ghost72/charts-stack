#!/usr/bin/env python3
"""
Einmaliges, idempotentes Cleanup der ALTEN charts-flashback-Altlasten, die VOR
dem Deezer-Casing-/Jahr-Map-Fix ins System kamen.

Hintergrund (verifiziert am 2026-10-08):
  Bevor charts-flashback die Schreibweise ueber Deezer kanonisierte, schrieb es
  Artist/Titel GROSS und mit {JJJJ}-Suffix im Titel. charts-download legte dafuer
  Eintraege im Manifest (.charts-manifest.json, Ebene "entries") an, z.B.
    'r.e.m. - man on the moon.mp3' -> R.E.M. | MAN ON THE MOON {1992} | src=flashback
  charts-import bildet Zielordner/-datei UND das Titel-Tag AUS DEM MANIFEST (nicht
  aus dem Dateinamen). Darum entstehen bei jedem Import-Lauf erneut GROSS-{JJJJ}-
  Ordner wie 'R.E.M/MAN ON THE MOON {1992}/MAN ON THE MOON {1992}.mp3' - und das
  Titel-Tag wird ebenfalls GROSS+{JJJJ} gesetzt, was Navidrome anzeigt.

  Der NEUE flashback-Code schreibt sauber; neue missing/Manifest-Eintraege sind
  korrekt. Es sind ausschliesslich die ALTEN Manifest-Eintraege, die weiter
  GROSS-{JJJJ}-Dubletten erzeugen. Diese Eintraege sind reine Altlast.

Was dieses Skript tut (jeweils nur, wenn es die Bedingung exakt trifft):
  1. MANIFEST: entfernt alle entries mit src == "flashback" UND (Titel enthaelt
     ein {JJJJ}-Suffix ODER Titel/Artist ist komplett GROSS). Danach ignoriert
     charts-import (REQUIRE_MANIFEST=1) die zugehoerigen Downloads -> keine
     Neu-Einsortierung mehr. Ein Backup des Manifests wird vorher geschrieben.
  2. LIBRARY: loescht Album-Ordner, deren Name ein {JJJJ}-Suffix traegt
     (die GROSS-Dubletten). Die saubere, bereits migrierte Version steht
     daneben und bleibt unangetastet. Zur Sicherheit wird NUR geloescht, wenn
     im selben Artist-Ordner eine Variante OHNE {JJJJ}-Suffix existiert (sonst
     waere es evtl. die einzige Kopie -> dann nur WARN, kein Loeschen).
  3. Leere Artist-Ordner, die durch (2) entstehen, werden entfernt.

Die Jahr-Map (.charts-flashback-years.json) wird NICHT angefasst: ihr Key ist
lowercase 'artist|title' und damit casing-stabil; die sauberen Dateien ziehen
ihr Jahr-Tag korrekt daraus.

Aufruf (auf dem NAS-Host, Library beschreibbar):
  python3 cleanup_flashback_legacy.py --music /volume2/music/Music \\
      --manifest /volume2/music/Charts/.charts-manifest.json [--dry-run]
Default-Pfade sind die Host-Pfade; mit --dry-run wird nur angezeigt.
"""

import argparse
import json
import os
import re
import shutil
import sys
import time

_YEAR_SUFFIX = re.compile(r"\{\s*(19|20)\d{2}\s*\}")


def _is_upper(s: str) -> bool:
    """True, wenn s mind. einen Buchstaben hat und komplett GROSS ist."""
    s = (s or "").strip()
    return bool(s) and any(c.isalpha() for c in s) and s == s.upper()


def _is_legacy_flashback(entry: dict) -> bool:
    """Alt-Eintrag: aus flashback UND GROSS-Titel/Artist ODER {JJJJ} im Titel."""
    if (entry.get("source") or "") != "flashback":
        return False
    title = entry.get("title") or ""
    artist = entry.get("artist") or ""
    if _YEAR_SUFFIX.search(title):
        return True
    # GROSS-Titel (>3 Zeichen, um Akronyme wie "U2" nicht zu treffen) oder
    # GROSS-Artist. Der neue Code liefert Mixed-Case, Altlasten sind GROSS.
    if _is_upper(title) and len(title.strip()) > 3:
        return True
    if _is_upper(artist) and len(artist.strip()) > 3:
        return True
    return False


def clean_manifest(manifest_path: str, dry_run: bool) -> int:
    if not os.path.isfile(manifest_path):
        print(f"  Manifest nicht gefunden: {manifest_path}")
        return 0
    with open(manifest_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    entries = data.get("entries", {})
    if not isinstance(entries, dict):
        print("  Manifest hat keine 'entries'-Map - uebersprungen.")
        return 0

    to_remove = [k for k, v in entries.items()
                 if isinstance(v, dict) and _is_legacy_flashback(v)]
    if not to_remove:
        print("  Manifest: keine Alt-flashback-Eintraege gefunden.")
        return 0

    print(f"  Manifest: {len(to_remove)} Alt-flashback-Eintraege zu entfernen:")
    for k in to_remove:
        v = entries[k]
        print(f"    - {k!r} -> {v.get('artist')} | {v.get('title')}")

    if dry_run:
        return len(to_remove)

    backup = f"{manifest_path}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(manifest_path, backup)
    print(f"  Backup: {backup}")
    for k in to_remove:
        del entries[k]
    tmp = manifest_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=0)
    os.replace(tmp, manifest_path)
    print(f"  Manifest aktualisiert ({len(entries)} Eintraege verbleiben).")
    return len(to_remove)


def clean_library(music_dir: str, dry_run: bool) -> int:
    """Loescht Album-Ordner mit {JJJJ}-Suffix, wenn daneben eine Variante OHNE
    Suffix existiert. Entfernt danach leer gewordene Artist-Ordner."""
    removed = 0
    if not os.path.isdir(music_dir):
        print(f"  Library nicht gefunden: {music_dir}")
        return 0

    for artist in sorted(os.listdir(music_dir)):
        artist_dir = os.path.join(music_dir, artist)
        if not os.path.isdir(artist_dir):
            continue
        try:
            albums = os.listdir(artist_dir)
        except OSError:
            continue
        suffixed = [a for a in albums if _YEAR_SUFFIX.search(a)]
        for alb in suffixed:
            # Basisname ohne {JJJJ}-Suffix (und ohne doppelte Spaces).
            base = _YEAR_SUFFIX.sub("", alb).strip()
            # Gibt es eine Variante OHNE Suffix (case-insensitiv), die NICHT der
            # Suffix-Ordner selbst ist? Nur dann ist das Loeschen sicher.
            has_clean = any(
                other != alb and not _YEAR_SUFFIX.search(other)
                and other.strip().lower() == base.lower()
                for other in albums
            )
            path = os.path.join(artist_dir, alb)
            if not has_clean:
                print(f"  WARN: {artist}/{alb} hat KEINE saubere Variante "
                      f"daneben -> NICHT geloescht (bitte manuell pruefen).")
                continue
            print(f"  Library: loesche Dublette {artist}/{alb}")
            if not dry_run:
                shutil.rmtree(path, ignore_errors=True)
            removed += 1

        # Leer gewordenen Artist-Ordner entfernen (nur echte Leere, @eaDir zaehlt
        # nicht als Inhalt -> wird mitentfernt).
        if not dry_run:
            try:
                rest = [x for x in os.listdir(artist_dir) if x != "@eaDir"]
                if not rest:
                    shutil.rmtree(artist_dir, ignore_errors=True)
                    print(f"  Library: leerer Artist-Ordner entfernt: {artist}")
            except OSError:
                pass
    if removed == 0:
        print("  Library: keine {JJJJ}-Dubletten gefunden.")
    return removed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--music", default="/volume2/music/Music")
    ap.add_argument("--manifest", default="/volume2/music/Charts/.charts-manifest.json")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    tag = " (DRY-RUN)" if args.dry_run else ""
    print(f"== charts-flashback Altlasten-Cleanup{tag} ==")
    print(f"Manifest: {args.manifest}")
    print(f"Library:  {args.music}")
    print()
    print("[1/2] Manifest bereinigen")
    n_manifest = clean_manifest(args.manifest, args.dry_run)
    print()
    print("[2/2] Library-Dubletten bereinigen")
    n_lib = clean_library(args.music, args.dry_run)
    print()
    print(f"Fertig{tag}. Manifest-Eintraege: {n_manifest}, "
          f"Library-Dubletten: {n_lib}.")
    print("Jahr-Tags/Jahr-Map NICHT angefasst. Danach einen vollstaendigen "
          "Navidrome-Scan ausloesen, damit verwaiste DB-Eintraege verschwinden.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
