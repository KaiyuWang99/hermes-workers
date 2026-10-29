"""Run Claude Code with one bounded Hermes credential rotation on failure."""

from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from ..config import hermes_root
from ..credentials import rotate_credential
from ..provider_errors import classify, normalize_error
from ..runner_support import install_terminate_handlers, terminate_child
from ..fallbacks import result_event
from .claudecode import _CLAUDE_SETTINGS_ENV_VARS, _apply_provider_env
from .claude_proxy import BuiltinClaudeProxy


logger = logging.getLogger(__name__)

_TAIL_LIMIT = 64 * 1024
_HEARTBEAT_INTERVAL = 30.0
_active_child: subprocess.Popen[bytes] | None = None
_session_claim_taken = False


def _heartbeat() -> None:
    """Extend the claim and stamp liveness through Hermes' own worker bridge.

    ``heartbeat_current_worker_from_env`` is the call a native worker's agent
    loop makes as a side effect of real progress, so reusing it inherits core's
    rate limit, run-id fence, and claim-TTL extension instead of the runner
    keeping a liveness rule the board cannot compare against.
    """
    try:
        from tools.kanban_tools import heartbeat_current_worker_from_env

        heartbeat_current_worker_from_env()
    except Exception as exc:
        logger.debug("_heartbeat: kanban heartbeat failed: %s", exc)


def _liveness_grace_seconds() -> float:
    """Silence still worth reporting as liveness, read from core.

    ``release_stale_claims`` reclaims a live PID whose heartbeat is older than
    ``DEFAULT_CLAIM_HEARTBEAT_MAX_STALE_SECONDS`` as wedged, so heartbeating
    past that point only hides the stall.  Tolerating exactly core's threshold
    keeps a slow upstream turn alive without making a wedged worker look busy.
    """
    try:
        from hermes_cli.kanban_db import DEFAULT_CLAIM_HEARTBEAT_MAX_STALE_SECONDS

        return float(DEFAULT_CLAIM_HEARTBEAT_MAX_STALE_SECONDS)
    except Exception as exc:
        logger.debug("_liveness_grace_seconds: default unavailable: %s", exc)
        return 3600.0


def _reported_status(text: str) -> int | None:
    """HTTP status explicitly reported in Claude Code's final result event."""
    status = result_event(text).get("api_error_status")
    return status if isinstance(status, int) and not isinstance(status, bool) else None


def _result_error(text: str):
    """Structured provider error from Claude Code's final result event only."""
    result = result_event(text)
    return normalize_error(
        status_code=result.get("api_error_status"),
        error_type=result.get("api_error_type", ""),
        error_code=result.get("api_error_code", ""),
        error_body=result.get("api_error_body"),
        error_message=result.get("api_error_message") or result.get("result", ""),
        source="workers-claude-code",
    )


def _failure_status(text: str) -> int | None:
    """Only rotate on an upstream HTTP status the classifier marks rotatable."""
    error = _result_error(text)
    if error.status_code is None:
        return None
    return error.status_code if classify(error).should_rotate_credential else None


def _final_text(output: str) -> str:
    result = result_event(output)
    value = result.get("result")
    if isinstance(value, str):
        return value
    structured = result.get("structured_output")
    if structured is not None:
        return json.dumps(structured, ensure_ascii=False)
    return output


def _resume_command(command: list[str], session_id: str, prompt: str) -> list[str]:
    """Resume the same Claude session while preserving the launch policy."""
    args = list(command[:-1])
    for flag in ("--session-id", "--resume"):
        while flag in args:
            index = args.index(flag)
            del args[index : index + 2]
    args.extend(["--resume", session_id, prompt])
    return args


def _session_released(command: list[str]) -> list[str]:
    """Resume a claimed session instead of replaying the initial task in a new one."""
    if "--session-id" not in command[:-1]:
        return list(command)
    session_id = command[command.index("--session-id") + 1]
    return _resume_command(
        command, session_id,
        "Continue the interrupted turn using the existing conversation and tool results. "
        "Do not repeat completed actions.",
    )


