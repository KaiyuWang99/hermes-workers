"""Claude Code CLI backend with a per-task Hermes Kanban MCP server."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from ..common import (
    WorkerLaunchContext,
    source_root as _source_root,
    worker_log,
    worker_runner_command,
)
from ..config import hermes_root, plugin_root, inheritance_policy
from ..credentials import (
    credential_status,
    pool_provider,
    require_credentials,
    select_credential,
)
from ..fallbacks import fallback_entries_for
from ..provider_catalog import (
    configured_model_rows,
    ensure_opencode_session_header,
)
from .claudecode_cli import (  # noqa: F401 — re-exported for callers and tests
    _binary_stamp,
    _cli_flags,
    _probe_cli_flags,
    _resolve_binary,
    _supports,
    capabilities,
)
from .claudecode_command import (
    _mcp_config,
    _system_prompt_file,
    build_command as _build_command,
)
from .claudecode_runtime import (  # noqa: F401 — re-exported for callers and tests
    _apply_provider_env,
    _auth_style,
    _effective_target,
    _provider_runtime as _provider_runtime_impl,
    _route_runtime,
    _split_target,
)
from .claudecode_settings import (  # noqa: F401 — re-exported for callers and tests
    _AUTH_STYLE_VARS,
    _CLAUDE_AUTH_ENV_VARS,
    _CLAUDE_CAPABILITY_ENV_VARS,
    _CLAUDE_SETTINGS_ENV_VARS,
    defaults,
    model_options,
    validate_settings,
)

# The imports above are also re-exports: claude_runner imports the settings tuple
# and env helper from here, and existing callers/monkeypatches target this module.

logger = logging.getLogger(__name__)


def _entry(config: dict[str, Any]) -> dict[str, Any]:
    return dict((config.get("workers") or {}).get("claude-code") or {})


def validate_selection(
    entry: dict[str, Any], *, models: list[dict[str, Any]] | None = None
) -> None:
    """Validate an enabled Claude Code target and its Hermes credentials."""
    del models
    if not bool(entry.get("enabled")):
        return
    if not str(entry.get("binary") or "").strip():
        raise ValueError("Claude Code executable is required.")
    model = str(entry.get("model") or "").strip()
    if not model:
        raise ValueError("Claude Code model is required.")
    provider, _model = _split_target(model)
    status = credential_status(provider, model=_model)
    if not status.get("configured"):
        raise ValueError(
            f"Claude Code provider {provider!r} has no matching Hermes credential."
        )
    needs_caps = (
        bool(entry.get("strict_mcp_config"))
        or entry.get("native_skills", True) is False
        or entry.get("settings_sources") is not None
        or bool(str(entry.get("permission_mode") or "").strip())
        or bool(str(entry.get("reasoning_effort") or "").strip() not in ("", "default"))
    )
    if needs_caps:
        caps = capabilities(str(entry.get("binary") or ""))
        features = caps.get("features") or {}
        choices = caps.get("choices") or {}
        if features:
            if entry.get("strict_mcp_config") and features.get("strict_mcp_config") is False:
                raise ValueError(
                    "This Claude Code CLI cannot disable its native MCP configuration."
                )
            if entry.get("settings_sources") is not None:
                if features.get("settings_sources") is False:
                    raise ValueError(
                        "This Claude Code CLI cannot select native settings sources."
                    )
                allowed = choices.get("setting_sources") or ()
                invalid = [
                    source
                    for source in entry.get("settings_sources") or []
                    if allowed and source not in allowed
                ]
                if invalid:
                    raise ValueError(
                        "This Claude Code CLI does not support setting source(s): "
                        + ", ".join(str(source) for source in invalid)
                    )
            if entry.get("native_skills", True) is False and features.get("native_skills") is False:
                raise ValueError(
                    "This Claude Code CLI cannot disable its native Skills."
                )
            permission = str(entry.get("permission_mode") or "").strip()
            if permission:
                if features.get("permission_mode") is False:
                    raise ValueError(
                        "This Claude Code CLI cannot select a permission mode."
                    )
                allowed = choices.get("permission_mode") or ()
                if allowed and permission not in allowed:
                    raise ValueError(
                        f"This Claude Code CLI does not support permission mode {permission!r}."
                    )
            effort = str(entry.get("reasoning_effort") or "").strip()
            if effort and effort != "default":
                if features.get("effort") is False:
                    raise ValueError(
                        "This Claude Code CLI cannot select a reasoning effort."
                    )
                allowed = choices.get("effort") or ()
                if allowed and effort not in allowed:
                    raise ValueError(
                        f"This Claude Code CLI does not support reasoning effort {effort!r}."
                    )


def _provider_runtime(
    provider: str,
    model: str,
    fallback_entry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _provider_runtime_impl(
        provider,
        model,
        fallback_entry,
        credential_check=require_credentials,
        credential_select=select_credential,
    )


def build_command(
    task: Any,
    workspace: str,
    entry: dict[str, Any],
    env: dict[str, str],
    mcp_path: Path,
    *,
    client_model: str | None = None,
    prompt: str | None = None,
    session_id: str | None = None,
    system_prompt_path: Path | None = None,
) -> list[str]:
    return _build_command(
        task,
        workspace,
        entry,
        env,
        mcp_path,
        client_model=client_model,
        prompt=prompt,
        session_id=session_id,
        system_prompt_path=system_prompt_path,
        resolve_binary=_resolve_binary,
        cli_flags=_cli_flags,
    )


def spawn(
    launch: WorkerLaunchContext,
    *,
    config: dict[str, Any],
) -> int:
    task = launch.task
    workspace = launch.workspace
    board = launch.board
    entry = _entry(config)
    env = dict(launch.env)
    session_id = launch.session_id
    env["WORKERS_CLAUDE_SESSION_ID"] = session_id
    provider, model = _effective_target(task, entry)
    runtime = _route_runtime(_provider_runtime(provider, model))
    ensure_opencode_session_header(runtime, session_id)
    runtime["pin_model_aliases"] = bool(entry.get("pin_model_aliases", True))
    runtime["pin_subagent_model"] = bool(entry.get("pin_subagent_model", True))
    env["WORKERS_CLAUDE_SILENT_TIMEOUT_SECONDS"] = str(entry.get("silent_timeout_seconds", 0))
    state_dir = hermes_root() / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", str(task.id)) or "task"
    run_dir = Path(tempfile.mkdtemp(prefix=f"workers-claude-run-{safe_id}-", dir=state_dir))
    env["WORKERS_CLAUDE_RUN_DIR"] = str(run_dir)
    try:
        system_prompt_path = _system_prompt_file(run_dir, launch.prompt)
        # Same minimal trigger the native worker gets (`chat -q "work kanban task
        # <id>"`); the protocol lives once in the system prompt file.
        prompt = f"work kanban task {task.id}"
        mcp_path = _mcp_config(run_dir, launch.mcp_servers)
        hermes_hook_settings = None
        if bool(entry.get("sync_hermes_hooks")):
            from .. import claude_hooks

            hermes_hook_settings = claude_hooks.build_settings(
                env["HERMES_HOME"],
                state_dir=run_dir,
                task_id=str(task.id),
                selection=(
                    entry.get("hermes_hook_selection")
                    if isinstance(entry.get("hermes_hook_selection"), dict)
                    else None
                ),
            )
        source_root = _source_root()
        env["PYTHONPATH"] = os.pathsep.join((str(plugin_root()), str(source_root)))
        candidates: list[tuple[dict[str, Any], dict[str, Any]]] = [(runtime, entry)]
        inherited_fallbacks = fallback_entries_for(provider, model) if inheritance_policy(config)["fallback_models"] else []
        for fallback_entry in inherited_fallbacks:
            try:
                fallback_provider = pool_provider(fallback_entry["provider"])
                fallback_model = str(fallback_entry["model"])
                fallback_runtime = _route_runtime(
                    _provider_runtime(
                        fallback_provider,
                        fallback_model,
                        fallback_entry=fallback_entry,
                    )
                )
                fallback_runtime["pin_model_aliases"] = bool(entry.get("pin_model_aliases", True))
                fallback_runtime["pin_subagent_model"] = bool(entry.get("pin_subagent_model", True))
                ensure_opencode_session_header(fallback_runtime, session_id)
                candidates.append((fallback_runtime, entry))
            except Exception as exc:
                # Hermes skips a fallback that cannot resolve its endpoint or
                # credential; the primary Worker must still start normally.
                logger.debug(
                    "claudecode: skipped fallback %s/%s: %s",
                    fallback_entry.get("provider"),
                    fallback_entry.get("model"),
                    exc,
                )
                continue

        attempts: list[dict[str, Any]] = []
        for candidate_runtime, candidate_entry in candidates:
            candidate_command = build_command(
                task,
                workspace,
                candidate_entry,
                env,
                mcp_path,
                client_model=candidate_runtime.get("client_model"),
                prompt=prompt,
                session_id=session_id,
                system_prompt_path=system_prompt_path,
            )
            attempts.append({"command": candidate_command, "runtime": candidate_runtime})

        runner_command = worker_runner_command("workers_core.backends.claude_runner")
        log_handle = open(worker_log(str(task.id), board), "ab")
        try:
            process = subprocess.Popen(
                runner_command,
                cwd=workspace,
                stdin=subprocess.PIPE,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                env=env,
                start_new_session=True,
            )
            if process.stdin is None:
                raise RuntimeError("Claude worker runner stdin pipe was not created")
            payload_value: dict[str, Any]
            if len(attempts) == 1:
                # Keep the original payload shape when no usable fallback exists.
                payload_value = {
                    "command": attempts[0]["command"],
                    "runtime": attempts[0]["runtime"],
                }
            else:
                payload_value = {"attempts": attempts}
            if hermes_hook_settings is not None:
                payload_value["hermes_hook_settings"] = hermes_hook_settings
            payload_value["goal"] = {
                "enabled": bool(getattr(task, "goal_mode", False)),
                "task_id": str(task.id),
                "run_id": getattr(task, "current_run_id", None),
                "max_turns": getattr(task, "goal_max_turns", None),
                "session_id": session_id,
            }
            # The runner deletes these task-scoped fragments once every attempt
            # and goal turn is done, so the state directory does not accumulate
            # per-task MCP/system-prompt files.
            payload_value["cleanup_paths"] = [str(mcp_path), str(system_prompt_path)]
            payload_value["cleanup_dir"] = str(run_dir)
            payload = json.dumps(payload_value, ensure_ascii=False).encode("utf-8")
            try:
                process.stdin.write(payload)
                process.stdin.close()
            except (BrokenPipeError, OSError) as exc:
                try:
                    process.terminate()
                except OSError:
                    pass
                raise RuntimeError(f"Claude worker runner rejected launch payload: {exc}") from exc
        finally:
            log_handle.close()
        from ..process_group import supervise

        supervise(process.pid, task_id=str(task.id), board=board, cleanup_dir=run_dir)
        return int(process.pid)
    except BaseException:
        shutil.rmtree(run_dir, ignore_errors=True)
        raise


def doctor(config: dict[str, Any]) -> dict[str, Any]:
    entry = _entry(config)
    binary = _resolve_binary(str(entry.get("binary") or "claude"))
    configured_model = str(entry.get("model") or "")
    try:
        provider, model = _split_target(configured_model)
        credential = credential_status(provider, model=model)
    except RuntimeError as exc:
        provider = ""
        credential = {
            "configured": False,
            "available": False,
            "mode": "missing",
            "error": str(exc),
        }
    result: dict[str, Any] = {
        "backend": "claude-code",
        "enabled": bool(entry.get("enabled")),
        "binary": binary or str(entry.get("binary") or "claude"),
        "binary_ok": bool(binary),
        "model": configured_model,
        "provider": provider,
        "credential": credential,
    }
    if not result["enabled"]:
        result.update({"ok": True, "disabled": True})
        return result
    if not binary:
        result.update({"ok": False, "error": "Claude Code executable is unavailable"})
        return result
    try:
        version = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, timeout=10
        )
        result.update(
            {
                "version": (version.stdout or version.stderr).strip(),
                "version_ok": version.returncode == 0,
                "auth_ok": bool(credential.get("available")),
                "auth_source": "hermes-credential-pool",
                "ok": version.returncode == 0 and bool(credential.get("available")),
                "capabilities": capabilities(binary),
            }
        )
    except (OSError, subprocess.SubprocessError) as exc:
        result.update({"ok": False, "error": str(exc)})
    return result


def list_models(
    config: dict[str, Any], *, refresh: bool = False
) -> list[dict[str, Any]]:
    del config
    return configured_model_rows(force_refresh=refresh)
