#!/usr/bin/env python3
"""
One-off, idempotent cleanup of the OLD charts-flashback legacy artifacts that
entered the system BEFORE the Deezer casing / year-map fix.

Background (verified 2026-10-08):
  Before charts-flashback canonicalized the spelling via Deezer, it wrote
  artist/title in UPPERCASE and with a {YYYY} suffix in the title. charts-download
  created manifest entries for them (.charts-manifest.json, "entries" level), e.g.
    'r.e.m. - man on the moon.mp3' -> R.E.M. | MAN ON THE MOON {1992} | src=flashback
  charts-import derives the target folder/file AND the title tag FROM THE MANIFEST
  (not from the file name). So every import run recreates UPPERCASE-{YYYY} folders
  like 'R.E.M/MAN ON THE MOON {1992}/MAN ON THE MOON {1992}.mp3' - and the title
  tag is also set to UPPERCASE+{YYYY}, which Navidrome then displays.

  The NEW flashback code writes cleanly; new missing/manifest entries are
  correct. It is exclusively the OLD manifest entries that keep producing
  UPPERCASE-{YYYY} duplicates. Those entries are pure legacy.

What this script does (each only when the condition matches exactly):
  1. MANIFEST: removes all entries with src == "flashback" AND (title contains
     a {YYYY} suffix OR title/artist is entirely UPPERCASE). Afterwards
     charts-import (REQUIRE_MANIFEST=1) ignores the associated downloads -> no
     more re-sorting. A backup of the manifest is written beforehand.
  2. LIBRARY: deletes album folders whose name carries a {YYYY} suffix
     (the UPPERCASE duplicates). The clean, already-migrated version sits
     next to it and is left untouched. For safety it only deletes when a
     variant WITHOUT the {YYYY} suffix exists in the same artist folder
     (otherwise it might be the only copy -> then only WARN, no delete).
  3. Empty artist folders left behind by (2) are removed.

The year map (.charts-flashback-years.json) is NOT touched: its key is
lowercase 'artist|title' and therefore casing-stable; the clean files pull
their year tag from it correctly.

Usage (on the NAS host, library writable):
  python3 cleanup_flashback_legacy.py --music /volume2/music/Music \\
      --manifest /volume2/music/Charts/.charts-manifest.json [--dry-run]
Default paths are the host paths; with --dry-run it only shows.
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
    """True if s has at least one letter and is entirely UPPERCASE."""
    s = (s or "").strip()
    return bool(s) and any(c.isalpha() for c in s) and s == s.upper()


def _is_legacy_flashback(entry: dict) -> bool:
    """Legacy entry: from flashback AND UPPERCASE title/artist OR {YYYY} in title."""
    if (entry.get("source") or "") != "flashback":
        return False
    title = entry.get("title") or ""
    artist = entry.get("artist") or ""
    if _YEAR_SUFFIX.search(title):
        return True
    # UPPERCASE title (>3 chars, so acronyms like "U2" are not matched) or
    # UPPERCASE artist. The new code yields mixed-case; legacy is UPPERCASE.
    if _is_upper(title) and len(title.strip()) > 3:
        return True
    if _is_upper(artist) and len(artist.strip()) > 3:
        return True
    return False


def clean_manifest(manifest_path: str, dry_run: bool) -> int:
    if not os.path.isfile(manifest_path):
        print(f"  Manifest not found: {manifest_path}")
        return 0
    with open(manifest_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    entries = data.get("entries", {})
    if not isinstance(entries, dict):
        print("  Manifest has no 'entries' map - skipped.")
        return 0

    to_remove = [k for k, v in entries.items()
                 if isinstance(v, dict) and _is_legacy_flashback(v)]
    if not to_remove:
        print("  Manifest: no legacy flashback entries found.")
        return 0

    print(f"  Manifest: {len(to_remove)} legacy flashback entries to remove:")
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
    print(f"  Manifest updated ({len(entries)} entries remain).")
    return len(to_remove)


def clean_library(music_dir: str, dry_run: bool) -> int:
    """Delete album folders with a {YYYY} suffix when a variant WITHOUT the
    suffix exists next to them. Afterwards remove artist folders left empty."""
    removed = 0
    if not os.path.isdir(music_dir):
        print(f"  Library not found: {music_dir}")
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
            # Base name without the {YYYY} suffix (and without double spaces).
            base = _YEAR_SUFFIX.sub("", alb).strip()
            # Is there a variant WITHOUT the suffix (case-insensitive) that is NOT
            # the suffixed folder itself? Only then is deleting safe.
            has_clean = any(
                other != alb and not _YEAR_SUFFIX.search(other)
                and other.strip().lower() == base.lower()
                for other in albums
            )
            path = os.path.join(artist_dir, alb)
            if not has_clean:
                print(f"  WARN: {artist}/{alb} has NO clean variant "
                      f"next to it -> NOT deleted (please check manually).")
                continue
            print(f"  Library: deleting duplicate {artist}/{alb}")
            if not dry_run:
                shutil.rmtree(path, ignore_errors=True)
            removed += 1

        # Remove the now-empty artist folder (only genuine emptiness; @eaDir does
        # not count as content -> it is removed along with it).
        if not dry_run:
            try:
                rest = [x for x in os.listdir(artist_dir) if x != "@eaDir"]
                if not rest:
                    shutil.rmtree(artist_dir, ignore_errors=True)
                    print(f"  Library: removed empty artist folder: {artist}")
            except OSError:
                pass
    if removed == 0:
        print("  Library: no {YYYY} duplicates found.")
    return removed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--music", default="/volume2/music/Music")
    ap.add_argument("--manifest", default="/volume2/music/Charts/.charts-manifest.json")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    tag = " (DRY-RUN)" if args.dry_run else ""
    print(f"== charts-flashback legacy cleanup{tag} ==")
    print(f"Manifest: {args.manifest}")
    print(f"Library:  {args.music}")
    print()
    print("[1/2] Clean up manifest")
    n_manifest = clean_manifest(args.manifest, args.dry_run)
    print()
    print("[2/2] Clean up library duplicates")
    n_lib = clean_library(args.music, args.dry_run)
    print()
    print(f"Done{tag}. Manifest entries: {n_manifest}, "
          f"library duplicates: {n_lib}.")
    print("Year tags / year map NOT touched. Afterwards trigger a full "
          "Navidrome scan so orphaned DB entries disappear.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
