from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from workers_core import config
from workers_core.common import WorkerLaunchContext
from workers_core.backends import qoder


def fake_task(**patch):
    value = {"id": "t_qoder", "goal_mode": False}
    value.update(patch)
    return SimpleNamespace(**value)


def fake_binary(tmp_path: Path) -> Path:
    binary = tmp_path / "qoderclicn"
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(0o755)
    return binary


def fake_launch(tmp_path: Path, *, env=None, mcp_servers=None) -> WorkerLaunchContext:
    return WorkerLaunchContext(
        task=fake_task(),
        workspace=str(tmp_path),
        board=None,
        session_id="session-qoder",
        env=dict(env or {}),
        mcp_servers=dict(mcp_servers or {}),
        prompt="worker prompt",
    )


def test_defaults_and_settings_validation():
    entry = qoder.defaults()
    assert entry["enabled"] is False
    assert entry["model"] == ""
    assert entry["permission_mode"] == "auto"
    assert entry["binary"]
    normalized = qoder.validate_settings(
        {**entry, "future_backend_setting": {"retain": True}}
    )
    assert normalized["future_backend_setting"] == {"retain": True}
    with pytest.raises(ValueError, match="permission_mode"):
        qoder.validate_settings({**entry, "permission_mode": "always_ask"})
    with pytest.raises(ValueError, match="reasoning_effort"):
        qoder.validate_settings({**entry, "reasoning_effort": "extreme"})
    with pytest.raises(ValueError, match="binary is required"):
        qoder.validate_settings({**entry, "binary": ""})


def test_defaults_keep_the_stable_launcher_symlink(tmp_path, monkeypatch):
    payload = tmp_path / "qoderclicn-1.0.0"
    payload.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    launcher = tmp_path / "qoderclicn"
    launcher.symlink_to(payload)
    monkeypatch.setattr(
        qoder.shutil,
        "which",
        lambda name: str(launcher) if name == "qoderclicn" else None,
    )
    assert qoder._qoder_binary() == str(launcher)


def test_reasoning_options_are_from_the_qoder_cli():
    options = qoder.model_options({}, model="Auto")
    assert options["options"] == [
        "auto",
        "none",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
        "ultracode",
    ]
    assert options["default"] == ""
    assert options["source"] == "qoder-cli"


def test_build_command_is_headless_and_scoped(tmp_path):
    binary = fake_binary(tmp_path)
    entry = {**qoder.defaults(), "binary": str(binary)}
    mcp_path = tmp_path / "mcp.json"
    command = qoder.build_command(
        fake_task(), str(tmp_path), entry, mcp_path, "worker prompt"
    )
    assert command[0] == str(binary.resolve())
    assert "--print" in command
    assert command[command.index("--mcp-config") + 1] == str(mcp_path)
    assert "--strict-mcp-config" in command
    assert command[command.index("--output-format") + 1] == "text"
    assert command[command.index("--permission-mode") + 1] == "auto"
    assert command[command.index("--allowed-tools") + 1] == "mcp__hermes-worker__*"
    assert "--no-session-persistence" in command
    assert command[-1] == "worker prompt"
    assert "--dangerously-skip-permissions" not in command

    modeled = qoder.build_command(
        fake_task(),
        str(tmp_path),
        {**entry, "model": "DeepSeek-V4-Pro", "reasoning_effort": "high"},
        mcp_path,
        "prompt",
    )
    assert modeled[modeled.index("--model") + 1] == "DeepSeek-V4-Pro"
    assert modeled[modeled.index("--reasoning-effort") + 1] == "high"

    task_effort = qoder.build_command(
        fake_task(reasoning_effort="xhigh"),
        str(tmp_path),
        entry,
        mcp_path,
        "prompt",
    )
    assert task_effort[task_effort.index("--reasoning-effort") + 1] == "xhigh"

    goal = qoder.build_command(
        fake_task(goal_mode=True), str(tmp_path), entry, mcp_path, "prompt"
    )
    assert "--no-session-persistence" not in goal

    with pytest.raises(RuntimeError, match="unsupported Qoder reasoning effort"):
        qoder.build_command(
            fake_task(reasoning_effort="extreme"),
            str(tmp_path),
            entry,
            mcp_path,
            "prompt",
        )


def test_clean_env_removes_qoder_sdk_markers():
    cleaned = qoder._clean_env(
        {
            "HOME": "/tmp/home",
            "QODER_CONFIG_DIR": "/tmp/qoder",
            "QODER_AGENT_SDK_VERSION": "1.0.0",
            "QODER_AGENT_SDK_ENTRYPOINT": "/tmp/worker.mjs",
            "QODER_SDK_AUTH_PAYLOAD_FILE": "/tmp/auth.json",
            "QODER_SESSION_TYPE": "sdk",
            "QODERCN_SESSION_TYPE": "sdk",
        }
    )
    assert cleaned == {"HOME": "/tmp/home", "QODER_CONFIG_DIR": "/tmp/qoder"}


