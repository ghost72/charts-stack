"""Tests for rebuilding the central environment file from its template."""

import importlib
import json
import pathlib
import stat

import pytest
from fastapi.testclient import TestClient

from app import env_editor, i18n


ROOT = pathlib.Path(__file__).resolve().parents[2]
LANG_DIR = ROOT / "lang"
TEMPLATE = ROOT / ".env.example"


@pytest.fixture
def env_files(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "CONFIG_PASSWORD=old-private\n"
        "APP_USERS=listener:old-private\n"
        "CHARTS_LANG=de\n"
        "MB_ENABLED=0\n"
        "CUSTOM_TOKEN=private-custom-value\n",
        encoding="utf-8",
    )
    backup_dir = tmp_path / ".env-backups"
    monkeypatch.setattr(env_editor.config, "ENV_FILE", str(env_file))
    monkeypatch.setattr(env_editor.config, "ENV_EXAMPLE_FILE", str(TEMPLATE))
    monkeypatch.setattr(env_editor.config, "ENV_BACKUP_DIR", str(backup_dir))
    return env_file, backup_dir


def _payload(configuration, **updates):
    return {
        "revision": configuration["revision"],
        "template_revision": configuration["template_revision"],
        "values": updates.get("values", {}),
        "secrets": updates.get("secrets", {}),
        "unset": updates.get("unset", []),
        "clear_secrets": updates.get("clear_secrets", []),
    }


def test_template_catalog_includes_active_and_optional_variables():
    template = TEMPLATE.read_text(encoding="utf-8")
    fields, defaults = env_editor._load_schema(template)

    assert len(fields) > len(defaults)
    assert fields["APP_USERS"]["optional"] is True
    assert fields["CONFIG_PASSWORD"]["optional"] is False
    assert fields["MB_ENABLED"]["groups"] == [
        "OPTIONAL: charts-flashback (top up the FLASHBACK FM rotation)",
        "Fine-tuning (change only if needed)",
        "charts-energy (Swiss NRJ / energy.ch webradio source)",
    ]


def test_configuration_hides_secret_values_and_lists_custom_names(env_files):
    env_file, _backup_dir = env_files
    env_file.write_text(
        "CONFIG_PASSWORD=do-not-return\nCUSTOM_TOKEN=also-private\n",
        encoding="utf-8",
    )

    configuration = env_editor.get_configuration()
    encoded = json.dumps(configuration)
    fields = {
        field["key"]: field
        for group in configuration["groups"]
        for field in group["fields"]
    }

    assert fields["CONFIG_PASSWORD"]["configured"] is True
    assert "value" not in fields["CONFIG_PASSWORD"]
    assert "do-not-return" not in encoded
    assert "also-private" not in encoded
    assert configuration["unknown"] == [
        {"key": "CUSTOM_TOKEN", "configured": True},
    ]


def test_existing_download_limit_and_lastfm_switch_are_editable(env_files):
    env_file, _backup_dir = env_files
    env_file.write_text("MAX_PER_RUN=23\nLASTFM_ENABLED=1\n", encoding="utf-8")
    configuration = env_editor.get_configuration()
    fields = {field["key"]: field for group in configuration["groups"]
              for field in group["fields"]}
    assert configuration["unknown"] == []
    assert fields["MAX_PER_RUN"]["value"] == "23"
    assert fields["LASTFM_ENABLED"]["value"] == "1"
    assert fields["MAX_PER_RUN"]["optional"] and fields["LASTFM_ENABLED"]["optional"]

    rebuilt = env_editor.save_configuration(_payload(configuration), "test-user")
    assert {"MAX_PER_RUN", "LASTFM_ENABLED"}.isdisjoint(rebuilt["changed_keys"])
    assert "MAX_PER_RUN=23" in env_file.read_text(encoding="utf-8")
    assert "LASTFM_ENABLED=1" in env_file.read_text(encoding="utf-8")

    saved = env_editor.save_configuration(
        _payload(rebuilt, values={"MAX_PER_RUN": "24", "LASTFM_ENABLED": "0"}),
        "test-user",
    )
    assert saved["services"] == ["charts-download", "charts-recommend", "charts-upgrade"]


