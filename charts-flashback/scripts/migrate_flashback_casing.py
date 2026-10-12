#!/usr/bin/env python3
"""
Einmalige, idempotente Migration der 7 bereits GROSS in der Library liegenden
charts-flashback-Songs auf ihre kanonische Schreibweise.

Hintergrund: FLASHBACK FM lieferte Artist/Titel frueher in GROSSBUCHSTABEN;
diese Rohwerte landeten als Ordner-/Dateiname in der Library (z.B.
``U2/WHERE THE STREETS HAVE NO NAME/WHERE THE STREETS HAVE NO NAME.mp3``). Ab
sofort kanonisiert charts-flashback die Schreibweise ueber Deezer; dieses Skript
raeumt die 7 Altlasten nach.

KONSISTENZ IST DAS ZIEL: Kuenftige flashback-Songs bekommen GENAU das Casing,
das app.deezer.resolve_canonical() liefert. Damit Alt- und Neufaelle identisch
geschrieben sind, holt dieses Skript die Zielschreibweise der 7 Songs ZUR
LAUFZEIT ueber dieselbe resolve_canonical()-Funktion - KEINE hartkodierten
Zielnamen als Normalfall. So gibt es per Konstruktion keine Drift zwischen der
laufenden Aufloesung und der Migration.

FALLBACK: Liefert resolve_canonical() fuer einen Song keinen validierten
Deezer-Treffer (Netz weg / kein Match -> Rueckgabe == GROSS-Rohwert), faellt das
Skript fuer DIESEN Song auf eine hartkodierte Schreibweise (FALLBACK_CASING)
zurueck und LOGGT das deutlich (WARN), damit der User es manuell pruefen kann -
statt den Song GROSS zu lassen oder abzubrechen.

Manuell auf der NAS auszufuehren (youruser@your-nas), die Library liegt dort
unter /volume2/music/Music. Basis-Pfad per CLI-Arg oder Env MUSIC_DIR
ueberschreibbar (Default /volume2/music/Music). Das Skript importiert das
app-Paket (fuer resolve_canonical) - am einfachsten im Container ausfuehren:
``docker exec charts-flashback python /app/scripts/migrate_flashback_casing.py``
(Library dort unter /music/Music), oder auf der NAS mit gesetztem PYTHONPATH auf
das charts-flashback-Verzeichnis.

WICHTIG (Lehre aus der Erstmigration): Der charts-flashback-Container mountet
die Library READ-ONLY (/music:ro) - er muss nur lesen (Matching). Das --dry-run
laeuft im Container problemlos, der ECHTE Lauf scheitert dort aber mit
``OSError: [Errno 30] Read-only file system``. Darum den echten Rename ENTWEDER
direkt auf dem NAS-Host laufen lassen (als root, mit PYTHONPATH auf das
charts-flashback-Verzeichnis, damit ``import app.deezer`` klappt) ODER in einem
Container, der die Library beschreibbar gemountet hat (z.B. charts-import mit
/music=rw) - dann aber ohne resolve_canonical, da nur charts-flashback das
deezer-Modul hat. Fuer die Erstmigration wurde der Host-Weg genommen.

Pro Song wird umbenannt:
  - Artist-Ordner  <ARTIST GROSS>        -> <Artist kanonisch>
  - Album-Ordner   <TITLE GROSS>         -> <Title kanonisch>
  - Audiodatei(en) <TITLE GROSS>.<ext>   -> <Title kanonisch>.<ext>

Robustheit:
  - Idempotent: fehlt die Quelle, ist aber das Ziel schon da -> uebersprungen.
  - Zielpfad darf noch nicht existieren (ausser reiner Casing-Wechsel) -> sonst
    Konflikt-Warnung, kein Ueberschreiben.
  - Case-insensitive Mounts: reiner Casing-Wechsel laeuft ueber einen
    Zwischen-Rename (``.casingtmp``), damit er auch dort greift.
  - @eaDir (Synology-Thumbnails) wandert beim Ordner-Rename automatisch mit; es
    wird nicht gesondert angefasst (DSM erzeugt es bei Bedarf neu).
  - chown 1027:100 (PUID/PGID) auf jeden angefassten Pfad; Fehler nicht fatal.
  - --dry-run zeigt nur, was passieren wuerde.

WICHTIG - Jahr-Map NICHT anfassen: Die Jahr-Tags (date/originaldate) der 7 Songs
sind bereits manuell korrekt gesetzt und werden hier NICHT beruehrt (nur Ordner-
und Dateinamen). Der Key in .charts-flashback-years.json ist zudem lowercase
'artist|title' (flashback main._key == charts-import importer._flashback_year,
beide .lower()); Casing-Aenderungen am Namen aendern den Key NICHT -> die
Jahr-Map bleibt gueltig, kein Eingriff noetig.
"""

