#!/usr/bin/env python3
"""
One-off, idempotent migration of the 7 charts-flashback songs already sitting in
the library in UPPERCASE to their canonical spelling.

Background: FLASHBACK FM used to deliver artist/title in UPPERCASE; those raw
values ended up as folder/file names in the library (e.g.
``U2/WHERE THE STREETS HAVE NO NAME/WHERE THE STREETS HAVE NO NAME.mp3``). From
now on charts-flashback canonicalizes the spelling via Deezer; this script
cleans up the 7 legacy cases.

CONSISTENCY IS THE GOAL: future flashback songs get EXACTLY the casing that
app.deezer.resolve_canonical() returns. So that old and new cases are written
identically, this script fetches the target spelling of the 7 songs AT RUNTIME
via the same resolve_canonical() function - NO hardcoded target names as the
normal case. By construction there is no drift between the live resolution and
the migration.

FALLBACK: if resolve_canonical() returns no validated Deezer match for a song
(no network / no match -> returns == UPPERCASE raw value), the script falls back
to a hardcoded spelling (FALLBACK_CASING) for THAT song and LOGS it clearly
(WARN) so the user can check it manually - instead of leaving the song UPPERCASE
or aborting.

Run manually on the NAS (youruser@your-nas), where the library lives under
/volume2/music/Music. The base path is overridable via CLI arg or env MUSIC_DIR
(default /volume2/music/Music). The script imports the app package (for
resolve_canonical) - easiest to run inside the container:
``docker exec charts-flashback python /app/scripts/migrate_flashback_casing.py``
(library there under /music/Music), or on the NAS with PYTHONPATH set to the
charts-flashback directory.

IMPORTANT (lesson from the first migration): the charts-flashback container
mounts the library READ-ONLY (/music:ro) - it only needs to read (matching). The
--dry-run runs fine in the container, but the REAL run fails there with
``OSError: [Errno 30] Read-only file system``. So run the real rename EITHER
directly on the NAS host (as root, with PYTHONPATH on the charts-flashback
directory so ``import app.deezer`` works) OR in a container that has the library
mounted writable (e.g. charts-import with /music=rw) - but then without
resolve_canonical, since only charts-flashback has the deezer module. The first
migration used the host route.

Per song the following is renamed:
  - Artist folder <ARTIST UPPER>        -> <Artist canonical>
  - Album folder  <TITLE UPPER>         -> <Title canonical>
  - Audio file(s) <TITLE UPPER>.<ext>   -> <Title canonical>.<ext>

Robustness:
  - Idempotent: if the source is missing but the target already exists -> skipped.
  - The target path must not exist yet (except a pure casing change) -> otherwise
    conflict warning, no overwrite.
  - Case-insensitive mounts: a pure casing change goes through an intermediate
    rename (``.casingtmp``) so it works there too.
  - @eaDir (Synology thumbnails) moves along automatically on a folder rename; it
    is not touched separately (DSM recreates it on demand).
  - chown 1027:100 (PUID/PGID) on every touched path; errors are not fatal.
  - --dry-run only shows what would happen.

IMPORTANT - do NOT touch the year map: the year tags (date/originaldate) of the 7
songs are already set correctly by hand and are NOT touched here (only folder and
file names). The key in .charts-flashback-years.json is also lowercase
'artist|title' (flashback main._key == charts-import importer._flashback_year,
both .lower()); casing changes to the name do NOT change the key -> the year map
stays valid, no intervention needed.
"""

import argparse
import os
import sys

# Make the app package importable regardless of where the script is started
# (scripts/ sits directly under the project root that contains the app package).
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from app import deezer  # noqa: E402

# PUID/PGID of the container workloads on the NAS (youruser: UID 1027, GID 100).
CHOWN_UID = 1027
CHOWN_GID = 100

DEFAULT_MUSIC_DIR = os.environ.get("MUSIC_DIR", "/volume2/music/Music")

