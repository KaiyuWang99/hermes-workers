"""Direct Codex CLI backend with a required Hermes Kanban MCP callback."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..common import WorkerLaunchContext, worker_log, worker_runner_command


def _codex_binary() -> str:
    """Prefer Hermes' configured CLI, then PATH and installed desktop bundles."""
    from hermes_cli.config import load_config_readonly
    from hermes_cli.codex_runtime_switch import get_configured_codex_binary

    from ..config import _which

    configured = get_configured_codex_binary(load_config_readonly() or {})
    if configured != "codex":
        return _which(configured, configured)
    found = shutil.which("codex")
    if found:
        return found
    for applications in (Path.home() / "Applications", Path("/Applications")):
        for resource in sorted(applications.glob("*.app/Contents/Resources/codex*")):
            candidates = (
                [resource]
                if resource.is_file()
                else sorted(resource.glob("*.app/Contents/MacOS/codex"))
            )
            for candidate in candidates:
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    return str(candidate.resolve())
    return "codex"


def defaults() -> dict[str, Any]:
    return {
        "enabled": False,
        "binary": _codex_binary(),
        "codex_home": "",
        "model": "",
        "reasoning_effort": "",
        "service_tier": "",
        "sandbox": "workspace-write",
        "network_access": False,
        "ephemeral": True,
        "ignore_user_config": True,
    }


def validate_settings(entry: dict[str, Any]) -> dict[str, Any]:
    """Normalize Codex settings while preserving unrecognized user fields."""
    if not isinstance(entry, Mapping):
        raise ValueError("workers.codex must be an object")
    result = dict(entry)
    for key in ("ignore_user_config",):
        if key in result and not isinstance(result[key], bool):
            raise ValueError(f"workers.codex.{key} must be a boolean")
    if not str(result.get("binary") or "").strip():
        raise ValueError("workers.codex.binary is required")
    if bool(result.get("enabled")) and not str(result.get("model") or "").strip():
        raise ValueError("workers.codex.model is required when enabled")
    return result


def validate_selection(
    entry: dict[str, Any], *, models: list[dict[str, Any]] | None = None
) -> None:
    """Validate the selected Codex model and its advertised effort values."""
    if not bool(entry.get("enabled")):
        return
    catalog = models or []
    selected = str(entry.get("model") or "")
    row = next((item for item in catalog if item.get("id") == selected), None)
    if catalog and row is None:
        raise ValueError(f"Codex model {selected!r} is unavailable.")
    effort = str(entry.get("reasoning_effort") or "")
    if row and effort:
        supported = {
            str(item.get("reasoningEffort"))
            for item in (row.get("supportedReasoningEfforts") or [])
            if isinstance(item, dict)
        }
        if supported and effort not in supported:
            raise ValueError(
                f"Codex model {selected!r} does not support reasoning {effort!r}."
            )
    service_tier = str(entry.get("service_tier") or "")
    if row and service_tier:
        tiers = row.get("serviceTiers")
        if isinstance(tiers, list):
            supported_tiers = {
                str(item.get("id"))
                for item in tiers
                if isinstance(item, dict) and item.get("id") is not None
            }
            if service_tier not in supported_tiers:
                raise ValueError(
                    f"Codex model {selected!r} does not support service tier {service_tier!r}."
                )


def model_options(
    entry: dict[str, Any],
    *,
    model: str,
    refresh: bool = False,
    scope: str = "",
    models_loader: Any = None,
) -> dict[str, Any]:
    from ..options import _codex_options

    return _codex_options(
        entry,
        model=model,
        refresh=refresh,
        scope=scope,
        models_loader=models_loader,
    )


def _toml_string(value: str) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _toml_array(values: Iterable[str]) -> str:
    return "[" + ",".join(_toml_string(item) for item in values) + "]"


def _toml_table(values: dict[str, str]) -> str:
    return "{" + ",".join(
        f"{_toml_string(key)}={_toml_string(value)}"
        for key, value in values.items()
    ) + "}"


