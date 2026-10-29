from __future__ import annotations

import pytest

from workers_core import config
from workers_core.backend_registry import BACKENDS, EXTERNAL_BACKENDS, get_backend
from workers_core.backends import claudecode, codex, opencode


def test_static_backend_specs_are_complete_and_public_view_is_limited():
    assert set(BACKENDS) == {"native", "claude-code", "opencode", "codex"}
    assert {spec.id for spec in EXTERNAL_BACKENDS} == {
        "claude-code",
        "opencode",
        "codex",
    }
    assert get_backend("native").label == "Hermes Native"
    assert get_backend("claude-code").label == "Claude Code"
    assert get_backend("opencode").label == "OpenCode Worker"
    assert get_backend("codex").label == "Codex"
    assert get_backend("claude-code").inherit_credentials is True
    assert get_backend("claude-code").supervises_children is True
    assert get_backend("opencode").supervises_children is True
    assert get_backend("codex").supervises_children is False
    assert get_backend("native").selection_fields == ()
    assert get_backend("opencode").selection_fields == (
        "model",
        "reasoning_effort",
        "binary",
    )
    assert get_backend("codex").selection_fields == (
        "model",
        "reasoning_effort",
        "binary",
        "codex_home",
        "service_tier",
    )
    assert get_backend("claude-code").selection_fields == (
        "model",
        "reasoning_effort",
        "binary",
        "strict_mcp_config",
        "settings_sources",
        "native_skills",
        "permission_mode",
    )
    assert set(get_backend("codex").public()) == {
        "id",
        "label",
        "model_source",
        "option_kind",
    }
    for spec in EXTERNAL_BACKENDS:
        assert spec.selection_fields, "external backends must declare selection fields"
    for spec in EXTERNAL_BACKENDS:
        module = spec.load()
        assert callable(module.defaults)
        assert callable(module.validate_settings)
        assert callable(module.validate_selection)
        assert callable(module.model_options)
        assert callable(module.list_models)
        assert callable(module.doctor)
        assert callable(module.spawn)
    with pytest.raises(ValueError, match="unknown worker backend"):
        get_backend("missing-cli")


def test_cli_defaults_are_neutral_and_backend_normalizers_keep_unknown_fields():
    document = config.default_config()
    assert "default_worker" not in document
    for spec in EXTERNAL_BACKENDS:
        entry = document["workers"][spec.id]
        assert entry["enabled"] is False
        assert entry["model"] == ""
        assert entry["reasoning_effort"] == ""
        normalized = spec.load().validate_settings(
            {**entry, "future_backend_setting": {"retain": True}}
        )
        assert normalized["future_backend_setting"] == {"retain": True}
    assert document["workers"]["claude-code"]["permission_mode"] == ""


def test_backend_owned_settings_validation():
    with pytest.raises(ValueError, match="sync_hermes_hooks"):
        claudecode.validate_settings(
            {**claudecode.defaults(), "sync_hermes_hooks": "false"}
        )
    with pytest.raises(ValueError, match="inherit_user_config"):
        opencode.validate_settings(
            {**opencode.defaults(), "inherit_user_config": "false"}
        )
    with pytest.raises(ValueError, match="ignore_user_config"):
        codex.validate_settings(
            {**codex.defaults(), "ignore_user_config": "false"}
        )


def test_backend_selection_validation_uses_explicit_catalog_and_credential_fixtures(
    monkeypatch,
):
    monkeypatch.setattr(
        opencode,
        "credential_status",
        lambda provider, model=None: {"configured": provider == "fixture-provider"},
    )
    opencode.validate_selection(
        {"enabled": True, "model": "fixture-provider/model"}
    )
    with pytest.raises(ValueError, match="no matching Hermes credential"):
        opencode.validate_selection(
            {"enabled": True, "model": "unconfigured-provider/model"}
        )

    monkeypatch.setattr(
        claudecode,
        "credential_status",
        lambda provider, model=None: {"configured": provider == "fixture-provider"},
    )
    claudecode.validate_selection(
        {
            "enabled": True,
            "binary": "claude",
            "model": "fixture-provider/model",
        }
    )
    with pytest.raises(ValueError, match="no matching Hermes credential"):
        claudecode.validate_selection(
            {
                "enabled": True,
                "binary": "claude",
                "model": "unconfigured-provider/model",
            }
        )

    catalog = [
        {
            "id": "fixture-codex-model",
            "supportedReasoningEfforts": [{"reasoningEffort": "high"}],
        }
    ]
    codex.validate_selection(
        {
            "enabled": True,
            "model": "fixture-codex-model",
            "reasoning_effort": "high",
        },
        models=catalog,
    )
    with pytest.raises(ValueError, match="does not support reasoning"):
        codex.validate_selection(
            {
                "enabled": True,
                "model": "fixture-codex-model",
                "reasoning_effort": "unsupported",
            },
            models=catalog,
        )


