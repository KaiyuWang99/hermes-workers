"""Backend-neutral worker launch orchestration.

Environment construction lives in :mod:`workers_core.common_env`, MCP
inheritance in :mod:`workers_core.common_mcp`, and prompt context rendering in
:mod:`workers_core.common_context`.  Names are re-exported here so existing
``from ..common import ...`` call sites stay stable.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

from .common_context import _profile_context_prompt, protocol_prompt
from .common_env import build_worker_env, worker_mcp_env, worker_toolsets
from .common_mcp import inherited_mcp_toolsets, worker_mcp_servers
from .contracts import WorkerLaunchContext


__all__ = [
    "WorkerLaunchContext",
    "build_launch_context",
    "build_worker_env",
    "inherited_mcp_toolsets",
    "protocol_prompt",
    "source_root",
    "worker_log",
    "worker_mcp_env",
    "worker_mcp_servers",
    "worker_runner_command",
    "worker_toolsets",
]


def source_root() -> Path:
    """Hermes source root that owns the kanban runtime modules."""
    from hermes_cli import kanban_db

    return Path(kanban_db.__file__).resolve().parent.parent


def worker_runner_command(module: str) -> list[str]:
    """Launch runner modules through Hermes' active dependency generation."""
    from hermes_cli._launchers import runtime_command

    package_root = str(Path(__file__).resolve().parent.parent)
    code = (
        "import sys, runpy; "
        f"sys.path.insert(0, {package_root!r}); "
        f"runpy.run_module({module!r}, run_name='__main__', alter_sys=True)"
    )
    return runtime_command(source_root(), code=code)


def worker_log(task_id: str, board: str | None) -> Path:
    from hermes_cli.kanban_db import worker_log_path
    # The rotation helper lives in its own module since the dispatch module was
    # split (hermes_cli/kanban_worker_log.py); the old private name
    # ``_rotate_worker_log`` no longer exists on kanban_db_dispatch, and importing
    # it there raised ImportError on every worker spawn.
    from hermes_cli.kanban_worker_log import (
        rotate_worker_log,
        worker_log_rotation_config,
    )

    path = worker_log_path(task_id, board=board)
    path.parent.mkdir(parents=True, exist_ok=True)
    rotate_bytes, backup_count = worker_log_rotation_config()
    rotate_worker_log(path, rotate_bytes, backup_count)
    return path


def build_launch_context(
    task: Any,
    workspace: str,
    board: str | None,
    config: dict[str, Any],
    *,
    env: dict[str, str],
) -> WorkerLaunchContext:
    """Build inputs inside the native profile/workspace scope set by the router."""
    if not getattr(task, "assignee", None):
        raise ValueError(f"task {task.id} has no assignee")
    if not workspace or not os.path.isdir(workspace):
        raise RuntimeError(f"Worker workspace is not a directory: {workspace!r}")

    session_id = str(uuid.uuid4())
    from .config import inheritance_policy, plugin_root

    policy = inheritance_policy(config)
    toolsets = worker_toolsets(env["HERMES_HOME"])
    bridge_toolsets = toolsets if policy["hermes_tools"] else ["kanban"]
    if policy["mcp"]:
        servers = worker_mcp_servers(
            env["HERMES_HOME"],
            toolsets,
            workspace=workspace,
        )
        disabled_mcp_toolsets = list(servers)
    else:
        servers = {}
        # Merely omitting direct servers would re-expose them through the
        # Hermes bridge, including profiles enabling composite/all toolsets.
        disabled_mcp_toolsets = inherited_mcp_toolsets(env["HERMES_HOME"])

    mcp_env = worker_mcp_env(env)
    mcp_env.update(
        {
            "PYTHONPATH": os.pathsep.join((str(plugin_root()), str(source_root()))),
            "WORKERS_HERMES_TOOLSETS": json.dumps(bridge_toolsets),
            "WORKERS_DIRECT_MCP_TOOLSETS": json.dumps(disabled_mcp_toolsets),
            "WORKERS_SESSION_ID": session_id,
        }
    )
    # Launch the MCP server through Hermes' active dependency generation like
    # the runners: a bare sys.executable cannot import Hermes' dependencies.
    mcp_command = worker_runner_command("workers_core.capability_mcp")
    servers["hermes-worker"] = {
        "command": mcp_command[0],
        "args": mcp_command[1:],
        "cwd": str(plugin_root()),
        "env": mcp_env,
    }

    prompt = "\n\n".join(
        part
        for part in (
            protocol_prompt(str(task.id)).strip(),
            _profile_context_prompt(
                task_id=str(task.id),
                session_id=session_id,
                hermes_home=env["HERMES_HOME"],
                workspace=workspace,
                toolsets=toolsets,
                skills=getattr(task, "skills", None) or [],
                inheritance=policy,
            ),
        )
        if part
    )
    return WorkerLaunchContext(
        task=task,
        workspace=workspace,
        board=board,
        session_id=session_id,
        env=env,
        mcp_servers=servers,
        prompt=prompt,
    )