def _merge_settings(settings: dict[str, Any], existing: str) -> dict[str, Any]:
    """Fold a command-supplied --settings payload into ours; ours wins."""
    try:
        if existing.lstrip().startswith("{"):
            other: Any = json.loads(existing)
        else:
            with open(existing, encoding="utf-8") as handle:
                other = json.load(handle)
    except Exception as exc:
        logger.debug("_merge_settings: existing --settings unreadable: %s", exc)
        other = None
    return {**other, **settings} if isinstance(other, dict) else settings


def _run_once(command: list[str], env: dict[str, str]) -> tuple[int, str]:
    global _active_child
    command = _apply_pre_llm_context(command, env)
    comment_input = None
    initial_prompt = None
    wait_interval = _HEARTBEAT_INTERVAL
    stdin_mode: int | None = subprocess.DEVNULL
    if (
        "--input-format" in command[:-1]
        and command[command.index("--input-format") + 1] == "stream-json"
    ):
        # Caller-owned streaming sessions feed their messages
        # over stdin; closing it would end the session before it starts.
        stdin_mode = None
    elif env.get("HERMES_KANBAN_TASK"):
        from tools.kanban_tools import _COMMENT_POLL_MIN_INTERVAL_SECONDS

        initial_prompt = command[-1]
        command = [*command[:-1], "--input-format", "stream-json"]
        stdin_mode = subprocess.PIPE
        wait_interval = min(_HEARTBEAT_INTERVAL, _COMMENT_POLL_MIN_INTERVAL_SECONDS)
    _active_child = subprocess.Popen(
        command,
        stdin=stdin_mode,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
    )
    if _active_child.stdout is None:
        raise RuntimeError("Claude Code stdout pipe was not created")
    if initial_prompt is not None:
        from ..claude_comment_input import ClaudeCommentInput

        if _active_child.stdin is None:
            raise RuntimeError("Claude Code streaming input pipe was not created")
        comment_input = ClaudeCommentInput(
            _active_child.stdin, env.get("WORKERS_CLAUDE_SESSION_ID", "")
        )
        # Existing comments are available through kanban_show.worker_context.
        comment_input.poll()
        if not comment_input.steer(initial_prompt):
            terminate_child(_active_child)
            raise RuntimeError("Claude Code rejected the initial streaming message")
    tail = bytearray()
    lines: queue.Queue[bytes | None] = queue.Queue()

    def _reader(stream: Any) -> None:
        try:
            while True:
                line = stream.readline()
                if not line:
                    break
                lines.put(line)
        finally:
            lines.put(None)

    # A slow upstream turn emits nothing for minutes, so silence within core's
    # wedge threshold still counts as liveness.  Past it the runner stops
    # heartbeating, which lets the board reclaim a wedged worker instead of
    # watching a timer that never goes stale.
    reader = threading.Thread(target=_reader, args=(_active_child.stdout,), daemon=True)
    reader.start()
    last_heartbeat = time.monotonic()
    last_output = last_heartbeat
    liveness_grace = _liveness_grace_seconds()
    silent_timeout = float(env.get("WORKERS_CLAUDE_SILENT_TIMEOUT_SECONDS") or 0)
    try:
        while True:
            if comment_input is not None and not comment_input.closed:
                comment_input.poll()
            try:
                line = lines.get(timeout=wait_interval)
            except queue.Empty:
                now = time.monotonic()
                if silent_timeout > 0 and now - last_output >= silent_timeout:
                    terminate_child(_active_child)
                    break
                if (
                    now - last_output < liveness_grace
                    and now - last_heartbeat >= _HEARTBEAT_INTERVAL
                ):
                    _heartbeat()
                    last_heartbeat = now
                if (
                    _active_child.poll() is not None
                    and not reader.is_alive()
                    and lines.empty()
                ):
                    break
                continue
            if line is None:
                break
            last_output = time.monotonic()
            sys.stdout.buffer.write(line)
            sys.stdout.buffer.flush()
            tail.extend(line)
            if len(tail) > _TAIL_LIMIT:
                del tail[:-_TAIL_LIMIT]
            if comment_input is not None:
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeError):
                    event = None
                if isinstance(event, dict):
                    comment_input.finish_turn(event)
            now = time.monotonic()
            if now - last_heartbeat >= _HEARTBEAT_INTERVAL:
                _heartbeat()
                last_heartbeat = now
        return_code = int(_active_child.wait())
        return return_code, tail.decode("utf-8", errors="replace")
    finally:
        if comment_input is not None:
            comment_input.close()
        _active_child = None


