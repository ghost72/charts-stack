#!/usr/bin/env python3
"""
Einmalige, idempotente Korrektur der flashback-Songs, die bereits GROSS in der
Library einsortiert sind (Altfaelle von vor dem Deezer-Casing-Matching-Fix).

Hintergrund: charts-import kanonisiert ab sofort GROSS-Namen beim Import (letzte
Instanz, siehe tags.build_target_path/_canonicalize). Das verhindert KUENFTIGE
GROSS-Faelle. Songs, die aber schon VOR dem Fix einsortiert wurden, liegen
weiter GROSS in der Library - ihre Download-Dateien sind oft laengst weg
(KEEP_LATEST-Cleanup), ein normaler Import-Lauf fasst sie daher nicht mehr an.
Dieses Skript raeumt genau diese Bestands-Altfaelle nach.

Vorgehen (SICHER, nutzt denselben konservativen Resolver wie der Import):
  Fuer jeden Manifest-Eintrag mit src=flashback und GROSS-Titel/Artist:
  1. app.deezer.resolve_canonical() -> kanonische Schreibweise. Liefert Deezer
     KEINEN sicheren Treffer (Alias-Kuenstler wie OMD, von FLASHBACK falsch
     benannte Titel), bleibt der Song unveraendert und landet in der
     "manuell"-Restliste - NIE wird geraten.
  2. Library-Ordner/Datei auf die kanonische Schreibweise umbenennen
     (Artist-Ordner, Album-Ordner, Audiodatei).
  3. Tags title/album setzen (vorhandenes date/originaldate bleibt erhalten);
     Herkunfts-Tags werden durch write_tags ohnehin mitentfernt.
  4. Manifest-Eintrag (artist/title) auf die kanonische Schreibweise
     aktualisieren, damit charts-import den Song nicht erneut GROSS behandelt.

Idempotent: schon korrigierte Songs (Manifest bereits Mixed-Case) tauchen nicht
mehr als GROSS-Verdacht auf; existiert der Zielordner schon, wird nicht
ueberschrieben. --dry-run zeigt nur, was passieren wuerde.

Aufruf (im charts-import-Container; /music ist dort beschreibbar, deezer+tags
liegen im selben app-Paket):
  docker exec charts-import python /app/scripts/recanonicalize_flashback.py --dry-run
  docker exec charts-import python /app/scripts/recanonicalize_flashback.py [--clear-cache]
Danach einen Navidrome-Scan ausloesen.

--clear-cache: loescht vorher die Deezer-Cache-Eintraege der betroffenen Songs,
damit mit dem NEUEN Matching neu aufgeloest wird (sonst liefert der Cache evtl.
den alten GROSS-Miss von vor dem Fix). Beim ersten Lauf nach dem Fix empfohlen.
"""

import argparse
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app import config, tags, deezer  # noqa: E402
from mutagen import File as MutagenFile  # noqa: E402

CHOWN_UID, CHOWN_GID = 1027, 100


def _is_upper(s: str) -> bool:
    s = (s or "").strip()
    return bool(s) and len(s) > 3 and any(c.isalpha() for c in s) and s == s.upper()


def _needs_fix(meta: dict) -> bool:
    if (meta.get("source") or "") != "flashback":
        return False
    return _is_upper(meta.get("title") or "") or _is_upper(meta.get("artist") or "")


def _read_year(path: str) -> str:
    try:
        audio = MutagenFile(path, easy=True)
        if audio is None:
            return ""
        for key in ("date", "originaldate", "year"):
            v = audio.get(key)
            if v:
                s = str(v[0] if isinstance(v, list) else v)
                if len(s) >= 4 and s[:4].isdigit():
                    return s[:4]
    except Exception:
        pass
    return ""


def _chown(path: str) -> None:
    try:
        os.chown(path, CHOWN_UID, CHOWN_GID)
    except OSError:
        pass


def _rename(src: str, dst: str, dry: bool) -> bool:
    if src == dst or not os.path.exists(src):
        return False
    casing_only = src.lower() == dst.lower()
    if not casing_only and os.path.exists(dst):
        print(f"    WARN: Ziel existiert, uebersprungen: {dst}")
        return False
    if dry:
        print(f"    [dry] rename {src} -> {dst}")
        return True
    if casing_only:
        tmp = src + ".recanontmp"
        os.rename(src, tmp)
        os.rename(tmp, dst)
    else:
        os.rename(src, dst)
    _chown(dst)
    print(f"    rename {src} -> {dst}")
    return True


