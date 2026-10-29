from __future__ import annotations

import io
import json
import sys
from contextlib import contextmanager
from types import SimpleNamespace

import tools.kanban_tools as kanban_tools
from workers_core.backends import claude_runner
from workers_core.claude_comment_input import ClaudeCommentInput


class CaptureInput(io.BytesIO):
    def close(self):
        self.saved = self.getvalue()
        super().close()


def test_native_comments_skip_history_and_self_and_deliver_once(monkeypatch):
    rows = [SimpleNamespace(id=1, author="operator", body="historical note")]

    @contextmanager
    def board(*_args, **_kwargs):
        yield SimpleNamespace(list_comments_after=lambda _conn, _task, after_id: [
            row for row in rows if row.id > after_id
        ]), None

    monkeypatch.setenv("HERMES_KANBAN_TASK", "comment-task")
    monkeypatch.setattr(kanban_tools, "_board", board)
    monkeypatch.setattr(kanban_tools, "_persisted_identity", lambda: "worker")
    monkeypatch.setattr(kanban_tools, "_is_dispatcher_owned_worker", lambda: True)
    monkeypatch.setattr(kanban_tools, "_COMMENT_POLL_MIN_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(kanban_tools, "_comment_poll_last_attempt", 0)
    monkeypatch.setattr(kanban_tools, "_comment_watermark", {})
    stream = CaptureInput()
    bridge = ClaudeCommentInput(stream, "original-session")

    assert not bridge.poll()
    assert bridge.steer("initial task")
    rows.extend([
        SimpleNamespace(id=2, author="worker", body="my own progress"),
        SimpleNamespace(id=3, author="operator", body="new instruction"),
    ])
    assert bridge.poll()
    assert not bridge.poll()
    messages = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert len(messages) == 2
    note = messages[1]["message"]["content"]
    assert "new instruction" in note
    assert "historical note" not in note
    assert "my own progress" not in note
    assert messages[1]["session_id"] == "original-session"

    bridge.finish_turn({"type": "result", "subtype": "success"})
    assert not stream.closed  # the queued operator message still needs its turn
    bridge.finish_turn({"type": "result", "subtype": "success"})
    assert stream.closed


def test_error_closes_input_without_sending_more_notes(monkeypatch):
    monkeypatch.setattr(ClaudeCommentInput, "poll", lambda _self: (_ for _ in ()).throw(
        AssertionError("error results must not poll for another turn")
    ))
    stream = CaptureInput()
    bridge = ClaudeCommentInput(stream, "original-session")
    bridge.steer("task")
    bridge.finish_turn({"type": "result", "subtype": "error_during_execution", "is_error": True})
    assert stream.closed
    assert not bridge.steer("follow-up")


def test_runner_keeps_stream_open_until_queued_comment_is_answered(tmp_path, monkeypatch):
    cli = tmp_path / "fake_claude.py"
    cli.write_text(
        "import sys,json\n"
        "for line in sys.stdin:\n"
        " message=json.loads(line)\n"
        " print(json.dumps({'type':'result','subtype':'success','is_error':False,"
        "'result':message['message']['content']}),flush=True)\n"
    )
    polls = []

    def native_injection(agent):
        polls.append(agent)
        return agent.steer("operator follow-up") if len(polls) == 2 else False

    monkeypatch.setattr(kanban_tools, "inject_new_comments_from_env", native_injection)
    monkeypatch.setattr(claude_runner, "_apply_pre_llm_context", lambda command, _env: command)
    monkeypatch.setattr(claude_runner, "_liveness_grace_seconds", lambda: 60)
    monkeypatch.setattr(claude_runner, "sys", SimpleNamespace(stdout=SimpleNamespace(buffer=io.BytesIO())))
    code, output = claude_runner._run_once(
        [sys.executable, str(cli), "--print", "--output-format", "stream-json", "initial task"],
        {"HERMES_KANBAN_TASK": "comment-task", "WORKERS_CLAUDE_SESSION_ID": "original-session"},
    )
    results = [json.loads(line)["result"] for line in output.splitlines()]
    assert code == 0
    assert results == ["initial task", "operator follow-up"]
    assert polls[0].closed


def test_existing_streaming_caller_keeps_its_stdin(monkeypatch):
    seen = []

    def popen(command, **kwargs):
        seen.append((command, kwargs))
        return SimpleNamespace(stdout=io.BytesIO(), wait=lambda: 0)

    monkeypatch.setattr(claude_runner.subprocess, "Popen", popen)
    monkeypatch.setattr(claude_runner, "_apply_pre_llm_context", lambda command, _env: command)
    monkeypatch.setattr(claude_runner, "_liveness_grace_seconds", lambda: 60)
    command = ["claude", "--input-format", "stream-json", "--output-format", "stream-json", ""]
    assert claude_runner._run_once(command, {"HERMES_KANBAN_TASK": "caller-owned"}) == (0, "")
    assert seen[0][0] == command
    assert seen[0][1]["stdin"] is None
