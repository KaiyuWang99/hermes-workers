"""Run one Hermes shell hook from Claude Code's hook protocol.

Claude Code invokes this module as a command hook and supplies its native JSON
event on stdin.  The bridge converts that input to Hermes' existing shell-hook
wire format, executes the original command through Hermes' own shell-hook
runner, and converts blocking/modify/context results back to Claude Code.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from agent.shell_hooks import (
    ShellHookSpec,
    run_once as run_hermes_hook,
)


logger = logging.getLogger(__name__)

CLAUDE_TO_HERMES_TOOLS = {
    "Bash": "terminal",
    "PowerShell": "terminal",
    "Read": "read_file",
    "Write": "write_file",
    "Edit": "patch",
    "Glob": "search_files",
    "Grep": "search_files",
    "WebFetch": "web_extract",
    "WebSearch": "web_search",
    "Agent": "delegate_task",
    "Task": "delegate_task",
}


def _load_hook(manifest: Path, hook_index: int) -> dict[str, Any]:
    value = json.loads(manifest.read_text(encoding="utf-8"))
    hooks = value.get("hooks") if isinstance(value, dict) else None
    if not isinstance(hooks, list) or not 0 <= hook_index < len(hooks):
        raise ValueError("invalid Hermes hook manifest/index")
    hook = hooks[hook_index]
    if not isinstance(hook, dict):
        raise ValueError("invalid Hermes hook manifest entry")
    return hook


def _hermes_tool_name(value: Any) -> str | None:
    raw = str(value or "")
    return CLAUDE_TO_HERMES_TOOLS.get(raw, raw or None)


def _hermes_kwargs(event: str, payload: dict[str, Any]) -> dict[str, Any]:
    raw_tool = payload.get("tool_name")
    tool_name = _hermes_tool_name(raw_tool)
    args = payload.get("tool_input")
    kwargs: dict[str, Any] = {
        "tool_name": tool_name,
        "args": args if isinstance(args, dict) else None,
        "session_id": payload.get("session_id") or "",
        "parent_session_id": payload.get("parent_session_id"),
        "task_id": os.environ.get("HERMES_KANBAN_TASK", ""),
    }
    if payload.get("tool_use_id"):
        kwargs["tool_call_id"] = payload["tool_use_id"]
    omitted = {
        "hook_event_name",
        "tool_name",
        "tool_input",
        "session_id",
        "parent_session_id",
        "cwd",
    }
    for key, value in payload.items():
        if key in omitted:
            continue
        kwargs[key] = value
    if event == "pre_llm_call":
        kwargs["user_message"] = str(payload.get("prompt") or "")
        kwargs.setdefault("conversation_history", [])
        kwargs.setdefault("is_first_turn", payload.get("source") == "startup")
    return kwargs


def _native_result(
    event: str,
    parsed: dict[str, Any] | None,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    if not isinstance(parsed, dict):
        return None
    action = str(parsed.get("action") or "").strip().lower()
    if event == "pre_tool_call":
        if action == "block":
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": str(
                        parsed.get("message") or "Blocked by Hermes hook."
                    ),
                }
            }
        if action == "modify" and isinstance(parsed.get("args"), dict):
            original = payload.get("tool_input")
            updated = dict(original) if isinstance(original, dict) else {}
            updated.update(parsed["args"])
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "updatedInput": updated,
                }
            }
        return None
    if event == "pre_verify" and action == "continue":
        return {
            "decision": "block",
            "reason": str(parsed.get("message") or "Continue working.")
        }
    if event == "pre_llm_call" and parsed.get("context"):
        return {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": str(parsed["context"]),
            }
        }
    return None


def run_once(manifest: Path, hook_index: int, payload: dict[str, Any]) -> int:
    hook = _load_hook(manifest, hook_index)
    event = str(hook.get("event") or "").strip()
    command = str(hook.get("command") or "").strip()
    if not event or not command:
        raise ValueError("Hermes hook entry is missing event/command")

    spec = ShellHookSpec(
        event=event,
        command=command,
        matcher=hook.get("matcher"),
        timeout=int(hook.get("timeout") or 60),
        fail_closed=bool(hook.get("fail_closed")),
    )
    if event in {"pre_tool_call", "post_tool_call"} and not spec.matches_tool(
        _hermes_tool_name(payload.get("tool_name"))
    ):
        return 0
    result = run_hermes_hook(spec, _hermes_kwargs(event, payload))
    native = _native_result(event, result.get("parsed"), payload)
    if native is not None:
        sys.stdout.write(json.dumps(native, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--hook-index", required=True, type=int)
    args = parser.parse_args(argv)
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("Claude hook payload must be an object")
        return run_once(Path(args.manifest), args.hook_index, payload)
    except Exception as exc:
        sys.stderr.write(f"Hermes Claude hook bridge failed: {exc}\n")
        try:
            hook = _load_hook(Path(args.manifest), args.hook_index)
        except Exception as reload_exc:
            logger.debug("main: hook manifest reload failed: %s", reload_exc)
            return 0
        if hook.get("event") == "pre_tool_call" and hook.get("fail_closed"):
            return 2
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
