"""Template-backed editor for the stack's central .env file."""

import hashlib
import json
import logging
import os
import re
import stat
from collections import OrderedDict
from datetime import datetime, timezone
from io import StringIO

from dotenv import dotenv_values
from dotenv.parser import parse_stream

from . import config

logger = logging.getLogger("charts-config.env")

_MAX_FILE_BYTES = 2 * 1024 * 1024
_MAX_VALUE_CHARS = 512 * 1024
_ANY_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ACTIVE_RE = re.compile(r"^(?P<indent>\s*)(?P<key>[A-Z][A-Z0-9_]*)=(?P<raw>.*?)(?P<ending>\r?\n)?$")
_COMMENT_RE = re.compile(r"^(?P<indent>\s*#\s*)(?P<key>[A-Z][A-Z0-9_]*)=(?P<raw>.*?)(?P<ending>\r?\n)?$")
_HEADING_RE = re.compile(
    r"^#\s*(?:─{2,}|-{2,})\s*(.*?)\s*(?:─{2,}|-{2,})\s*$")
_SECRET_NAMES = {
    "APP_PASSWORD",
    "APP_USERS",
    "CONFIG_PASSWORD",
    "DASHBOARD_PASSWORD",
    "LASTFM_API_KEY",
    "LIDARR_API_KEY",
    "LISTENBRAINZ_TOKEN",
    "PUSHOVER_TOKEN",
    "PUSHOVER_USER",
    "SESSION_SECRET",
    "SLSKD_API_KEY",
    "SONGREQUEST_USERS",
    "USER_PUSHOVER_DEVICES",
}
_ALL_SERVICES = (
    "charts-docker-proxy", "slskd", "charts-sync", "charts-hitparade",
    "charts-energy",
    "charts-download", "charts-import", "charts-recommend", "charts-digest",
    "charts-flashback", "charts-upgrade", "charts-songrequest",
    "charts-watchdog", "charts-dashboard", "charts-config",
)
_USER_SERVICES = tuple(
    service for service in _ALL_SERVICES if service != "charts-docker-proxy")
_PYTHON_SERVICES = tuple(
    service for service in _USER_SERVICES if service != "slskd")
_PUSHOVER_SERVICES = (
    "charts-sync", "charts-hitparade", "charts-download", "charts-import",
    "charts-recommend", "charts-digest", "charts-flashback", "charts-energy",
    "charts-upgrade", "charts-watchdog",
)
_PUSHOVER_SUFFIXES = {
    "SYNC": "charts-sync",
    "HITPARADE": "charts-hitparade",
    "DOWNLOAD": "charts-download",
    "IMPORT": "charts-import",
    "RECOMMEND": "charts-recommend",
    "DIGEST": "charts-digest",
    "FLASHBACK": "charts-flashback",
    "ENERGY": "charts-energy",
    "UPGRADE": "charts-upgrade",
    "WATCHDOG": "charts-watchdog",
    "MANUAL": "charts-import",
    "SONGREQUEST": "charts-songrequest",
}
_DOC_BASE = "https://github.com/ghost72/charts-stack/blob/main/"
_DOC_LINKS = (
    (("required", "user / permissions"),
     (("README.md#installation-ein-projekt-eine-env", "Stack-Installation"),)),
    (("charts-recommend",),
     (("charts-recommend/README.md#1-konfiguration", "charts-recommend"),)),
    (("charts-flashback",),
     (("charts-flashback/README.md#konfiguration-zentrale-env", "charts-flashback"),)),
    (("charts-digest",),
     (("charts-digest/README.md#konfiguration-zentrale-env", "charts-digest"),)),
    (("slskd",),
     (("slskd/README.md#1-key-festlegen-in-die-zentrale-env-schreiben", "slskd-Setup"),)),
    (("last.fm", "apple music"),
     (("charts-sync/README.md#konfiguration", "charts-sync-Konfiguration"),)),
    (("pushover",),
     (("README.md#monitoring-dashboard-watchdog", "Monitoring"),)),
    (("watchdog",),
     (("charts-watchdog/README.md#konfiguration-zentrale-env", "charts-watchdog"),)),
    (("lidarr",),
     (("charts-import/README.md#konfiguration-env", "charts-import / Lidarr"),)),
    (("web ui port",),
     (("charts-songrequest/README.md#konfiguration-env", "charts-songrequest"),)),
    (("charts-config",),
     (("charts-config/README.md#konfiguration-zentrale-env", "charts-config"),)),
    (("charts-dashboard",),
     (("charts-dashboard/README.md#konfiguration-zentrale-env", "charts-dashboard"),)),
    (("shared login",),
     (("charts-dashboard/README.md#konfiguration-zentrale-env", "Gemeinsamer Login"),)),
    (("charts-download",),
     (("charts-download/README.md#konfiguration-env", "charts-download"),)),
    (("charts-hitparade",),
     (("charts-hitparade/README.md#konfiguration-zentrale-env", "charts-hitparade"),)),
    (("language",),
     (("README.md#sprache-logs-pushover-dashboard", "Sprache"),)),
    (("charts-sync",),
     (("charts-sync/README.md#konfiguration", "charts-sync"),)),
    (("fine-tuning",),
     (("charts-download/README.md#konfiguration-env", "charts-download"),
      ("charts-import/README.md#konfiguration-env", "charts-import"),
      ("charts-upgrade/README.md#1-konfiguration", "charts-upgrade"),
      ("charts-sync/README.md#konfiguration", "charts-sync"))),
    (("charts-import",),
     (("charts-import/README.md#konfiguration-env", "charts-import"),)),
    (("charts-energy",),
     (("charts-energy/README.md#konfiguration-zentrale-env", "charts-energy"),)),
)


