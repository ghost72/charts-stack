"""
Pruefung, ob ein Song bereits in der Navidrome-Library liegt.

Vor der slskd-Suche wird geprueft, ob der angeforderte Song schon unter
MUSIC_DIR/<Artist>/ existiert - egal in welchem Album-Ordner und egal ob als
Single oder Lidarr-Album-Track. So wird kein Duplikat geladen und das UI kann
sofort "schon vorhanden" melden.

Die Logik ist bewusst identisch zu charts-import (_song_present_for_artist /
_title_key / _extract_title_from_filename), damit beide Dienste denselben Song
als "vorhanden" erkennen.
"""

import os
import re

# Als Audio erkannte Endungen (wie charts-import).
AUDIO_EXTENSIONS = {".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".wma"}

# Ungueltige Zeichen fuer Ordnernamen (wie tags._clean_component in charts-import).
_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _clean_component(value: str, fallback: str) -> str:
    """Bereinigt einen Pfad-Bestandteil (Artist-Ordnername) - wie charts-import."""
    value = (value or "").strip()
    if not value:
        value = fallback
    value = _INVALID_CHARS.sub("_", value)
    value = re.sub(r"\s+", " ", value).strip()
    value = value.rstrip(". ")
    return value or fallback


def _title_key(title: str) -> str:
    """Normalisierter Songtitel fuer den Vergleich (identisch zu charts-import)."""
    t = (title or "").lower()
    t = re.sub(r"\((?:feat|ft|with)\.?[^)]*\)", " ", t)
    t = re.sub(r"\[(?:feat|ft|with)\.?[^\]]*\]", " ", t)
    t = re.sub(r"\((?:remaster|remastered|.*version|.*edit|official|audio|video|lyric[s]?)[^)]*\)", " ", t)
    return re.sub(r"[^a-z0-9]+", "", t)


def _extract_title_from_filename(filename: str) -> str:
    """Holt den Songtitel aus einem Dateinamen (identisch zu charts-import)."""
    base = os.path.splitext(os.path.basename(filename))[0]
    base = re.sub(r"\s*\(\d+\)\s*$", "", base)   # (2), (3)
    base = re.sub(r"_\d{6,}$", "", base)          # slskd-Suffix
    if " - " in base:
        base = base.rsplit(" - ", 1)[-1]
    base = re.sub(r"^\s*\d{1,3}\s*[-.\)]\s*", "", base)  # fuehrende Tracknr.
    return base.strip()


def _find_artist_dir(music_dir: str, artist: str) -> str | None:
    """
    Findet den Kuenstler-Ordner unter music_dir - CASE-INSENSITIV. Auf ext4
    (Linux) sind Ordnernamen case-sensitiv, d.h. 'dermot kennedy' wuerde den
    Ordner 'Dermot Kennedy' sonst verfehlen. Wir vergleichen daher normalisiert
    (bereinigt + lowercase), damit die Eingabe-Schreibweise egal ist.
    """
    safe_artist = _clean_component(artist, "")
    if not safe_artist:
        return None
    # 1. Schneller Weg: exakter Ordner existiert.
    exact = os.path.join(music_dir, safe_artist)
    if os.path.isdir(exact):
        return exact
    # 2. Case-insensitiver Vergleich ueber alle Kuenstler-Ordner.
    target = safe_artist.lower()
    try:
        for name in os.listdir(music_dir):
            if name.lower() == target and os.path.isdir(os.path.join(music_dir, name)):
                return os.path.join(music_dir, name)
    except OSError:
        pass
    return None


def read_bitrate(path: str) -> int:
    """
    Liest die Bitrate (kbps) einer Audiodatei. 0, wenn nicht lesbar.
    Fuer den Qualitaetsvergleich vorhandene Datei vs. slskd-Treffer.
    """
    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(path)
        if audio is not None and audio.info is not None:
            br = getattr(audio.info, "bitrate", 0) or 0
            return int(br / 1000)  # mutagen liefert bit/s -> kbps
    except Exception:
        pass
    return 0


def find_existing(music_dir: str, artist: str, title: str) -> str | None:
    """
    Gibt den Pfad zurueck, wenn der Song bereits unter MUSIC_DIR/<Artist>/
    existiert (egal welches Album), sonst None. Vergleich ueber den
    normalisierten Songtitel; der Kuenstler-Ordner wird case-insensitiv
    gesucht, damit die Eingabe-Schreibweise keine Rolle spielt.
    """
    if not music_dir or not os.path.isdir(music_dir):
        return None
    artist_dir = _find_artist_dir(music_dir, artist)
    if not artist_dir:
        return None
    want = _title_key(title)
    if not want:
        return None
    try:
        for album in os.listdir(artist_dir):
            album_path = os.path.join(artist_dir, album)
            if not os.path.isdir(album_path):
                continue
            for name in os.listdir(album_path):
                ext = os.path.splitext(name)[1].lower()
                if ext not in AUDIO_EXTENSIONS:
                    continue
                if _title_key(_extract_title_from_filename(name)) == want:
                    return os.path.join(album_path, name)
    except OSError:
        pass
    return None


def canonical_artist(music_dir: str, artist: str) -> str | None:
    """
    Liefert die KANONISCHE Schreibweise des Kuenstlers = der echte Basename des
    bereits vorhandenen Kuenstler-Ordners (z.B. Eingabe 'rod stewart' ->
    'Rod Stewart'), oder None, wenn der Kuenstler noch nicht in der Library ist.

    Grund: Der Ordner wird case-insensitiv gefunden, aber der neue Song soll mit
    der bereits etablierten Schreibweise getaggt/abgelegt werden statt mit der
    (evtl. klein getippten) User-Eingabe.
    """
    if not music_dir or not os.path.isdir(music_dir):
        return None
    artist_dir = _find_artist_dir(music_dir, artist)
    if not artist_dir:
        return None
    # WICHTIG: Den ECHTEN Ordnernamen vom Dateisystem holen, nicht den von
    # _find_artist_dir evtl. konstruierten Pfad. Auf case-INSENSITIVEN FS
    # (z.B. macOS/SMB) wuerde dessen Schnellpfad os.path.join(dir, eingabe)
    # treffen und die klein getippte Eingabe zurueckgeben. Darum den Parent
    # scannen und den real existierenden Namen case-insensitiv auswaehlen.
    want = os.path.basename(artist_dir)
    try:
        for name in os.listdir(music_dir):
            if name.lower() == want.lower() and os.path.isdir(
                    os.path.join(music_dir, name)):
                return name
    except OSError:
        pass
    return want or None


def canonical_title_from_file(path: str) -> str:
    """
    Holt den kanonischen Songtitel aus dem Dateinamen einer VORHANDENEN Datei
    (fuer den Upgrade-Fall: die vorhandene Schreibweise beibehalten statt der
    User-Eingabe). Nutzt dieselbe Extraktion wie das Vorhanden-Matching.
    """
    return _extract_title_from_filename(path)


def title_case(title: str) -> str:
    """
    Konservative Title-Case-Normalisierung fuer NEU angeforderte Songs (kein
    vorhandener Datei-Anker): jeder durch Leerzeichen getrennte Wortteil bekommt
    einen grossen Anfangsbuchstaben, der Rest des Wortes bleibt unveraendert
    (so bleibt z.B. 'all for love' -> 'All For Love', aber bereits gesetzte
    Binnengrossschreibung wie 'iTunes' wird nicht zerstoert). Bewusst simpel -
    keine Sonderfall-Behandlung von Fuellwoertern.
    """
    t = (title or "").strip()
    if not t:
        return t
    return " ".join(w[:1].upper() + w[1:] if w else w for w in t.split(" "))
