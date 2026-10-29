"""Native profile MCP inheritance and Claude's additive MCP configuration."""

from contextlib import nullcontext
from types import SimpleNamespace
import json
import sys

import pytest

from workers_core import capability_mcp, common, config
from workers_core.backends import claudecode


def _select(monkeypatch, servers, toolsets):
    import hermes_cli.kanban_db_dispatch as dispatch
    import tools.mcp_tool_config as native_config

    monkeypatch.setattr(dispatch, "_worker_profile_scope", lambda _home: nullcontext())
    monkeypatch.setattr(native_config, "_load_mcp_config", lambda: servers)
    return common.worker_mcp_servers("/tmp/profile", toolsets)


def test_enabled_servers_need_no_card_declaration(monkeypatch):
    servers = {
        "first": {"command": sys.executable, "args": ["-V"]},
        "second": {"command": sys.executable},
        "off": {"command": sys.executable, "enabled": "false"},
        "not_in_toolsets": {"command": sys.executable},
    }
    selected = _select(monkeypatch, servers, ["first", "second", "off"])
    assert list(selected) == ["first", "second"]
    assert selected["first"]["args"] == ["-V"]


def test_http_and_sse_keep_native_connection_inputs(monkeypatch):
    import tools.mcp_tool_transport as transport

    calls = []

    def connect(name, raw):
        calls.append(name)
        return ["https://resolved.invalid/mcp", {"X-Profile": "resolved"}], set()

    monkeypatch.setattr(transport, "_connect_inputs", connect)
    selected = _select(monkeypatch, {
        "http": {"url": "https://configured.invalid"},
        "sse": {"url": "https://configured.invalid", "transport": "sse"},
    }, ["http", "sse"])
    assert calls == ["http", "sse"]
    assert selected == {
        name: {"type": kind, "url": "https://resolved.invalid/mcp",
               "headers": {"X-Profile": "resolved"}}
        for name, kind in [("http", "http"), ("sse", "sse")]
    }


def test_profile_loader_inherits_portable_servers_and_restores_scope(tmp_path, monkeypatch):
    """Real Hermes config, secret, toolset and connection resolution, A -> B -> A."""
    import hermes_cli.plugins as plugins
    from hermes_constants import get_hermes_home

    monkeypatch.setattr(plugins, "discover_plugins", lambda *a, **k: None)
    monkeypatch.setattr(plugins, "get_plugin_manager", lambda: SimpleNamespace(
        get_portable_mcp_servers=lambda: {"portable": {"command": sys.executable}},
    ))
    monkeypatch.setattr(plugins, "get_portable_mcp_server_names_nowait", lambda: {"portable"})
    original_home = get_hermes_home()
    homes = []
    for label in ("a", "b"):
        home = tmp_path / label
        home.mkdir()
        (home / "config.yaml").write_text(
            "platform_toolsets:\n  cli: [skills, selected, portable]\n"
            "mcp_servers:\n"
            f"  selected:\n    url: https://{label}.invalid/mcp\n"
            "    headers:\n      X-Profile: '${WORKERS_TEST_PROFILE_VALUE}'\n"
            f"  off:\n    command: {sys.executable}\n    enabled: false\n"
        )
        (home / ".env").write_text(f"WORKERS_TEST_PROFILE_VALUE={label}\n")
        homes.append(home)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for home, label in [(homes[0], "a"), (homes[1], "b"), (homes[0], "a")]:
        selected = common.worker_mcp_servers(
            str(home), common.worker_toolsets(str(home)), workspace=str(workspace)
        )
        assert set(selected) == {"selected", "portable"}
        assert selected["selected"]["url"] == f"https://{label}.invalid/mcp"
        assert selected["selected"]["headers"]["X-Profile"] == label
        assert selected["portable"]["cwd"] == str(workspace)
        assert get_hermes_home() == original_home


def test_connection_error_restores_profile_and_workspace(tmp_path, monkeypatch):
    import tools.mcp_tool_config as native_config
    import tools.mcp_tool_transport as transport
    from agent import runtime_cwd
    from hermes_constants import get_hermes_home

    home = tmp_path / "profile"
    home.mkdir()
    before_home = get_hermes_home()
    before_cwd = runtime_cwd.resolve_context_cwd()
    monkeypatch.setattr(native_config, "_load_mcp_config", lambda: {"broken": {"url": "x"}})

    def fail(*args):
        raise RuntimeError("native connection error")

    monkeypatch.setattr(transport, "_connect_inputs", fail)
    with pytest.raises(RuntimeError, match="native connection error"):
        common.worker_mcp_servers(str(home), ["broken"], workspace=str(tmp_path))
    assert get_hermes_home() == before_home
    assert runtime_cwd.resolve_context_cwd() == before_cwd


