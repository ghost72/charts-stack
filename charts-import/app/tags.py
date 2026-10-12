"""
Tag-Lesung und Zielpfad-Bildung.

Metadaten-Quellen in dieser Reihenfolge:
  1. Manifest von charts-download (SAUBERE Chart-Artist/Titel) - bevorzugt.
     Wird zusaetzlich in die Datei-Tags geschrieben, damit Navidrome/charts-sync
     korrekte Werte sehen (statt "Unknown Album").
  2. Datei-Tags (mutagen).
  3. Fallback auf den Dateinamen.

Zielpfad-Schema: Music/<Artist>/<Album>/<Track>.<ext>.
"""

import json
import logging
import os
import re

from mutagen import File as MutagenFile

from . import config
from .i18n import t

logger = logging.getLogger(__name__)

# --- Herkunfts-/Encoder-Tags, die aus Chart-Downloads entfernt werden ---
# Diese Frames verraten die QUELLE (Soulseek-Uploader/Release-Group-Signaturen
# wie "eXenz", "PMEDIA", "www.t.me/...", der fre:ac-Werbekommentar) bzw. das
# Rip-/Encoder-Tool. Sie werden bei jedem Manifest-Import entfernt, damit keine
# Herkunftsspuren in der Library landen. Gezielt per ID3-Vollschnittstelle
# (NICHT easy=True, der COMM/TENC/TXXX nicht zuverlaessig loeschen kann).
#
# WICHTIG - WAS GELOESCHT WIRD:
#   COMM = Kommentar (fast immer Uploader-/Release-Group-Signatur) -> leeren
#   TENC = Encoded by, TSSE = Encoder-Settings (Rip-Tool) -> leeren
#   WXXX/WCOM/WOAF/WPUB = URL-Frames (Quellen-Links) -> leeren
#   TOWN = File owner -> leeren
#   TXXX = benutzerdefinierte Freitext-Frames: NUR geloescht, wenn der
#          Beschreibungsname NICHT auf der Schutzliste steht (s.u.).
#
# WAS GESCHUETZT BLEIBT (NIE angefasst) - inhaltlich wertvolle Tags, die bei
# dieser Library in TXXX stecken: MusicBrainz-IDs und ReplayGain-Werte. Diese
# duerfen auf KEINEN Fall verloren gehen (Metadaten/Lautstaerke-Normalisierung).
_PROVENANCE_FULL_PREFIXES = ("COMM", "TENC", "TSSE", "WXXX",
                             "WCOM", "WOAF", "WPUB", "TOWN")
# TXXX-Beschreibungen (Teil nach "TXXX:"), die GESCHUETZT sind (case-insensitiv,
# als Substring geprueft): alles rund um MusicBrainz und ReplayGain.
_TXXX_PROTECTED_SUBSTR = ("musicbrainz", "replaygain")


def _is_protected_txxx(frame_key: str) -> bool:
    """True, wenn ein TXXX-Frame geschuetzt ist (MusicBrainz/ReplayGain)."""
    ks = str(frame_key)
    if not ks.upper().startswith("TXXX"):
        return False
    desc = ks.split(":", 1)[1].lower() if ":" in ks else ""
    return any(sub in desc for sub in _TXXX_PROTECTED_SUBSTR)


def strip_provenance_tags(path: str) -> bool:
    """
    Entfernt Herkunfts-/Encoder-Frames (COMM/TENC/TSSE/WXXX/... + nicht
    geschuetzte TXXX) aus einer Audiodatei, laesst alle inhaltlichen Tags
    unberuehrt - insbesondere TXXX:MusicBrainz* und TXXX:*replaygain*. Verlustfrei
    (nur Metadaten, kein Re-Encoding). Gibt True zurueck, wenn tatsaechlich etwas
    entfernt wurde (dann wurde die Datei neu gespeichert), sonst False.
    Fehler sind nicht fatal (Log + False).
    """
    # Direkt die ID3-Tag-Sicht nutzen (nur der Tag-Block, NICHT das Audio) -
    # robuster als MutagenFile(), das die MPEG-Frames parst und bei leicht
    # defekten Dateien mit "can't sync to MPEG frame" scheitern wuerde.
    try:
        from mutagen.id3 import ID3, ID3NoHeaderError
        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            return False  # kein ID3-Block -> nichts zu entfernen
        to_delete = []
        for k in list(tags.keys()):
            ku = str(k).upper()
            if ku.startswith("TXXX"):
                # TXXX nur, wenn NICHT geschuetzt (MusicBrainz/ReplayGain bleiben).
                if not _is_protected_txxx(k):
                    to_delete.append(k)
            elif ku.startswith(_PROVENANCE_FULL_PREFIXES):
                to_delete.append(k)
        if not to_delete:
            return False
        for k in to_delete:
            try:
                del tags[k]
            except Exception:
                pass
        tags.save(path)
        return True
    except Exception as e:
        logger.warning(t("import.tags.strip_failed", path=path, error=e))
        return False

