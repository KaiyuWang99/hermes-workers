"""Expose a Worker's current Hermes tool surface to Claude Code over MCP."""

from __future__ import annotations

import json
import os
import sys
from typing import Any


def _toolsets(key: str = "WORKERS_HERMES_TOOLSETS") -> list[str]:
    try:
        value = json.loads(os.environ.get(key, "[]"))
    except json.JSONDecodeError:
        value = []
    return [str(item) for item in value if str(item).strip()]


def create_server():
    from hermes_cli.plugins import discover_plugins

    discover_plugins()
    from mcp.server import MCPServer
    from model_tools import _AGENT_LOOP_TOOLS, get_tool_definitions, handle_function_call

    # Hermes' own schema->signature mapper (shared with the codex-facing
    # hermes-tools MCP server) so both bridges track core's typing rules.
    from agent.transports.hermes_tools_mcp_server import _signature_from_schema

    enabled_toolsets = _toolsets()
    disabled_toolsets = _toolsets("WORKERS_DIRECT_MCP_TOOLSETS")
    definitions = get_tool_definitions(
        enabled_toolsets=enabled_toolsets or None,
        # Core subtracts direct external MCP servers and any MCP inheritance
        # exclusions after enabling the profile's composite toolsets.
        disabled_toolsets=disabled_toolsets,
        quiet_mode=True,
        skip_tool_search_assembly=True,
    ) or []
    functions = {
        item["function"]["name"]: item["function"]
        for item in definitions
        if isinstance(item, dict)
        and item.get("type") == "function"
        and isinstance(item.get("function"), dict)
        and item["function"].get("name")
    }
    # Session search has a native standalone DB path; the other loop tools
    # still require a live Hermes AIAgent and remain owned by Claude here.
    unsupported = set(_AGENT_LOOP_TOOLS) - {"session_search"}
    exposed_tools = [name for name in functions if name not in unsupported]
    server = MCPServer(
        "hermes-worker",
        instructions="Hermes tools enabled for this Kanban Worker profile.",
    )
    task_id = os.environ.get("HERMES_KANBAN_TASK") or None
    session_id = os.environ.get("WORKERS_SESSION_ID") or None

    for name, spec in functions.items():
        if name in unsupported:
            continue
        schema = spec.get("parameters") or {"type": "object", "properties": {}}
        signature, annotations = _signature_from_schema(schema)

        def make_handler(tool_name: str):
            def dispatch(**kwargs: Any) -> str:
                args = {key: value for key, value in kwargs.items() if value is not None}
                if tool_name == "session_search":
                    from tools.registry import registry

                    # Reuse the registered native handler, including defaults,
                    # profile DB resolution and connection cleanup. The generic
                    # model dispatcher deliberately rejects agent-loop names.
                    return registry.dispatch(
                        tool_name, args, current_session_id=session_id
                    )
                return handle_function_call(
                    tool_name,
                    args,
                    task_id=task_id,
                    session_id=session_id,
                    enabled_tools=exposed_tools,
                    enabled_toolsets=enabled_toolsets or None,
                    disabled_toolsets=disabled_toolsets,
                )

            dispatch.__name__ = tool_name
            dispatch.__doc__ = spec.get("description") or f"Hermes {tool_name} tool"
            dispatch.__signature__ = signature
            dispatch.__annotations__ = {**annotations, "return": str}
            return dispatch

        handler = make_handler(name)
        try:
            server.add_tool(handler, name=name, description=handler.__doc__)
        except TypeError:
            server.tool(name=name, description=handler.__doc__)(handler)
    return server


def main() -> int:
    import asyncio

    async def run() -> None:
        await create_server().run_stdio_async()

    try:
        asyncio.run(run())
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print(f"Hermes Worker capability MCP failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
