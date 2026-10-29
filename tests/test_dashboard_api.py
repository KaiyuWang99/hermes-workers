from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def load_api():
    path = Path(__file__).resolve().parents[1] / "dashboard" / "plugin_api.py"
    name = "workers_dashboard_test"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_state_and_atomic_configuration(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    api = load_api()
    monkeypatch.setattr(
        api,
        "credential_status",
        lambda provider, model=None: {
            "provider": provider,
            "configured": True,
            "available": True,
            "mode": "pool",
            "next_available_at": None,
        },
    )
    monkeypatch.setattr(
        api,
        "_models",
        lambda backend, refresh=False: (
            [{"id": "gpt-5.6-sol", "supportedReasoningEfforts": [{"reasoningEffort": "xhigh"}]}]
            if backend == "codex"
            else [{"id": "opencode-go/deepseek-v4-flash"}]
        ),
    )

    state = api.get_state()
    document = state["configuration"]
    assert state["claude_capabilities"] == {}
    assert document["config_path"] == str(home / "config.yaml")
    document.pop("config_path", None)
    document["routes_by_assignee"] = {"default": "codex"}
    saved = api.put_configuration({"configuration": document})

    assert saved["configuration"]["routes_by_assignee"] == {"default": "codex"}
    assert (home / "config.yaml").is_file()
    assert not (home / "configs" / "workers.json").exists()


def test_state_exposes_claude_capabilities_when_enabled(monkeypatch):
    api = load_api()
    from workers_core import config
    from workers_core.backends import claudecode

    document = config.default_config()
    document["workers"]["claude-code"]["enabled"] = True
    fake = {
        "binary": "claude",
        "version": "9.9.9",
        "features": {"strict_mcp_config": False},
        "choices": {"setting_sources": ["user"]},
    }
    monkeypatch.setattr(api, "load_config", lambda: document)
    monkeypatch.setattr(api, "_light_status", lambda _document: {})
    monkeypatch.setattr(claudecode, "capabilities", lambda binary: fake)

    assert api.get_state()["claude_capabilities"] == fake


def test_unchanged_unavailable_provider_does_not_block_other_settings(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    api = load_api()
    from workers_core import config

    previous = config.default_config()
    previous["workers"]["claude-code"].update({
        "enabled": True,
        "model": "missing-provider/model-a",
    })
    config.save_config(previous)
    monkeypatch.setattr(
        api,
        "credential_status",
        lambda provider, model=None: {"provider": provider, "configured": False, "available": False},
    )

    updated = dict(previous)
    updated["routes_by_assignee"] = {"default": "opencode"}
    saved = api.put_configuration({"configuration": updated})

    assert saved["configuration"]["routes_by_assignee"] == {"default": "opencode"}
    assert saved["configuration"]["workers"]["claude-code"]["model"] == "missing-provider/model-a"


def test_changed_enabled_backends_use_registry_selection_validators(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    api = load_api()
    from workers_core.config import default_config
    from workers_core.backends import claudecode, codex, opencode

    previous = default_config()
    draft = default_config()
    draft["workers"]["opencode"].update(enabled=True, model="provider/model")
    draft["workers"]["codex"].update(enabled=True, model="codex-model")
    draft["workers"]["claude-code"].update(enabled=True, model="provider/model")
    monkeypatch.setattr(api, "load_config", lambda: previous)

    validation_calls = []
    for backend, module in (
        ("opencode", opencode),
        ("codex", codex),
        ("claude-code", claudecode),
    ):
        monkeypatch.setattr(
            module,
            "validate_selection",
            lambda entry, *, models=None, backend=backend: validation_calls.append(
                (backend, entry, models)
            ),
        )

    codex_rows = [{"id": "codex-model", "supportedReasoningEfforts": []}]
    cache_reads = []
    monkeypatch.setattr(
        api,
        "_cached_models",
        lambda backend: cache_reads.append(backend) or codex_rows,
    )
    saved = []
    monkeypatch.setattr(api, "save_config", lambda value: saved.append(value))
    monkeypatch.setattr(api, "get_state", lambda: {"ok": True})

    assert api.put_configuration({"configuration": draft}) == {"ok": True}

    assert [item[0] for item in validation_calls] == ["opencode", "codex", "claude-code"]
    assert validation_calls[0][2] is None
    assert validation_calls[1][2] == codex_rows
    assert validation_calls[2][2] is None
    assert cache_reads == ["codex"]
    assert len(saved) == 1


@pytest.mark.parametrize(
    ("backend", "field"),
    [
        ("opencode", "model"),
        ("opencode", "reasoning_effort"),
        ("opencode", "binary"),
        ("codex", "model"),
        ("codex", "reasoning_effort"),
        ("codex", "binary"),
        ("codex", "codex_home"),
        ("claude-code", "model"),
        ("claude-code", "reasoning_effort"),
        ("claude-code", "binary"),
    ],
)
def test_changed_selection_fields_revalidate_enabled_backend(monkeypatch, backend, field):
    api = load_api()
    from workers_core.backend_registry import get_backend
    from workers_core.config import default_config
    from workers_core.backends import claudecode, codex, opencode

    # The registry is the single source of selection fields; the API keeps none.
    assert not hasattr(api, "_SELECTION_FIELDS")
    assert field in get_backend(backend).selection_fields

    previous = default_config()
    draft = default_config()
    for document in (previous, draft):
        document["workers"][backend].update(enabled=True, model="provider/old")
    draft["workers"][backend][field] = (
        "provider/new" if field == "model" else "changed-value"
    )

    calls = []
    for backend_id, module in (
        ("opencode", opencode),
        ("codex", codex),
        ("claude-code", claudecode),
    ):
        monkeypatch.setattr(
            module,
            "validate_selection",
            lambda _entry, *, models=None, backend_id=backend_id: calls.append(
                (backend_id, models)
            ),
        )
    monkeypatch.setattr(api, "_cached_models", lambda _backend: None)

    api._validate_backend_choices(draft, previous)

    assert [item[0] for item in calls] == [backend]


def test_unrelated_backend_settings_do_not_revalidate_legacy_selection(monkeypatch):
    api = load_api()
    from workers_core.config import default_config
    from workers_core.backends import claudecode, codex, opencode

    previous = default_config()
    draft = default_config()
    for document in (previous, draft):
        document["workers"]["claude-code"].update(
            enabled=True, model="missing-provider/legacy-model", reasoning_effort="legacy"
        )
        document["workers"]["opencode"].update(
            enabled=True, model="missing-provider/legacy-model", reasoning_effort="legacy"
        )
        document["workers"]["codex"].update(
            enabled=True,
            model="legacy-model",
            reasoning_effort="legacy",
            codex_home="/legacy/codex-home",
        )
    draft["workers"]["claude-code"].update(
        sync_hermes_hooks=True, tools=["Read"]
    )
    draft["workers"]["opencode"]["inherit_user_config"] = False
    draft["workers"]["codex"].update(network_access=True, sandbox="read-only")
    draft["inheritance"]["mcp"] = False

    for module in (claudecode, opencode, codex):
        monkeypatch.setattr(
            module,
            "validate_selection",
            lambda *_args, **_kwargs: pytest.fail("unrelated settings must preserve legacy choices"),
        )
    monkeypatch.setattr(
        api,
        "_cached_models",
        lambda *_args: pytest.fail("no selection validation means no catalog read"),
    )

    api._validate_backend_choices(draft, previous)


def test_state_returns_opencode_and_claude_credential_dtos(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    api = load_api()
    document = {
        "workers": {
            "opencode": {"model": "opencode-provider/model-a"},
            "claude-code": {"model": "claude-provider/model-b"},
        }
    }
    calls = []
    status_by_provider = {
        "opencode-provider": {
            "provider": "opencode-provider", "configured": True, "available": True, "mode": "pool"
        },
        "claude-provider": {
            "provider": "claude-provider", "configured": True, "available": True, "mode": "pool"
        },
    }

    def credential(provider, model=None):
        calls.append((provider, model))
        return status_by_provider[provider]

    monkeypatch.setattr(api, "load_config", lambda: document)
    monkeypatch.setattr(api, "public_config", lambda value: value)
    monkeypatch.setattr(api, "credential_status", credential)
    monkeypatch.setattr(api, "doctor_backend", lambda *_args, **_kwargs: pytest.fail("doctor called"))

    state = api.get_state()

    assert calls == [("opencode-provider", "model-a"), ("claude-provider", "model-b")]
    assert state["status"] == {
        "opencode": {"credential": status_by_provider["opencode-provider"]},
        "claude-code": {"credential": status_by_provider["claude-provider"]},
    }

    document["workers"]["opencode"]["model"] = "malformed-model"
    calls.clear()
    invalid_state = api.get_state()
    assert calls == [("claude-provider", "model-b")]
    assert invalid_state["status"]["opencode"]["credential"] == {
        "configured": False,
        "available": False,
        "mode": "missing",
        "error": "Worker model must use provider/model format",
    }


def test_model_refresh_reaches_shared_catalog(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    api = load_api()
    calls = []
    monkeypatch.setattr(
        api,
        "list_backend_models",
        lambda backend, refresh=False: calls.append((backend, refresh)) or [],
    )

    api._models("opencode", refresh=True)

    assert calls == [("opencode", True)]


def test_model_cache_isolated_by_profile(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    api = load_api()
    calls = []
    profile = {"path": home / "profiles" / "default"}

    monkeypatch.setattr(api, "_model_cache", {})
    monkeypatch.setattr(api, "_profile_cache_key", lambda: str(profile["path"]))
    monkeypatch.setattr(
        api,
        "list_backend_models",
        lambda backend, refresh=False: calls.append((backend, refresh))
        or [{"id": f"{backend}-model"}],
    )

    assert api._models("opencode") == [{"id": "opencode-model"}]
    assert api._models("opencode") == [{"id": "opencode-model"}]

    profile["path"] = home / "profiles" / "coder"
    assert api._models("opencode") == [{"id": "opencode-model"}]
    assert calls == [("opencode", False), ("opencode", False)]


def test_cached_models_skips_expired_catalog_without_refreshing(monkeypatch):
    api = load_api()
    profile = "profile-a"
    rows = [{"id": "stale-model"}]
    now = api.time.monotonic()
    monkeypatch.setattr(api, "_profile_cache_key", lambda: profile)
    monkeypatch.setattr(api, "_model_cache", {
        ("codex", profile): (now - api._MODEL_CACHE_TTL + 1, rows)
    })
    monkeypatch.setattr(
        api,
        "list_backend_models",
        lambda *_args, **_kwargs: pytest.fail("save validation must not refresh the catalog"),
    )

    assert api._cached_models("codex") == rows
    api._model_cache[("codex", profile)] = (now - api._MODEL_CACHE_TTL - 1, rows)
    assert api._cached_models("codex") is None


def test_state_includes_sanitized_hermes_hooks(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    api = load_api()
    hooks = [{"id": "hook-1", "event": "pre_tool_call", "selected": True}]
    from workers_core import claude_hooks
    monkeypatch.setattr(claude_hooks, "list_hooks", lambda *args, **kwargs: hooks)
    monkeypatch.setattr(
        claude_hooks, "orphan_selections", lambda *args, **kwargs: ["hook-renamed"]
    )

    state = api.get_state()
    assert "hermes_hooks" not in state
    response = api.get_hooks()
    assert response["hermes_hooks"] == hooks
    assert response["orphaned_selections"] == ["hook-renamed"]


def test_doctor_defaults_to_native(monkeypatch):
    api = load_api()
    from workers_core.config import default_config

    seen = []
    monkeypatch.setattr(api, "load_config", lambda: default_config())
    monkeypatch.setattr(
        api,
        "doctor_backend",
        lambda backend, config: seen.append(backend) or {"backend": backend, "ok": True},
    )

    result = api.doctor()

    assert seen == ["native"]
    assert result["backend"] == "native"


def test_draft_doctor_defaults_to_native(monkeypatch):
    api = load_api()
    from workers_core.config import default_config

    seen = []
    monkeypatch.setattr(
        api,
        "doctor_backend",
        lambda backend, config: seen.append(backend) or {"backend": backend, "ok": True},
    )

    result = api.doctor_draft({"configuration": default_config()})

    assert seen == ["native"]
    assert result["backend"] == "native"


def test_native_doctor_marks_cli_and_authentication_not_applicable():
    api = load_api()

    checks = api._diagnostic_checks("native", {"backend": "native", "ok": True}, scope="draft")

    assert checks["cli"]["status"] == "not_applicable"
    assert checks["authentication"]["status"] == "not_applicable"
    assert checks["execution"]["status"] == "not_tested"


def test_draft_environment_check_does_not_save_or_expose_cli_output(monkeypatch):
    api = load_api()
    from workers_core.config import default_config, validate_config

    document = default_config()
    document["workers"]["codex"]["model"] = "draft-model"
    checked = []
    monkeypatch.setattr(api, "save_config", lambda *_: pytest.fail("diagnostics must not save"))
    monkeypatch.setattr(api, "load_config", lambda: pytest.fail("draft check must use the draft"))
    monkeypatch.setattr(api, "doctor_backend", lambda backend, config: checked.append((backend, config)) or {
        "binary": "/local/codex", "binary_ok": True, "version_ok": True,
        "version": "codex-cli 1.2.3 account=private@example.invalid", "login_ok": True,
        "login": "private-login-output", "error": "private-error-output", "ok": True,
    })

    result = api.doctor_draft({"backend": "codex", "configuration": document})
    assert checked == [("codex", validate_config(document))]
    assert result["ok"] is True
    assert result["diagnostics"]["scope"] == "draft"
    assert result["diagnostics"]["cli"]["version"] == "1.2.3"
    assert result["diagnostics"]["authentication"]["status"] == "passed"
    assert result["diagnostics"]["authentication"]["source"] == "codex-login"
    assert result["diagnostics"]["execution"]["status"] == "not_tested"
    assert "private" not in str(result)
    assert "backends" not in result


def test_diagnostic_checks_include_cli_capabilities():
    api = load_api()
    result = api._diagnostic_checks(
        "claude-code",
        {
            "binary_ok": True,
            "version_ok": True,
            "version": "9.9.9",
            "capabilities": {
                "version": "9.9.9",
                "features": {"strict_mcp_config": False},
            },
        },
        scope="draft",
    )

    assert result["cli"]["features"] == {"strict_mcp_config": False}
    assert "constraints" not in result["cli"]


def test_login_diagnostic_source_uses_backend_id():
    api = load_api()
    result = api._diagnostic_checks("opencode", {"login_ok": True}, scope="draft")

    assert result["authentication"]["source"] == "opencode-login"


@pytest.mark.parametrize("version_ok,available", [(True, False), (False, True)])
def test_environment_check_separates_cli_and_credential_readiness(monkeypatch, version_ok, available):
    api = load_api()
    from workers_core.config import default_config

    monkeypatch.setattr(api, "doctor_backend", lambda *_: {
        "binary_ok": True, "version_ok": version_ok, "version": "2.3.4",
        "credential": {"configured": True, "available": available}, "ok": False,
    })
    result = api.doctor_draft({"backend": "claude-code", "configuration": default_config()})
    checks = result["diagnostics"]
    assert checks["cli"]["status"] == ("passed" if version_ok else "failed")
    assert checks["authentication"]["configured"] is True
    assert checks["authentication"]["status"] == ("passed" if available else "failed")
    assert checks["execution"]["status"] == "not_tested"
    assert result["ok"] is False


def test_invalid_draft_is_rejected_before_environment_probe(monkeypatch):
    api = load_api()
    monkeypatch.setattr(api, "doctor_backend", lambda *_: pytest.fail("invalid draft must not run a CLI"))
    with pytest.raises(Exception) as error:
        api.doctor_draft({"backend": "claude-code", "configuration": {}})
    assert error.value.status_code == 400


def test_codex_login_timeout_preserves_successful_cli_check(tmp_path, monkeypatch):
    import subprocess
    from workers_core.config import default_config
    from workers_core.backends import codex

    api = load_api()
    binary = tmp_path / "codex"
    binary.touch()
    binary.chmod(0o700)
    document = default_config()
    document["workers"]["codex"]["binary"] = str(binary)

    def run(command, **_kwargs):
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, stdout="codex-cli 1.2.3", stderr="")
        raise subprocess.TimeoutExpired(command, 10)

    monkeypatch.setattr(codex.subprocess, "run", run)
    result = api.doctor_draft({"backend": "codex", "configuration": document})
    assert result["diagnostics"]["cli"]["status"] == "passed"
    assert result["diagnostics"]["authentication"]["status"] == "failed"
    assert result["diagnostics"]["execution"]["status"] == "not_tested"