# The 7 UPPERCASE raw values (how folder/file currently sit in the library).
# The target spelling is fetched AT RUNTIME via resolve_canonical().
RAW_SONGS = [
    ("FREDDIE MERCURY", "I WAS BORN TO LOVE YOU"),            # 1. (1985)
    ("BLOODHOUND GANG", "THE BAD TOUCH"),                     # 2. (1999)
    ("MEAT LOAF", "YOU TOOK THE WORDS RIGHT OUT OF MY MOUTH"),# 3. (1977)
    ("R.E.M", "MAN ON THE MOON"),                             # 4. (1992)
    ("PHILIP BAILEY FEAT. PHIL COLLINS", "EASY LOVER"),       # 5. (1984)
    ("U2", "WHERE THE STREETS HAVE NO NAME"),                 # 6. (1987)
    ("MARILLION", "KAYLEIGH"),                                # 7. (1985)
]

# Hardcoded EMERGENCY spelling - ONLY used when resolve_canonical() returns no
# validated Deezer match (then the UPPERCASE raw value comes back). Key =
# UPPERCASE raw value (artist, title). Applied with a loud WARN log.
# Note: this can differ slightly from the live Deezer casing (e.g. short
# words) - hence only a fallback, not the normal case.
FALLBACK_CASING = {
    ("FREDDIE MERCURY", "I WAS BORN TO LOVE YOU"):
        ("Freddie Mercury", "I Was Born to Love You"),
    ("BLOODHOUND GANG", "THE BAD TOUCH"):
        ("Bloodhound Gang", "The Bad Touch"),
    ("MEAT LOAF", "YOU TOOK THE WORDS RIGHT OUT OF MY MOUTH"):
        ("Meat Loaf", "You Took the Words Right Out of My Mouth"),
    ("R.E.M", "MAN ON THE MOON"):
        ("R.E.M.", "Man on the Moon"),
    ("PHILIP BAILEY FEAT. PHIL COLLINS", "EASY LOVER"):
        ("Philip Bailey feat. Phil Collins", "Easy Lover"),
    ("U2", "WHERE THE STREETS HAVE NO NAME"):
        ("U2", "Where the Streets Have No Name"),
    ("MARILLION", "KAYLEIGH"):
        ("Marillion", "Kayleigh"),
}


def _resolve_target(old_artist: str, old_title: str) -> tuple[str, str]:
    """
    Target spelling for a song: first Deezer (resolve_canonical, exactly as in
    the running service), otherwise the hardcoded fallback with a loud WARN log.

    resolve_canonical() returns the unchanged (UPPERCASE) raw values when there
    is no validated Deezer match. If we detect that (result == raw value even
    though the raw value is UPPERCASE), we fall back to FALLBACK_CASING.
    """
    new_artist, new_title = deezer.resolve_canonical(old_artist, old_title)
    unchanged = (new_artist == old_artist and new_title == old_title)
    if unchanged:
        fb = FALLBACK_CASING.get((old_artist, old_title))
        if fb:
            print(f"    WARN: no validated Deezer match for "
                  f"'{old_artist} / {old_title}' -> fallback casing "
                  f"'{fb[0]} / {fb[1]}' (please check manually).")
            return fb
        print(f"    WARN: no Deezer match AND no fallback for "
              f"'{old_artist} / {old_title}' -> stays UPPERCASE (please check).")
    return new_artist, new_title


def _chown(path: str, dry_run: bool) -> None:
    """Set owner to 1027:100. Errors are not fatal (log only)."""
    if dry_run:
        print(f"    [dry-run] chown {CHOWN_UID}:{CHOWN_GID} {path}")
        return
    try:
        os.chown(path, CHOWN_UID, CHOWN_GID)
    except OSError as e:
        print(f"    WARN: chown failed ({path}): {e}")


def _rename(src: str, dst: str, dry_run: bool) -> bool:
    """
    Rename src -> dst. A pure casing change (same paths under a case-insensitive
    comparison but different spelling) goes through an intermediate rename so it
    works on case-insensitive mounts too.
    Returns: True if renamed (or would be in dry-run).
    """
    if src == dst:
        return False
    # Detect a pure casing change platform-independently: os.path.normcase
    # lowercases ONLY on Windows, not on Linux/macOS - so compare via .lower()
    # directly.
    casing_only = src.lower() == dst.lower()

    # Only report a conflict if the target really is a DIFFERENT path (not just
    # a casing change). On case-insensitive mounts the target seemingly already
    # "exists" for a pure casing change - but that is the same file.
    if not casing_only and os.path.exists(dst):
        print(f"    WARN: target already exists, skipped: {dst}")
        return False

    if dry_run:
        print(f"    [dry-run] rename {src} -> {dst}")
        return True

    if casing_only:
        tmp = src + ".casingtmp"
        # If an earlier run aborted, clean up tmp.
        if os.path.exists(tmp) and tmp.lower() != src.lower():
            print(f"    WARN: intermediate path already exists: {tmp}")
            return False
        os.rename(src, tmp)
        os.rename(tmp, dst)
    else:
        os.rename(src, dst)
    print(f"    rename {src} -> {dst}")
    return True


