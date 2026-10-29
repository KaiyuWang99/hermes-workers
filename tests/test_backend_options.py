from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from workers_core import options
from workers_core.backend_registry import EXTERNAL_BACKENDS, get_backend


def load_api():
    path = Path(__file__).resolve().parents[1] / "dashboard" / "plugin_api.py"
    name = "workers_dashboard_backend_options_test"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def verbose_output():
    return """OpenCode verbose model metadata:
{
  "providerID": "provider-a",
  "id": "model-a",
  "name": "Model A",
  "variants": {
    "balanced": {"reasoningEffort": "medium"},
    "long": {"thinkingConfig": {"budgetTokens": 32000}}
  },
  "headers": {"Authorization": "private-test-value"}
}
{
  "providerID": "provider-a",
  "id": "model-b",
  "variants": {}
}
"""


def _opencode_entry(tmp_path: Path) -> dict:
    binary = tmp_path / "opencode"
    binary.write_text("test executable placeholder", encoding="utf-8")
    binary.chmod(0o700)
    return {
        "binary": str(binary),
        "model": "provider-a/model-a",
        "inherit_user_config": True,
        "inherit_project_config": True,
    }


def test_opencode_binary_can_be_resolved_from_path(tmp_path, monkeypatch):
    binary = tmp_path / "opencode"
    binary.write_text("test executable placeholder", encoding="utf-8")
    binary.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path))

    resolved = options._opencode_binary(
        {"binary": "opencode"}, get_backend("opencode").load()
    )

    assert resolved == binary.resolve()


def test_parse_opencode_verbose_extracts_only_per_model_variant_names():
    records = options.parse_opencode_models_verbose(verbose_output())

    assert records == {
        "provider-a/model-a": ("balanced", "long"),
        "provider-a/model-b": (),
    }
    assert "private-test-value" not in str(records)
    assert "reasoningEffort" not in str(records)


def test_opencode_options_are_model_specific_and_default_only_when_empty(tmp_path, monkeypatch):
    options._OPENCODE_CACHE.clear()
    monkeypatch.setattr(options, "_opencode_config_signature", lambda *_args: "config-1")
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=verbose_output(), stderr="")

    monkeypatch.setattr(options.subprocess, "run", run)
    entry = _opencode_entry(tmp_path)

    selected = options.get_backend_options(
        "opencode", entry, model="provider-a/model-a", scope="profile-a"
    )
    empty = options.get_backend_options(
        "opencode", entry, model="provider-a/model-b", scope="profile-a"
    )

    assert selected == {
        "backend": "opencode",
        "kind": "variant",
        "options": ["balanced", "long"],
        "default": "",
        "source": "opencode-models-verbose",
    }
    assert empty["options"] == []
    assert empty["default"] == ""
    assert "default" in empty["message"].lower()
    assert len(calls) == 1
    assert calls[0][0] == [entry["binary"], "models", "--verbose"]
    assert "private-test-value" not in str(selected)


@pytest.mark.parametrize("model", ["", "not-a-provider-model", "provider-a/"])
def test_opencode_empty_or_invalid_model_does_not_probe(tmp_path, monkeypatch, model):
    entry = _opencode_entry(tmp_path)
    calls = []

    def forbidden(*_args, **_kwargs):
        calls.append(True)
        pytest.fail("an invalid or empty model must not trigger metadata discovery")

    monkeypatch.setattr(options, "_opencode_binary", forbidden)
    monkeypatch.setattr(options, "_opencode_config_signature", forbidden)
    monkeypatch.setattr(options, "_probe_opencode_variants", forbidden)

    result = options.get_backend_options("opencode", entry, model=model)

    assert result["options"] == []
    assert result["default"] == ""
    assert "provider/model" in result["message"]
    assert calls == []