def _apply_pre_llm_context(command: list[str], env: dict[str, str]) -> list[str]:
    if not command:
        return command
    from hermes_cli.plugins import invoke_hook

    values = invoke_hook(
        "pre_llm_call",
        session_id=env.get("WORKERS_CLAUDE_SESSION_ID", ""),
        task_id=env.get("HERMES_KANBAN_TASK", ""),
        platform="cli",
        model=env.get("ANTHROPIC_MODEL", ""),
        user_message=str(command[-1]),
        conversation_history=[],
        is_first_turn="--resume" not in command,
    )
    contexts = []
    for value in values:
        context = (
            value
            if isinstance(value, str)
            else value.get("context") if isinstance(value, dict) else None
        )
        if context and str(context).strip():
            contexts.append(str(context).strip())
    if not contexts:
        return command
    updated = list(command)
    updated[-1] = str(updated[-1]) + "\n\n" + "\n\n".join(contexts)
    return updated


def _run_routed(
    command: list[str],
    env: dict[str, str],
    runtime: dict[str, str],
    hook_settings: dict[str, Any] | None = None,
    proxy: BuiltinClaudeProxy | None = None,
) -> tuple[int, str]:
    def run_with_settings(child_env: dict[str, str]) -> tuple[int, str]:
        keys = _CLAUDE_SETTINGS_ENV_VARS
        # The settings fragment carries the routed API key, so keep it in a
        # private 0700 subdirectory of the Hermes state dir instead of the
        # shared system temp dir: a SIGKILL skips the unlink below, and any
        # file left behind should not be world-readable.
        run_dir = env.get("WORKERS_CLAUDE_RUN_DIR")
        state_dir = Path(run_dir) if run_dir else hermes_root() / "state" / "workers-claude"
        state_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(state_dir, 0o700)
        fd, path = tempfile.mkstemp(
            prefix="workers-claude-settings-", suffix=".json", dir=str(state_dir)
        )
        handle = os.fdopen(fd, "w", encoding="utf-8")
        try:
            settings: dict[str, Any] = {
                "env": {key: child_env[key] for key in keys if child_env.get(key)}
            }
            if isinstance(hook_settings, dict):
                hooks = hook_settings.get("hooks")
                if isinstance(hooks, dict) and hooks:
                    settings["hooks"] = hooks
            # Merge an existing --settings payload instead of stacking a
            # second one, which Claude Code rejects as a malformed argument.
            settings_index = (
                command.index("--settings") if "--settings" in command[:-1] else None
            )
            if settings_index is not None:
                settings = _merge_settings(settings, command[settings_index + 1])
            json.dump(settings, handle, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            handle.close()
            os.chmod(path, 0o600)
            if settings_index is None:
                routed_command = [*command[:-1], "--settings", path, command[-1]]
            else:
                routed_command = [
                    *command[: settings_index + 1],
                    path,
                    *command[settings_index + 2 :],
                ]
            return _run_once(routed_command, child_env)
        finally:
            try:
                handle.close()
            except Exception as exc:
                logger.debug("run_with_settings: settings handle close failed: %s", exc)
            try:
                os.unlink(path)
            except OSError:
                pass

    if runtime.get("route") != "builtin":
        child_env = dict(env)
        _apply_provider_env(child_env, runtime)
        return run_with_settings(child_env)

    def run_with_proxy(active_proxy: BuiltinClaudeProxy) -> tuple[int, str]:
        child_runtime = dict(runtime)
        child_runtime.update(
            {
                "base_url": active_proxy.base_url,
                "api_key": "WORKERS_BUILTIN",
                "client_model": runtime["model"],
            }
        )
        child_env = dict(env)
        _apply_provider_env(child_env, child_runtime)
        return run_with_settings(child_env)

    if proxy is not None:
        return run_with_proxy(proxy)
    with BuiltinClaudeProxy(runtime) as owned_proxy:
        return run_with_proxy(owned_proxy)


def _run_candidate(
    command: list[str],
    runtime: dict[str, Any],
    env: dict[str, str],
    hook_settings: dict[str, Any] | None = None,
    proxy: BuiltinClaudeProxy | None = None,
) -> tuple[int, str]:
    if not command or not all(
        runtime.get(key) for key in ("provider", "model", "base_url", "api_key")
    ):
        raise RuntimeError("Claude runner received an invalid launch payload")

    global _session_claim_taken
    for attempt in range(2):
        run_command = _session_released(command) if _session_claim_taken else command
        _session_claim_taken = True
        return_code, output_tail = _run_routed(
            run_command, dict(env), runtime, hook_settings, proxy
        )
        if return_code == 0 or attempt > 0:
            return return_code, output_tail
        status = _failure_status(output_tail)
        if status is None:
            return return_code, output_tail
        error = _result_error(output_tail)
        failure = {
            "provider": runtime["provider"],
            "model": runtime["model"],
            "credential_id": runtime.get("credential_id", ""),
            "api_key": runtime["api_key"],
            "status_code": status,
            "source": "workers-claude-code",
            "error_type": error.error_type,
            "error_code": error.error_code,
            "error_body": error.body,
            "error_message": error.message,
        }
        next_credential = rotate_credential(failure)
        if not next_credential or not next_credential.get("key"):
            return return_code, output_tail
        next_key = str(next_credential["key"])
        if next_key == runtime["api_key"]:
            return return_code, output_tail
        runtime["api_key"] = next_key
        runtime["credential_id"] = str(next_credential.get("credential_id") or "")
        # Keep the downstream-visible credential identity in sync with the
        # rotated key so the restarted child reports the right pool entry.
        env["WORKERS_CLAUDE_CREDENTIAL_ID"] = runtime["credential_id"]
        sys.stderr.write(
            f"[workers] Claude Code credential rotated after HTTP {status}; "
            "restarting this worker once.\n"
        )
        sys.stderr.flush()
    return 1, output_tail


def _run_goal(
    command: list[str],
    runtime: dict[str, Any],
    env: dict[str, str],
    first_output: str,
    goal: dict[str, Any],
    hook_settings: dict[str, Any] | None = None,
    proxy: BuiltinClaudeProxy | None = None,
) -> int:
    if not bool(goal.get("enabled")):
        return 0
    task_id = str(goal.get("task_id") or env.get("HERMES_KANBAN_TASK") or "")
    session_id = str(
        goal.get("session_id") or result_event(first_output).get("session_id") or ""
    )
    if not task_id or not session_id:
        raise RuntimeError("goal-mode Claude Worker is missing task/session identity")

    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc
    from hermes_cli.goals import DEFAULT_MAX_TURNS, run_kanban_goal_loop

    run_id = goal.get("run_id")
    run_id = int(run_id) if run_id is not None else None
    with kbc.connect_closing() as conn:
        task = kb.get_task(conn, task_id)
    if task is None:
        raise RuntimeError(f"goal-mode task not found: {task_id}")
    goal_text = "\n\n".join(
        part for part in (task.title or "", task.body or "") if part
    ).strip()

    def run_turn(prompt: str) -> str:
        resume = _resume_command(command, session_id, prompt)
        return_code, output = _run_candidate(
            resume, runtime, env, hook_settings, proxy
        )
        if return_code != 0:
            raise RuntimeError(f"Claude resume exited with status {return_code}")
        return _final_text(output)

    def task_status() -> str | None:
        with kbc.connect_closing() as conn:
            return kb.goal_run_status(conn, task_id, run_id)

    def block(reason: str) -> None:
        with kbc.connect_closing() as conn:
            kb.block_task(conn, task_id, reason=reason, expected_run_id=run_id)

    outcome = run_kanban_goal_loop(
        task_id=task_id,
        goal_text=goal_text,
        run_turn=run_turn,
        task_status_fn=task_status,
        block_fn=block,
        max_turns=int(goal.get("max_turns") or DEFAULT_MAX_TURNS),
        first_response=_final_text(first_output),
        log=lambda message: sys.stderr.write(f"[workers] {message}\n"),
    )
    return 1 if outcome.get("outcome") == "stopped" else 0


def run_payload(payload: dict[str, Any]) -> int:
    global _session_claim_taken
    _session_claim_taken = False
    attempts = payload.get("attempts")
    if not isinstance(attempts, list) or not attempts:
        attempts = [
            {
                "command": payload.get("command"),
                "runtime": payload.get("runtime"),
                "env": payload.get("env"),
            }
        ]

    base_env = {
        str(key): str(value)
        for key, value in (payload.get("env") or {}).items()
    }
    hook_settings = payload.get("hermes_hook_settings")
    if not isinstance(hook_settings, dict):
        hook_settings = None
    for index, raw_attempt in enumerate(attempts):
        if not isinstance(raw_attempt, dict):
            raise RuntimeError("Claude runner received an invalid launch attempt")
        command = [str(item) for item in raw_attempt.get("command") or []]
        runtime = dict(raw_attempt.get("runtime") or {})
        attempt_hook_settings = raw_attempt.get(
            "hermes_hook_settings", hook_settings
        )
        if not isinstance(attempt_hook_settings, dict):
            attempt_hook_settings = None
        attempt_env = dict(base_env)
        attempt_env.update(
            {
                str(key): str(value)
                for key, value in (raw_attempt.get("env") or {}).items()
            }
        )
        # One bridge now lives for the whole attempt, including every goal-mode
        # resume turn, so upstream TLS connections survive across turns and the
        # bridge-side credential rotation stays visible to later turns. This is
        # only safe because BuiltinClaudeProxy keeps no per-request state.
        proxy: BuiltinClaudeProxy | None = None
        if runtime.get("route") == "builtin":
            proxy = BuiltinClaudeProxy(runtime)
            proxy.__enter__()
        try:
            return_code, output_tail = _run_candidate(
                command, runtime, attempt_env, attempt_hook_settings, proxy
            )
            if return_code == 0:
                goal = dict(payload.get("goal") or {})
                return _run_goal(
                    command,
                    runtime,
                    attempt_env,
                    output_tail,
                    goal,
                    attempt_hook_settings,
                    proxy,
                )
        finally:
            if proxy is not None:
                proxy.__exit__(None, None, None)
        if index == len(attempts) - 1:
            return return_code
        error = _result_error(output_tail)
        if error.status_code is None or not classify(error).should_fallback:
            return return_code
        next_runtime = attempts[index + 1].get("runtime") or {}
        sys.stderr.write(
            f"[workers] Claude Code provider failure; switching to fallback "
            f"{next_runtime.get('provider', '')}/{next_runtime.get('model', '')}.\n"
        )
        sys.stderr.flush()
    return 1


def main() -> int:
    install_terminate_handlers(lambda: _active_child)
    cleanup_paths: list[str] = []
    cleanup_dir: str | None = None
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("launch payload must be an object")
        payload["env"] = dict(os.environ)
        cleanup_dir = payload.get("cleanup_dir")
        raw_cleanup = payload.get("cleanup_paths")
        if isinstance(raw_cleanup, list):
            cleanup_paths = [str(item) for item in raw_cleanup]
        return run_payload(payload)
    except Exception as exc:
        sys.stderr.write(f"Claude worker runner failed: {exc}\n")
        return 1
    finally:
        # Task-scoped MCP/system-prompt fragments are only needed while this
        # runner (and its goal turns) is alive.
        for path in cleanup_paths:
            try:
                os.unlink(path)
            except OSError:
                pass
        if cleanup_dir:
            shutil.rmtree(cleanup_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