def test_codex_rejects_a_service_tier_the_selected_model_does_not_advertise():
    catalog = [{"id": "fixture-codex-model", "serviceTiers": [{"id": "flex"}]}]

    codex.validate_selection(
        {
            "enabled": True,
            "model": "fixture-codex-model",
            "service_tier": "flex",
        },
        models=catalog,
    )
    with pytest.raises(ValueError, match="does not support service tier"):
        codex.validate_selection(
            {
                "enabled": True,
                "model": "fixture-codex-model",
                "service_tier": "priority",
            },
            models=catalog,
        )
    # A catalog row without tier metadata is unknown, not a rejection.
    codex.validate_selection(
        {
            "enabled": True,
            "model": "fixture-codex-model",
            "service_tier": "priority",
        },
        models=[{"id": "fixture-codex-model"}],
    )


def _claude_caps(features=None, choices=None):
    return {
        "binary": "claude",
        "version": "",
        "features": features or {},
        "choices": choices or {},
    }


@pytest.mark.parametrize(
    "settings, features, message",
    [
        ({"strict_mcp_config": True}, {"strict_mcp_config": False}, "native MCP"),
        ({"settings_sources": ["user"]}, {"settings_sources": False}, "settings sources"),
        ({"native_skills": False}, {"native_skills": False}, "native Skills"),
    ],
)
def test_claude_selection_rejects_settings_the_cli_cannot_honor(
    monkeypatch, settings, features, message
):
    monkeypatch.setattr(
        claudecode,
        "credential_status",
        lambda provider, model=None: {"configured": True},
    )
    monkeypatch.setattr(
        claudecode, "capabilities", lambda binary: _claude_caps(features=features)
    )
    with pytest.raises(ValueError, match=message):
        claudecode.validate_selection(
            {
                "enabled": True,
                "binary": "claude",
                "model": "fixture-provider/model",
                **settings,
            }
        )


def test_claude_selection_accepts_settings_when_the_cli_advertises_flags(monkeypatch):
    monkeypatch.setattr(
        claudecode,
        "credential_status",
        lambda provider, model=None: {"configured": True},
    )
    monkeypatch.setattr(
        claudecode,
        "capabilities",
        lambda binary: _claude_caps(
            features={
                "strict_mcp_config": True,
                "settings_sources": True,
                "native_skills": True,
                "permission_mode": True,
                "effort": True,
            },
            choices={
                "setting_sources": ["user", "project", "local"],
                "permission_mode": ["bypassPermissions"],
                "effort": ["high"],
            },
        ),
    )
    claudecode.validate_selection(
        {
            "enabled": True,
            "binary": "claude",
            "model": "fixture-provider/model",
            "strict_mcp_config": True,
            "settings_sources": ["user"],
            "native_skills": False,
            "permission_mode": "bypassPermissions",
            "reasoning_effort": "high",
        }
    )


@pytest.mark.parametrize(
    "settings, message",
    [
        ({"settings_sources": ["future"]}, "setting source"),
        ({"permission_mode": "future"}, "permission mode"),
        ({"reasoning_effort": "turbo"}, "reasoning effort"),
    ],
)
def test_claude_selection_rejects_unsupported_help_choices(
    monkeypatch, settings, message
):
    monkeypatch.setattr(
        claudecode,
        "credential_status",
        lambda provider, model=None: {"configured": True},
    )
    monkeypatch.setattr(
        claudecode,
        "capabilities",
        lambda binary: _claude_caps(
            features={
                "settings_sources": True,
                "permission_mode": True,
                "effort": True,
            },
            choices={
                "setting_sources": ["user", "project", "local"],
                "permission_mode": ["bypassPermissions"],
                "effort": ["high"],
            },
        ),
    )
    with pytest.raises(ValueError, match=message):
        claudecode.validate_selection(
            {
                "enabled": True,
                "binary": "claude",
                "model": "fixture-provider/model",
                **settings,
            }
        )


@pytest.mark.parametrize(
    "helper_name",
    ("_claude_options", "_opencode_options", "_codex_options"),
)
def test_options_helpers_are_explicit_per_backend(helper_name):
    from workers_core import options

    assert callable(getattr(options, helper_name, None))


@pytest.mark.parametrize(
    ("module", "helper_name"),
    [
        (claudecode, "_claude_options"),
        (opencode, "_opencode_options"),
        (codex, "_codex_options"),
    ],
)
def test_backend_model_options_delegates_only_to_its_own_helper(
    monkeypatch, module, helper_name
):
    from workers_core import options

    entry = {"model": "fixture-provider/fixture-model"}
    models_loader = lambda backend, refresh: (backend, refresh)
    captured = {}
    expected = {"options": ["fixture-option"], "default": "", "source": helper_name}

    def helper(actual_entry, **kwargs):
        captured["entry"] = actual_entry
        captured.update(kwargs)
        return expected

    monkeypatch.setattr(options, helper_name, helper)
    result = module.model_options(
        entry,
        model="fixture-provider/fixture-model",
        refresh=True,
        scope="fixture-profile",
        models_loader=models_loader,
    )

    assert result is expected
    assert captured == {
        "entry": entry,
        "model": "fixture-provider/fixture-model",
        "refresh": True,
        "scope": "fixture-profile",
        "models_loader": models_loader,
    }
