"""
Optional Pushover notification.

Only becomes active when PUSHOVER_TOKEN and PUSHOVER_USER are set.
Deliberately kept lean (identical setup to the jellyfin-webhook).
"""

import logging

import httpx

from . import config
from .i18n import t

logger = logging.getLogger(__name__)

# Pushover sends with html=1 and interprets < > & as HTML. Song titles/names
# with these characters (e.g. "<", "&") would break the display. So escape &,
# <, > and only restore the formatting tags we deliberately use.
_ALLOWED_TAGS = ["b", "i", "u"]


def _escape_html_keep_tags(text: str) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    for tag in _ALLOWED_TAGS:
        text = text.replace(f"&lt;{tag}&gt;", f"<{tag}>")
        text = text.replace(f"&lt;/{tag}&gt;", f"</{tag}>")
    return text


def send(title: str, message: str) -> None:
    """Send a Pushover message if configured."""
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
    # Only send the sound if set - otherwise the account default (no sound field).
    if config.PUSHOVER_SOUND:
        payload["sound"] = config.PUSHOVER_SOUND
    # PUSHOVER_DEVICE may be a comma-separated list of device names. The
    # Pushover API accepts only ONE device name per request in the device
    # field, so send a separate message per device. All devices belong to the
    # same Pushover account. Empty => [""] => one request without a device field
    # => all devices of the account.
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