def test_bridge_subtracts_direct_mcp_toolsets_through_native_filter(monkeypatch):
    import mcp.server
    import model_tools
    import hermes_cli.plugins as plugins

    calls = []
    added = []

    class Server:
        def __init__(self, *a, **k):
            pass

        def add_tool(self, handler, **kwargs):
            added.append(kwargs["name"])

    def definitions(**kwargs):
        calls.append(kwargs)
        return [{"type": "function", "function": {
            "name": "kanban_show", "parameters": {"type": "object"},
        }}]

    monkeypatch.setenv("WORKERS_HERMES_TOOLSETS", '["skills", "external"]')
    monkeypatch.setenv("WORKERS_DIRECT_MCP_TOOLSETS", '["external"]')
    monkeypatch.setattr(plugins, "discover_plugins", lambda: None)
    monkeypatch.setattr(mcp.server, "MCPServer", Server, raising=False)
    monkeypatch.setattr(model_tools, "get_tool_definitions", definitions)
    capability_mcp.create_server()
    assert calls[0]["disabled_toolsets"] == ["external"]
    assert calls[0]["enabled_toolsets"] == ["skills", "external"]
    assert added == ["kanban_show"]


@pytest.mark.parametrize("inherit_tools", [True, False])
def test_launch_marks_direct_servers_and_ignores_old_card_directive(tmp_path, monkeypatch, inherit_tools):
    task = SimpleNamespace(id="test", assignee="default", skills=[],
                           body="worker-mcp: no-longer-an-activation-rule")
    monkeypatch.setattr(common, "worker_toolsets", lambda home: ["skills", "external"])
    monkeypatch.setattr(common, "worker_mcp_servers", lambda *a, **k: {
        "external": {"command": sys.executable},
    })
    monkeypatch.setattr(common, "worker_runner_command", lambda module: [sys.executable, "-m", module])
    monkeypatch.setattr(common, "_profile_context_prompt", lambda **k: "profile context")
    document = config.default_config()
    document["inheritance"]["hermes_tools"] = inherit_tools
    launch = common.build_launch_context(
        task, str(tmp_path), None, document, env={"HERMES_HOME": str(tmp_path)}
    )
    assert set(launch.mcp_servers) == {"external", "hermes-worker"}
    bridge_env = launch.mcp_servers["hermes-worker"]["env"]
    assert json.loads(bridge_env["WORKERS_DIRECT_MCP_TOOLSETS"]) == ["external"]
    assert json.loads(bridge_env["WORKERS_HERMES_TOOLSETS"]) == (["skills", "external"] if inherit_tools else ["kanban"])


@pytest.mark.parametrize("all_off", [False, True])
def test_mcp_off_omits_direct_servers_but_keeps_required_bridge(tmp_path, monkeypatch, all_off):
    import tools.mcp_tool_config as native_config

    task = SimpleNamespace(id="test-card", assignee="default", skills=["missing-skill"])
    home = tmp_path / "profile"
    home.mkdir()
    toolsets = ["skills", "browser", "external", "hermes-cli"]
    monkeypatch.setattr(common, "worker_toolsets", lambda _home: toolsets)
    monkeypatch.setattr(native_config, "_load_mcp_config", lambda: {
        "external": {"command": "not-started"},
        "browser": {"url": "https://not-connected.invalid"},
    })

    def should_not_connect(*args, **kwargs):
        raise AssertionError("MCP disabled: direct connection inputs must not be resolved")

    monkeypatch.setattr(common, "worker_mcp_servers", should_not_connect)
    monkeypatch.setattr(common, "worker_runner_command", lambda module: [sys.executable, "-m", module])
    policy = {key: False for key in config.INHERITANCE_DEFAULTS} if all_off else {"mcp": False}
    # MCP-only off must leave the other independent categories enabled.
    seen = {}

    def profile_context(**kwargs):
        seen.update(kwargs)
        return "" if all_off else "other inherited context"

    monkeypatch.setattr(common, "_profile_context_prompt", profile_context)
    launch = common.build_launch_context(
        task, str(tmp_path), None, {"inheritance": policy},
        env={"HERMES_HOME": str(home), "HERMES_KANBAN_TASK": task.id},
    )
    assert set(launch.mcp_servers) == {"hermes-worker"}
    bridge_env = launch.mcp_servers["hermes-worker"]["env"]
    assert json.loads(bridge_env["WORKERS_HERMES_TOOLSETS"]) == (["kanban"] if all_off else toolsets)
    assert json.loads(bridge_env["WORKERS_DIRECT_MCP_TOOLSETS"]) == ["mcp-external", "mcp-browser"]
    assert bridge_env["HERMES_KANBAN_TASK"] == task.id
    assert "Hermes Kanban Worker for Card test-card" in launch.prompt
    assert common.protocol_prompt(task.id).strip() in launch.prompt
    assert seen["inheritance"] == {**config.INHERITANCE_DEFAULTS, **policy}
    assert ("other inherited context" in launch.prompt) is not all_off


