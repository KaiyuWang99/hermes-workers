from __future__ import annotations

import pytest

from workers_core import config, legacy
from workers_core.backend_registry import EXTERNAL_BACKENDS


def test_ensure_config_uses_native_config_without_synthesizing_json(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))

    path = config.ensure_config()
    document = config.load_config()

    assert path == home / "config.yaml"
    assert document["schema"] == "workers-config-v1"
    assert "workers" in document
    assert document["workers"]["claude-code"]["sync_hermes_hooks"] is False
    assert not (home / "configs" / "workers.json").exists()
    assert not (home / "config.yaml").exists()


def test_legacy_default_worker_is_ignored_and_removed():
    document = config.default_config()
    document["default_worker"] = "unknown"

    normalized = config.validate_config(document)

    assert "default_worker" not in normalized


def test_retired_display_keys_are_not_kept():
    document = config.default_config()
    document["config_path"] = "/old/workers.json"
    document["skill_roots"] = ["/old/skills"]

    normalized = config.validate_config(document)

    assert "config_path" not in normalized
    assert "skill_roots" not in normalized


def test_configuration_requires_object_containers():
    try:
        config.validate_config([])
    except ValueError as exc:
        assert "configuration must be an object" in str(exc)
    else:
        raise AssertionError("invalid configuration container was accepted")

    document = config.default_config()
    document["workers"]["claude-code"] = None
    try:
        config.validate_config(document)
    except ValueError as exc:
        assert "workers.claude-code" in str(exc)
    else:
        raise AssertionError("non-object Claude Code configuration was accepted")