def test_list_models_parses_catalog(monkeypatch):
    entry = {**qoder.defaults(), "binary": "/tmp/qoderclicn"}
    monkeypatch.setattr(qoder, "_resolve_binary", lambda _value: "/tmp/qoderclicn")
    monkeypatch.setattr(
        qoder,
        "_run_cli",
        lambda _binary, _args, timeout: subprocess.CompletedProcess(
            [], 0, "MODEL\nAuto\nDeepSeek-V4-Pro\n", ""
        ),
    )
    rows = qoder.list_models({"workers": {"qoder": entry}})
    assert [row["id"] for row in rows] == ["Auto", "DeepSeek-V4-Pro"]

    monkeypatch.setattr(
        qoder,
        "_run_cli",
        lambda _binary, _args, timeout: subprocess.CompletedProcess(
            [], 1, "Not logged in. Run `qoderclicn login` to authenticate.", ""
        ),
    )
    with pytest.raises(RuntimeError, match="Not logged in"):
        qoder.list_models({"workers": {"qoder": entry}})


def test_doctor_reports_login_without_account_details(monkeypatch):
    entry = {**qoder.defaults(), "binary": "/tmp/qoderclicn"}
    monkeypatch.setattr(qoder, "_resolve_binary", lambda _value: "/tmp/qoderclicn")

    def run(_binary, args, timeout):
        if args == ["--version"]:
            return subprocess.CompletedProcess(args, 0, "1.1.81\n", "")
        return subprocess.CompletedProcess(
            args,
            0,
            "Version: 1.1.81\nUsername: private\nEmail: private@example.com\n",
            "",
        )

    monkeypatch.setattr(qoder, "_run_cli", run)
    result = qoder.doctor({"workers": {"qoder": entry}})
    assert result["ok"] is True
    assert result["login"] == "Logged in"
    assert "private" not in json.dumps(result)

    monkeypatch.setattr(
        qoder,
        "_run_cli",
        lambda _binary, args, timeout: subprocess.CompletedProcess(
            args, 0, "Version: 1.1.81\nNot logged in\n", ""
        ),
    )
    result = qoder.doctor({"workers": {"qoder": entry}})
    assert result["ok"] is False
    assert result["login_ok"] is False


def test_spawn_writes_private_mcp_config_and_sanitizes_env(tmp_path, monkeypatch):
    binary = fake_binary(tmp_path)
    entry = {
        **qoder.defaults(),
        "binary": str(binary),
        "model": "Auto",
    }
    document = {**config.default_config(), "workers": {"qoder": entry}}
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    log_path = tmp_path / "worker.log"
    captured = {}

    class FakeProcess:
        pid = 4321

    def popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(qoder.subprocess, "Popen", popen)
    monkeypatch.setattr(qoder, "worker_log", lambda *_args, **_kwargs: log_path)
    monkeypatch.setattr(qoder, "hermes_root", lambda: tmp_path)
    from workers_core import process_group

    monkeypatch.setattr(
        process_group,
        "supervise",
        lambda pid, **kwargs: captured.update(supervise=(pid, kwargs)),
    )

    launch = fake_launch(
        workspace,
        env={
            "HOME": str(tmp_path),
            "QODER_AGENT_SDK_VERSION": "1.0.0",
            "QODER_SESSION_TYPE": "sdk",
        },
        mcp_servers={
            "hermes-worker": {
                "command": "/usr/bin/python3",
                "args": ["-m", "workers_core.capability_mcp"],
            }
        },
    )
    assert qoder.spawn(launch, config=document) == 4321

    kwargs = captured["kwargs"]
    assert kwargs["cwd"] == str(workspace)
    assert kwargs["start_new_session"] is True
    assert "QODER_AGENT_SDK_VERSION" not in kwargs["env"]
    assert "QODER_SESSION_TYPE" not in kwargs["env"]
    assert kwargs["env"]["HOME"] == str(tmp_path)
    assert captured["command"][-1] == "worker prompt"
    assert captured["supervise"][0] == 4321
    run_dir = Path(captured["supervise"][1]["cleanup_dir"])
    mcp_path = run_dir / "mcp.json"
    payload = json.loads(mcp_path.read_text(encoding="utf-8"))
    assert payload["mcpServers"]["hermes-worker"]["command"] == "/usr/bin/python3"
    assert mcp_path.stat().st_mode & 0o777 == 0o600