def test_opencode_options_cache_keys_binary_config_scope_and_refresh(tmp_path, monkeypatch):
    options._OPENCODE_CACHE.clear()
    entry = _opencode_entry(tmp_path)
    signature = {"value": "config-1"}
    calls = []

    monkeypatch.setattr(
        options, "_opencode_config_signature", lambda *_args: signature["value"]
    )
    monkeypatch.setattr(
        options.subprocess,
        "run",
        lambda command, **kwargs: calls.append((command, kwargs))
        or SimpleNamespace(returncode=0, stdout=verbose_output(), stderr=""),
    )

    options.get_backend_options("opencode", entry, model="provider-a/model-a", scope="a")
    options.get_backend_options("opencode", entry, model="provider-a/model-b", scope="a")
    assert len(calls) == 1, "switching the selected model reuses the CLI catalog"

    signature["value"] = "config-2"
    options.get_backend_options("opencode", entry, model="provider-a/model-a", scope="a")
    assert len(calls) == 2, "a variant configuration change invalidates the catalog"

    options.get_backend_options(
        "opencode", entry, model="provider-a/model-a", scope="a", refresh=True
    )
    assert len(calls) == 3
    assert calls[-1][0][-1] == "--refresh"

    options.get_backend_options("opencode", entry, model="provider-a/model-a", scope="b")
    assert len(calls) == 4, "profile scopes keep CLI metadata caches separate"
    options._OPENCODE_CACHE.clear()


def test_opencode_cli_probe_error_is_not_guessed_or_exposed(tmp_path, monkeypatch):
    options._OPENCODE_CACHE.clear()
    entry = _opencode_entry(tmp_path)
    monkeypatch.setattr(options, "_opencode_config_signature", lambda *_args: "config-1")
    monkeypatch.setattr(
        options.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired("opencode", 20)
        ),
    )

    with pytest.raises(options.BackendOptionsError):
        options.get_backend_options("opencode", entry, model="provider-a/model-a")
    options._OPENCODE_CACHE.clear()


def test_opencode_unrecognized_nonempty_output_is_an_error(tmp_path, monkeypatch):
    options._OPENCODE_CACHE.clear()
    entry = _opencode_entry(tmp_path)
    monkeypatch.setattr(options, "_opencode_config_signature", lambda *_args: "config-1")
    monkeypatch.setattr(
        options.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0, stdout="OpenCode changed its catalog format", stderr=""
        ),
    )

    with pytest.raises(options.BackendOptionsError, match="format was not recognized"):
        options.get_backend_options("opencode", entry, model="provider-a/model-a")
    options._OPENCODE_CACHE.clear()


def test_backend_option_kinds_use_their_own_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(options, "claude_effort_options", lambda *_args, **_kwargs: ("low", "max"))
    claude = options.get_backend_options(
        "claude-code", {"binary": "/configured/claude"}, model="provider/model"
    )
    codex = options.get_backend_options(
        "codex",
        {"model": "model-a"},
        model="model-a",
        models_loader=lambda backend, refresh: [
            {
                "id": "model-a",
                "supportedReasoningEfforts": [
                    {"reasoningEffort": "low"},
                    {"reasoningEffort": "model-specific"},
                ],
                "defaultReasoningEffort": "model-specific",
            },
            {
                "id": "model-b",
                "supportedReasoningEfforts": [{"reasoningEffort": "high"}],
                "defaultReasoningEffort": "high",
            },
        ],
    )

    assert claude["kind"] == "effort"
    assert claude["options"] == ["low", "max"]
    assert claude["default"] == ""
    assert claude["source"] == "claude-cli-help"
    assert codex == {
        "backend": "codex",
        "kind": "reasoning_effort",
        "options": ["low", "model-specific"],
        "default": "model-specific",
        "source": "codex-model/list",
    }

    codex_b = options.get_backend_options(
        "codex",
        {"model": "model-a"},
        model="model-b",
        models_loader=lambda _backend, _refresh: [
            {"id": "model-b", "supportedReasoningEfforts": [{"reasoningEffort": "high"}]}
        ],
    )
    assert codex_b["options"] == ["high"]
    assert codex_b["default"] == ""


def test_native_backend_has_no_option_source():
    native = options.get_backend_options("native", {})

    assert native == {
        "backend": "native",
        "kind": None,
        "options": [],
        "default": "",
        "source": "none",
    }


