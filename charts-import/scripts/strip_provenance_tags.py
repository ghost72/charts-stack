#!/usr/bin/env python3
"""
Einmalige, idempotente Bereinigung von Herkunfts-/Encoder-Tags aus der GANZEN
Library.

Hintergrund: Fast alle MP3s tragen in COMM-/TENC-/URL-Frames Herkunftsspuren -
Soulseek-Uploader-/Release-Group-Signaturen ("eXenz", "PMEDIA",
"www.t.me/pmedia_music", "BY ELECTRO75", der fre:ac-Werbekommentar) bzw. das
Rip-/Encoder-Tool. Diese Spuren sollen NICHT in der Library stehen.
charts-import entfernt sie ab sofort bei jedem Import automatisch
(tags.strip_provenance_tags, in write_tags); dieses Skript raeumt die BEREITS
vorhandenen Dateien einmalig nach.

Es verwendet GENAU DIESELBE Loesch-Logik wie der Import (tags.strip_provenance_
tags), damit Alt-Bestand und neue Downloads konsistent behandelt werden.

GELOESCHT: COMM, TENC, TSSE, WXXX/WCOM/WOAF/WPUB, TOWN sowie benutzerdefinierte
  TXXX-Freitext-Frames (z.B. TXXX:LOG, Uploader-URLs).
GESCHUETZT (NIE angefasst): TXXX:MusicBrainz* und TXXX:*replaygain* (IDs +
  Lautstaerke-Normalisierung) sowie alle Standard-Tags (Titel/Album/Artist/
  Datum/Track/Cover, die regulaeren ReplayGain-Frames). Verlustfrei (nur
  Metadaten, kein Re-Encoding).

SCOPE: die ganze Library (MUSIC_DIR). Da COMM/TENC bei dieser Library ueberall
nur Uploader-/Tool-Spuren tragen und die wertvollen TXXX (MusicBrainz/ReplayGain)
explizit geschuetzt sind, ist der Library-weite Lauf sicher. Mit --artist kann
man den Lauf auf einen einzelnen Kuenstler-Ordner beschraenken (Test).

Aufruf (im charts-import-Container; /music ist dort rw):
  docker exec charts-import python /app/scripts/strip_provenance_tags.py --dry-run
  docker exec charts-import python /app/scripts/strip_provenance_tags.py
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config, tags  # noqa: E402
from mutagen.id3 import ID3, ID3NoHeaderError  # noqa: E402


def _provenance_frames(path: str) -> list[str]:
    """Liefert die Frame-Keys, die strip_provenance_tags entfernen WUERDE
    (fuer die Dry-Run-Anzeige). Spiegelt die Logik in tags.strip_provenance_tags."""
    try:
        t = ID3(path)
    except (ID3NoHeaderError, Exception):
        return []
    out = []
    for k in t.keys():
        ku = str(k).upper()
        if ku.startswith("TXXX"):
            if not tags._is_protected_txxx(k):
                out.append(str(k))
        elif ku.startswith(tags._PROVENANCE_FULL_PREFIXES):
            out.append(str(k))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="nur anzeigen, nichts schreiben")
    ap.add_argument("--artist", default="",
                    help="nur diesen Kuenstler-Unterordner (Test)")
    ap.add_argument("--verbose", action="store_true",
                    help="pro Datei die entfernten Frame-Keys zeigen")
    args = ap.parse_args()

    root = config.MUSIC_DIR
    if args.artist:
        root = os.path.join(config.MUSIC_DIR, args.artist)

    tag = " (DRY-RUN)" if args.dry_run else ""
    print(f"== Herkunfts-Tags aus der Library entfernen{tag} ==")
    print(f"Scope: {root}")
    print("Geschuetzt: TXXX:MusicBrainz*, TXXX:*replaygain* + Standard-Tags")
    print()

    total = cleaned = 0
    for dp, _dirs, fs in os.walk(root):
        if "@eaDir" in dp:
            continue
        for name in fs:
            if os.path.splitext(name)[1].lower() != ".mp3":
                continue
            p = os.path.join(dp, name)
            total += 1
            frames = _provenance_frames(p)
            if not frames:
                continue
            rel = os.path.relpath(p, config.MUSIC_DIR)
            if args.dry_run:
                cleaned += 1
                if args.verbose:
                    print(f"  wuerde bereinigen: {rel}  [{', '.join(frames)}]")
            else:
                if tags.strip_provenance_tags(p):
                    cleaned += 1
                    if args.verbose:
                        print(f"  bereinigt: {rel}  [{', '.join(frames)}]")
            # Fortschritt bei grossen Laeufen (alle 500 Dateien)
            if total % 500 == 0:
                print(f"  … {total} geprueft, {cleaned} betroffen")

    print()
    print(f"Fertig{tag}. {total} MP3 geprueft, {cleaned} "
          f"{'wuerden bereinigt' if args.dry_run else 'bereinigt'}.")
    print("MusicBrainz-/ReplayGain-TXXX + Standard-Tags unberuehrt. "
          "Danach einen Navidrome-Scan ausloesen.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