class EnvEditorError(ValueError):
    """Raised when the environment template or update is invalid."""

    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.code = code
        self.status = status


def _read_file(path: str) -> bytes:
    try:
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode):
            raise EnvEditorError("file_invalid", 500)
        if info.st_size > _MAX_FILE_BYTES:
            raise EnvEditorError("file_too_large", 500)
        with open(path, "rb") as fh:
            data = fh.read(_MAX_FILE_BYTES + 1)
    except EnvEditorError:
        raise
    except OSError as exc:
        raise EnvEditorError("file_unavailable", 500) from exc
    if len(data) > _MAX_FILE_BYTES:
        raise EnvEditorError("file_too_large", 500)
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EnvEditorError("file_invalid", 500) from exc
    return data


def _parse_values(text: str) -> OrderedDict[str, str]:
    try:
        values = dotenv_values(stream=StringIO(text), interpolate=False)
    except (OSError, ValueError) as exc:
        raise EnvEditorError("file_invalid", 500) from exc
    return OrderedDict(
        (key, "" if value is None else str(value))
        for key, value in values.items()
        if isinstance(key, str) and key
    )


def _raw_values(text: str) -> dict[str, str]:
    raw_values = {}
    for binding in parse_stream(StringIO(text)):
        if not binding.key or binding.value is None:
            continue
        original = binding.original.string
        prefix = re.match(
            rf"^\s*(?:export\s+)?{re.escape(binding.key)}\s*=\s*",
            original,
        )
        if not prefix:
            continue
        raw = original[prefix.end():]
        if raw.endswith("\n"):
            raw = raw[:-1]
        if raw.endswith("\r"):
            raw = raw[:-1]
        raw_values[binding.key] = _split_comment(raw)[0]
    return raw_values


def _split_comment(raw: str) -> tuple[str, str]:
    quote = ""
    escaped = False
    for index, char in enumerate(raw):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote != "'":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in ("'", '"'):
            quote = char
        elif char == "#" and index > 0 and raw[index - 1].isspace():
            return raw[:index].rstrip(), raw[index - 1:]
    return raw.rstrip(), ""


def _parse_sample(key: str, raw: str) -> str:
    value, _comment = _split_comment(raw)
    return _parse_values(f"{key}={value}\n").get(key, "")


def _is_secret(key: str) -> bool:
    return (key in _SECRET_NAMES
            or key.endswith(("_PASSWORD", "_TOKEN", "_SECRET"))
            or key.endswith("_API_KEY"))


def _docs_for(section: str) -> list[dict[str, str]]:
    normalized = section.casefold()
    for markers, links in _DOC_LINKS:
        if any(marker in normalized for marker in markers):
            return [
                {"label": label, "url": _DOC_BASE + path}
                for path, label in links
            ]
    return [
        {"label": "Stack-Installation",
         "url": _DOC_BASE + "README.md#installation-ein-projekt-eine-env"}
    ]


