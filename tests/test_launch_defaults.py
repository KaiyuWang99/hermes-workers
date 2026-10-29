from types import SimpleNamespace

import pytest

from workers_core.backends import claudecode
from workers_core import config, registry


def test_default_effort_keeps_claude_cli_default(tmp_path, monkeypatch):
    monkeypatch.setattr(claudecode, "_resolve_binary", lambda value: "claude")
    monkeypatch.setattr(claudecode, "_cli_flags", lambda binary: frozenset())
    task = SimpleNamespace(model_override=None, provider_override=None, reasoning_effort=None)
    command = claudecode.build_command(task, str(tmp_path), {
        "binary": "claude", "model": "anthropic/sonnet", "reasoning_effort": "default",
    }, {}, tmp_path / "mcp.json", prompt="task")
    assert "--effort" not in command
    assert "--strict-mcp-config" not in command


def test_ambiguous_enabled_backends_require_an_explicit_route():
    document = config.default_config()
    document["workers"]["claude-code"]["enabled"] = False
    document["workers"]["opencode"]["enabled"] = True
    document["workers"]["codex"]["enabled"] = True
    with pytest.raises(ValueError, match="enable only one external Worker"):
        registry.resolve_backend(SimpleNamespace(assignee="default"), document)


def test_removed_fragment_gc_setting_is_not_returned():
    document = config.default_config()
    document["state_retention_days"] = 5
    assert "state_retention_days" not in config.validate_config(document)
