"""Worker process environment and profile-scoped toolset selection."""

from __future__ import annotations

import os
from typing import Any


def build_worker_env(
    task: Any,
    workspace: str,
    board: str | None,
    *,
    inherit_credentials: bool = False,
) -> dict[str, str]:
    from hermes_cli.profiles import normalize_profile_name, resolve_profile_env
    from tools.environments.local import served_profile_child_env

    profile = normalize_profile_name(str(getattr(task, "assignee", "") or ""))
    profile_home = resolve_profile_env(profile)
    env = served_profile_child_env(
        target_home=profile_home, inherit_credentials=inherit_credentials
    )
    from gateway.session_context import _VAR_MAP

    for key in _VAR_MAP:
        env.pop(key, None)

    env["HERMES_HOME"] = profile_home
    env["HERMES_PROFILE"] = profile
    env["HERMES_SESSION_SOURCE"] = "kanban"
    env["HERMES_KANBAN_TASK"] = str(task.id)
    env["HERMES_KANBAN_WORKSPACE"] = workspace
    if getattr(task, "tenant", None):
        env["HERMES_TENANT"] = str(task.tenant)
    if getattr(task, "branch_name", None):
        env["HERMES_KANBAN_BRANCH"] = str(task.branch_name)
    if getattr(task, "current_run_id", None) is not None:
        env["HERMES_KANBAN_RUN_ID"] = str(task.current_run_id)
    if getattr(task, "claim_lock", None):
        env["HERMES_KANBAN_CLAIM_LOCK"] = str(task.claim_lock)
    if bool(getattr(task, "goal_mode", False)):
        env["HERMES_KANBAN_GOAL_MODE"] = "1"
        if getattr(task, "goal_max_turns", None) is not None:
            env["HERMES_KANBAN_GOAL_MAX_TURNS"] = str(
                int(task.goal_max_turns)
            )

    from hermes_cli.kanban_db import (
        _normalize_board_slug,
        get_current_board,
        kanban_db_path,
        workspaces_root,
    )

    env["HERMES_KANBAN_DB"] = str(kanban_db_path(board=board))
    env["HERMES_KANBAN_WORKSPACES_ROOT"] = str(workspaces_root(board=board))
    env["HERMES_KANBAN_BOARD"] = _normalize_board_slug(board) or get_current_board()
    from hermes_cli.kanban_db_dispatch import _worker_terminal_timeout_env

    for key in ("TERMINAL_TIMEOUT", "TERMINAL_MAX_FOREGROUND_TIMEOUT"):
        timeout = _worker_terminal_timeout_env(
            getattr(task, "max_runtime_seconds", None), env.get(key)
        )
        if timeout is not None:
            env[key] = timeout
    if os.path.isabs(workspace) and os.path.isdir(workspace):
        env["TERMINAL_CWD"] = workspace
    env.pop("HERMES_TUI", None)
    return env


def worker_toolsets(hermes_home: str) -> list[str]:
    """Resolve the same profile-scoped CLI toolsets as a native Worker."""
    from hermes_cli.kanban_db_dispatch import _resolve_worker_cli_toolsets

    return list(_resolve_worker_cli_toolsets(hermes_home) or [])


def worker_mcp_env(env: dict[str, str]) -> dict[str, str]:
    """Keep the environment Hermes MCP tools need to identify this Worker."""
    shared = {
        "HERMES_HOME",
        "HERMES_PROFILE",
        "HERMES_SESSION_SOURCE",
        "HERMES_TENANT",
        "TERMINAL_CWD",
        "TERMINAL_TIMEOUT",
        "TERMINAL_MAX_FOREGROUND_TIMEOUT",
    }
    return {
        key: value
        for key, value in env.items()
        if key.startswith("HERMES_KANBAN_") or key in shared
    }
