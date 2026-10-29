from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from workers_core import common, config, fallbacks, process_group, registry
from workers_core.backends import claude_runner, claudecode


def _document():
    document = config.default_config()
    for backend, entry in document["workers"].items():
        entry["enabled"] = backend == "claude-code"
    document["workers"]["claude-code"]["model"] = "test-provider/test-model"
    return document


def test_legacy_native_default_does_not_override_enabled_codex():
    document = config.default_config()
    document["default_worker"] = "native"
    document["workers"]["codex"].update(
        enabled=True, model="fixture-codex-model"
    )

    assert registry.resolve_backend(
        SimpleNamespace(assignee="default"), document
    ) == "codex"


@pytest.mark.parametrize("failure_status", [429, 404])
def test_credential_retry_and_provider_fallback_resume_the_original_session(
    monkeypatch, failure_status
):
    seen = []
    results = iter([
        (1, json.dumps({"type": "result", "api_error_status": failure_status,
                        "session_id": "original-session"})),
        (0, '{"type":"result","result":"finished","session_id":"original-session"}'),
    ])

    def run(command, *_args):
        seen.append(list(command))
        return next(results)

    monkeypatch.setattr(claude_runner, "_session_claim_taken", False)
    monkeypatch.setattr(claude_runner, "_run_routed", run)
    monkeypatch.setattr(claude_runner, "rotate_credential", lambda _payload: {
        "credential_id": "second-key", "key": "key-2",
    })
    goal_sessions = []
    monkeypatch.setattr(claude_runner, "_run_goal", lambda *args: (
        goal_sessions.append(args[4]["session_id"]) or 0
    ))
    command = ["claude", "--print", "--session-id", "original-session",
               "work kanban task same-id"]
    runtime = {"provider": "provider-a", "model": "model-a",
               "base_url": "https://provider.test", "api_key": "key-1"}
    payload = {"command": command, "runtime": runtime,
               "goal": {"enabled": True, "session_id": "original-session"}}
    if failure_status == 404:
        payload["attempts"] = [
            {"command": command, "runtime": runtime},
            {"command": command, "runtime": {**runtime, "model": "model-b"}},
        ]

    assert claude_runner.run_payload(payload) == 0
    assert seen[0] == command
    assert "--session-id" not in seen[1]
    assert seen[1][seen[1].index("--resume") + 1] == "original-session"
    assert seen[1][-1] != command[-1]
    assert goal_sessions == ["original-session"]


@pytest.mark.parametrize("fail_spawn", [False, True])
def test_real_profile_scope_restores_a_after_b_and_keeps_dispatch_board(
    tmp_path, monkeypatch, fail_spawn
):
    from agent import runtime_cwd
    from agent.secret_scope import get_secret
    from hermes_constants import get_hermes_home
    from hermes_cli.config import load_config
    from hermes_cli import profiles

    homes = {"default": tmp_path / "home", "b": tmp_path / "home" / "profiles" / "b"}
    workspaces = {}
    for name, home in homes.items():
        home.mkdir(parents=True)
        (home / ".env").write_text(f"AUDIT_PROVIDER_TOKEN={name}-token\n")
        (home / "config.yaml").write_text(
            f"model:\n  provider: provider-{name}\n  default: model-{name}\n"
            f"fallback_providers:\n  - provider: provider-{name}\n    model: fallback-{name}\n"
            "toolsets: [skills, kanban]\n"
        )
        skill = home / "skills" / "audit-skill" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text(f"---\nname: audit-skill\ndescription: {name} profile skill\n---\nInstructions.\n")
        workspace = tmp_path / f"workspace-{name}"
        workspace.mkdir()
        workspaces[name] = workspace

    monkeypatch.setenv("HERMES_HOME", str(homes["default"]))
    monkeypatch.setenv("AUDIT_PROVIDER_TOKEN", "default-token")
    monkeypatch.setattr(profiles, "resolve_profile_env", lambda name: str(homes[name]))
    monkeypatch.setattr(registry, "load_config", _document)
    monkeypatch.setattr(common, "worker_runner_command", lambda _module: [sys.executable])
    monkeypatch.setattr(process_group, "supervise", lambda *_args, **_kwargs: None)
    observed = []

    def spawn(launch, **_kwargs):
        name = launch.task.assignee
        assert get_hermes_home() == homes[name]
        assert runtime_cwd.scoped_session_cwd() == str(workspaces[name])
        assert load_config()["model"]["provider"] == f"provider-{name}"
        assert get_secret("AUDIT_PROVIDER_TOKEN") == f"{name}-token"
        assert launch.env["AUDIT_PROVIDER_TOKEN"] == f"{name}-token"
        assert f"{name} profile skill" in launch.prompt
        assert fallbacks.fallback_entries_for("unused", "unused")[0]["model"] == f"fallback-{name}"
        assert Path(launch.env["HERMES_KANBAN_DB"]).is_relative_to(homes["default"])
        observed.append(name)
        if fail_spawn and name == "b":
            raise RuntimeError("synthetic spawn failure")
        return 1234

    monkeypatch.setattr(registry, "backend_module", lambda _backend: SimpleNamespace(spawn=spawn))
    token = runtime_cwd.set_session_cwd("outer-workspace")
    try:
        for name in ("default", "b", "default"):
            task = SimpleNamespace(id="same-id", assignee=name, skills=["audit-skill"])
            if fail_spawn and name == "b":
                with pytest.raises(RuntimeError, match="synthetic spawn failure"):
                    registry.spawn_task(task, str(workspaces[name]), board="dispatch-board")
            else:
                assert registry.spawn_task(task, str(workspaces[name]), board="dispatch-board") == 1234
            assert get_hermes_home() == homes["default"]
            assert get_secret("AUDIT_PROVIDER_TOKEN") == "default-token"
            assert runtime_cwd.scoped_session_cwd() == "outer-workspace"
    finally:
        runtime_cwd.reset_session_cwd(token)
    assert observed == ["default", "b", "default"]