def test_mcp_off_native_subtraction_preserves_colliding_builtin_toolsets(tmp_path, monkeypatch):
    """Canonical MCP exclusions work with aliases and composite native toolsets."""
    import model_tools
    import toolsets
    from tools.registry import ToolRegistry
    import tools.mcp_tool_config as native_config

    registry = ToolRegistry()
    registry.register("mcp_browser_test", "mcp-browser", {}, lambda: "{}")
    registry.register_toolset_alias("browser", "mcp-browser")
    registry.register("mcp_external_test", "mcp-external", {}, lambda: "{}")
    registry.register_toolset_alias("external", "mcp-external")
    monkeypatch.setattr(toolsets, "_registry", lambda: registry)
    monkeypatch.setitem(toolsets.TOOLSETS, "test-composite", {
        "tools": [], "includes": ["browser", "external", "skills"],
    })
    monkeypatch.setattr(native_config, "_load_mcp_config", lambda: {"browser": {}, "external": {}})
    disabled = common.inherited_mcp_toolsets(str(tmp_path))
    enabled = model_tools._select_tool_names(["test-composite"], [], True)
    selected = model_tools._select_tool_names(["test-composite"], disabled, True)
    assert {"mcp_browser_test", "mcp_external_test"} <= enabled
    assert not {"mcp_browser_test", "mcp_external_test"} & selected
    assert set(toolsets.TOOLSETS["browser"]["tools"]) <= selected
    assert set(toolsets.TOOLSETS["skills"]["tools"]) <= selected


def test_bridge_dispatch_keeps_mcp_exclusions_for_dynamic_catalog_paths(monkeypatch):
    import mcp.server
    import model_tools
    import hermes_cli.plugins as plugins

    handlers, calls = {}, []

    class Server:
        def __init__(self, *a, **k):
            pass

        def add_tool(self, handler, **kwargs):
            handlers[kwargs["name"]] = handler

    monkeypatch.setenv("WORKERS_HERMES_TOOLSETS", '["hermes-cli"]')
    monkeypatch.setenv("WORKERS_DIRECT_MCP_TOOLSETS", '["mcp-external"]')
    monkeypatch.setattr(plugins, "discover_plugins", lambda: None)
    monkeypatch.setattr(mcp.server, "MCPServer", Server, raising=False)
    monkeypatch.setattr(model_tools, "get_tool_definitions", lambda **kwargs: [{
        "type": "function", "function": {"name": "tool_search", "parameters": {"type": "object"}},
    }])
    monkeypatch.setattr(model_tools, "handle_function_call", lambda *args, **kwargs: calls.append(kwargs) or "{}")
    capability_mcp.create_server()
    handlers["tool_search"]()
    assert calls[0]["enabled_toolsets"] == ["hermes-cli"]
    assert calls[0]["disabled_toolsets"] == ["mcp-external"]


def test_strict_mcp_is_opt_in_and_old_policy_is_removed():
    document = config.default_config()
    document["mcp_policy"] = {"always": ["obsolete"], "on_demand": []}
    entry = document["workers"]["claude-code"]
    entry.pop("strict_mcp_config")
    normalized = config.validate_config(document)
    assert "mcp_policy" not in normalized
    assert normalized["workers"]["claude-code"]["strict_mcp_config"] is False
    entry["strict_mcp_config"] = True
    assert config.validate_config(document)["workers"]["claude-code"]["strict_mcp_config"] is True


@pytest.mark.parametrize("strict", [None, False, True])
def test_claude_command_keeps_native_mcp_unless_explicitly_strict(tmp_path, monkeypatch, strict):
    monkeypatch.setattr(claudecode, "_resolve_binary", lambda value: "claude")
    monkeypatch.setattr(claudecode, "_cli_flags", lambda binary: frozenset({"--strict-mcp-config"}))
    entry = {"binary": "claude", "model": "anthropic/sonnet"}
    if strict is not None:
        entry["strict_mcp_config"] = strict
    task = SimpleNamespace(model_override=None, provider_override=None, reasoning_effort=None)
    command = claudecode.build_command(task, str(tmp_path), entry, {}, tmp_path / "mcp.json")
    assert "--mcp-config" in command
    assert ("--strict-mcp-config" in command) is (strict is True)