def _short_description(lines: list[str]) -> str:
    text = " ".join(" ".join(line.split()) for line in lines if line.strip())
    if not text:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    description = sentences[0].strip()
    if len(description) > 240:
        description = description[:237].rsplit(" ", 1)[0] + "..."
    return description


def _load_schema(template_text: str) -> tuple[OrderedDict, OrderedDict]:
    fields: OrderedDict[str, dict] = OrderedDict()
    current_group = "Allgemein"
    pending_comments: list[str] = []
    template_values = _parse_values(template_text)

    for line in template_text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        heading = _HEADING_RE.match(body)
        if heading:
            current_group = heading.group(1).strip()
            pending_comments = []
            continue

        active = _ACTIVE_RE.match(body)
        commented = _COMMENT_RE.match(body)
        match = active or commented
        if match:
            key = match.group("key")
            optional = commented is not None
            description = _short_description(pending_comments)
            pending_comments = []
            sample = (
                template_values.get(key, "")
                if not optional else _parse_sample(key, match.group("raw"))
            )
            field = fields.setdefault(key, {
                "key": key,
                "groups": [],
                "description": description,
                "optional": optional,
                "example_value": sample,
            })
            if current_group not in field["groups"]:
                field["groups"].append(current_group)
            field["optional"] = field["optional"] and optional
            if not field["description"] and description:
                field["description"] = description
            if not field["example_value"] and sample:
                field["example_value"] = sample
            continue

        if body.lstrip().startswith("#"):
            comment = body.lstrip()[1:].strip()
            if comment:
                pending_comments.append(comment)
        else:
            pending_comments = []

    return fields, template_values


def _services_for(key: str, groups: list[str]) -> list[str]:
    if key in {"MUSIC_ROOT", "PUID", "PGID"}:
        return list(_USER_SERVICES)
    if key == "TZ":
        return list(_ALL_SERVICES)
    if key == "CHARTS_LANG":
        return list(_PYTHON_SERVICES)
    if key == "PRESERVE_MTIME":
        return ["charts-config", "charts-import", "charts-songrequest"]
    if key == "SESSION_SECRET":
        return ["charts-config", "charts-dashboard", "charts-songrequest"]
    if key in {"APP_USERS", "APP_PASSWORD", "APP_PASSWORD_USERNAME"}:
        return ["charts-config", "charts-dashboard"]
    if key in {"PUSHOVER_TOKEN", "PUSHOVER_USER", "PUSHOVER_DEVICE",
               "PUSHOVER_DEVICES_TTL", "USER_PUSHOVER_DEVICES"}:
        return list(_PUSHOVER_SERVICES)
    if key == "LASTFM_API_KEY":
        return ["charts-config", "charts-dashboard", "charts-recommend",
                "charts-sync"]
    if key == "LASTFM_ENABLED":
        return ["charts-recommend"]
    if key == "DASHBOARD_GENRE_CACHE_TTL":
        return ["charts-config", "charts-dashboard"]
    if key == "MB_ENABLED":
        return ["charts-dashboard", "charts-energy", "charts-flashback",
                "charts-import"]
    if key.startswith(("MB_", "MUSICBRAINZ_")):
        return ["charts-energy", "charts-flashback", "charts-import"]
    if key.startswith("APPLE_CHARTS_"):
        return ["charts-dashboard", "charts-watchdog"]
    if key.startswith("PUSHOVER_SOUND_") or (
            key.startswith("PUSHOVER_") and key.endswith("_ENABLED")):
        suffix = key.removeprefix("PUSHOVER_SOUND_").removeprefix(
            "PUSHOVER_").removesuffix("_ENABLED")
        service = _PUSHOVER_SUFFIXES.get(suffix)
        return [service] if service else list(_PUSHOVER_SERVICES)
    if key.startswith("TAGEDITOR_") or key.startswith("CONFIG_"):
        return ["charts-config"]
    if key.startswith("DASHBOARD_"):
        return ["charts-dashboard"]
    if key.startswith("SONGREQUEST_") or key.startswith("YOUTUBE_"):
        return ["charts-songrequest"]
    if key.startswith("WATCH_"):
        return ["charts-watchdog"]
    if key.startswith("LIDARR_"):
        return ["charts-import"]
    if key.startswith("DIGEST_"):
        return ["charts-digest"]
    if key.startswith("RECOMMEND_") or key.startswith("LISTENBRAINZ_"):
        return ["charts-recommend"]
    if key.startswith("FLASHBACK_"):
        return ["charts-dashboard", "charts-flashback"]
    if key.startswith("HITPARADE_"):
        return ["charts-hitparade"]
    if key.startswith("ENERGY_"):
        if key == "ENERGY_FEED_MAX_AGE_HOURS":
            return ["charts-energy", "charts-sync"]
        return ["charts-dashboard", "charts-energy"]
    if key.startswith(("BACKFILL_", "YEAR_", "METADATA_", "IMPORT_")):
        return ["charts-import"]
    if key == "CHARTS_ARTIST_PLAYLIST":
        return ["charts-sync", "charts-config"]
    if key.startswith(("CHARTS_", "SYNC_")) or key in {
            "CHARTS", "CHART_LIMIT", "PLAYLIST_ORDER", "GENRE_FILTER",
            "EXCLUDE_TAGS", "EXCLUDE_TAG_TOP_N", "EXCLUDE_TAG_MIN_COUNT",
            "GENRE_FILTER_SLEEP_MS"}:
        return ["charts-sync"]
    if key in {"MIN_BITRATE"}:
        return ["charts-download", "charts-songrequest", "charts-upgrade"]
    if key == "MAX_PER_RUN":
        return ["charts-download", "charts-upgrade"]
    if key in {"REJECT_UPSAMPLED", "REJECT_UPSAMPLED_MIN_BITRATE",
               "CUTOFF_TOLERANCE_KHZ"}:
        return ["charts-import", "charts-songrequest"]
    if key == "DRY_RUN":
        return ["charts-download"]
    if key == "UPGRADE_DRY_RUN":
        return ["charts-upgrade"]
    if key in {"KEEP_LATEST", "REJECTED_RETENTION_DAYS",
               "TRASH_RETENTION_DAYS"}:
        return ["charts-import"]
    if key == "MISSING_FILES":
        return ["charts-download"]
    if key == "SLSKD_API_KEY":
        return ["charts-download", "charts-songrequest", "charts-upgrade", "slskd"]

    service_by_group = {
        service for service in _ALL_SERVICES
        if any(service in group.casefold() for group in groups)
    }
    return sorted(service_by_group)