def test_missing_claude_code_gets_default_config(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    document = config.default_config()
    document["workers"].pop("claude-code")

    normalized = config.validate_config(document)

    assert normalized["workers"]["claude-code"]["enabled"] is False
    assert normalized["workers"]["claude-code"]["sync_hermes_hooks"] is False
    assert "default_worker" not in normalized


def test_validation_allows_multiple_enabled_legacy_backends():
    document = config.default_config()
    document["default_worker"] = "native"
    document["workers"]["opencode"].update(
        enabled=True, model="fixture-opencode-model"
    )
    document["workers"]["codex"].update(
        enabled=True, model="fixture-codex-model"
    )

    normalized = config.validate_config(document)

    assert "default_worker" not in normalized
    assert normalized["workers"]["opencode"]["enabled"] is True
    assert normalized["workers"]["codex"]["enabled"] is True


def test_new_install_defaults_are_neutral_and_saved_choices_are_preserved():
    document = config.default_config()

    assert "default_worker" not in document
    assert set(document["workers"]) == {spec.id for spec in EXTERNAL_BACKENDS}
    for entry in document["workers"].values():
        assert entry["enabled"] is False
        assert entry["model"] == ""
        assert entry["reasoning_effort"] == ""
    assert document["workers"]["claude-code"]["permission_mode"] == ""

    document["default_worker"] = "native"
    document["workers"]["opencode"].update(
        enabled=True,
        model="fixture-provider/user-model",
        reasoning_effort="user-variant",
        future_setting={"keep": True},
    )
    document["workers"]["claude-code"].update(
        model="fixture-provider/claude-model",
        permission_mode="bypassPermissions",
    )
    normalized = config.validate_config(document)

    assert "default_worker" not in normalized
    assert normalized["workers"]["opencode"]["enabled"] is True
    assert normalized["workers"]["opencode"]["model"] == "fixture-provider/user-model"
    assert normalized["workers"]["opencode"]["reasoning_effort"] == "user-variant"
    assert normalized["workers"]["opencode"]["future_setting"] == {"keep": True}
    assert normalized["workers"]["claude-code"]["permission_mode"] == "bypassPermissions"


def test_claude_hook_sync_setting_must_be_boolean(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    document = config.default_config()
    document["workers"]["claude-code"]["sync_hermes_hooks"] = "yes"

    try:
        config.validate_config(document)
    except ValueError as exc:
        assert "sync_hermes_hooks" in str(exc)
    else:
        raise AssertionError("invalid Claude hook sync setting was accepted")


def test_claude_hook_selection_is_normalized_and_validated(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    document = config.default_config()
    document["workers"]["claude-code"]["hermes_hook_selection"] = {
        "hook-abc": True,
        "hook-def": False,
    }

    normalized = config.validate_config(document)

    assert normalized["workers"]["claude-code"]["hermes_hook_selection"] == {
        "hook-abc": True,
        "hook-def": False,
    }

    document["workers"]["claude-code"]["hermes_hook_selection"] = {
        "hook-abc": "yes"
    }
    try:
        config.validate_config(document)
    except ValueError as exc:
        assert "hermes_hook_selection" in str(exc)
    else:
        raise AssertionError("invalid Claude hook selection was accepted")


def test_inheritance_missing_keys_preserve_previous_behavior_and_unknown_fields():
    document = config.default_config()
    document.pop("inheritance")
    assert config.inheritance_policy() == config.INHERITANCE_DEFAULTS
    assert config.inheritance_policy({}) == config.INHERITANCE_DEFAULTS
    assert config.validate_config(document)["inheritance"] == config.INHERITANCE_DEFAULTS
    assert "inheritance" not in document

    document["inheritance"] = {"skills": False, "future_setting": {"keep": True}}
    normalized = config.validate_config(document)
    assert normalized["inheritance"] == {
        **config.INHERITANCE_DEFAULTS,
        "skill_index": False, "auto_skills": False, "card_skills": False,
        "future_setting": {"keep": True},
    }
    assert config.inheritance_policy(document)["project_rules"] is True
    assert config.inheritance_policy(document)["auto_skills"] is False


def test_legacy_groups_expand_without_overriding_explicit_individual_choices():
    # The 1.5 group switches live in the compatibility layer, not on the main path.
    assert set(legacy.LEGACY_INHERITANCE_GROUPS) == {"profile_context", "skills"}
    assert not hasattr(config, "LEGACY_INHERITANCE_GROUPS")
    document = config.default_config()
    document["inheritance"] = {"profile_context": False, "skills": False, "user_profile": True, "card_skills": True}
    policy = config.validate_config(document)["inheritance"]
    assert policy["user_profile"] is True and policy["card_skills"] is True
    assert all(policy[key] is False for key in ("soul", "personality", "memory", "external_memory", "plugin_context", "skill_index", "auto_skills"))
    assert policy["hermes_tools"] is True and policy["fallback_models"] is True
    assert "profile_context" not in policy and "skills" not in policy


def test_migration_entry_point_is_isolated_in_the_legacy_module():
    assert callable(legacy.migrate_legacy_config)
    assert not hasattr(config, "migrate_legacy_config")
    assert not hasattr(config, "_legacy_config_path")


@pytest.mark.parametrize("backend,key", [("claude-code", "native_skills"), ("codex", "ignore_user_config"), ("opencode", "inherit_user_config"), ("opencode", "inherit_project_config")])
def test_native_cli_switches_require_booleans(backend, key):
    document = config.default_config()
    document["workers"][backend][key] = "false"
    with pytest.raises(ValueError, match=key):
        config.validate_config(document)


@pytest.mark.parametrize("sources", [False, "user", [False], [""]])
def test_claude_settings_sources_reject_invalid_shapes(sources):
    document = config.default_config()
    document["workers"]["claude-code"]["settings_sources"] = sources
    with pytest.raises(ValueError, match="settings_sources"):
        config.validate_config(document)


def test_claude_settings_sources_leave_cli_specific_values_to_the_cli():
    document = config.default_config()
    document["workers"]["claude-code"]["settings_sources"] = ["future"]
    normalized = config.validate_config(document)
    assert normalized["workers"]["claude-code"]["settings_sources"] == ["future"]


@pytest.mark.parametrize("key", config.INHERITANCE_DEFAULTS)
@pytest.mark.parametrize("value", ["false", 0, None, []])
def test_inheritance_rejects_non_boolean_switches(key, value):
    document = config.default_config()
    document["inheritance"] = {key: value}
    with pytest.raises(ValueError, match=f"inheritance.{key} must be a boolean"):
        config.validate_config(document)


@pytest.mark.parametrize("value", [None, False, "all", []])
def test_inheritance_requires_object(value):
    document = config.default_config()
    document["inheritance"] = value
    with pytest.raises(ValueError, match="inheritance must be an object"):
        config.validate_config(document)
