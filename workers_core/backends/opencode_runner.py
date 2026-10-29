"""Run OpenCode attempts and walk the Hermes fallback model chain."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..config import hermes_root
from ..provider_errors import classify, normalize_error
from ..runner_support import install_terminate_handlers


_TAIL_LIMIT = 64 * 1024
_active_child: subprocess.Popen[bytes] | None = None


def _status_path(task_id: str, env: dict[str, str] | None = None) -> Path:
    """The attempt's hook-decision file; the legacy state path is the fallback."""
    target = str(
        (env if env is not None else os.environ).get("WORKERS_OPENCODE_STATUS") or ""
    ).strip()
    if target:
        return Path(target)
    return hermes_root() / "state" / f"workers-opencode-{task_id}.status"


def _clear_reported_status(
    task_id: str, env: dict[str, str] | None = None
) -> None:
    if task_id:
        try:
            _status_path(task_id, env).unlink()
        except OSError:
            pass


def _reported(task_id: str, env: dict[str, str] | None = None) -> dict[str, Any]:
    """Latest hook decision for this card; an old integer file is a bare status."""
    if not task_id:
        return {}
    try:
        value = _status_path(task_id, env).read_text(encoding="utf-8").strip()
    except OSError:
        return {}
    if value.isdigit():
        return {"status_code": int(value)}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    status = parsed.get("status_code")
    decision: dict[str, Any] = {
        "status_code": status
        if isinstance(status, int) and not isinstance(status, bool)
        else None
    }
    if isinstance(parsed.get("should_fallback"), bool):
        decision["should_fallback"] = parsed["should_fallback"]
    return decision


def _reported_status(
    task_id: str, env: dict[str, str] | None = None
) -> int | None:
    return _reported(task_id, env).get("status_code")


def _run_once(command: list[str], env: dict[str, str]) -> tuple[int, str]:
    global _active_child
    _active_child = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
    )
    if _active_child.stdout is None:
        raise RuntimeError("OpenCode stdout pipe was not created")
    tail = bytearray()
    while True:
        chunk = _active_child.stdout.read(8192)
        if not chunk:
            break
        sys.stdout.buffer.write(chunk)
        sys.stdout.buffer.flush()
        tail.extend(chunk)
        if len(tail) > _TAIL_LIMIT:
            del tail[:-_TAIL_LIMIT]
    return_code = int(_active_child.wait())
    _active_child = None
    return return_code, tail.decode("utf-8", errors="replace")


def run_payload(payload: dict[str, Any]) -> int:
    attempts = payload.get("attempts")
    if not isinstance(attempts, list) or not attempts:
        raise RuntimeError("OpenCode runner received no launch attempts")
    task_id = str((payload.get("env") or {}).get("HERMES_KANBAN_TASK") or "")
    if not task_id:
        for candidate in attempts:
            raw_env = candidate.get("env") if isinstance(candidate, dict) else None
            task_id = str((raw_env or {}).get("HERMES_KANBAN_TASK") or "")
            if task_id:
                break

    for index, raw_attempt in enumerate(attempts):
        if not isinstance(raw_attempt, dict):
            raise RuntimeError("OpenCode runner received an invalid launch attempt")
        command = [str(item) for item in raw_attempt.get("command") or []]
        env = {
            str(key): str(value)
            for key, value in (raw_attempt.get("env") or {}).items()
        }
        if not command:
            raise RuntimeError("OpenCode runner received an empty command")
        _clear_reported_status(task_id, env)
        return_code, output_tail = _run_once(command, env)
        if return_code == 0 or index == len(attempts) - 1:
            return return_code
        decision = _reported(task_id, env)
        fallback = decision.get("should_fallback")
        if not isinstance(fallback, bool):
            status = decision.get("status_code")
            fallback = (
                isinstance(status, int)
                and not isinstance(status, bool)
                and classify(normalize_error(status_code=status)).should_fallback
            )
        if not fallback:
            return return_code
        provider = str(attempts[index + 1].get("provider") or "")
        model = str(attempts[index + 1].get("model") or "")
        sys.stderr.write(
            f"[workers] OpenCode provider failure; switching to fallback "
            f"{provider}/{model}.\n"
        )
        sys.stderr.flush()
    return 1


def main() -> int:
    install_terminate_handlers(lambda: _active_child)
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("launch payload must be an object")
        return run_payload(payload)
    except Exception as exc:
        sys.stderr.write(f"OpenCode worker runner failed: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
