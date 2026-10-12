"""
Optionale Pushover-Benachrichtigung.

Wird nur aktiv, wenn PUSHOVER_TOKEN und PUSHOVER_USER gesetzt sind.
Bewusst schlank gehalten (identisches Setup wie im jellyfin-webhook).
"""

import logging
import time

import httpx

from . import config
from .i18n import t

logger = logging.getLogger(__name__)

# Cache der gültigen Pushover-Gerätenamen (vom Account geladen). Siehe
# _valid_pushover_devices(). TTL, damit neu angelegte Geräte irgendwann sichtbar
# werden, ohne bei jeder Nachricht einen Validate-Call zu machen.
_pushover_devices_cache: "set[str] | None" = None
_pushover_devices_ts: float = 0.0


def _valid_pushover_devices(client: httpx.Client):
    """Liefert die gültigen Gerätenamen des Pushover-Accounts (gecacht).

    WICHTIG – der Grund für diese Funktion: Pushover verwirft einen Request mit
    einem UNBEKANNTEN/deaktivierten `device`-Namen NICHT, sondern stellt ihn
    ersatzweise an ALLE Geräte des Accounts zu. Steht also ein toter Gerätename
    (z.B. ein deaktiviertes Gerät) in der Liste, bekommt jedes Gerät zusätzlich
    zu seiner gezielten Nachricht noch die "an alle"-Streuung -> Doppel-Push bei
    jedem Empfänger. Darum filtern wir vor dem Senden gegen die echte Geräte-
    liste des Accounts (users/validate.json liefert sie im Feld `devices`).

    Rückgabe: Set der gültigen Namen, oder None wenn die Liste nicht ermittelt
    werden konnte (dann wird NICHT gefiltert – fail-open, damit ein API-Problem
    die Benachrichtigungen nicht komplett unterdrückt)."""
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
    # Kommagetrennte Liste splitten/strippen, Duplikate entfernen (Reihenfolge
    # erhalten).
    requested = []
    seen = set()
    for d in config.PUSHOVER_DEVICE.split(","):
        d = d.strip()
        if d and d not in seen:
            seen.add(d)
            requested.append(d)

    try:
        with httpx.Client(timeout=15.0) as client:
            # Gegen die echte Geräteliste des Accounts filtern (verhindert den
            # "unbekannter Name -> an alle"-Fallback von Pushover). Nur filtern,
            # wenn konkrete Geräte angefragt sind UND die Liste abrufbar war.
            if requested:
                valid = _valid_pushover_devices(client)
                if valid is not None:
                    dropped = [d for d in requested if d not in valid]
                    if dropped:
                        logger.warning(t("notify.devices.unknown_skipped",
                                         devices=", ".join(dropped)))
                    requested = [d for d in requested if d in valid]
                    if not requested:
                        # Alle angefragten Geräte ungültig: NICHT device-los
                        # senden (das ginge an alle) – lieber gar nicht senden.
                        logger.error(t("notify.devices.none_valid",
                                       devices=", ".join(dropped), title=title))
                        return

            # Leere Anfrage (device war leer) => ein Request ohne device-Feld
            # => alle Geräte des Accounts.
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