@pytest.mark.parametrize("runner_failure", [False, True])
def test_same_task_on_two_boards_has_independent_files_and_cleanup(
    tmp_path, monkeypatch, runner_failure
):
    monkeypatch.setattr(claudecode, "hermes_root", lambda: tmp_path)
    monkeypatch.setattr(process_group, "supervise", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(claudecode, "_resolve_binary", lambda _value: sys.executable)
    monkeypatch.setattr(claudecode, "_cli_flags", lambda _binary: frozenset())
    monkeypatch.setattr(claudecode, "worker_runner_command", lambda _module: [sys.executable])
    monkeypatch.setattr(claudecode, "worker_log", lambda *_args: tmp_path / "worker.log")
    monkeypatch.setattr(claudecode, "fallback_entries_for", lambda *_args: [])
    monkeypatch.setattr(claudecode, "_provider_runtime", lambda provider, model: {
        "provider": provider, "model": model, "api_mode": "anthropic_messages",
        "base_url": "https://provider.test", "api_key": "test-key", "credential_id": "test-id",
    })
    payloads = []

    class Pipe(io.BytesIO):
        def close(self):
            payloads.append(json.loads(self.getvalue()))
            super().close()

    monkeypatch.setattr(claudecode.subprocess, "Popen", lambda *_args, **_kwargs: (
        SimpleNamespace(pid=1234, stdin=Pipe())
    ))
    for board in ("first", "second"):
        task = SimpleNamespace(id="same-id", assignee="default", current_run_id=1)
        launch = common.WorkerLaunchContext(
            task=task, workspace=str(tmp_path), board=board, session_id=f"session-{board}",
            env={"HERMES_HOME": str(tmp_path)}, prompt=f"context-{board}",
            mcp_servers={"server": {"command": f"server-{board}"}},
        )
        assert claudecode.spawn(launch, config=_document()) == 1234

    first, second = payloads
    first_dir, second_dir = Path(first["cleanup_dir"]), Path(second["cleanup_dir"])
    assert first_dir != second_dir
    assert first_dir.stat().st_mode & 0o777 == 0o700
    assert second_dir.stat().st_mode & 0o777 == 0o700
    assert json.loads(Path(first["cleanup_paths"][0]).read_text())["mcpServers"]["server"]["command"] == "server-first"
    assert Path(first["cleanup_paths"][1]).read_text() == "context-first"
    assert Path(second["cleanup_paths"][1]).read_text() == "context-second"

    monkeypatch.setattr(claude_runner.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(json.dumps(first).encode())))
    monkeypatch.setattr(claude_runner, "install_terminate_handlers", lambda _get_child: None)
    def finish(_payload):
        if runner_failure:
            raise RuntimeError("synthetic runner failure")
        return 0

    monkeypatch.setattr(claude_runner, "run_payload", finish)
    assert claude_runner.main() == int(runner_failure)
    assert not first_dir.exists()
    assert second_dir.is_dir()
    assert Path(second["cleanup_paths"][1]).read_text() == "context-second"
