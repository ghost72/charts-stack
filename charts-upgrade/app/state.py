"""
Persistenter Zustand fuer charts-upgrade.

Enthaelt:
  - "seen":  Cache der bereits analysierten Dateien {abspath: "mtime:size"},
             damit die CPU-teure ffmpeg-Cutoff-Analyse pro Datei nur einmal
             laeuft (nicht bei jedem Lauf erneut).
  - "requested": bereits angestossene Upgrades {song_key: {...}} - verhindert,
             dass derselbe Song mehrfach parallel geladen wird.
  - "blocked_providers": {song_key: {username: ts}} - Anbieter, die fuer einen
             Song eine Datei geliefert haben, die NICHT besser war als die
             vorhandene. Werden bei der naechsten Suche uebersprungen (kein
             endloses "laden -> nicht besser -> wieder denselben laden").

Gespeichert als JSON. song_key = "normalized_artist|normalized_title".
"""

import json
import logging
import os
import time

from . import config
from .i18n import t
from .matching import normalize

logger = logging.getLogger(__name__)


def _key(artist: str, title: str) -> str:
    return f"{normalize(artist)}|{normalize(title)}"


def load() -> dict:
    """Laedt den Zustand. Leere Struktur, wenn keine/ungueltige Datei."""
    try:
        with open(config.STATE_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            data.setdefault("seen", {})
            data.setdefault("requested", {})
            data.setdefault("blocked_providers", {})
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {"seen": {}, "requested": {}, "blocked_providers": {}}


def save(state: dict) -> None:
    """Schreibt den Zustand atomar."""
    try:
        os.makedirs(os.path.dirname(config.STATE_FILE), exist_ok=True)
        tmp = config.STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, config.STATE_FILE)
    except OSError as e:
        logger.error(t("upgrade.state.save_failed", error=e))


def already_requested(state: dict, artist: str, title: str) -> bool:
    return _key(artist, title) in state.get("requested", {})


def mark_requested(state: dict, artist: str, title: str,
                   username: str, filename: str) -> None:
    state.setdefault("requested", {})[_key(artist, title)] = {
        "artist": artist,
        "title": title,
        "username": username,
        "filename": filename,
        "ts": int(time.time()),
    }


def blocked_providers_for(state: dict, artist: str, title: str,
                          ttl_days: int = 30) -> set[str]:
    """
    Anbieter, die fuer diesen Song schon eine nicht-bessere Datei geliefert
    haben. Verfallene Eintraege (aelter als ttl_days) werden lazy entfernt.
    """
    key = _key(artist, title)
    song_block = state.get("blocked_providers", {}).get(key)
    if not isinstance(song_block, dict) or not song_block:
        return set()
    if ttl_days and ttl_days > 0:
        cutoff = time.time() - ttl_days * 86400
        for user in [u for u, ts in song_block.items()
                     if not isinstance(ts, (int, float)) or ts < cutoff]:
            song_block.pop(user, None)
        if not song_block:
            state.get("blocked_providers", {}).pop(key, None)
    return set(song_block.keys())


def block_provider(state: dict, artist: str, title: str, username: str) -> None:
    """Merkt einen Anbieter fuer diesen Song als 'nicht besser' vor."""
    key = _key(artist, title)
    state.setdefault("blocked_providers", {}).setdefault(key, {})[username] = int(time.time())


def clear_requested(state: dict, artist: str, title: str) -> None:
    state.get("requested", {}).pop(_key(artist, title), None)
