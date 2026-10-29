"""MCP inheritance: translate profile MCP selection into worker connections."""

from __future__ import annotations

from typing import Any, Iterable


def worker_mcp_servers(
    hermes_home: str,
    toolsets: Iterable[str],
    *,
    workspace: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Translate the profile's native MCP selection into Claude connection entries."""
    from agent import runtime_cwd
    from hermes_cli.kanban_db_dispatch import _worker_profile_scope
    from tools.mcp_tool_common import mcp_server_enabled
    from tools.mcp_tool_config import _load_mcp_config
    from tools.mcp_tool_transport import _connect_inputs
    from toolsets import get_toolset, resolve_toolset

    enabled_names = set(toolsets)
    selected: dict[str, dict[str, Any]] = {}
    with _worker_profile_scope(hermes_home):
        selected_tools = set().union(*(set(resolve_toolset(value)) for value in enabled_names))
        # Native composites may name a configured MCP before its tools have
        # connected and registered. Follow the native definitions in that case.
        pending = list(enabled_names)
        expanded_names: set[str] = set()
        while pending:
            value = pending.pop()
            if value not in expanded_names:
                expanded_names.add(value)
                pending.extend((get_toolset(value) or {}).get("includes", []))
        cwd_token = runtime_cwd.set_session_cwd(workspace) if workspace else None
        try:
            for name, raw in _load_mcp_config().items():
                canonical = f"mcp-{name}"
                included = bool(selected_tools.intersection(resolve_toolset(canonical)))
                if not mcp_server_enabled(raw) or not (
                    name in expanded_names or canonical in expanded_names
                    or enabled_names.intersection({"all", "*"}) or included
                ):
                    continue
                inputs, _header_names = _connect_inputs(name, raw)
                if "url" in raw:
                    url, headers = inputs
                    entry = {
                        "type": "sse" if raw.get("transport") == "sse" else "http",
                        "url": url,
                    }
                    if headers:
                        entry["headers"] = headers
                else:
                    command, env, cwd = inputs
                    entry = {"command": command, "args": list(raw.get("args") or [])}
                    if env:
                        entry["env"] = env
                    if cwd:
                        entry["cwd"] = str(cwd)
                selected[name] = entry
        finally:
            if cwd_token is not None:
                runtime_cwd.reset_session_cwd(cwd_token)
    return selected


def inherited_mcp_toolsets(hermes_home: str) -> list[str]:
    """Identify external MCP toolsets without opening their connections.

    Use native config/profile resolution and canonical toolset names.  A server
    alias can collide with built-ins such as ``memory`` or ``browser``; disabling
    its canonical ``mcp-*`` toolset preserves those built-in Hermes tools.
    """
    from hermes_cli.kanban_db_dispatch import _worker_profile_scope
    from tools.mcp_tool_config import _load_mcp_config

    with _worker_profile_scope(hermes_home):
        return [f"mcp-{name}" for name in _load_mcp_config()]
