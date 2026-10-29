"""Run ``codex exec --json`` and record provider failures without acting on them.

Codex authenticates with its own login, so Workers never rotates credentials for
it. The JSONL events expose only a free-text message on failure; this runner
records that message with the shared classifier verdict for diagnostics and
leaves fallback/rotation to the caller's policy (currently none).
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .provider_errors import classify, normalize_error
from .runner_support import install_terminate_handlers


_active_child: subprocess.Popen[bytes] | None = None


def _timestamp() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _write(line: str) -> None:
    sys.stdout.write(line if line.endswith("\n") else line + "\n")
    sys.stdout.flush()


def _record_failure(
    status_path: str, event: str, message: str, model: str
) -> dict[str, Any]:
    error = normalize_error(
        provider="codex",
        model=model,
        error_message=message,
        source="workers-codex",
    )
    decision = classify(error)
    record = {
        "status_code": None,
        "reason": decision.reason,
        "message": message,
        "event": event,
        "model": model,
        # Record-only: no status means both actions stay false.
        "should_rotate_credential": decision.should_rotate_credential,
        "should_fallback": decision.should_fallback,
        "recorded_at": _timestamp(),
    }
    try:
        target = Path(status_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass
    return record


def _failure_event(event: dict[str, Any]) -> tuple[str, str] | None:
    kind = str(event.get("type") or "")
    if kind == "turn.failed":
        error = event.get("error")
        message = error.get("message") if isinstance(error, dict) else error
        return kind, str(message or "")
    if kind == "error":
        return kind, str(event.get("message") or "")
    if kind == "item.completed":
        item = event.get("item")
        if isinstance(item, dict) and item.get("type") == "error":
            return kind, str(item.get("message") or "")
    return None


def _read_stderr(stream) -> None:
    for raw in stream:
        _write(raw.decode("utf-8", "replace").rstrip("\n"))


def run_payload(payload: dict[str, Any]) -> int:
    command = [str(item) for item in payload.get("command") or []]
    if not command:
        raise RuntimeError("Codex runner received an empty command")
    env = {str(key): str(value) for key, value in (payload.get("env") or {}).items()}
    workspace = str(payload.get("workspace") or "")
    model = str(payload.get("model") or "")
    status_path = str(payload.get("status_path") or "")
    prompt = str(payload.get("prompt") or "")
    if status_path:
        # A successful run must not leave a previous failure record behind.
        try:
            Path(status_path).unlink()
        except OSError:
            pass

    global _active_child
    _active_child = subprocess.Popen(
        command,
        cwd=workspace or None,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    child = _active_child
    if child.stdin is None or child.stdout is None or child.stderr is None:
        child.terminate()
        raise RuntimeError("Codex runner could not create its pipes")
    stderr_thread = threading.Thread(
        target=_read_stderr, args=(child.stderr,), daemon=True
    )
    stderr_thread.start()
    try:
        try:
            child.stdin.write(prompt.encode("utf-8"))
            child.stdin.close()
        except (BrokenPipeError, OSError) as exc:
            _write(f"[workers] Codex rejected its startup prompt: {exc}")
        for raw in child.stdout:
            line = raw.decode("utf-8", "replace")
            text = line.strip()
            try:
                event = json.loads(text) if text.startswith("{") else None
            except ValueError:
                event = None
            if not isinstance(event, dict):
                if text:
                    _write(text)
                continue
            if event.get("type") == "item.completed":
                item = event.get("item")
                if (
                    isinstance(item, dict)
                    and item.get("type") == "agent_message"
                    and item.get("text")
                ):
                    _write(str(item["text"]))
            failure = _failure_event(event)
            if failure:
                event_type, message = failure
                record = _record_failure(
                    status_path, event_type, message, model
                )
                _write(
                    "[workers] Codex failure recorded: "
                    f"{record['reason']} — {record['message']}"
                )
        return_code = int(child.wait())
    finally:
        _active_child = None
        stderr_thread.join(timeout=2)
    if return_code != 0:
        _write(f"[workers] Codex exited with status {return_code}")
    return return_code


def main() -> int:
    install_terminate_handlers(lambda: _active_child)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("launch payload must be an object")
        return run_payload(payload)
    except Exception as exc:
        sys.stderr.write(f"Codex worker runner failed: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