def _field_type(key: str) -> str:
    if key in {"CHARTS", "ENERGY_CHANNELS"}:
        return "json"
    if key.endswith("_ENABLED") or key in {
            "CONFIG_DONATE", "DASHBOARD_DONATE", "PRESERVE_MTIME",
            "GENRE_FILTER", "DRY_RUN", "UPGRADE_DRY_RUN",
            "REJECT_UPSAMPLED", "WATCH_STATUS_ENABLED",
            "ENERGY_LIVENESS_ON_START"}:
        return "boolean"
    if key == "CHARTS_LANG":
        return "language"
    if key == "PLAYLIST_ORDER":
        return "playlist_order"
    return "text"


def _configuration_data(current_text: str, template_text: str) -> dict:
    fields, template_values = _load_schema(template_text)
    current_values = _parse_values(current_text)
    grouped: OrderedDict[str, dict] = OrderedDict()

    for key, field in fields.items():
        section = field["groups"][0]
        group = grouped.setdefault(section, {
            "name": section,
            "links": _docs_for(section),
            "fields": [],
        })
        has_current = key in current_values
        if has_current:
            value = current_values.get(key, "")
            source = "saved"
        elif not field["optional"]:
            value = template_values.get(key, "")
            source = "example"
        else:
            value = ""
            source = "unset"

        output = {
            "key": key,
            "label": key.replace("_", " ").title(),
            "description": field["description"],
            "groups": field["groups"],
            "optional": field["optional"],
            "is_set": has_current,
            "source": source,
            "type": _field_type(key),
            "services": _services_for(key, field["groups"]),
            "links": [
                link
                for name in field["groups"]
                for link in _docs_for(name)
            ],
        }
        if field["optional"] and field["example_value"] and not _is_secret(key):
            output["example_value"] = field["example_value"]
        if _is_secret(key):
            output["secret"] = True
            output["configured"] = bool(current_values.get(key, ""))
        else:
            output["secret"] = False
            output["value"] = value
        group["fields"].append(output)

    unknown_keys = [
        {"key": key, "configured": bool(value)}
        for key, value in current_values.items()
        if key not in fields
    ]
    return {
        "ok": True,
        "revision": hashlib.sha256(current_text.encode("utf-8")).hexdigest(),
        "template_revision": hashlib.sha256(
            template_text.encode("utf-8")).hexdigest(),
        "groups": list(grouped.values()),
        "unknown": unknown_keys,
    }