# Manifest-Cache (pro Prozess einmal laden pro Lauf reicht, aber wir lesen es
# hier lazy und cachen es simpel).
_manifest_cache: dict | None = None


def _load_manifest() -> dict:
    """Laedt das charts-download-Manifest (Basename -> Metadaten)."""
    global _manifest_cache
    if _manifest_cache is not None:
        return _manifest_cache
    entries = {}
    try:
        with open(config.MANIFEST_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            entries = data.get("entries", {})
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        entries = {}
    _manifest_cache = entries
    return entries


def reload_manifest() -> None:
    """Erzwingt ein Neuladen des Manifests (zu Beginn jedes Laufs)."""
    global _manifest_cache
    _manifest_cache = None


def _manifest_lookup(path: str) -> dict | None:
    """
    Sucht die sauberen Metadaten fuer eine Datei anhand des Basenamens.
    Toleriert slskd-typische numerische Suffixe (z.B.
    'Stargazing_639241038209671901.mp3'), damit dieselbe Datei von
    verschiedenen Anbietern denselben Manifest-Eintrag findet.
    """
    entries = _load_manifest()
    base = os.path.basename(path.replace("\\", "/")).lower()
    # 1. exakter Treffer
    if base in entries:
        return entries[base]
    # 2. ohne slskd-Suffix vor der Endung: 'name_639....mp3' -> 'name.mp3'
    stripped = re.sub(r"_\d{6,}(\.[a-z0-9]+)$", r"\1", base)
    if stripped != base and stripped in entries:
        return entries[stripped]
    return None


def manifest_lookup(path: str) -> dict | None:
    """Oeffentlicher Zugriff auf den Manifest-Eintrag einer Datei (oder None)."""
    return _manifest_lookup(path)

# Zeichen, die in Datei-/Ordnernamen auf den meisten Dateisystemen Probleme
# machen. Werden durch Unterstriche ersetzt.
_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _clean_component(value: str, fallback: str) -> str:
    """Bereinigt einen einzelnen Pfad-Bestandteil (Ordner- oder Dateiname)."""
    value = (value or "").strip()
    if not value:
        value = fallback
    # Ungueltige Zeichen ersetzen, Mehrfach-Leerzeichen zusammenfassen,
    # abschliessende Punkte/Leerzeichen entfernen (Windows/SMB-Kompatibilitaet).
    value = _INVALID_CHARS.sub("_", value)
    value = re.sub(r"\s+", " ", value).strip()
    value = value.rstrip(". ")
    return value or fallback


def _first_tag(audio, *keys) -> str:
    """Liefert den ersten vorhandenen Tag-Wert aus einer Liste moeglicher Keys."""
    for key in keys:
        try:
            value = audio.get(key)
        except Exception:
            value = None
        if not value:
            continue
        # mutagen liefert je nach Format Listen oder Strings.
        if isinstance(value, list):
            value = value[0] if value else ""
        text = str(value).strip()
        if text:
            return text
    return ""


def read_metadata(path: str) -> tuple[str, str, str, bool]:
    """
    Liefert (artist, album, title, from_manifest).

    Reihenfolge:
      1. Manifest (saubere Chart-Metadaten von charts-download) -> bevorzugt.
      2. Datei-Tags (mutagen).
      3. Fallback auf Dateiname / "Unknown ...".

    from_manifest=True signalisiert, dass die Werte aus dem Manifest stammen und
    daher auch in die Datei-Tags geschrieben werden sollten.
    """
    filename = os.path.splitext(os.path.basename(path))[0]

    # 1. Manifest bevorzugen. WICHTIG: Die ORIGINAL-Werte (fuer die Tags)
    # zurueckgeben, nicht die pfad-bereinigten - sonst wuerde z.B. "HUNTR/X"
    # als Tag "HUNTR_X" gespeichert und charts-sync koennte es nicht matchen.
    # Die Pfad-Bereinigung passiert erst in build_target_path.
    m = _manifest_lookup(path)
    if m and m.get("artist") and m.get("title"):
        artist = (m.get("artist") or "").strip() or "Unknown Artist"
        title = (m.get("title") or "").strip() or filename
        album = (m.get("album") or m.get("title") or "").strip() or title
        return artist, album, title, True

    # 2. Datei-Tags
    artist = album = title = ""
    try:
        audio = MutagenFile(path, easy=True)
        if audio is not None:
            artist = _first_tag(audio, "albumartist", "artist")
            album = _first_tag(audio, "album")
            title = _first_tag(audio, "title")
    except Exception as e:
        logger.warning(t("import.tags.read_failed", path=path, error=e))

    # 3. Fallback
    artist = _clean_component(artist, "Unknown Artist")
    album = _clean_component(album, "Unknown Album")
    title = _clean_component(title, filename)
    return artist, album, title, False


def write_tags(path: str, artist: str, title: str, album: str,
               year=None) -> None:
    """
    Schreibt Artist/Titel/Album in die Datei-Tags (nur fuer Manifest-Treffer).
    Fehler werden geloggt, aber nicht weitergereicht - der Import soll auch
    dann gelingen, wenn das Tag-Schreiben fehlschlaegt.

    year = optionales Erscheinungsjahr aus dem Manifest (z.B. "1987"). Ist es
    gesetzt, wird es als date/originaldate-Tag geschrieben, damit die
    Dekaden-Smart-Playlists in Navidrome den Song der richtigen Dekade zuordnen.
    Fehlt year (None/""), bleiben die Datum-Tags unangetastet -> Verhalten wie
    bisher (kein Ueberschreiben vorhandener Jahre mit Leerwert).
    """
    try:
        audio = MutagenFile(path, easy=True)
        if audio is None:
            logger.debug(t("import.tags.not_writable", path=path))
            return
        audio["artist"] = artist
        audio["albumartist"] = artist
        audio["title"] = title
        audio["album"] = album
        if year:
            audio["date"] = str(year)
            audio["originaldate"] = str(year)
        audio.save()
    except Exception as e:
        logger.warning(t("import.tags.write_failed", path=path, error=e))
    # Herkunfts-/Encoder-Frames (fre:ac-Kommentar, TENC/TSSE/TXXX ...) entfernen,
    # damit keine Quellen-Spuren in der Library landen. Separat nach dem
    # Easy-Save, weil der Easy-Modus diese Frames nicht loeschen kann.
    strip_provenance_tags(path)


def canonical_artist(music_dir: str, artist: str) -> str | None:
    """
    Liefert die KANONISCHE Kuenstler-Schreibweise = der echte Basename eines
    bereits vorhandenen Kuenstler-Ordners unter music_dir (CASE-INSENSITIV
    gesucht), oder None, wenn der Kuenstler noch nicht existiert.

    Grund: Manifest-/Tag-Werte koennen eine abweichende Gross-/Kleinschreibung
    tragen (z.B. ein alter 'rod stewart'-Manifest-Eintrag), waehrend die Library
    den Kuenstler schon als 'Rod Stewart' fuehrt. Ohne diese Aufloesung entstuende
    auf case-sensitiven Dateisystemen (ext4) ein zweiter Ordner 'rod stewart'.
    Dies greift fuer ALLE Dienste, die charts-import nutzt (songrequest, sync,
    download) - nicht nur fuer manuelle Requests.
    """
    safe_artist = _clean_component(artist, "")
    if not safe_artist or not music_dir or not os.path.isdir(music_dir):
        return None
    # 1. Exakter Ordner vorhanden -> echten Basename vom FS holen (nicht den
    #    konstruierten Pfad, der auf case-insensitiven FS die Eingabe spiegelt).
    target = safe_artist.lower()
    try:
        for name in os.listdir(music_dir):
            if name.lower() == target and os.path.isdir(os.path.join(music_dir, name)):
                return name
    except OSError:
        pass
    return None


def _looks_uppercase(artist: str, title: str) -> bool:
    """
    True, wenn Artist ODER Titel 'verdaechtig GROSS' ist (mind. ein Buchstabe,
    komplett Grossschreibung, laenger als 3 Zeichen). Kurze echte Akronyme wie
    'U2', 'ABBA', 'INXS' sollen NICHT ausloesen, wenn der Rest sauber ist -
    daher die Laengenschwelle und die ODER-Logik nur als Verdachts-Gate (die
    eigentliche Entscheidung faellt die Deezer-Validierung).
    """
    for s in (artist, title):
        s = (s or "").strip()
        if s and len(s) > 3 and any(c.isalpha() for c in s) and s == s.upper():
            return True
    return False


def _canonicalize(artist: str, title: str) -> tuple[str, str]:
    """
    Loest die kanonische (Mixed-Case-)Schreibweise ueber Deezer auf. Lazy-Import
    des deezer-Moduls, damit tags.py ohne httpx/Netz importierbar bleibt und ein
    Fehler nie den Import blockiert (Fallback: unveraenderte Rohwerte).
    """
    try:
        from . import deezer
        return deezer.resolve_canonical(artist, title)
    except Exception as e:  # Import-/Laufzeitfehler nie fatal
        logger.debug(t("import.tags.canonicalize_skipped", artist=artist,
                       title=title, error=e))
        return artist, title


def build_target_path(music_dir: str, path: str) -> tuple[str, tuple]:
    """
    Baut den Zielpfad Music/<Artist>/<Album>/<Track>.<ext>.

    Rueckgabe: (zielpfad, (artist, title, album, from_manifest)) - die
    Metadaten werden mit zurueckgegeben, damit der Importer bei Manifest-
    Treffern die Tags in der (kopierten) Datei setzen kann.
    """
    artist, album, title, from_manifest = read_metadata(path)
    ext = os.path.splitext(path)[1].lower()
    # DEEZER-KANONISIERUNG (letzte Instanz): Ist der Manifest-Name noch GROSS/
    # verdaechtig (durchgerutschter flashback-Rohwert), die offizielle
    # Mixed-Case-Schreibweise ueber Deezer aufloesen - BEVOR Pfad/Tags gebildet
    # werden. Nur fuer Manifest-Treffer und nur bei GROSS-Verdacht (spart
    # API-Last; saubere Namen bleiben unberuehrt). Liefert Deezer keinen
    # sicheren Treffer, bleibt der Rohwert (unresolved_upper=True -> der Importer
    # meldet das per Pushover zur manuellen Pruefung, importiert aber trotzdem).
    unresolved_upper = False
    if from_manifest and _looks_uppercase(artist, title):
        new_artist, new_title = _canonicalize(artist, title)
        if (new_artist, new_title) != (artist, title):
            artist, title = new_artist, new_title
            # Album war bei Chart-Singles = Titel -> mitziehen, wenn es dem
            # alten Titel entsprach (sonst Album unveraendert lassen).
            if _looks_uppercase("", album) or album.lower() == title.lower():
                album = new_title
        else:
            unresolved_upper = True
    # KANONISCHE Kuenstler-Schreibweise erzwingen: existiert der Kuenstler
    # bereits (egal in welcher Schreibweise), dessen echten Ordnernamen nehmen -
    # fuer Pfad UND Tag. Verhindert Dubletten wie 'rod stewart' neben
    # 'Rod Stewart' und sorgt dafuer, dass die etablierte Schreibweise gewinnt,
    # egal was Manifest/Tags sagen.
    canon = canonical_artist(music_dir, artist)
    if canon:
        artist = canon
    # Pfad-Bestandteile bereinigen (Dateisystem-sicher), Tags behalten Original.
    safe_artist = _clean_component(artist, "Unknown Artist")
    safe_album = _clean_component(album, "Unknown Album")
    track_name = _clean_component(title, "track") + ext
    target = os.path.join(music_dir, safe_artist, safe_album, track_name)
    return target, (artist, title, album, from_manifest, unresolved_upper)
