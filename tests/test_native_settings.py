from __future__ import annotations

import json

import pytest

from hermes_constants import get_hermes_home, reset_hermes_home_override, set_hermes_home_override
from workers_core import config, legacy as legacy_config


def _legacy(home, document):
    path = home / "configs" / "workers.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _save_at(home, document):
    token = set_hermes_home_override(home)
    try:
        config.save_config(document)
    finally:
        reset_hermes_home_override(token)


def test_visual_page_saves_without_an_automatic_json_form(tmp_path, monkeypatch):
    from hermes_cli.plugins_settings import plugin_settings_fields

    home = tmp_path / "hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    assert plugin_settings_fields("workers", config.plugin_root()) == []

    document = config.default_config()
    config.save_config(document)
    present, stored = config._native_settings(home)
    assert present and stored == config.validate_config(document)
    assert not (home / "configs" / "workers.json").exists()


def test_legacy_migration_preserves_document_and_archives_only_after_readback(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    document = config.default_config()
    document["routes_by_assignee"] = {"worker-b": "claude-code"}
    document["workers"]["claude-code"].update({
        "credential_binding": "opencode-go:primary",
        "hidden_backend_value": {"mode": "preserve", "enabled": False},
    })
    document["workers"]["future-provider"] = {"model": "future/model", "enabled": True}
    original = _legacy(home, document).read_bytes()
    legacy = home / "configs" / "workers.json"

    assert legacy_config.migrate_legacy_config() is True
    present, stored = config._native_settings(home)
    assert present and stored == config.validate_config(document)
    assert json.loads(legacy.with_name("workers.json.migrated").read_text()) == document
    assert not legacy.exists()
    assert legacy_config.migrate_legacy_config() is False
    assert config.load_config() == config.validate_config(document)
    assert original == legacy.with_name("workers.json.migrated").read_bytes()


def test_existing_native_configuration_wins_and_legacy_is_retained(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    native = config.default_config()
    native["workers"]["opencode"].update(
        enabled=True, model="fixture-opencode-model"
    )
    _save_at(home, native)
    legacy = _legacy(home, config.default_config())
    original = legacy.read_bytes()

    assert legacy_config.migrate_legacy_config() is False
    assert legacy.read_bytes() == original
    assert not legacy.with_name("workers.json.migrated").exists()
    loaded = config.load_config()
    assert loaded["workers"]["opencode"]["enabled"] is True
    assert loaded["workers"]["opencode"]["model"] == "fixture-opencode-model"


def test_corrupt_legacy_file_fails_closed_without_replacing_it(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    legacy = home / "configs" / "workers.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text('{"schema":', encoding="utf-8")
    original = legacy.read_bytes()

    with pytest.raises(RuntimeError, match="Cannot read legacy Workers configuration"):
        config.load_config()

    assert legacy.read_bytes() == original
    assert not (home / "config.yaml").exists()
    assert not legacy.with_name("workers.json.migrated").exists()


def test_native_management_refusal_keeps_legacy_source(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    legacy = _legacy(home, config.default_config())
    original = legacy.read_bytes()

    from hermes_cli import plugins_state

    def refuse(*_args, **_kwargs):
        raise PermissionError("administrator-managed setting")

    monkeypatch.setattr(plugins_state, "save_plugin_setting", refuse)
    with pytest.raises(PermissionError, match="administrator-managed"):
        legacy_config.migrate_legacy_config()

    assert legacy.read_bytes() == original
    assert not legacy.with_name("workers.json.migrated").exists()


def test_failed_readback_keeps_legacy_source(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    legacy = _legacy(home, config.default_config())
    original = legacy.read_bytes()
    native_settings = config._native_settings
    reads = 0

    def mismatch_on_readback(target):
        nonlocal reads
        reads += 1
        if reads == 2:
            different = config.default_config()
            different["workers"]["opencode"].update(
                enabled=True, model="fixture-provider/model"
            )
            return True, different
        return native_settings(target)

    monkeypatch.setattr(config, "_native_settings", mismatch_on_readback)
    with pytest.raises(RuntimeError, match="did not match"):
        legacy_config.migrate_legacy_config()

    assert legacy.read_bytes() == original
    assert not legacy.with_name("workers.json.migrated").exists()


def test_existing_archive_is_never_overwritten(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    legacy = _legacy(home, config.default_config())
    archive = legacy.with_name("workers.json.migrated")
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_text("older archive", encoding="utf-8")
    original = legacy.read_bytes()

    assert legacy_config.migrate_legacy_config() is True
    assert archive.read_text(encoding="utf-8") == "older archive"
    assert legacy.read_bytes() == original
    assert config.load_config() == config.validate_config(config.default_config())


def test_profile_configuration_overrides_shared_and_empty_profile_inherits(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    profile = home / "profiles" / "worker-b"
    profile.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    shared = config.default_config()
    shared["workers"]["codex"].update(
        enabled=True, model="fixture-codex-model"
    )
    shared["inheritance"]["mcp"] = False
    _save_at(home, shared)
    loaded_shared = config.load_config()
    assert loaded_shared["workers"]["codex"]["enabled"] is True
    assert loaded_shared["workers"]["codex"]["model"] == "fixture-codex-model"

    selected = config.default_config()
    selected["workers"]["opencode"].update(
        enabled=True, model="fixture-opencode-model"
    )
    selected["inheritance"]["project_rules"] = False
    _save_at(profile, selected)
    token = set_hermes_home_override(profile)
    try:
        profile_config = config.load_config()
        assert profile_config["workers"]["opencode"]["enabled"] is True
        assert profile_config["workers"]["opencode"]["model"] == "fixture-opencode-model"
        assert profile_config["workers"]["codex"]["enabled"] is False
        assert profile_config["inheritance"]["project_rules"] is False
        assert profile_config["inheritance"]["mcp"] is True
    finally:
        reset_hermes_home_override(token)
    assert get_hermes_home() == home
    restored_shared = config.load_config()
    assert restored_shared["workers"]["codex"]["enabled"] is True
    assert restored_shared["workers"]["codex"]["model"] == "fixture-codex-model"
    assert restored_shared["workers"]["opencode"]["enabled"] is False
    assert restored_shared["inheritance"]["mcp"] is False

    empty_profile = home / "profiles" / "worker-c"
    empty_profile.mkdir(parents=True)
    before = get_hermes_home()
    token = set_hermes_home_override(empty_profile)
    try:
        inherited = config.load_config()
        assert inherited["workers"]["codex"]["enabled"] is True
        assert inherited["workers"]["codex"]["model"] == "fixture-codex-model"
        assert inherited["inheritance"]["mcp"] is False
    finally:
        reset_hermes_home_override(token)
    assert get_hermes_home() == before == home