def get_configuration() -> dict:
    current = _read_file(config.ENV_FILE).decode("utf-8")
    template = _read_file(config.ENV_EXAMPLE_FILE).decode("utf-8")
    return _configuration_data(current, template)


def _serialize_value(value: str) -> str:
    if any(ord(char) < 32 and char not in ("\n", "\t") for char in value):
        raise EnvEditorError("invalid_value")
    if len(value) > _MAX_VALUE_CHARS:
        raise EnvEditorError("value_too_large")
    if not value:
        return ""
    if (value == value.strip()
            and not any(char in value for char in ("#", "$", "'", '"', "\\"))
            and "\n" not in value and "\r" not in value and "\t" not in value):
        return value
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _render_template(
        template_text: str,
        current_values: OrderedDict[str, str],
        current_raw_values: dict[str, str],
        template_values: OrderedDict[str, str],
        fields: OrderedDict[str, dict],
        overrides: dict[str, str],
        unset: set[str]) -> tuple[str, OrderedDict[str, str]]:
    values = dict(current_values)
    for key, default in template_values.items():
        values.setdefault(key, default)
    values.update(overrides)

    activated = set(overrides) | set(current_values)
    lines: list[str] = []

    def serialize(key: str, value: str) -> str:
        if (key not in overrides and current_values.get(key) == value
                and key in current_raw_values):
            return current_raw_values[key]
        return _serialize_value(value)

    for line in template_text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        ending = line[len(body):]
        active = _ACTIVE_RE.match(body)
        commented = _COMMENT_RE.match(body)
        match = active or commented
        if not match:
            lines.append(line)
            continue

        key = match.group("key")
        if key not in fields:
            lines.append(line)
            continue
        if key in unset:
            if commented:
                lines.append(line)
            else:
                raw, inline_comment = _split_comment(match.group("raw"))
                lines.append(
                    f"{match.group('indent')}# {key}={raw}{inline_comment}{ending}")
            continue
        if commented and key not in activated:
            lines.append(line)
            continue

        _raw, inline_comment = _split_comment(match.group("raw"))
        value = values.get(key, "")
        prefix = match.group("indent")
        if commented:
            prefix = prefix.split("#", 1)[0]
        lines.append(
            f"{prefix}{key}={serialize(key, value)}{inline_comment}{ending}")

    unknown = [
        (key, value) for key, value in current_values.items()
        if key not in fields
    ]
    if unknown:
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines.append("\n")
        if lines and lines[-1].strip():
            lines.append("\n")
        lines.append("# ─── Custom variables preserved from the previous .env ───\n")
        for key, value in unknown:
            if _ANY_KEY_RE.fullmatch(key):
                lines.append(f"{key}={serialize(key, value)}\n")

    output = "".join(lines)
    output_values = _parse_values(output)
    return output, output_values


def _validate_payload(payload: object, fields: OrderedDict[str, dict]) -> tuple[
        str, str, dict[str, str], set[str]]:
    if not isinstance(payload, dict):
        raise EnvEditorError("invalid_payload")
    revision = payload.get("revision")
    template_revision = payload.get("template_revision")
    values = payload.get("values", {})
    secrets = payload.get("secrets", {})
    unset_values = payload.get("unset", [])
    clear_secrets = payload.get("clear_secrets", [])
    if (not isinstance(revision, str) or not isinstance(template_revision, str)
            or not isinstance(values, dict) or not isinstance(secrets, dict)
            or not isinstance(unset_values, list)
            or not isinstance(clear_secrets, list)):
        raise EnvEditorError("invalid_payload")

    overrides: dict[str, str] = {}
    for key, value in values.items():
        if (key not in fields or _is_secret(key) or not isinstance(value, str)):
            raise EnvEditorError("invalid_payload")
        _serialize_value(value)
        _validate_value(key, value)
        overrides[key] = value
    for key, value in secrets.items():
        if (key not in fields or not _is_secret(key)
                or not isinstance(value, str) or not value):
            raise EnvEditorError("invalid_payload")
        _serialize_value(value)
        overrides[key] = value

    if (any(not isinstance(key, str) for key in unset_values + clear_secrets)):
        raise EnvEditorError("invalid_payload")
    unset = set(unset_values)
    clear = set(clear_secrets)
    if (any(key not in fields or not fields[key]["optional"] for key in unset)
            or any(key not in fields or not _is_secret(key) for key in clear)):
        raise EnvEditorError("invalid_payload")
    if unset & set(overrides):
        raise EnvEditorError("invalid_payload")
    for key in clear:
        overrides[key] = ""
    return revision, template_revision, overrides, unset