import argparse
import os
import sys

# app-Paket importierbar machen, egal von wo das Skript gestartet wird
# (scripts/ liegt direkt unter dem Projekt-Root, der das app-Paket enthaelt).
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from app import deezer  # noqa: E402

# PUID/PGID der Container-Workloads auf der NAS (youruser: UID 1027, GID 100).
CHOWN_UID = 1027
CHOWN_GID = 100

DEFAULT_MUSIC_DIR = os.environ.get("MUSIC_DIR", "/volume2/music/Music")

# Die 7 GROSS-Rohwerte (so liegen Ordner/Datei aktuell in der Library).
# Die Zielschreibweise wird ZUR LAUFZEIT via resolve_canonical() geholt.
RAW_SONGS = [
    ("FREDDIE MERCURY", "I WAS BORN TO LOVE YOU"),            # 1. (1985)
    ("BLOODHOUND GANG", "THE BAD TOUCH"),                     # 2. (1999)
    ("MEAT LOAF", "YOU TOOK THE WORDS RIGHT OUT OF MY MOUTH"),# 3. (1977)
    ("R.E.M", "MAN ON THE MOON"),                             # 4. (1992)
    ("PHILIP BAILEY FEAT. PHIL COLLINS", "EASY LOVER"),       # 5. (1984)
    ("U2", "WHERE THE STREETS HAVE NO NAME"),                 # 6. (1987)
    ("MARILLION", "KAYLEIGH"),                                # 7. (1985)
]

