#!/usr/bin/env python3
"""
One-off, idempotent cleanup of provenance/encoder tags from the ENTIRE library.

Background: almost all MP3s carry provenance traces in COMM/TENC/URL frames -
Soulseek uploader / release-group signatures ("eXenz", "PMEDIA",
"www.t.me/pmedia_music", "BY ELECTRO75", the fre:ac promo comment) or the
rip/encoder tool. These traces should NOT live in the library. charts-import
now strips them automatically on every import (tags.strip_provenance_tags, in
write_tags); this script cleans up the ALREADY present files once.

It uses EXACTLY THE SAME removal logic as the import (tags.strip_provenance_
tags) so existing files and new downloads are handled consistently.

REMOVED: COMM, TENC, TSSE, WXXX/WCOM/WOAF/WPUB, TOWN plus user-defined
  TXXX free-text frames (e.g. TXXX:LOG, uploader URLs).
PROTECTED (never touched): TXXX:MusicBrainz* and TXXX:*replaygain* (IDs +
  loudness normalization) as well as all standard tags (title/album/artist/
  date/track/cover, the regular ReplayGain frames). Lossless (metadata only,
  no re-encoding).

SCOPE: the whole library (MUSIC_DIR). Since COMM/TENC in this library carry
only uploader/tool traces everywhere and the valuable TXXX (MusicBrainz/
ReplayGain) are explicitly protected, the library-wide run is safe. Use
--artist to restrict the run to a single artist folder (test).

Usage (inside the charts-import container; /music is rw there):
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
    """Return the frame keys that strip_provenance_tags WOULD remove
    (for the dry-run display). Mirrors the logic in tags.strip_provenance_tags."""
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
                    help="only show, write nothing")
    ap.add_argument("--artist", default="",
                    help="only this artist subfolder (test)")
    ap.add_argument("--verbose", action="store_true",
                    help="show the removed frame keys per file")
    args = ap.parse_args()

    root = config.MUSIC_DIR
    if args.artist:
        root = os.path.join(config.MUSIC_DIR, args.artist)

    tag = " (DRY-RUN)" if args.dry_run else ""
    print(f"== Remove provenance tags from the library{tag} ==")
    print(f"Scope: {root}")
    print("Protected: TXXX:MusicBrainz*, TXXX:*replaygain* + standard tags")
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
                    print(f"  would clean: {rel}  [{', '.join(frames)}]")
            else:
                if tags.strip_provenance_tags(p):
                    cleaned += 1
                    if args.verbose:
                        print(f"  cleaned: {rel}  [{', '.join(frames)}]")
            # Progress on large runs (every 500 files)
            if total % 500 == 0:
                print(f"  … {total} checked, {cleaned} affected")

    print()
    print(f"Done{tag}. {total} MP3 checked, {cleaned} "
          f"{'would be cleaned' if args.dry_run else 'cleaned'}.")
    print("MusicBrainz/ReplayGain TXXX + standard tags untouched. "
          "Afterwards trigger a Navidrome scan.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
