"""One Hermes transport description survives each CLI's serialization boundary."""

from copy import deepcopy
import json
import stat
import tomllib

from workers_core.contracts import McpServerSpec, WorkerLaunchContext
from workers_core.common import WorkerLaunchContext as LegacyLaunchContext
from workers_core.backends import claudecode, codex, opencode


def test_launch_context_keeps_legacy_import_compatible():
    assert LegacyLaunchContext is WorkerLaunchContext


def test_resolved_transports_keep_child_credentials_and_names_across_backends(tmp_path):
    servers: dict[str, McpServerSpec] = {
        "local.with.dots": {
            "command": "/test/tool",
            "args": ["--transport", "stdio"],
            "env": {"TOKEN": "child-only-token"},
            "cwd": str(tmp_path / "workspace with spaces"),
        },
        "remote.with.dots": {
            "type": "http",
            "url": "https://mcp.example.test/rpc",
            "headers": {"Authorization": "Bearer remote-only-token"},
        },
    }
    original = deepcopy(servers)

    claude_path = claudecode._mcp_config(tmp_path, servers)
    assert json.loads(claude_path.read_text()) == {"mcpServers": original}
    assert stat.S_IMODE(claude_path.stat().st_mode) == 0o600

    opencode_local = opencode._mcp_entry(servers["local.with.dots"])
    assert opencode_local == {
        "type": "local", "enabled": True,
        "command": ["/test/tool", "--transport", "stdio"],
        "environment": {"TOKEN": "child-only-token"},
        "cwd": str(tmp_path / "workspace with spaces"),
    }
    assert opencode._mcp_entry(servers["remote.with.dots"]) == {
        "type": "remote", "enabled": True,
        "url": "https://mcp.example.test/rpc",
        "headers": {"Authorization": "Bearer remote-only-token"},
    }

    parent_env = {"TOKEN": "parent-token"}
    argv = codex._mcp_args(servers, parent_env)
    assert argv[0] == "-c"
    tables = tomllib.loads(argv[1])["mcp_servers"]
    assert set(tables) == set(servers)
    local = tables["local.with.dots"]
    envelope = local["env_vars"][0]
    assert json.loads(parent_env[envelope]) == {
        "command": "/test/tool", "args": ["--transport", "stdio"],
        "env": {"TOKEN": "child-only-token"},
    }
    assert local["cwd"] == str(tmp_path / "workspace with spaces")
    remote = tables["remote.with.dots"]
    assert remote["url"] == servers["remote.with.dots"]["url"]
    assert parent_env[remote["env_http_headers"]["Authorization"]] == "Bearer remote-only-token"
    assert parent_env["TOKEN"] == "parent-token"
    assert "child-only-token" not in " ".join(argv)
    assert "remote-only-token" not in " ".join(argv)
    assert servers == original