# Hartkodierte NOTFALL-Schreibweise - NUR benutzt, wenn resolve_canonical()
# keinen validierten Deezer-Treffer liefert (dann GROSS-Rohwert zurueck). Key =
# GROSS-Rohwert (artist, title). Wird dann mit lautem WARN-Log angewendet.
# Hinweis: Dies kann vom Live-Deezer-Casing leicht abweichen (z.B. kurze
# Woerter) - daher nur Fallback, nicht Normalfall.
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
    Zielschreibweise fuer einen Song: zuerst Deezer (resolve_canonical, exakt wie
    im laufenden Dienst), sonst hartkodierter Fallback mit lautem WARN-Log.

    resolve_canonical() gibt bei fehlendem/validiertem Deezer-Treffer die
    unveraenderten (GROSS-)Rohwerte zurueck. Erkennen wir das (Ergebnis ==
    Rohwert, obwohl der Rohwert GROSS ist), greifen wir auf FALLBACK_CASING.
    """
    new_artist, new_title = deezer.resolve_canonical(old_artist, old_title)
    unchanged = (new_artist == old_artist and new_title == old_title)
    if unchanged:
        fb = FALLBACK_CASING.get((old_artist, old_title))
        if fb:
            print(f"    WARN: Kein validierter Deezer-Treffer fuer "
                  f"'{old_artist} / {old_title}' -> Fallback-Casing "
                  f"'{fb[0]} / {fb[1]}' (bitte manuell pruefen).")
            return fb
        print(f"    WARN: Kein Deezer-Treffer UND kein Fallback fuer "
              f"'{old_artist} / {old_title}' -> bleibt GROSS (bitte pruefen).")
    return new_artist, new_title


def _chown(path: str, dry_run: bool) -> None:
    """Setzt Eigentuemer auf 1027:100. Fehler sind nicht fatal (nur Log)."""
    if dry_run:
        print(f"    [dry-run] chown {CHOWN_UID}:{CHOWN_GID} {path}")
        return
    try:
        os.chown(path, CHOWN_UID, CHOWN_GID)
    except OSError as e:
        print(f"    WARN: chown fehlgeschlagen ({path}): {e}")


def _rename(src: str, dst: str, dry_run: bool) -> bool:
    """
    Benennt src -> dst um. Reiner Casing-Wechsel (gleiche Pfade bei
    case-insensitivem Vergleich, aber unterschiedliche Schreibweise) laeuft ueber
    einen Zwischen-Rename, damit er auch auf case-insensitiven Mounts greift.
    Rueckgabe: True, wenn umbenannt (oder im dry-run wuerde).
    """
    if src == dst:
        return False
    # Reiner Casing-Wechsel plattformunabhaengig erkennen: os.path.normcase
    # lowercased NUR unter Windows, nicht unter Linux/macOS - deshalb direkt
    # ueber .lower() vergleichen.
    casing_only = src.lower() == dst.lower()

    # Konflikt nur melden, wenn das Ziel wirklich ein ANDERER Pfad ist (nicht
    # nur ein Casing-Wechsel). Auf case-insensitiven Mounts "existiert" das Ziel
    # bei reinem Casing-Wechsel scheinbar schon - das ist aber dieselbe Datei.
    if not casing_only and os.path.exists(dst):
        print(f"    WARN: Ziel existiert bereits, uebersprungen: {dst}")
        return False

    if dry_run:
        print(f"    [dry-run] rename {src} -> {dst}")
        return True

    if casing_only:
        tmp = src + ".casingtmp"
        # Falls ein frueherer Lauf abbrach, tmp aufraeumen.
        if os.path.exists(tmp) and tmp.lower() != src.lower():
            print(f"    WARN: Zwischenpfad existiert bereits: {tmp}")
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
    Benennt die Audiodatei(en) im Album-Ordner von <old_title>.<ext> auf
    <new_title>.<ext> um (Dateiendung bleibt). Fasst jede Datei an, deren
    Basisname (ohne Endung) dem alten Titel entspricht (case-insensitiv).
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

    # 1. Artist-Ordner auf kanonische Schreibweise bringen (falls noetig).
    if os.path.isdir(old_artist_dir):
        _rename(old_artist_dir, new_artist_dir, dry_run)
        if not dry_run and os.path.isdir(new_artist_dir):
            _chown(new_artist_dir, dry_run)
    elif os.path.isdir(new_artist_dir):
        pass  # Artist-Ordner schon kanonisch (idempotent)
    else:
        print(f"    WARN: Artist-Ordner nicht gefunden: {old_artist_dir}")
        return

    # Ab hier mit dem (ggf. schon umbenannten) kanonischen Artist-Ordner weiter.
    # Im dry-run existiert new_artist_dir evtl. noch nicht -> auf vorhandenen
    # Pfad zurueckfallen, damit die Vorschau weiterlaufen kann.
    base_artist_dir = new_artist_dir if os.path.isdir(new_artist_dir) else old_artist_dir

    old_album_dir = os.path.join(base_artist_dir, old_title)
    new_album_dir = os.path.join(base_artist_dir, new_title)

    # 2. Album-Ordner umbenennen (falls noetig).
    if os.path.isdir(old_album_dir):
        _rename(old_album_dir, new_album_dir, dry_run)
        if not dry_run and os.path.isdir(new_album_dir):
            _chown(new_album_dir, dry_run)
    elif os.path.isdir(new_album_dir):
        pass  # Album-Ordner schon kanonisch (idempotent)
    else:
        print(f"    WARN: Album-Ordner nicht gefunden: {old_album_dir}")
        return

    # 3. Audiodatei(en) im (ggf. umbenannten) Album-Ordner anpassen.
    target_album_dir = new_album_dir if os.path.isdir(new_album_dir) else old_album_dir
    _migrate_file_in(target_album_dir, old_title, new_title, dry_run)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migriert die 7 GROSS-Songs auf kanonische Schreibweise.")
    parser.add_argument("music_dir", nargs="?", default=DEFAULT_MUSIC_DIR,
                        help=f"Library-Basis-Pfad (Default: {DEFAULT_MUSIC_DIR})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Nur anzeigen, was passieren wuerde.")
    args = parser.parse_args()

    music_dir = args.music_dir
    if not os.path.isdir(music_dir):
        print(f"FEHLER: Library-Pfad existiert nicht: {music_dir}")
        return 1

    print(f"Library: {music_dir}{'  (DRY-RUN)' if args.dry_run else ''}")
    print(f"{len(RAW_SONGS)} Songs werden geprueft.\n")

    for old_artist, old_title in RAW_SONGS:
        # Zielschreibweise exakt wie der laufende Dienst (Deezer) ermitteln.
        new_artist, new_title = _resolve_target(old_artist, old_title)
        _migrate_song(music_dir, old_artist, old_title,
                      new_artist, new_title, args.dry_run)

    print("\nFertig. Jahr-Tags (date/originaldate) und Jahr-Map "
          "(.charts-flashback-years.json) wurden NICHT angefasst "
          "(Tags bereits korrekt; Map-Key ist lowercase -> casing-stabil).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
