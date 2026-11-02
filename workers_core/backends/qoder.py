"""Qoder CN CLI backend with the Hermes Kanban MCP bridge.

Qoder keeps its own account login. Workers never read or forward that
credential; the CLI resolves it from its own config. The adapter only removes
Qoder's SDK-worker markers from the inherited environment so a CLI launched
from inside a Qoder session does not mistake itself for an SDK worker.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Mapping

from ..common import WorkerLaunchContext, worker_log
from ..common_mcp import WORKER_MCP_SERVER, write_mcp_config
from ..config import hermes_root
from ..executables import resolve_executable

_QODER_BINARIES = ("qoderclicn", "qodercn", "qoder")
_PERMISSION_MODES = {
    "default",
    "accept_edits",
    "auto",
    "bypass_permissions",
    "dont_ask",
}
_REASONING_EFFORTS = (
    "auto",
    "none",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
    "ultracode",
)
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _resolve_binary(value: str) -> str | None:
    return resolve_executable(value)


def _qoder_binary() -> str:
    # Keep the stable launcher (a PATH entry or ~/.local/bin symlink), not the
    # versioned payload it points at: Qoder's updater replaces that symlink.
    for name in _QODER_BINARIES:
        found = shutil.which(name)
        if found:
            return str(Path(found))
    for name in _QODER_BINARIES:
        candidate = Path.home() / ".local" / "bin" / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return "qoderclicn"


def _clean_env(env: Mapping[str, str]) -> dict[str, str]:
    """Drop Qoder's in-process SDK markers, which reject headless launches."""
    cleaned: dict[str, str] = {}
    for key, value in env.items():
        if key.startswith(("QODER_AGENT_SDK_", "QODER_SDK_")):
            continue
        if key == "QODER_SESSION_TYPE" or key.endswith("_SESSION_TYPE"):
            continue
        cleaned[key] = value
    return cleaned


def defaults() -> dict[str, Any]:
    return {
        "enabled": False,
        "binary": _qoder_binary(),
        "model": "",
        "reasoning_effort": "",
        "permission_mode": "auto",
    }


def validate_settings(entry: dict[str, Any]) -> dict[str, Any]:
    """Normalize Qoder settings while preserving unrecognized user fields."""
    if not isinstance(entry, Mapping):
        raise ValueError("workers.qoder must be an object")
    result = dict(entry)
    if not str(result.get("binary") or "").strip():
        raise ValueError("workers.qoder.binary is required")
    mode = str(result.get("permission_mode") or "auto").strip().lower()
    if mode not in _PERMISSION_MODES:
        raise ValueError(
            "workers.qoder.permission_mode must be one of "
            + ", ".join(sorted(_PERMISSION_MODES))
        )
    result["permission_mode"] = mode
    effort = str(result.get("reasoning_effort") or "").strip().lower()
    if effort and effort not in _REASONING_EFFORTS:
        raise ValueError(
            "workers.qoder.reasoning_effort must be one of "
            + ", ".join(_REASONING_EFFORTS)
        )
    result["reasoning_effort"] = effort
    return result


def validate_selection(
    entry: dict[str, Any], *, models: list[dict[str, Any]] | None = None
) -> None:
    if not bool(entry.get("enabled")):
        return
    binary = _resolve_binary(str(entry.get("binary") or ""))
    if not binary:
        raise ValueError("Qoder CLI executable is unavailable.")
    selected = str(entry.get("model") or "").strip()
    catalog = models or []
    if selected and catalog and not any(item.get("id") == selected for item in catalog):
        raise ValueError(f"Qoder model {selected!r} is unavailable.")


def model_options(
    entry: dict[str, Any],
    *,
    model: str,
    refresh: bool = False,
    scope: str = "",
    models_loader: Any = None,
) -> dict[str, Any]:
    del entry, model, refresh, scope, models_loader
    return {
        "options": list(_REASONING_EFFORTS),
        "default": "",
        "source": "qoder-cli",
    }


def _entry(config: dict[str, Any]) -> dict[str, Any]:
    return dict((config.get("workers") or {}).get("qoder") or {})


