"""
Optional Lidarr integration.

Purpose: keep chart single artists in Lidarr "unmonitored" so Lidarr knows
(and can tag) them but does NOT download their complete catalog. This way the
default monitoring in Lidarr can stay enabled for your real albums/compilations
while the chart singles are exempted.

Flow per newly imported artist (after the charts-import run):
  1. Search for the artist in Lidarr (GET /api/v1/artist, name match).
     - found     -> set monitored=false (+ unmonitor albums)
     - not there -> optionally create via API, directly unmonitored
  2. This way Lidarr grabs nothing automatically for these artists.

Only active if LIDARR_URL + LIDARR_API_KEY are set (otherwise a quiet no-op).
All errors are only logged and do NOT affect the import.
"""

import logging
import unicodedata

import httpx

from . import config
from .i18n import t

logger = logging.getLogger(__name__)


def _norm(text: str) -> str:
    """Robust name comparison: accents removed, lowercase, alphanumeric only."""
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
    """Searches an artist already present in Lidarr by name comparison."""
    resp = client.get("/api/v1/artist")
    resp.raise_for_status()
    target = _norm(artist_name)
    for a in resp.json():
        if _norm(a.get("artistName", "")) == target:
            return a
    return None


def _lookup_artist(client: httpx.Client, artist_name: str) -> dict | None:
    """
    Resolves an artist via the MusicBrainz search (for new creation).
    Takes the first hit with an exact (normalized) name match, otherwise the
    first hit.
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
    """Sets all of an artist's albums to unmonitored. Returns the count."""
    resp = client.get("/api/v1/album", params={"artistId": artist_id})
    resp.raise_for_status()
    album_ids = [al["id"] for al in resp.json() if al.get("monitored")]
    if not album_ids:
        return 0
    # Lidarr bulk endpoint: unmonitor several albums in one call.
    client.put("/api/v1/album/monitor",
               json={"albumIds": album_ids, "monitored": False}).raise_for_status()
    return len(album_ids)


def _set_unmonitored(client: httpx.Client, artist: dict) -> None:
    """Sets an existing artist to unmonitored (artist + albums)."""
    if artist.get("monitored"):
        artist["monitored"] = False
        # monitorNewItems=none: do not automatically want future releases either.
        artist["monitorNewItems"] = "none"
        client.put(f"/api/v1/artist/{artist['id']}", json=artist).raise_for_status()
    n = _unmonitor_albums(client, artist["id"])
    logger.info(t("import.lidarr.unmonitored", artist=artist.get("artistName"), albums=n))


def _add_unmonitored(client: httpx.Client, artist_name: str) -> bool:
    """
    Creates a new artist in Lidarr, DIRECTLY unmonitored so Lidarr grabs
    nothing. Returns True on success.
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
    Main entry point: ensures the given artists are unmonitored in Lidarr (set
    existing ones, optionally create missing ones).

    No-op if Lidarr is not configured. Errors are only logged.
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