def test_save_rebuilds_template_preserves_values_and_creates_private_backup(env_files):
    env_file, backup_dir = env_files
    previous = env_file.read_bytes()
    configuration = env_editor.get_configuration()

    saved = env_editor.save_configuration(_payload(configuration, values={
        "CHARTS_LANG": "fr",
    }, secrets={
        "CONFIG_PASSWORD": "next#private",
    }, unset=[
        "APP_USERS",
    ]), "test-user")

    rebuilt = env_file.read_text(encoding="utf-8")
    assert rebuilt.startswith("#")
    assert "REQUIRED" in rebuilt
    assert "CHARTS_LANG=fr" in rebuilt
    assert "CONFIG_PASSWORD='next#private'" in rebuilt
    assert "CUSTOM_TOKEN=private-custom-value" in rebuilt
    assert len([
        line for line in rebuilt.splitlines()
        if line.startswith("MB_ENABLED=")
    ]) == 3
    assert all(
        line == "MB_ENABLED=0"
        for line in rebuilt.splitlines()
        if line.startswith("MB_ENABLED=")
    )
    assert not any(line.startswith("APP_USERS=") for line in rebuilt.splitlines())
    assert saved["backup_created"] is True
    assert "charts-config" in saved["services"]
    assert "charts-sync" in saved["services"]
    assert saved["apply_command"].startswith(
        "docker compose up -d --no-deps --force-recreate ")
    assert "do-not-return" not in json.dumps(saved)

    backups = list(backup_dir.glob("*.bak"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == previous
    assert stat.S_IMODE(backups[0].stat().st_mode) == 0o600
    assert stat.S_IMODE(backup_dir.stat().st_mode) == 0o700


def test_save_refuses_stale_page_without_overwriting_external_change(env_files):
    env_file, backup_dir = env_files
    configuration = env_editor.get_configuration()
    env_file.write_text("CHARTS_LANG=external\n", encoding="utf-8")

    with pytest.raises(env_editor.EnvEditorError, match="stale_file") as error:
        env_editor.save_configuration(
            _payload(configuration, values={"CHARTS_LANG": "fr"}), "test-user")

    assert error.value.status == 409
    assert env_file.read_text(encoding="utf-8") == "CHARTS_LANG=external\n"
    assert not backup_dir.exists()


def test_invalid_json_does_not_change_env_or_create_backup(env_files):
    env_file, backup_dir = env_files
    previous = env_file.read_bytes()
    configuration = env_editor.get_configuration()

    with pytest.raises(env_editor.EnvEditorError, match="invalid_json"):
        env_editor.save_configuration(
            _payload(configuration, values={"CHARTS": "{"}), "test-user")

    assert env_file.read_bytes() == previous
    assert not backup_dir.exists()


def test_multiline_custom_value_survives_template_rebuild(env_files):
    env_file, _backup_dir = env_files
    env_file.write_text(
        "CUSTOM_TEXT='first line\nsecond line'\n",
        encoding="utf-8",
    )
    configuration = env_editor.get_configuration()

    env_editor.save_configuration(_payload(configuration), "test-user")

    values = env_editor._parse_values(env_file.read_text(encoding="utf-8"))
    assert values["CUSTOM_TEXT"] == "first line\nsecond line"


def test_unchanged_compose_expressions_survive_template_rebuild(env_files):
    env_file, _backup_dir = env_files
    env_file.write_text(
        "MUSIC_ROOT=${MEDIA_ROOT}\n"
        "APP_PASSWORD=$SHARED_PASSWORD\n",
        encoding="utf-8",
    )
    configuration = env_editor.get_configuration()

    env_editor.save_configuration(
        _payload(configuration, values={"CHARTS_LANG": "fr"}), "test-user")

    rebuilt = env_file.read_text(encoding="utf-8")
    assert "MUSIC_ROOT=${MEDIA_ROOT}" in rebuilt
    assert "APP_PASSWORD=$SHARED_PASSWORD" in rebuilt
    assert "CHARTS_LANG=fr" in rebuilt


def _load_main(monkeypatch):
    monkeypatch.setenv("CONFIG_PASSWORD", "test-password")
    monkeypatch.setenv("CHARTS_LANG", "en")
    i18n.setup(lang="en", lang_dir=LANG_DIR)
    import app.config as config_module
    importlib.reload(config_module)
    import app.main as main_module
    importlib.reload(main_module)
    return main_module


def test_env_api_requires_auth_and_never_returns_secret_value(
        env_files, monkeypatch):
    env_file, backup_dir = env_files
    env_file.write_text("CONFIG_PASSWORD=api-private\n", encoding="utf-8")
    main_module = _load_main(monkeypatch)
    monkeypatch.setattr(main_module.env_editor.config, "ENV_FILE", str(env_file))
    monkeypatch.setattr(
        main_module.env_editor.config, "ENV_EXAMPLE_FILE", str(TEMPLATE))
    monkeypatch.setattr(
        main_module.env_editor.config, "ENV_BACKUP_DIR", str(backup_dir))
    main_module._sessions["env-test-token"] = "test-user"
    client = TestClient(main_module.app)

    assert client.get("/api/env").status_code == 401
    assert client.post("/api/env", json={}).status_code == 401
    client.cookies.set("session", "env-test-token")
    response = client.get("/api/env")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "api-private" not in response.text
    saved = client.post(
        "/api/env",
        json=_payload(response.json(), values={"CHARTS_LANG": "fr"}),
    )
    assert saved.status_code == 200
    assert saved.headers["cache-control"] == "no-store"
    assert "api-private" not in saved.text
    assert "CHARTS_LANG=fr" in env_file.read_text(encoding="utf-8")
