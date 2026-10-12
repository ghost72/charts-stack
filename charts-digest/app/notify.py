"""
Optionale Pushover-Benachrichtigung.

Wird nur aktiv, wenn PUSHOVER_TOKEN und PUSHOVER_USER gesetzt sind.
KOPIE aus charts-import (keine Laufzeit-Abhaengigkeit zwischen den Diensten).
"""

import logging
import time

import httpx

from . import config
from .i18n import t

logger = logging.getLogger(__name__)

_pushover_devices_cache: "set[str] | None" = None
_pushover_devices_ts: float = 0.0


def _valid_pushover_devices(client: httpx.Client):
    """Liefert die gueltigen Geraetenamen des Pushover-Accounts (gecacht).

    Grund: Pushover verwirft einen Request mit UNBEKANNTEM device-Namen nicht,
    sondern stellt ihn an ALLE Geraete zu -> Doppel-Push. Daher vor dem Senden
    gegen die echte Geraeteliste filtern. Rueckgabe: Set gueltiger Namen oder
    None (dann NICHT filtern - fail-open)."""
    global _pushover_devices_cache, _pushover_devices_ts
    now = time.time()
    if _pushover_devices_cache is not None and (now - _pushover_devices_ts) < config.PUSHOVER_DEVICES_TTL:
        return _pushover_devices_cache
    try:
        resp = client.post(
            "https://api.pushover.net/1/users/validate.json",
            data={"token": config.PUSHOVER_TOKEN, "user": config.PUSHOVER_USER},
        )
        if resp.status_code == 200:
            payload = resp.json()
            devs = payload.get("devices")
            if isinstance(devs, list):
                _pushover_devices_cache = {str(d) for d in devs}
                _pushover_devices_ts = now
                return _pushover_devices_cache
        logger.warning(t("notify.devices.load_failed", status=resp.status_code))
    except Exception as e:
        logger.warning(t("notify.devices.unreachable", error=e))
    return None


_ALLOWED_TAGS = ["b", "i", "u"]


def _escape_html_keep_tags(text: str) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    for tag in _ALLOWED_TAGS:
        text = text.replace(f"&lt;{tag}&gt;", f"<{tag}>")
        text = text.replace(f"&lt;/{tag}&gt;", f"</{tag}>")
    return text


def send(title: str, message: str, device: "str | None" = None) -> None:
    """Sendet eine Pushover-Nachricht, falls konfiguriert."""
    if not config.PUSHOVER_ENABLED:
        return
    if not config.PUSHOVER_TOKEN or not config.PUSHOVER_USER:
        logger.info(t("notify.not_configured", title=title))
        return

    payload = {
        "token": config.PUSHOVER_TOKEN,
        "user": config.PUSHOVER_USER,
        "title": title,
        "message": _escape_html_keep_tags(message),
        "html": 1,
    }
    # Ton nur mitschicken, wenn gesetzt - sonst Account-Default (kein sound-Feld).
    if config.PUSHOVER_SOUND:
        payload["sound"] = config.PUSHOVER_SOUND
    device_src = config.PUSHOVER_DEVICE if device is None else device
    requested = []
    seen = set()
    for d in device_src.split(","):
        d = d.strip()
        if d and d not in seen:
            seen.add(d)
            requested.append(d)

    if device is not None and not requested:
        logger.info(t("notify.no_valid_device", title=title))
        return

    try:
        with httpx.Client(timeout=15.0) as client:
            if requested:
                valid = _valid_pushover_devices(client)
                if valid is not None:
                    dropped = [d for d in requested if d not in valid]
                    if dropped:
                        logger.warning(t("notify.devices.unknown_skipped",
                                         devices=", ".join(dropped)))
                    requested = [d for d in requested if d in valid]
                    if not requested:
                        logger.error(t("notify.devices.none_valid",
                                       devices=", ".join(dropped), title=title))
                        return

            devices = requested or [""]
            for dev in devices:
                data = dict(payload)
                if dev:
                    data["device"] = dev
                resp = client.post("https://api.pushover.net/1/messages.json", data=data)
                target = dev or t("notify.target.all_devices")
                if resp.status_code == 200:
                    logger.info(t("notify.sent", title=title, target=target))
                else:
                    logger.error(t("notify.send_failed",
                                   status=resp.status_code, target=target, error=resp.text))
    except Exception as e:
        logger.error(t("notify.exception", error=e))