def _validate_value(key: str, value: str) -> None:
    if not value:
        return
    if _field_type(key) == "boolean" and value.casefold() not in {
            "1", "0", "true", "false", "yes", "no", "on", "off"}:
        raise EnvEditorError("invalid_value")
    if _field_type(key) == "language" and value.casefold() not in {
            "en", "de", "fr"}:
        raise EnvEditorError("invalid_value")
    if _field_type(key) == "playlist_order" and value not in {
            "chart", "newest-first"}:
        raise EnvEditorError("invalid_value")
    if _field_type(key) == "json":
        try:
            json.loads(value)
        except json.JSONDecodeError as exc:
            raise EnvEditorError("invalid_json") from exc


def _write_backup(data: bytes) -> None:
    directory = config.ENV_BACKUP_DIR
    try:
        os.makedirs(directory, mode=0o700, exist_ok=True)
        if not stat.S_ISDIR(os.lstat(directory).st_mode):
            raise EnvEditorError("backup_unavailable", 500)
        os.chmod(directory, 0o700)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        path = os.path.join(directory, f"env-{stamp}.bak")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
    except EnvEditorError:
        raise
    except OSError as exc:
        raise EnvEditorError("backup_unavailable", 500) from exc


def _write_file(path: str, data: bytes, previous: bytes) -> None:
    modified = False
    try:
        with open(path, "r+b") as fh:
            fh.seek(0)
            fh.truncate()
            modified = True
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
    except OSError as exc:
        if modified:
            try:
                with open(path, "r+b") as fh:
                    fh.seek(0)
                    fh.truncate()
                    fh.write(previous)
                    fh.flush()
                    os.fsync(fh.fileno())
            except OSError:
                logger.critical("Could not restore .env after a failed write")
        raise EnvEditorError("write_failed", 500) from exc


def save_configuration(payload: object, username: str) -> dict:
    template_bytes = _read_file(config.ENV_EXAMPLE_FILE)
    current_bytes = _read_file(config.ENV_FILE)
    try:
        template_text = template_bytes.decode("utf-8")
        current_text = current_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EnvEditorError("file_invalid", 500) from exc
    fields, template_values = _load_schema(template_text)
    revision, template_revision, overrides, unset = _validate_payload(
        payload, fields)
    current_hash = hashlib.sha256(current_bytes).hexdigest()
    template_hash = hashlib.sha256(template_bytes).hexdigest()
    if revision != current_hash or template_revision != template_hash:
        raise EnvEditorError("stale_file", 409)

    current_values = _parse_values(current_text)
    current_raw_values = _raw_values(current_text)
    output, output_values = _render_template(
        template_text, current_values, current_raw_values, template_values,
        fields, overrides, unset)
    output_bytes = output.encode("utf-8")
    if len(output_bytes) > _MAX_FILE_BYTES:
        raise EnvEditorError("file_too_large")
    changed_keys = {
        key for key in fields
        if current_values.get(key) != output_values.get(key)
        or ((key in current_values) != (key in output_values))
    }
    backup_created = output_bytes != current_bytes
    if backup_created:
        _write_backup(current_bytes)
        _write_file(config.ENV_FILE, output_bytes, current_bytes)

    services = sorted({
        service
        for key in changed_keys
        for service in _services_for(key, fields[key]["groups"])
    })
    if services:
        command = ("docker compose up -d --no-deps --force-recreate "
                   + " ".join(services))
    else:
        command = ""
    logger.info("User %s updated .env settings: %s",
                username, ", ".join(sorted(changed_keys)) or "template sync")
    result = get_configuration()
    result.update({
        "backup_created": backup_created,
        "changed_keys": sorted(changed_keys),
        "services": services,
        "apply_command": command,
    })
    return result
