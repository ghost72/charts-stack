"""
Optional Pushover notification.

Only becomes active if PUSHOVER_TOKEN and PUSHOVER_USER are set.
Kept identically lean to charts-sync / jellyfin-webhook.
"""

import logging
import time

import httpx

from . import config
from .i18n import t

logger = logging.getLogger(__name__)

# Cache of the valid Pushover device names (loaded from the account). See
# _valid_pushover_devices(). TTL so newly added devices eventually become
# visible without making a validate call on every message.
_pushover_devices_cache: "set[str] | None" = None
_pushover_devices_ts: float = 0.0


def _valid_pushover_devices(client: httpx.Client):
    """Returns the valid device names of the Pushover account (cached).

    IMPORTANT – the reason for this function: Pushover does NOT reject a request
    with an UNKNOWN/disabled `device` name, but instead delivers it to ALL
    devices of the account. So if a dead device name (e.g. a disabled device) is
    in the list, every device gets, in addition to its targeted message, the
    "to all" broadcast -> double push for every recipient. That is why we filter
    before sending against the account's real device list (users/validate.json
    returns it in the `devices` field).

    Return: set of valid names, or None if the list could not be determined
    (then we do NOT filter – fail-open, so an API problem does not suppress
    notifications entirely)."""
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


# Pushover messages are sent with html=1. Pushover then interprets < > & as
# HTML. A song title/reason with e.g. "<", ">" or "&" would break the display
# (the rest of the line is swallowed as a tag). So we generally escape &, <, >
# and afterwards restore only the formatting tags we deliberately use.
_ALLOWED_TAGS = ["b", "i", "u"]


def _escape_html_keep_tags(text: str) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    for tag in _ALLOWED_TAGS:
        text = text.replace(f"&lt;{tag}&gt;", f"<{tag}>")
        text = text.replace(f"&lt;/{tag}&gt;", f"</{tag}>")
    return text


def send(title: str, message: str, device: "str | None" = None,
         sound: "str | None" = None) -> None:
    """Sends a Pushover message if configured.

    device: optional per-call recipient. If set (not None), EXACTLY this device
    name/list is used instead of config.PUSHOVER_DEVICE – e.g. for the
    availability message to the device of the user who requested a song
    manually. The device validation filter below also applies to this per-call
    device, i.e. an invalid name is NOT sent (no "to all" fallback). An empty
    string as device leads – after the filter – to nothing being sent
    (deliberately: never device-less to all).

    sound: optional per-call sound. None (default) => the service default
    config.PUSHOVER_SOUND ("incoming") is used. A non-empty string overrides the
    service default (e.g. "magic" for the availability message of a song
    requested manually via charts-songrequest). An empty string => NO sound
    field => Pushover takes the account default.
    """
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
    # Determine the sound: a per-call sound (if passed) overrides the service
    # default config.PUSHOVER_SOUND. sound=None => service default; sound="" =>
    # deliberately NO sound field (account default). Set the field only if the
    # finally chosen value is not empty.
    effective_sound = config.PUSHOVER_SOUND if sound is None else sound
    if effective_sound:
        payload["sound"] = effective_sound
    # Device source: a per-call device (if passed) overrides the global
    # config.PUSHOVER_DEVICE list. PUSHOVER_DEVICE may be a comma-separated
    # list; the Pushover API takes only ONE device name per request, so one
    # message per device. Empty => [""] => one request without a device field
    # => all devices of the account.
    device_src = config.PUSHOVER_DEVICE if device is None else device
    # Split/strip the comma-separated list, remove duplicates (keep order).
    requested = []
    seen = set()
    for d in device_src.split(","):
        d = d.strip()
        if d and d not in seen:
            seen.add(d)
            requested.append(d)

    # Special case per-call device: if a device was passed explicitly but after
    # splitting yields NO valid target (empty string etc.), then do NOT send
    # device-less (that would go to all devices) – do not send at all. Affects
    # only the per-call case; the global config (device is None) may still go
    # device-less to all if PUSHOVER_DEVICE is deliberately empty.
    if device is not None and not requested:
        logger.info(t("import.notify.no_valid_device", title=title))
        return

    try:
        with httpx.Client(timeout=15.0) as client:
            # Filter against the account's real device list (prevents Pushover's
            # "unknown name -> to all" fallback). Only filter if concrete
            # devices are requested AND the list could be fetched.
            if requested:
                valid = _valid_pushover_devices(client)
                if valid is not None:
                    dropped = [d for d in requested if d not in valid]
                    if dropped:
                        logger.warning(t("notify.devices.unknown_skipped",
                                         devices=", ".join(dropped)))
                    requested = [d for d in requested if d in valid]
                    if not requested:
                        # All requested devices invalid: do NOT send without a
                        # device (that would go to all) – rather send nothing.
                        logger.error(t("notify.devices.none_valid",
                                       devices=", ".join(dropped), title=title))
                        return

            # Empty request (device was empty) => one request without a device
            # field => all devices of the account.
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
