#!/usr/bin/env python3
"""
One-off cleanup of upload-site stamps from library MP3s - REPORT first, write
only for a path list YOU reviewed.

Background: files from upload sites carry domain stamps in extra ID3 frames
(e.g. "fordjonly.com" in WORS, GRP1, TCOP, TIT1, TIT3, TOPE, TPUB, TRSN, TRSO).
Players render TIT3 as a title suffix ("Power (fordjonly.com)"). charts-import
strips these frames on every manifest import (tags.strip_provenance_tags); this
script finds the ALREADY present cases.

HARD RULE: the stack only ever modifies manifest-tracked songs, never the
hand-curated library (compilations such as Bravo Hits, Kuschelrock). Therefore
this script NEVER rewrites files on its own:

  1. Default mode = READ-ONLY REPORT. It scans the whole library (MUSIC_DIR,
     or --artist) and prints every file whose frames from the 9-frame list
     (WORS, GRP1, TCOP, TIT1, TIT3, TOPE, TPUB, TRSN, TRSO) hold a domain/URL-
     like value, as "path<TAB>frame<TAB>value". It never writes. The stack's
     trash (.charts-trash), Synology sidecars (@eaDir, #recycle) and every
     dot-folder are excluded: never scanned, reported or rewritten (listed
     paths inside them are refused by --apply-list).
  2. Writing happens ONLY with --apply-list FILE: a text file with one path per
     line (relative to MUSIC_DIR or absolute; blank lines and '#' comments are
     ignored; a tab-separated report line is accepted, only the first field is
     used). Each path must exist, be an .mp3 and lie inside MUSIC_DIR. Only the
     reported frames (9-frame list AND domain/URL-like value) are removed; all
     other frames - including real label/copyright text and the protected
     TXXX:MusicBrainz* / TXXX:*replaygain* / TXXX:CHARTS_* - stay untouched.
     mtime is preserved, ID3v2.3 is saved. Add --dry-run to preview the apply.

Review the report yourself, copy the paths you want cleaned into a file, then
apply. Curated compilations stay untouched unless you list them.

Usage (inside the charts-import container; /music is rw there):
  # 1. report (read-only)
  docker exec charts-import python /app/scripts/strip_provenance_tags.py > report.txt
  # 2. apply for a reviewed list
  docker exec charts-import python /app/scripts/strip_provenance_tags.py \
      --apply-list /tmp/reviewed.txt
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config, tags  # noqa: E402
from mutagen.id3 import ID3, ID3NoHeaderError  # noqa: E402

# The nine frames of the upload-site gap (exact 4-character IDs).
NINE_FRAMES = ("WORS", "GRP1", "TCOP", "TIT1", "TIT3", "TOPE", "TPUB",
               "TRSN", "TRSO")


def _frame_value(frame) -> str:
    """Text value of a frame (WORS carries a URL, the others carry text)."""
    try:
        url = getattr(frame, "url", None)
        if url:
            return str(url)
        return " ".join(str(x) for x in (getattr(frame, "text", None) or []))
    except Exception:
        return ""


def _stamped_frames(path: str) -> list[tuple[str, str]]:
    """Return [(frame_key, value)] of the 9-frame list whose value looks like a
    domain/URL upload-site stamp. Read-only."""
    try:
        t = ID3(path)
    except (ID3NoHeaderError, Exception):
        return []
    out = []
    for k in t.keys():
        if str(k).upper()[:4] not in NINE_FRAMES:
            continue
        value = _frame_value(t[k])
        if tags._UPLOAD_STAMP_RE.search(value):
            out.append((str(k), value))
    return out


def _clean_value(v: str) -> str:
    return " ".join(v.split())


# Same rule as the charts-config tag editor index (copied, not imported): never
# touch the stack's own trash (.charts-trash), Synology sidecars (@eaDir,
# #recycle) or any dot-folder (.stfolder, ...).
_EXCLUDED_DIRS = {".charts-trash", "@eaDir", "#recycle"}


def _is_excluded_dir(name: str) -> bool:
    return name in _EXCLUDED_DIRS or name.startswith(".")


def _walk_mp3(root: str):
    for dp, dirs, fs in os.walk(root):
        # Prune in place so os.walk never visits the excluded subtrees.
        dirs[:] = [d for d in dirs if not _is_excluded_dir(d)]
        for name in sorted(fs):
            if os.path.splitext(name)[1].lower() == ".mp3":
                yield os.path.join(dp, name)


def report(root: str) -> int:
    """Read-only scan. Prints 'path<TAB>frame<TAB>value' lines. Never writes."""
    print(f"# Read-only report. Scope: {root}", file=sys.stderr)
    total = hits = 0
    for p in _walk_mp3(root):
        total += 1
        frames = _stamped_frames(p)
        if frames:
            hits += 1
            rel = os.path.relpath(p, config.MUSIC_DIR)
            for key, value in frames:
                print(f"{rel}\t{key}\t{_clean_value(value)}")
        if total % 500 == 0:
            print(f"# ... {total} checked, {hits} with stamps", file=sys.stderr)
    print(f"# Done. {total} MP3 checked, {hits} with stamped frames. "
          "Nothing was written. Copy the paths you want cleaned into a file "
          "and run with --apply-list.", file=sys.stderr)
    return 0


def _read_list(list_file: str) -> list[str]:
    paths = []
    with open(list_file, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            first = line.split("\t", 1)[0].strip()
            if first and first not in paths:
                paths.append(first)
    return paths


def _resolve_in_music(entry: str) -> str | None:
    """Absolute real path if entry exists, is an .mp3 and lies inside
    MUSIC_DIR; otherwise None."""
    music = os.path.realpath(config.MUSIC_DIR)
    cand = entry if os.path.isabs(entry) else os.path.join(config.MUSIC_DIR, entry)
    real = os.path.realpath(cand)
    if os.path.commonpath([music, real]) != music or real == music:
        return None
    if not os.path.isfile(real) or os.path.splitext(real)[1].lower() != ".mp3":
        return None
    return real


def _in_excluded_folder(real: str) -> bool:
    """True if a (resolved, inside-MUSIC_DIR) path lies in an excluded folder."""
    rel = os.path.relpath(real, os.path.realpath(config.MUSIC_DIR))
    return any(_is_excluded_dir(part) for part in rel.split(os.sep)[:-1])


def _clean_file(path: str, frames: list[tuple[str, str]], dry_run: bool) -> bool:
    """Remove exactly the reported frames, mtime preserved, saved as ID3v2.3."""
    if dry_run:
        return True
    t = ID3(path)
    for key, _value in frames:
        try:
            del t[key]
        except KeyError:
            pass
    with tags.preserve_mtime(path):
        t.save(path, v2_version=3)
    return True


def apply_list(list_file: str, dry_run: bool = False) -> int:
    """Write only for the explicitly listed, validated paths."""
    # Always keep mtime in this script (otherwise Navidrome re-scans as new).
    config.PRESERVE_MTIME = True
    tag = " (DRY-RUN)" if dry_run else ""
    print(f"== Remove upload-site stamps for a reviewed list{tag} ==")
    print(f"List: {list_file}")
    try:
        entries = _read_list(list_file)
    except OSError as e:
        print(f"Cannot read list: {e}", file=sys.stderr)
        return 2
    cleaned = skipped = errors = 0
    for entry in entries:
        real = _resolve_in_music(entry)
        if real is None:
            skipped += 1
            print(f"  SKIP (missing / not an mp3 / outside MUSIC_DIR): {entry}")
            continue
        if _in_excluded_folder(real):
            skipped += 1
            print(f"  SKIP (inside excluded folder): {entry}")
            continue
        frames = _stamped_frames(real)
        if not frames:
            skipped += 1
            print(f"  SKIP (no stamped frames): {entry}")
            continue
        try:
            _clean_file(real, frames, dry_run)
        except Exception as e:  # keep going, report at the end
            errors += 1
            print(f"  ERROR: {entry}: {e}")
            continue
        cleaned += 1
        verb = "would clean" if dry_run else "cleaned"
        print(f"  {verb}: {entry}  [{', '.join(k for k, _ in frames)}]")
    print()
    print(f"Done{tag}. {len(entries)} listed, {cleaned} "
          f"{'would be cleaned' if dry_run else 'cleaned'}, {skipped} skipped, "
          f"{errors} errors.")
    print("Only the listed files were touched; mtime preserved. "
          "Afterwards trigger a Navidrome scan.")
    return 1 if errors else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--apply-list", metavar="FILE", default="",
                    help="WRITE for the paths in FILE (one per line, inside "
                         "MUSIC_DIR). Without this option the script is a "
                         "read-only report.")
    ap.add_argument("--dry-run", action="store_true",
                    help="with --apply-list: only show what would be cleaned")
    ap.add_argument("--artist", default="",
                    help="report only this artist subfolder (test)")
    args = ap.parse_args(argv)

    if args.apply_list:
        return apply_list(args.apply_list, dry_run=args.dry_run)

    root = config.MUSIC_DIR
    if args.artist:
        root = os.path.join(config.MUSIC_DIR, args.artist)
    return report(root)


if __name__ == "__main__":
    sys.exit(main())