def _mcp_args(
    servers: dict[str, dict[str, Any]], env: dict[str, str]
) -> list[str]:
    tables: list[str] = []
    for server_index, (name, server) in enumerate(servers.items()):
        pairs: dict[str, str] = {}
        if server.get("command"):
            server_env = server.get("env") or {}
            command = server["command"]
            args = list(server.get("args") or [])
            if server_env:
                # Codex env_vars forwards parent variables by name. A private
                # envelope lets each stdio child restore its own values without
                # putting credentials in argv or overwriting the Codex process.
                envelope = "WORKERS_MCP_STDIO_" + uuid.uuid4().hex
                env[envelope] = json.dumps({"command": command, "args": args, "env": server_env})
                command = sys.executable
                args = [str(Path(__file__).resolve().parents[1] / "mcp_stdio_env.py"), envelope]
                pairs["env_vars"] = _toml_array([envelope])
            pairs["command"] = _toml_string(command)
            pairs["args"] = _toml_array(args)
            if server.get("cwd"):
                pairs["cwd"] = _toml_string(server["cwd"])
        elif server.get("url"):
            pairs["url"] = _toml_string(server["url"])
            header_env = {}
            for header_index, (header, value) in enumerate(
                (server.get("headers") or {}).items()
            ):
                env_name = f"WORKERS_MCP_HEADER_{server_index}_{header_index}"
                env[env_name] = str(value)
                header_env[str(header)] = env_name
            if header_env:
                pairs["env_http_headers"] = _toml_table(header_env)
        pairs["enabled"] = "true"
        # Codex treats MCP tools as approval-gated unless this server says
        # otherwise; an unattended worker cannot answer a prompt, and
        # approval_policy=never rejects the call instead of granting it.
        pairs["default_tools_approval_mode"] = _toml_string("approve")
        fields = ",".join(f"{_toml_string(key)}={value}" for key, value in pairs.items())
        tables.append(f"{_toml_string(name)}={{" + fields + "}")

    # Codex splits override paths on dots literally. Encode the names as
    # TOML table keys in the value instead, preserving dotted names and native
    # MCP entries through Codex's recursive config merge.
    return ["-c", "mcp_servers={" + ",".join(tables) + "}"] if tables else []


