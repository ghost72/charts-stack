"""
Optionale Lidarr-Integration.

Zweck: Chart-Single-Kuenstler in Lidarr auf "unmonitored" halten, damit Lidarr
sie zwar kennt (und taggen kann), aber NICHT ihren kompletten Katalog
herunterlaedt. So kann das Standard-Monitoring in Lidarr fuer deine echten
Alben/Compilations aktiviert bleiben, waehrend die Charts-Singles davon
ausgenommen werden.

Ablauf pro neu importiertem Kuenstler (nach dem charts-import-Lauf):
  1. Kuenstler in Lidarr suchen (GET /api/v1/artist, Name-Match).
     - gefunden  -> auf monitored=false setzen (+ Alben unmonitoren)
     - nicht da  -> optional per API neu anlegen, direkt unmonitored
  2. So grabbt Lidarr fuer diese Kuenstler nichts automatisch.

Nur aktiv, wenn LIDARR_URL + LIDARR_API_KEY gesetzt sind (sonst stiller No-op).
Alle Fehler werden nur geloggt und beeintraechtigen den Import NICHT.
"""

import logging
import unicodedata

import httpx

from . import config
from .i18n import t

logger = logging.getLogger(__name__)


def _norm(text: str) -> str:
    """Robuster Namensvergleich: Akzente weg, klein, nur alphanumerisch."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return "".join(c for c in text if c.isalnum())


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=config.LIDARR_URL.rstrip("/"),
        headers={"X-Api-Key": config.LIDARR_API_KEY},
        timeout=30.0,
    )


def _find_artist(client: httpx.Client, artist_name: str) -> dict | None:
    """Sucht einen bereits in Lidarr vorhandenen Kuenstler per Namensvergleich."""
    resp = client.get("/api/v1/artist")
    resp.raise_for_status()
    target = _norm(artist_name)
    for a in resp.json():
        if _norm(a.get("artistName", "")) == target:
            return a
    return None


def _lookup_artist(client: httpx.Client, artist_name: str) -> dict | None:
    """
    Loest einen Kuenstler ueber die MusicBrainz-Suche auf (fuer Neuanlage).
    Nimmt den ersten Treffer mit exaktem (normalisiertem) Namensmatch, sonst
    den ersten Treffer.
    """
    resp = client.get("/api/v1/artist/lookup", params={"term": artist_name})
    resp.raise_for_status()
    results = resp.json()
    if not results:
        return None
    target = _norm(artist_name)
    for r in results:
        if _norm(r.get("artistName", "")) == target:
            return r
    return results[0]


def _unmonitor_albums(client: httpx.Client, artist_id: int) -> int:
    """Setzt alle Alben eines Kuenstlers auf unmonitored. Gibt Anzahl zurueck."""
    resp = client.get("/api/v1/album", params={"artistId": artist_id})
    resp.raise_for_status()
    album_ids = [al["id"] for al in resp.json() if al.get("monitored")]
    if not album_ids:
        return 0
    # Lidarr-Bulk-Endpoint: mehrere Alben in einem Call unmonitoren.
    client.put("/api/v1/album/monitor",
               json={"albumIds": album_ids, "monitored": False}).raise_for_status()
    return len(album_ids)


def _set_unmonitored(client: httpx.Client, artist: dict) -> None:
    """Setzt einen vorhandenen Kuenstler auf unmonitored (Artist + Alben)."""
    if artist.get("monitored"):
        artist["monitored"] = False
        # monitorNewItems=none: auch kuenftige Releases nicht automatisch wollen.
        artist["monitorNewItems"] = "none"
        client.put(f"/api/v1/artist/{artist['id']}", json=artist).raise_for_status()
    n = _unmonitor_albums(client, artist["id"])
    logger.info(t("import.lidarr.unmonitored", artist=artist.get("artistName"), albums=n))


def _add_unmonitored(client: httpx.Client, artist_name: str) -> bool:
    """
    Legt einen neuen Kuenstler in Lidarr an, DIREKT unmonitored, damit Lidarr
    nichts grabbt. Gibt True bei Erfolg zurueck.
    """
    if not config.LIDARR_ADD_MISSING:
        logger.debug(t("import.lidarr.add_disabled", artist=artist_name))
        return False
    found = _lookup_artist(client, artist_name)
    if not found or not found.get("foreignArtistId"):
        logger.info(t("import.lidarr.lookup_failed", artist=artist_name))
        return False
    payload = {
        "artistName": found["artistName"],
        "foreignArtistId": found["foreignArtistId"],
        "qualityProfileId": config.LIDARR_QUALITY_PROFILE_ID,
        "metadataProfileId": config.LIDARR_METADATA_PROFILE_ID,
        "rootFolderPath": config.LIDARR_ROOT_FOLDER,
        "monitored": False,
        "monitorNewItems": "none",
        "addOptions": {"monitor": "none", "searchForMissingAlbums": False},
    }
    client.post("/api/v1/artist", json=payload).raise_for_status()
    logger.info(t("import.lidarr.added", artist=found["artistName"]))
    return True


def unmonitor_artists(artist_names: list[str]) -> None:
    """
    Haupt-Einstieg: sorgt dafuer, dass die uebergebenen Kuenstler in Lidarr
    unmonitored sind (vorhandene setzen, fehlende optional anlegen).

    No-op, wenn Lidarr nicht konfiguriert ist. Fehler werden nur geloggt.
    """
    if not config.LIDARR_UNMONITOR:
        return
    if not config.LIDARR_URL or not config.LIDARR_API_KEY:
        logger.debug(t("import.lidarr.disabled"))
        return

    unique = sorted({n.strip() for n in artist_names if n and n.strip()})
    if not unique:
        return

    try:
        with _client() as client:
            for name in unique:
                try:
                    artist = _find_artist(client, name)
                    if artist:
                        _set_unmonitored(client, artist)
                    else:
                        _add_unmonitored(client, name)
                except Exception as e:
                    logger.warning(t("import.lidarr.artist_failed", artist=name, error=e))
    except Exception as e:
        logger.warning(t("import.lidarr.unreachable", error=e))
