"""Backend-neutral launch inputs resolved by Hermes.

MCP entries describe resolved transports, not a CLI configuration file. Each
backend owns its JSON/TOML serialization and authentication mechanism.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, NotRequired, TypedDict


class StdioMcpServer(TypedDict):
    command: str
    args: NotRequired[list[str]]
    env: NotRequired[dict[str, str]]
    cwd: NotRequired[str]


class RemoteMcpServer(TypedDict):
    type: Literal["http", "sse"]
    url: str
    headers: NotRequired[dict[str, str]]


McpServerSpec = StdioMcpServer | RemoteMcpServer


@dataclass(frozen=True)
class WorkerLaunchContext:
    task: Any
    workspace: str
    board: str | None
    session_id: str
    env: dict[str, str]
    mcp_servers: dict[str, McpServerSpec]
    prompt: str