def _is_git_repo(workspace: str) -> bool:
    try:
        run = subprocess.run(
            ["git", "-C", workspace, "rev-parse", "--is-inside-work-tree"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
        return run.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _entry(config: dict[str, Any]) -> dict[str, Any]:
    return dict((config.get("workers") or {}).get("codex") or {})


def _effective_model(task: Any, entry: dict[str, Any]) -> str:
    task_model = str(getattr(task, "model_override", "") or "").strip()
    provider = str(getattr(task, "provider_override", "") or "").strip().lower()
    if task_model and provider and provider not in {"openai", "openai-codex"}:
        raise RuntimeError(
            f"Card requests provider {provider!r}, which is incompatible with Codex"
        )
    return task_model or str(entry.get("model") or "").strip()


def build_command(
    task: Any,
    workspace: str,
    entry: dict[str, Any],
    env: dict[str, str],
    mcp_servers: dict[str, dict[str, Any]] | None = None,
) -> list[str]:
    binary = Path(str(entry.get("binary") or "")).expanduser()
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise RuntimeError(f"Codex executable is unavailable: {binary}")
    sandbox = str(entry.get("sandbox") or "workspace-write")
    if sandbox not in {"read-only", "workspace-write"}:
        raise RuntimeError(f"unsupported Codex sandbox: {sandbox!r}")
    command = [
        str(binary),
        "--ask-for-approval",
        "never",
        "--sandbox",
        sandbox,
        "--cd",
        workspace,
        "--add-dir",
        str(Path(env["HERMES_KANBAN_DB"]).resolve().parent),
        "exec",
        "--json",
        "--strict-config",
        "--color",
        "never",
    ]
    if bool(entry.get("ignore_user_config", True)):
        command.append("--ignore-user-config")
    if bool(entry.get("ephemeral", True)):
        command.append("--ephemeral")
    if not _is_git_repo(workspace):
        command.append("--skip-git-repo-check")
    model = _effective_model(task, entry)
    if not model:
        raise RuntimeError("Codex model is not configured")
    command.extend(["--model", model])
    reasoning = str(
        getattr(task, "reasoning_effort", "") or entry.get("reasoning_effort") or ""
    ).strip()
    if reasoning:
        command.extend(["-c", f"model_reasoning_effort={_toml_string(reasoning)}"])
    service_tier = str(entry.get("service_tier") or "").strip()
    if service_tier:
        command.extend(["-c", f"service_tier={_toml_string(service_tier)}"])
    command.extend(
        [
            "-c",
            "sandbox_workspace_write.network_access="
            + ("true" if bool(entry.get("network_access")) else "false"),
        ]
    )
    command.extend(_mcp_args(mcp_servers or {}, env))
    command.append("-")
    return command


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
    codex_home = str(entry.get("codex_home") or "").strip()
    if codex_home:
        env["CODEX_HOME"] = str(Path(codex_home).expanduser().resolve())
    command = build_command(task, workspace, entry, env, launch.mcp_servers)
    log_path = worker_log(str(task.id), board)
    payload = {
        "command": command,
        "env": env,
        "workspace": workspace,
        "model": _effective_model(task, entry),
        "status_path": str(log_path.with_suffix(".status.json")),
        "prompt": launch.prompt,
    }
    runner = worker_runner_command("workers_core.codex_runner")
    log_handle = open(log_path, "ab")
    try:
        process = subprocess.Popen(
            runner,
            cwd=workspace,
            stdin=subprocess.PIPE,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
        if process.stdin is None:
            raise RuntimeError("Codex runner stdin pipe was not created")
        try:
            process.stdin.write(
                json.dumps(payload, ensure_ascii=False).encode("utf-8")
            )
            process.stdin.close()
        except (BrokenPipeError, OSError) as exc:
            try:
                process.terminate()
            except OSError:
                pass
            raise RuntimeError(f"Codex runner rejected its launch payload: {exc}") from exc
    finally:
        log_handle.close()
    return int(process.pid)


def doctor(config: dict[str, Any]) -> dict[str, Any]:
    entry = _entry(config)
    binary = Path(str(entry.get("binary") or "")).expanduser()
    result: dict[str, Any] = {
        "backend": "codex",
        "binary": str(binary),
        "binary_ok": binary.is_file() and os.access(binary, os.X_OK),
        "model": str(entry.get("model") or ""),
    }
    if not result["binary_ok"]:
        result["ok"] = False
        result["error"] = "Codex executable is unavailable"
        return result
    try:
        version = subprocess.run(
            [str(binary), "--version"], capture_output=True, text=True, timeout=10
        )
        result.update({
            "version": (version.stdout or version.stderr).strip(),
            "version_ok": version.returncode == 0,
        })
        login_env = dict(os.environ)
        if entry.get("codex_home"):
            login_env["CODEX_HOME"] = str(Path(entry["codex_home"]).expanduser())
        login = subprocess.run(
            [str(binary), "login", "status"],
            capture_output=True,
            text=True,
            timeout=10,
            env=login_env,
        )
        result.update(
            {
                "login": (login.stdout or login.stderr).strip(),
                "login_ok": login.returncode == 0,
                "ok": version.returncode == 0 and login.returncode == 0,
            }
        )
    except (OSError, subprocess.SubprocessError) as exc:
        if "version_ok" in result:
            result["login_ok"] = False
        result.update({"ok": False, "error": str(exc)})
    return result


def list_models(
    config: dict[str, Any], *, refresh: bool = False
) -> list[dict[str, Any]]:
    del refresh
    entry = _entry(config)
    binary = str(entry.get("binary") or "codex")
    from agent.transports.codex_app_server import CodexAppServerClient

    client = CodexAppServerClient(
        codex_bin=binary,
        codex_home=str(entry.get("codex_home") or "") or None,
    )
    try:
        client.initialize(timeout=15)
        response = client.request("model/list", {}, timeout=30)
    finally:
        client.close()
    data = response.get("data") if isinstance(response, dict) else None
    if not isinstance(data, list):
        raise RuntimeError("Codex model/list returned an invalid payload")
    return [dict(item) for item in data if isinstance(item, dict) and item.get("id")]