def _find_audio(album_dir: str, title: str) -> str | None:
    if not os.path.isdir(album_dir):
        return None
    for name in os.listdir(album_dir):
        if name == "@eaDir":
            continue
        base, ext = os.path.splitext(name)
        if ext.lower() in config.AUDIO_EXTENSIONS and base.lower() == title.lower():
            return os.path.join(album_dir, name)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--clear-cache", action="store_true",
                    help="Deezer-Cache der betroffenen Songs vorher loeschen "
                         "(mit neuem Matching neu aufloesen).")
    args = ap.parse_args()

    music = config.MUSIC_DIR
    try:
        with open(config.MANIFEST_FILE, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError) as e:
        print(f"FEHLER: Manifest nicht lesbar: {e}")
        return 1
    entries = manifest.get("entries", {}) if isinstance(manifest, dict) else {}

    todo = [(k, v) for k, v in entries.items()
            if isinstance(v, dict) and _needs_fix(v)]
    tag = " (DRY-RUN)" if args.dry_run else ""
    print(f"== flashback-Recanonicalize{tag} ==")
    print(f"Library:  {music}")
    print(f"Manifest: {config.MANIFEST_FILE}")
    print(f"{len(todo)} GROSS-flashback-Eintraege zu pruefen.\n")

    if args.clear_cache:
        # Auch im Dry-Run leeren: sonst liefert resolve_canonical die alten
        # GROSS-Misses aus dem Cache und der Dry-Run zeigt faelschlich "0". Ein
        # geleerter Cache-Eintrag wird beim naechsten Lookup ohnehin neu gefuellt.
        try:
            cache = deezer._load_cache()
            removed = 0
            for _k, v in todo:
                ck = deezer._key(v.get("artist", ""), v.get("title", ""))
                if ck in cache:
                    del cache[ck]
                    removed += 1
            deezer._save_cache()
            print(f"Deezer-Cache: {removed} Eintraege invalidiert"
                  f"{' (dry-run: nur Cache, keine Dateiaenderung)' if args.dry_run else ''}.\n")
        except Exception as e:
            print(f"WARN: Cache-Invalidierung fehlgeschlagen: {e}\n")

    fixed = 0
    manual = []
    changed = False
    for key, meta in todo:
        old_a = (meta.get("artist") or "").strip()
        old_t = (meta.get("title") or "").strip()
        new_a, new_t = deezer.resolve_canonical(old_a, old_t)
        if (new_a, new_t) == (old_a, old_t):
            manual.append((old_a, old_t))
            continue

        print(f"- {old_a} / {old_t}  ->  {new_a} / {new_t}")

        canon_old_a = tags.canonical_artist(music, old_a) or old_a
        safe_old_a = tags._clean_component(canon_old_a, "Unknown Artist")
        safe_old_t = tags._clean_component(old_t, "track")
        safe_new_a = tags._clean_component(new_a, "Unknown Artist")
        safe_new_t = tags._clean_component(new_t, "track")

        old_artist_dir = os.path.join(music, safe_old_a)
        new_artist_dir = os.path.join(music, safe_new_a)
        if safe_old_a != safe_new_a:
            _rename(old_artist_dir, new_artist_dir, args.dry_run)
        base_artist_dir = new_artist_dir if os.path.isdir(new_artist_dir) \
            else old_artist_dir

        old_album_dir = os.path.join(base_artist_dir, safe_old_t)
        new_album_dir = os.path.join(base_artist_dir, safe_new_t)
        if safe_old_t != safe_new_t:
            _rename(old_album_dir, new_album_dir, args.dry_run)
        target_album_dir = new_album_dir if os.path.isdir(new_album_dir) \
            else old_album_dir

        f = _find_audio(target_album_dir, old_t) or \
            _find_audio(target_album_dir, new_t)
        if f:
            ext = os.path.splitext(f)[1]
            new_file = os.path.join(target_album_dir, safe_new_t + ext)
            if os.path.basename(f).lower() != os.path.basename(new_file).lower():
                _rename(f, new_file, args.dry_run)
            target_file = new_file if os.path.isfile(new_file) else f
            if not args.dry_run:
                year = _read_year(target_file)
                tags.write_tags(target_file, new_a, new_t, new_t,
                                year=year or None)
                print(f"    tags: title/album -> {new_t}"
                      f"{' (date '+year+')' if year else ''}")
        else:
            print(f"    WARN: Audiodatei nicht gefunden in {target_album_dir}")

        if not args.dry_run:
            entries[key]["artist"] = new_a
            entries[key]["title"] = new_t
            changed = True
        fixed += 1

    if changed and not args.dry_run:
        tmp = config.MANIFEST_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=0)
        os.replace(tmp, config.MANIFEST_FILE)
        print(f"\nManifest aktualisiert ({fixed} Eintraege).")

    print(f"\nFertig{tag}. {fixed} sicher korrigiert, "
          f"{len(manual)} brauchen manuelle Pruefung:")
    for a, t in sorted(manual):
        print(f"  MANUELL: {a} | {t}")
    if not args.dry_run and fixed:
        print("\nDanach einen Navidrome-Scan ausloesen.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