def _migrate_file_in(album_dir: str, old_title: str, new_title: str,
                     dry_run: bool) -> None:
    """
    Rename the audio file(s) in the album folder from <old_title>.<ext> to
    <new_title>.<ext> (file extension kept). Touches every file whose base name
    (without extension) matches the old title (case-insensitive).
    """
    if not os.path.isdir(album_dir):
        return
    for name in os.listdir(album_dir):
        if name == "@eaDir":
            continue
        base, ext = os.path.splitext(name)
        if base.lower() != old_title.lower():
            continue
        src = os.path.join(album_dir, name)
        dst = os.path.join(album_dir, new_title + ext)
        if _rename(src, dst, dry_run) and not dry_run:
            _chown(dst, dry_run)


def _migrate_song(music_dir: str, old_artist: str, old_title: str,
                  new_artist: str, new_title: str, dry_run: bool) -> None:
    print(f"- {old_artist} / {old_title}  ->  {new_artist} / {new_title}")

    old_artist_dir = os.path.join(music_dir, old_artist)
    new_artist_dir = os.path.join(music_dir, new_artist)

    # 1. Bring the artist folder to the canonical spelling (if needed).
    if os.path.isdir(old_artist_dir):
        _rename(old_artist_dir, new_artist_dir, dry_run)
        if not dry_run and os.path.isdir(new_artist_dir):
            _chown(new_artist_dir, dry_run)
    elif os.path.isdir(new_artist_dir):
        pass  # artist folder already canonical (idempotent)
    else:
        print(f"    WARN: artist folder not found: {old_artist_dir}")
        return

    # From here on continue with the (possibly already renamed) canonical artist
    # folder. In dry-run new_artist_dir may not exist yet -> fall back to the
    # existing path so the preview can continue.
    base_artist_dir = new_artist_dir if os.path.isdir(new_artist_dir) else old_artist_dir

    old_album_dir = os.path.join(base_artist_dir, old_title)
    new_album_dir = os.path.join(base_artist_dir, new_title)

    # 2. Rename the album folder (if needed).
    if os.path.isdir(old_album_dir):
        _rename(old_album_dir, new_album_dir, dry_run)
        if not dry_run and os.path.isdir(new_album_dir):
            _chown(new_album_dir, dry_run)
    elif os.path.isdir(new_album_dir):
        pass  # album folder already canonical (idempotent)
    else:
        print(f"    WARN: album folder not found: {old_album_dir}")
        return

    # 3. Adjust the audio file(s) in the (possibly renamed) album folder.
    target_album_dir = new_album_dir if os.path.isdir(new_album_dir) else old_album_dir
    _migrate_file_in(target_album_dir, old_title, new_title, dry_run)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrates the 7 UPPERCASE songs to canonical spelling.")
    parser.add_argument("music_dir", nargs="?", default=DEFAULT_MUSIC_DIR,
                        help=f"library base path (default: {DEFAULT_MUSIC_DIR})")
    parser.add_argument("--dry-run", action="store_true",
                        help="only show what would happen.")
    args = parser.parse_args()

    music_dir = args.music_dir
    if not os.path.isdir(music_dir):
        print(f"ERROR: library path does not exist: {music_dir}")
        return 1

    print(f"Library: {music_dir}{'  (DRY-RUN)' if args.dry_run else ''}")
    print(f"{len(RAW_SONGS)} songs will be checked.\n")

    for old_artist, old_title in RAW_SONGS:
        # Determine the target spelling exactly like the running service (Deezer).
        new_artist, new_title = _resolve_target(old_artist, old_title)
        _migrate_song(music_dir, old_artist, old_title,
                      new_artist, new_title, args.dry_run)

    print("\nDone. Year tags (date/originaldate) and the year map "
          "(.charts-flashback-years.json) were NOT touched "
          "(tags already correct; map key is lowercase -> casing-stable).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
