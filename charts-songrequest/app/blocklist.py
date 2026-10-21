"""
Persistent provider blocklist for charts-songrequest.

A Soulseek peer can accept a download request yet never deliver a byte
("Queued, Remotely" forever, or TimedOut/Errored). Once we detect such a
non-delivering provider we remember it here for a while and skip it on future
requests, so the retry across candidates reaches a peer that actually sends
the file.

On-disk format (JSON):
    {"blocked": {"<username>": <blocked_until_unix_ts>, ...}}

Entries past their timestamp are ignored (and lazily pruned) so a peer gets a
fresh chance after PROVIDER_BLOCK_DAYS - Soulseek users come and go. All
operations fail open: a broken/missing file just means "nobody blocked", never
an error that would stop a request.
"""

import json
import logging
import os
import time

from . import config
from .i18n import t

logger = logging.getLogger(__name__)


def _load() -> dict:
    """Load the blocklist file. Returns {"blocked": {user: until_ts}}."""
    try:
        with open(config.BLOCKLIST_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("blocked"), dict):
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {"blocked": {}}


def _save(data: dict) -> None:
    """Persist atomically. Errors are logged, never fatal."""
    try:
        os.makedirs(os.path.dirname(config.BLOCKLIST_FILE), exist_ok=True)
        tmp = config.BLOCKLIST_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, config.BLOCKLIST_FILE)
    except OSError as e:
        logger.debug(t("songrequest.blocklist.save_failed", error=e))


def blocked_users(now: float | None = None) -> set[str]:
    """
    Return the set of currently-blocked usernames (entries not yet expired).
    Expired entries are pruned from the file at the same time (lazy cleanup).
    PROVIDER_BLOCK_DAYS <= 0 disables the blocklist entirely -> empty set.
    """
    if config.PROVIDER_BLOCK_DAYS <= 0:
        return set()
    now = time.time() if now is None else now
    data = _load()
    blocked = data.get("blocked", {})
    active: set[str] = set()
    pruned = False
    for user, until in list(blocked.items()):
        try:
            until_ts = float(until)
        except (TypeError, ValueError):
            del blocked[user]
            pruned = True
            continue
        if until_ts > now:
            active.add(user)
        else:
            del blocked[user]
            pruned = True
    if pruned:
        _save(data)
    return active


def block(username: str, now: float | None = None) -> None:
    """
    Mark a provider as non-delivering for PROVIDER_BLOCK_DAYS from now. No-op if
    the blocklist is disabled or the username is empty.
    """
    if config.PROVIDER_BLOCK_DAYS <= 0 or not username:
        return
    now = time.time() if now is None else now
    until = now + config.PROVIDER_BLOCK_DAYS * 86400
    data = _load()
    data.setdefault("blocked", {})[username] = until
    _save(data)
    logger.info(t("songrequest.blocklist.blocked",
                  user=username, days=config.PROVIDER_BLOCK_DAYS))
