from __future__ import annotations

import json
import sys
from types import SimpleNamespace

from workers_core.backends import codex
from workers_core import codex_runner


def _task():
    return SimpleNamespace(
        id="t_codex",
        model_override="",
        provider_override="",
        reasoning_effort="",
    )


def test_build_command_requests_json_events(tmp_path):
    binary = tmp_path / "codex"
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(0o755)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    env = {"HERMES_KANBAN_DB": str(tmp_path / "kanban.db")}
    entry = {
        "binary": str(binary),
        "model": "gpt-test",
        "sandbox": "workspace-write",
        "ignore_user_config": True,
        "ephemeral": True,
        "network_access": False,
    }

    command = codex.build_command(_task(), str(workspace), entry, env, {})

    assert command[command.index("exec") + 1] == "--json"
    assert command[-1] == "-"


def _run_script(tmp_path, body: str):
    script = tmp_path / "fake_codex.py"
    script.write_text(body, encoding="utf-8")
    status = tmp_path / "status.json"
    payload = {
        "command": [sys.executable, str(script)],
        "env": {"HERMES_KANBAN_TASK": "t_codex"},
        "workspace": str(tmp_path),
        "model": "gpt-test",
        "status_path": str(status),
        "prompt": "go",
    }
    return codex_runner.run_payload(payload), status


def test_codex_runner_records_failure_without_actions(tmp_path, capsys):
    code, status = _run_script(
        tmp_path,
        "import json, sys\n"
        "print(json.dumps({'type':'thread.started','thread_id':'t'}))\n"
        "print(json.dumps({'type':'item.completed','item':{'id':'1','type':'agent_message','text':'working'}}))\n"
        "print(json.dumps({'type':'turn.failed','error':{'message':'unexpected status 403: This model is not available in your country'}}))\n"
        "sys.exit(1)\n",
    )

    assert code == 1
    record = json.loads(status.read_text(encoding="utf-8"))
    assert record["status_code"] is None
    assert record["should_rotate_credential"] is False
    assert record["should_fallback"] is False
    assert record["event"] == "turn.failed"
    assert "403" in record["message"]
    assert record["model"] == "gpt-test"
    out = capsys.readouterr().out
    assert "working" in out
    assert "Codex failure recorded" in out


def test_codex_runner_success_writes_no_status(tmp_path, capsys):
    code, status = _run_script(
        tmp_path,
        "import json\n"
        "print(json.dumps({'type':'item.completed','item':{'id':'1','type':'agent_message','text':'done'}}))\n"
        "print(json.dumps({'type':'turn.completed','usage':{}}))\n",
    )

    assert code == 0
    assert not status.exists()
    elapsed = capsys.readouterr().out
    assert "done" in elapsed
    assert "failure recorded" not in elapsed


def test_codex_runner_passes_non_json_lines_through(tmp_path, capsys):
    code, status = _run_script(tmp_path, "print('plain codex diagnostic')\n")

    assert code == 0
    assert not status.exists()
    assert "plain codex diagnostic" in capsys.readouterr().out


def test_codex_runner_clears_a_stale_failure_record(tmp_path):
    script = tmp_path / "fake_codex.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    status = tmp_path / "status.json"
    status.write_text('{"old": true}\n', encoding="utf-8")
    payload = {
        "command": [sys.executable, str(script)],
        "env": {"HERMES_KANBAN_TASK": "t_codex"},
        "workspace": str(tmp_path),
        "model": "gpt-test",
        "status_path": str(status),
        "prompt": "go",
    }

    assert codex_runner.run_payload(payload) == 0
    assert not status.exists()
