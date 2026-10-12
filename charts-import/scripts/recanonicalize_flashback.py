#!/usr/bin/env python3
"""
One-off, idempotent correction of the flashback songs already sorted into the
library in UPPERCASE (legacy cases from before the Deezer casing-matching fix).

Background: charts-import now canonicalizes UPPERCASE names on import (last
line of defense, see tags.build_target_path/_canonicalize). That prevents FUTURE
UPPERCASE cases. But songs sorted in BEFORE the fix still sit in the library in
UPPERCASE - their download files are often long gone (KEEP_LATEST cleanup), so a
normal import run no longer touches them. This script cleans up exactly those
pre-existing legacy cases.

Procedure (SAFE, uses the same conservative resolver as the import):
  For every manifest entry with src=flashback and UPPERCASE title/artist:
  1. app.deezer.resolve_canonical() -> canonical spelling. If Deezer returns
     NO safe match (alias artists like OMD, titles misnamed by FLASHBACK), the
     song stays unchanged and lands in the "manual" leftover list - nothing is
     ever guessed.
  2. Rename the library folder/file to the canonical spelling (artist folder,
     album folder, audio file).
  3. Set the title/album tags (existing date/originaldate is preserved);
     provenance tags are stripped by write_tags anyway.
  4. Update the manifest entry (artist/title) to the canonical spelling so
     charts-import does not treat the song as UPPERCASE again.

Idempotent: already corrected songs (manifest already mixed-case) no longer show
up as UPPERCASE suspects; if the target folder already exists, nothing is
overwritten. --dry-run only shows what would happen.

Usage (inside the charts-import container; /music is writable there, deezer+tags
live in the same app package):
  docker exec charts-import python /app/scripts/recanonicalize_flashback.py --dry-run
  docker exec charts-import python /app/scripts/recanonicalize_flashback.py [--clear-cache]
Afterwards trigger a Navidrome scan.

--clear-cache: first deletes the Deezer cache entries of the affected songs so
they are re-resolved with the NEW matching (otherwise the cache may return the
old UPPERCASE miss from before the fix). Recommended on the first run after the fix.
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
        print(f"    WARN: target exists, skipped: {dst}")
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
                    help="delete the Deezer cache of the affected songs first "
                         "(re-resolve with the new matching).")
    args = ap.parse_args()

    music = config.MUSIC_DIR
    try:
        with open(config.MANIFEST_FILE, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError) as e:
        print(f"ERROR: manifest not readable: {e}")
        return 1
    entries = manifest.get("entries", {}) if isinstance(manifest, dict) else {}

    todo = [(k, v) for k, v in entries.items()
            if isinstance(v, dict) and _needs_fix(v)]
    tag = " (DRY-RUN)" if args.dry_run else ""
    print(f"== flashback recanonicalize{tag} ==")
    print(f"Library:  {music}")
    print(f"Manifest: {config.MANIFEST_FILE}")
    print(f"{len(todo)} UPPERCASE flashback entries to check.\n")

    if args.clear_cache:
        # Clear even in dry-run: otherwise resolve_canonical returns the old
        # UPPERCASE misses from the cache and the dry-run wrongly shows "0". A
        # cleared cache entry is refilled on the next lookup anyway.
        try:
            cache = deezer._load_cache()
            removed = 0
            for _k, v in todo:
                ck = deezer._key(v.get("artist", ""), v.get("title", ""))
                if ck in cache:
                    del cache[ck]
                    removed += 1
            deezer._save_cache()
            print(f"Deezer cache: {removed} entries invalidated"
                  f"{' (dry-run: cache only, no file change)' if args.dry_run else ''}.\n")
        except Exception as e:
            print(f"WARN: cache invalidation failed: {e}\n")

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
            print(f"    WARN: audio file not found in {target_album_dir}")

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
        print(f"\nManifest updated ({fixed} entries).")

    print(f"\nDone{tag}. {fixed} corrected safely, "
          f"{len(manual)} need manual review:")
    for a, t in sorted(manual):
        print(f"  MANUAL: {a} | {t}")
    if not args.dry_run and fixed:
        print("\nAfterwards trigger a Navidrome scan.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