def test_unknown_backend_is_rejected():
    with pytest.raises(options.BackendOptionsError):
        options.get_backend_options("unknown", {})


def test_option_dispatch_uses_backend_module_not_option_kind(monkeypatch):
    calls = []
    fake_module = SimpleNamespace(
        model_options=lambda entry, **kwargs: calls.append((entry, kwargs))
        or {
            "options": ["backend-specific-a", "backend-specific-b"],
            "default": "backend-specific-a",
            "source": "fake-backend",
        }
    )
    fake_spec = SimpleNamespace(
        id="fake-effort",
        option_kind="effort",
        load=lambda: fake_module,
    )
    monkeypatch.setattr(options, "get_backend", lambda _backend: fake_spec)
    monkeypatch.setattr(
        options,
        "claude_effort_options",
        lambda *_args, **_kwargs: pytest.fail("kind must not select Claude discovery"),
    )

    result = options.get_backend_options(
        "fake-effort", {"model": "provider/model"}, model="provider/selected"
    )

    assert result == {
        "options": ["backend-specific-a", "backend-specific-b"],
        "default": "backend-specific-a",
        "source": "fake-backend",
        "backend": "fake-effort",
        "kind": "effort",
    }
    assert calls == [
        (
            {"model": "provider/model"},
            {
                "model": "provider/selected",
                "refresh": False,
                "scope": "",
                "models_loader": None,
            },
        )
    ]


def test_api_state_and_unified_option_routes(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    api = load_api()
    document = {
        "workers": {
            "opencode": {"binary": "/configured/opencode", "model": "provider/model"},
            "codex": {"binary": "/configured/codex", "model": "codex-model"},
            "claude-code": {"binary": "/configured/claude", "model": "provider/model"},
        }
    }
    monkeypatch.setattr(api, "load_config", lambda: document)
    monkeypatch.setattr(api, "public_config", lambda value: value)
    monkeypatch.setattr(api, "_light_status", lambda _value: {})
    monkeypatch.setattr(api, "_profile_cache_key", lambda: "profile-a")
    monkeypatch.setattr(
        api,
        "_models",
        lambda backend, refresh=False: [
            {
                "id": "codex-model",
                "supportedReasoningEfforts": [{"reasoningEffort": "xhigh"}],
                "defaultReasoningEffort": "xhigh",
            }
        ],
    )
    option_calls = []

    def resolve_options(backend, entry, **kwargs):
        option_calls.append((backend, entry, kwargs))
        return {
            "backend": backend,
            "kind": "variant",
            "options": ["fast", "deep"],
            "default": "",
            "source": "opencode-models-verbose",
        }

    monkeypatch.setattr(api, "get_backend_options", resolve_options)

    state = api.get_state()
    assert [item["id"] for item in state["backends"]] == [
        spec.id for spec in EXTERNAL_BACKENDS
    ]
    assert all("module" not in item for item in state["backends"])
    assert api.get_options("opencode", model="provider/model", refresh=True)["options"] == ["fast", "deep"]
    assert len(option_calls) == 1
    backend, entry, kwargs = option_calls[0]
    assert backend == "opencode"
    assert entry is document["workers"]["opencode"]
    assert kwargs["model"] == "provider/model"
    assert kwargs["refresh"] is True
    assert kwargs["scope"] == "profile-a"
    assert kwargs["models_loader"]("codex", False)[0]["id"] == "codex-model"
    api.get_options("opencode", model="")
    assert option_calls[-1][2]["model"] == ""

    with pytest.raises(HTTPException) as error:
        api.get_options("unknown")
    assert error.value.status_code == 404

    monkeypatch.setattr(
        api,
        "get_backend_options",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            HTTPException(status_code=503, detail="private provider output")
        ),
    )
    with pytest.raises(HTTPException) as error:
        api.get_options("opencode")
    assert error.value.status_code == 503
    assert error.value.detail == "Worker backend options are unavailable."
    assert "private" not in str(error.value.detail)
