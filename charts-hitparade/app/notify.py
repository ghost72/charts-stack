"""
Optionale Pushover-Benachrichtigung.

Wird nur aktiv, wenn PUSHOVER_TOKEN und PUSHOVER_USER gesetzt sind.
Bewusst schlank gehalten (identisches Setup wie im jellyfin-webhook).
"""

import logging

import httpx

from . import config
from .i18n import t

logger = logging.getLogger(__name__)

# Pushover sendet mit html=1 und interpretiert < > & als HTML. Songtitel/Namen
# mit diesen Zeichen (z.B. "<", "&") wuerden die Anzeige zerschiessen. Daher &,
# <, > escapen und nur die bewusst genutzten Formatierungs-Tags wiederherstellen.
_ALLOWED_TAGS = ["b", "i", "u"]


def _escape_html_keep_tags(text: str) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    for tag in _ALLOWED_TAGS:
        text = text.replace(f"&lt;{tag}&gt;", f"<{tag}>")
        text = text.replace(f"&lt;/{tag}&gt;", f"</{tag}>")
    return text


def send(title: str, message: str) -> None:
    """Sendet eine Pushover-Nachricht, falls konfiguriert."""
    if not config.PUSHOVER_ENABLED:
        return
    if not config.PUSHOVER_TOKEN or not config.PUSHOVER_USER:
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
    # PUSHOVER_DEVICE darf eine kommagetrennte Liste von Gerätenamen sein. Die
    # Pushover-API akzeptiert im device-Feld nur EINEN Gerätenamen pro Request,
    # deshalb pro Gerät eine eigene Nachricht senden. Alle Geräte gehören zum
    # selben Pushover-Account. Leer => [""] => ein Request ohne device-Feld =>
    # alle Geräte des Accounts.
    devices = [d.strip() for d in config.PUSHOVER_DEVICE.split(",") if d.strip()] or [""]

    try:
        with httpx.Client(timeout=15.0) as client:
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
