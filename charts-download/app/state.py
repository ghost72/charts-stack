"""
Persistenter Zustand: welche Songs wurden bereits angefragt/geladen.

Verhindert, dass derselbe Song bei jedem Lauf erneut gesucht und geladen wird.
Gespeichert als JSON, Schluessel ist "normalized_artist|normalized_title".
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
    """Laedt den Zustand. Leeres Dict, wenn keine/ungueltige Datei."""
    try:
        with open(config.STATE_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and "requested" in data:
            data.setdefault("failed", {})
            data.setdefault("blocked_providers", {})
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {"requested": {}, "failed": {}, "blocked_providers": {}}


def save(state: dict) -> None:
    """Schreibt den Zustand atomar."""
    try:
        os.makedirs(os.path.dirname(config.STATE_FILE), exist_ok=True)
        tmp = config.STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, config.STATE_FILE)
    except OSError as e:
        logger.error(t("download.state.save_failed", error=e))


def already_requested(state: dict, artist: str, title: str) -> bool:
    return _key(artist, title) in state.get("requested", {})


def mark_requested(state: dict, artist: str, title: str,
                   username: str, filename: str) -> None:
    """
    Vermerkt einen angefragten Download inkl. slskd-Referenz (username +
    filename), damit ein spaeterer Lauf den Download-Status nachverfolgen und
    Fehlschlaege wieder freigeben kann.
    """
    state.setdefault("requested", {})[_key(artist, title)] = {
        "artist": artist,
        "title": title,
        "username": username,
        "filename": filename,
    }


def clear_requested(state: dict, artist: str, title: str) -> None:
    """Entfernt einen Eintrag, damit der Song erneut gesucht/geladen wird."""
    state.get("requested", {}).pop(_key(artist, title), None)


# --- Fehlgeschlagene Suchen (Cooldown) ---
# Ein Song, der gesucht, aber nicht in ausreichender Qualitaet gefunden wurde,
# wird mit Zeitstempel vermerkt und fuer SEARCH_COOLDOWN_HOURS uebersprungen.
# So blockieren dauerhaft nicht-findbare Songs (z.B. viele Mundart-Titel) nicht
# in JEDEM Lauf das MAX_PER_RUN-Kontingent und die findbaren Songs kommen dran.
# Nach Ablauf des Cooldowns wird erneut versucht (falls inzwischen jemand eine
# gute Version teilt).

def mark_failed(state: dict, artist: str, title: str) -> None:
    """Vermerkt eine erfolglose Suche mit aktuellem Zeitstempel."""
    state.setdefault("failed", {})[_key(artist, title)] = {
        "artist": artist,
        "title": title,
        "ts": int(time.time()),
    }


def in_cooldown(state: dict, artist: str, title: str, cooldown_hours: int) -> bool:
    """
    True, wenn der Song kuerzlich (innerhalb cooldown_hours) erfolglos gesucht
    wurde und daher uebersprungen werden soll. cooldown_hours <= 0 = kein
    Cooldown (immer erneut suchen).
    """
    if cooldown_hours <= 0:
        return False
    entry = state.get("failed", {}).get(_key(artist, title))
    if not entry:
        return False
    age = time.time() - entry.get("ts", 0)
    return age < cooldown_hours * 3600


def clear_failed(state: dict, artist: str, title: str) -> None:
    """Entfernt einen Fehlversuch-Eintrag (z.B. nach erfolgreichem Download)."""
    state.get("failed", {}).pop(_key(artist, title), None)


# --- Anbieter-Blockliste pro Song ---
# charts-import merkt sich (ueber blocked_providers im State), welcher Soulseek-
# Anbieter fuer einen bestimmten Song eine schlechte/upgesampelte Datei geliefert
# hat. Diese Kombination (song, username) wird bei der naechsten Suche
# uebersprungen - so wird nicht endlos derselbe Muell vom selben Anbieter
# geladen. Bewusst PRO SONG (nicht global): derselbe Anbieter kann fuer andere
# Songs eine gute Datei haben. Die Blockade verfaellt nach BLOCK_TTL_DAYS, damit
# ein Anbieter, der seine Datei inzwischen ersetzt hat, wieder eine Chance
# bekommt.

def blocked_providers_for(state: dict, artist: str, title: str,
                          ttl_days: int) -> set[str]:
    """
    Liefert die aktuell (noch nicht verfallenen) geblockten Anbieter-Namen fuer
    diesen Song. Verfallene Eintraege (aelter als ttl_days) werden dabei gleich
    aus dem State entfernt (Lazy-Cleanup). ttl_days <= 0 -> nie verfallen.
    """
    key = _key(artist, title)
    song_block = state.get("blocked_providers", {}).get(key)
    if not isinstance(song_block, dict) or not song_block:
        return set()

    if ttl_days and ttl_days > 0:
        cutoff = time.time() - ttl_days * 86400
        expired = [user for user, ts in song_block.items()
                   if not isinstance(ts, (int, float)) or ts < cutoff]
        for user in expired:
            song_block.pop(user, None)
        if not song_block:
            # Ganzen Song-Eintrag entfernen, wenn keine Blockade mehr aktiv ist.
            state.get("blocked_providers", {}).pop(key, None)

    return set(song_block.keys())