def _run_cli(
    binary: str, args: list[str], *, timeout: float
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [binary, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_clean_env(dict(os.environ)),
    )


def list_models(
    config: dict[str, Any], *, refresh: bool = False
) -> list[dict[str, Any]]:
    del refresh
    entry = _entry(config)
    binary = _resolve_binary(str(entry.get("binary") or ""))
    if not binary:
        raise RuntimeError("Qoder CLI executable is unavailable.")
    try:
        result = _run_cli(binary, ["--list-models"], timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"Qoder model catalog failed: {exc}") from exc
    output = _ANSI_RE.sub("", (result.stdout or "") + (result.stderr or "")).strip()
    if result.returncode != 0 or "Not logged in" in output:
        raise RuntimeError(output or "Qoder CLI is not logged in.")
    rows: list[dict[str, Any]] = []
    for line in output.splitlines():
        name = line.strip().lstrip("-*•").strip()
        if not name or name.upper() == "MODEL":
            continue
        rows.append({"id": name, "displayName": name})
    if not rows:
        raise RuntimeError("Qoder CLI returned no models.")
    return rows


def doctor(config: dict[str, Any]) -> dict[str, Any]:
    entry = _entry(config)
    binary = _resolve_binary(str(entry.get("binary") or ""))
    result: dict[str, Any] = {
        "backend": "qoder",
        "binary": str(entry.get("binary") or ""),
        "binary_ok": bool(binary),
        "model": str(entry.get("model") or "Auto"),
    }
    if not binary:
        result.update({"ok": False, "error": "Qoder CLI executable is unavailable"})
        return result
    try:
        version = _run_cli(binary, ["--version"], timeout=10)
        status = _run_cli(binary, ["status"], timeout=10)
        status_text = _ANSI_RE.sub("", (status.stdout or "") + (status.stderr or ""))
        login_ok = status.returncode == 0 and "Username:" in status_text
        result.update(
            {
                "version": (version.stdout or version.stderr or "").strip(),
                "version_ok": version.returncode == 0,
                "login": "Logged in" if login_ok else "Not logged in",
                "login_ok": login_ok,
                "ok": version.returncode == 0 and login_ok,
            }
        )
    except (OSError, subprocess.SubprocessError) as exc:
        result.update({"ok": False, "login_ok": False, "error": str(exc)})
    return result


def build_command(
    task: Any,
    workspace: str,
    entry: dict[str, Any],
    mcp_path: Path,
    prompt: str,
) -> list[str]:
    del workspace
    binary = _resolve_binary(str(entry.get("binary") or ""))
    if not binary:
        raise RuntimeError("Qoder CLI executable is unavailable.")
    mode = str(entry.get("permission_mode") or "auto").strip().lower()
    if mode not in _PERMISSION_MODES:
        raise RuntimeError(f"unsupported Qoder permission mode: {mode!r}")
    command = [
        binary,
        "--print",
        "--mcp-config",
        str(mcp_path),
        "--strict-mcp-config",
        "--output-format",
        "text",
        "--permission-mode",
        mode,
        "--allowed-tools",
        f"mcp__{WORKER_MCP_SERVER}__*",
    ]
    if not bool(getattr(task, "goal_mode", False)):
        command.append("--no-session-persistence")
    model = str(entry.get("model") or "").strip()
    if model:
        command.extend(["--model", model])
    effort = str(
        getattr(task, "reasoning_effort", "") or entry.get("reasoning_effort") or ""
    ).strip().lower()
    if effort:
        if effort not in _REASONING_EFFORTS:
            raise RuntimeError(f"unsupported Qoder reasoning effort: {effort!r}")
        command.extend(["--reasoning-effort", effort])
    command.append(prompt)
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
    env = _clean_env(launch.env)
    state_dir = hermes_root() / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", str(task.id)) or "task"
    run_dir = Path(
        tempfile.mkdtemp(prefix=f"workers-qoder-run-{safe_id}-", dir=state_dir)
    )
    try:
        mcp_path = write_mcp_config(run_dir, launch.mcp_servers)
        command = build_command(task, workspace, entry, mcp_path, launch.prompt)
        log_path = worker_log(str(task.id), board)
        log_handle = open(log_path, "ab")
        try:
            process = subprocess.Popen(
                command,
                cwd=workspace,
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                env=env,
                start_new_session=True,
            )
        finally:
            log_handle.close()
    except BaseException:
        shutil.rmtree(run_dir, ignore_errors=True)
        raise
    from ..process_group import supervise

    supervise(
        process.pid,
        task_id=str(task.id),
        board=board,
        cleanup_dir=run_dir,
    )
    return int(process.pid)
