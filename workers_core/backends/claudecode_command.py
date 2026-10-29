"""Claude Code argv construction and per-run MCP/system-prompt files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .claudecode_cli import _cli_flags, _resolve_binary, _supports
from .claudecode_runtime import _effective_target


def _mcp_config(
    run_dir: Path,
    servers: dict[str, dict[str, Any]],
) -> Path:
    """Write the MCP definition into this run's private directory."""
    target = run_dir / "mcp.json"
    target.write_text(
        json.dumps({"mcpServers": servers}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    target.chmod(0o600)
    return target


def _system_prompt_file(run_dir: Path, content: str) -> Path:
    target = run_dir / "system.md"
    target.write_text(content, encoding="utf-8")
    target.chmod(0o600)
    return target


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
    resolve_binary: Any = None,
    cli_flags: Any = None,
) -> list[str]:
    resolve_binary = resolve_binary or _resolve_binary
    cli_flags = cli_flags or _cli_flags
    binary = resolve_binary(str(entry.get("binary") or "claude"))
    if not binary:
        raise RuntimeError("Claude Code executable is unavailable")
    flags = cli_flags(binary)
    if bool(getattr(task, "goal_mode", False)) and flags and not {
        "--session-id",
        "--resume",
    }.issubset(flags):
        raise RuntimeError(
            "Claude Code goal mode requires --session-id and --resume support"
        )
    _provider, model = _effective_target(task, entry)
    model = str(client_model or model)

    command = [
        binary,
        "--print",
        "--model",
        model,
        "--mcp-config",
        str(mcp_path),
    ]
    if bool(entry.get("strict_mcp_config", False)):
        if "--strict-mcp-config" not in flags:
            raise RuntimeError("This Claude Code CLI cannot disable its native MCP configuration")
        command.append("--strict-mcp-config")
    sources = entry.get("settings_sources")
    if sources is not None:
        if "--setting-sources" not in flags:
            raise RuntimeError("This Claude Code CLI cannot select native settings sources")
        command.extend(["--setting-sources", ",".join(sources)])
    if entry.get("native_skills", True) is False:
        if "--disable-slash-commands" not in flags:
            raise RuntimeError("This Claude Code CLI cannot disable its native Skills")
        command.append("--disable-slash-commands")
    if _supports(flags, "--output-format"):
        command.extend(["--output-format", "stream-json"])
    if _supports(flags, "--verbose"):
        command.append("--verbose")
    configured_tools = entry.get("tools")
    if configured_tools is not None and _supports(flags, "--tools"):
        command.extend(["--tools", ",".join(str(item) for item in configured_tools)])
    disallowed_tools = entry.get("disallowed_tools") or []
    if disallowed_tools and _supports(flags, "--disallowedTools"):
        command.extend(
            ["--disallowedTools", ",".join(str(item) for item in disallowed_tools)]
        )
    if session_id and _supports(flags, "--session-id"):
        command.extend(["--session-id", session_id])
    if system_prompt_path is not None and _supports(
        flags, "--append-system-prompt-file"
    ):
        command.extend(["--append-system-prompt-file", str(system_prompt_path)])
    elif system_prompt_path is not None and _supports(flags, "--append-system-prompt"):
        command.extend(
            ["--append-system-prompt", system_prompt_path.read_text(encoding="utf-8")]
        )
    permission_mode = str(entry.get("permission_mode") or "").strip()
    if permission_mode and _supports(flags, "--permission-mode"):
        command.extend(["--permission-mode", permission_mode])
    effort = str(
        getattr(task, "reasoning_effort", "") or entry.get("reasoning_effort") or ""
    ).strip()
    # --effort is a recent CLI flag; passing it to an older binary aborts the
    # launch with an unrecognized-argument exit that no fallback recognizes.
    if effort and effort != "default" and _supports(flags, "--effort"):
        command.extend(["--effort", effort])
    if bool(entry.get("no_session_persistence", False)) and not bool(
        getattr(task, "goal_mode", False)
    ) and _supports(flags, "--no-session-persistence"):
        command.append("--no-session-persistence")
    command.append(prompt or "")
    return command
