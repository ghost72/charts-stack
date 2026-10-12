"""
Manifest fuer die Uebergabe der sauberen Chart-Metadaten an charts-import.

Problem: slskd-Downloads haben oft schlechte oder fehlende Tags. charts-import
wuerde sie dann als "Unknown Artist/Unknown Album" einsortieren, und charts-sync
kann sie nicht gegen die Charts matchen.

Loesung: charts-download kennt beim Download den ECHTEN Chart-Kuenstler und
-Titel. Diese Zuordnung wird hier in eine gemeinsame JSON-Datei geschrieben
(im /charts-Ordner, den auch charts-import sieht). Schluessel ist der Basename
der heruntergeladenen Datei - der bleibt erhalten, egal wie slskd den Pfad
verschachtelt. charts-import schlaegt darin nach und taggt die Datei sauber.

Format:
{
  "entries": {
    "golden.mp3": {"artist": "HUNTR/X", "title": "Golden", "album": "Golden",
                   "source": "charts"},
    ...
  }
}

"source" ist das Playlist-Label (aus der missing-Datei, z.B. "charts",
"mundart", "80er-90er"), damit charts-import in seiner Import-Meldung die
Herkunft anzeigen kann. Optional - aeltere Eintraege ohne "source" sind ok.

"notify_device" (optional) ist der Pushover-Geraetename des Users, der den Song
manuell via charts-songrequest angefordert hat. charts-import schickt nach dem
Import eine Verfuegbarkeits-Meldung an genau dieses Geraet. Fehlt das Feld (alle
Nicht-manuellen Downloads sowie alte Eintraege), gibt es keine solche Meldung.
"""

import json
import logging
import os

from . import config
from .i18n import t

logger = logging.getLogger(__name__)


def _load(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and "entries" in data:
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {"entries": {}}


def _save(path: str, data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError as e:
        logger.error(t("download.manifest.save_failed", error=e))


def _basename_key(filename: str) -> str:
    """Normalisierter Schluessel: reiner Dateiname, kleingeschrieben."""
    base = os.path.basename(filename.replace("\\", "/"))
    return base.lower()


def record(filename: str, artist: str, title: str, source: str = "",
           replace: bool = False, notify_device: str = "") -> None:
    """
    Vermerkt fuer die heruntergeladene Datei die sauberen Chart-Metadaten.
    Album wird auf den Titel gesetzt (Chart-Songs sind Singles), damit
    charts-import nicht auf "Unknown Album" zurueckfaellt.

    source = Playlist-Label (z.B. "charts", "mundart"), damit charts-import in
    der Import-Meldung die Herkunft anzeigen kann.

    replace = True signalisiert charts-import einen QUALITAETS-UPGRADE: der Song
    ist evtl. schon in der Library, soll aber ersetzt werden, WENN die neue
    Datei eine hoehere Bitrate hat. Wird bei "Trotzdem laden" im UI gesetzt.

    notify_device = Pushover-Geraet des anfordernden Users (nur bei manuellen
    Requests via charts-songrequest). Ist es gesetzt, schickt charts-import nach
    dem erfolgreichen Import eine Verfuegbarkeits-Meldung an GENAU dieses Geraet.
    Leer -> kein Feld geschrieben -> keine Meldung (nie an alle senden).
    """
    data = _load(config.MANIFEST_FILE)
    entry = {
        "artist": artist,
        "title": title,
        "album": title,
    }
    if source:
        entry["source"] = source
    if replace:
        entry["replace"] = True
    if notify_device:
        entry["notify_device"] = notify_device
    data["entries"][_basename_key(filename)] = entry
    _save(config.MANIFEST_FILE, data)
