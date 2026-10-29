from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen

import pytest

from workers_core import config
from workers_core import credentials
from workers_core import fallbacks
from workers_core import provider_catalog
from workers_core import capability_mcp
from workers_core.common import WorkerLaunchContext
from workers_core.backends import (
    claude_proxy,
    claude_runner,
    claudecode,
    codex,
    opencode,
    opencode_runner,
)


def fake_task(**patch):
    value = {
        "id": "t_test",
        "assignee": "default",
        "model_override": None,
        "provider_override": None,
        "reasoning_effort": None,
    }
    value.update(patch)
    return SimpleNamespace(**value)


def fake_launch(task, workspace, *, env=None, mcp_servers=None, prompt="worker prompt"):
    return WorkerLaunchContext(
        task=task,
        workspace=str(workspace),
        board=None,
        session_id="session-1",
        env=dict(env or {}),
        mcp_servers=dict(mcp_servers or {}),
        prompt=prompt,
    )


def test_codex_command_is_isolated(tmp_path, monkeypatch):
    binary = tmp_path / "codex"
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(0o755)
    entry = config.default_config()["workers"]["codex"]
    entry["binary"] = str(binary)
    entry["model"] = "fixture-codex-model"
    monkeypatch.setattr(codex, "_is_git_repo", lambda _path: True)
    env = {"HERMES_KANBAN_DB": str(tmp_path / "kanban.db")}
    servers = {
        "hermes-worker": {
            "command": "/usr/bin/python3",
            "args": ["-m", "workers_core.capability_mcp"],
        }
    }
    command = codex.build_command(fake_task(), str(tmp_path), entry, env, servers)
    joined = " ".join(command)

    assert "--ask-for-approval never" in joined
    assert "--sandbox workspace-write" in joined
    assert "--ignore-user-config" in command
    import tomllib
    mcp_override = next(value for value in command if value.startswith("mcp_servers="))
    server = tomllib.loads(mcp_override)["mcp_servers"]["hermes-worker"]
    assert server["enabled"] is True
    assert server["default_tools_approval_mode"] == "approve"
    assert "dangerously-bypass" not in joined


@pytest.mark.parametrize(
    ("service_tier", "expected_override"),
    [
        (None, None),
        ("", None),
        ("priority", 'service_tier="priority"'),
        ("future-native-tier", 'service_tier="future-native-tier"'),
    ],
)
def test_codex_service_tier_is_opt_in_and_passes_explicit_values(
    tmp_path, monkeypatch, service_tier, expected_override
):
    binary = tmp_path / "codex"
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(0o755)
    entry = config.default_config()["workers"]["codex"]
    entry.update(binary=str(binary), model="fixture-codex-model", reasoning_effort="high")
    if service_tier is not None:
        entry["service_tier"] = service_tier
    monkeypatch.setattr(codex, "_is_git_repo", lambda _path: True)

    command = codex.build_command(
        fake_task(),
        str(tmp_path),
        entry,
        {"HERMES_KANBAN_DB": str(tmp_path / "kanban.db")},
    )

    overrides = [arg for arg in command if arg.startswith("service_tier=")]
    assert overrides == ([expected_override] if expected_override else [])
    assert command[command.index("--model") + 1] == "fixture-codex-model"
    assert 'model_reasoning_effort="high"' in command
    assert command[command.index("--ask-for-approval") + 1] == "never"


def test_opencode_worker_config_loads_runtime_plugin(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    base = tmp_path / "opencode.jsonc"
    base.write_text('{"model": "user/current-model", "plugin": [], "mcp": {}}\n', encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("OPENCODE_CONFIG", str(base))

    target = opencode._worker_config(
        "opencode-go/deepseek-v4-flash",
        provider_overlays={},
        mcp_servers={"hermes-worker": {"command": "/usr/bin/python3"}},
    )
    value = json.loads(target.read_text(encoding="utf-8"))

    assert value["model"] == "user/current-model"
    assert "hermes-worker" in value["mcp"]
    plugin_paths = [item for item in value["plugin"] if item.endswith("/opencode/index.js")]
    assert plugin_paths
    for item in plugin_paths:
        assert Path(unquote(urlsplit(item).path)).is_file()



def test_opencode_provider_overlay_preserves_active_model_and_excludes_keys(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    base = tmp_path / "opencode.jsonc"
    base.write_text(
        json.dumps(
            {
                "model": "opencode-go/current-user-model",
                "provider": {"custom": {"name": "custom"}},
                "mcp": {},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("OPENCODE_CONFIG", str(base))
    overlays = {
        "opencode-go": {
            "npm": "@ai-sdk/openai-compatible",
            "name": "OpenCode Go",
            "options": {"baseURL": "https://opencode.ai/zen/go/v1"},
            "models": {"ox-alpha-free": {"name": "ox-alpha-free"}},
        }
    }

    target = opencode._worker_config(
        "opencode-go/ox-alpha-free", provider_overlays=overlays
    )
    value = json.loads(target.read_text(encoding="utf-8"))

    assert value["model"] == "opencode-go/current-user-model"
    assert "custom" in value["provider"]
    assert value["provider"]["opencode-go"]["models"]["ox-alpha-free"]
    assert "api_key" not in target.read_text(encoding="utf-8").lower()


def test_opencode_provider_overlay_uses_hermes_runtime_metadata(monkeypatch):
    import hermes_cli.auth as auth

    monkeypatch.setattr(
        opencode,
        "resolve_model_runtime",
        lambda provider, model, **_kwargs: {
            "provider": provider,
            "model": model,
            "api_mode": "chat_completions",
            "base_url": "https://provider.test/v1",
        },
    )
    monkeypatch.setattr(
        auth,
        "PROVIDER_REGISTRY",
        {"provider-a": SimpleNamespace(name="Provider A")},
    )

    overlays, runtime = opencode._provider_overlay("provider-a", "model-a")

    assert runtime["api_mode"] == "chat_completions"
    assert overlays == {
        "provider-a": {
            "npm": "@ai-sdk/openai-compatible",
            "name": "Provider A",
            "options": {"baseURL": "https://provider.test/v1"},
            "models": {"model-a": {"name": "model-a"}},
        }
    }


def test_opencode_model_catalog_uses_hermes_catalog(monkeypatch):
    monkeypatch.setattr(
        opencode,
        "configured_model_rows",
        lambda **_kwargs: [
            {
                "id": "opencode-go/ox-alpha-free",
                "model": "ox-alpha-free",
                "provider": "opencode-go",
                "displayName": "opencode-go/ox-alpha-free",
            }
        ],
    )

    result = opencode.list_models(config.default_config())

    assert result == [
        {
            "id": "opencode-go/ox-alpha-free",
            "model": "ox-alpha-free",
            "provider": "opencode-go",
            "displayName": "opencode-go/ox-alpha-free",
        }
    ]


def test_fallback_entries_reuse_hermes_chain_and_skip_primary(monkeypatch):
    monkeypatch.setattr(
        fallbacks,
        "hermes_fallback_entries",
        lambda: [
            {"provider": "opencode-go", "model": "primary"},
            {"provider": "opencode-go", "model": "fallback"},
            {"provider": "opencode-go", "model": "fallback"},
        ],
    )

    assert fallbacks.fallback_entries_for("opencode-go", "primary") == [
        {"provider": "opencode-go", "model": "fallback"}
    ]


def test_fallback_chain_status_distinguishes_unreadable_from_unconfigured(monkeypatch):
    """A moved Hermes API must not look like a chain the operator left empty."""
    import hermes_cli.config as hermes_config

    def boom():
        raise RuntimeError("fallback_config moved")

    monkeypatch.setattr(hermes_config, "load_config", boom)

    status = fallbacks.fallback_chain_status()

    assert status["readable"] is False
    assert "fallback_config moved" in status["error"]
    assert fallbacks.hermes_fallback_entries() == []


def test_fallback_chain_status_reports_an_empty_chain_as_readable(monkeypatch):
    import hermes_cli.config as hermes_config
    import hermes_cli.fallback_config as hermes_fallback_config

    monkeypatch.setattr(hermes_config, "load_config", lambda: {})
    monkeypatch.setattr(hermes_fallback_config, "get_fallback_chain", lambda _cfg: [])

    assert fallbacks.fallback_chain_status() == {
        "readable": True,
        "entries": [],
        "error": "",
    }


def test_opencode_runner_switches_model_only_on_provider_failure(monkeypatch):
    commands: list[list[str]] = []
    results = iter([(1, "HTTP 429 rate limit"), (0, "ok")])

    def fake_run_once(command, _env):
        commands.append(command)
        return next(results)

    monkeypatch.setattr(opencode_runner, "_run_once", fake_run_once)
    monkeypatch.setattr(
        opencode_runner,
        "_reported",
        lambda _task_id, _env=None: {"status_code": 429, "should_fallback": True},
    )
    payload = {
        "attempts": [
            {"command": ["opencode", "--model", "primary"], "provider": "p", "model": "primary"},
            {"command": ["opencode", "--model", "fallback"], "provider": "p", "model": "fallback"},
        ]
    }

    assert opencode_runner.run_payload(payload) == 0
    assert commands == [
        ["opencode", "--model", "primary"],
        ["opencode", "--model", "fallback"],
    ]


def test_opencode_runner_derives_fallback_from_a_legacy_status(monkeypatch):
    commands: list[list[str]] = []
    results = iter([(1, "boom"), (0, "ok")])

    def fake_run_once(command, _env):
        commands.append(command)
        return next(results)

    monkeypatch.setattr(opencode_runner, "_run_once", fake_run_once)
    monkeypatch.setattr(
        opencode_runner, "_reported", lambda _task_id, _env=None: {"status_code": 429}
    )
    payload = {
        "attempts": [
            {"command": ["opencode", "--model", "primary"]},
            {"command": ["opencode", "--model", "fallback"]},
        ]
    }

    assert opencode_runner.run_payload(payload) == 0
    assert len(commands) == 2


def test_opencode_runner_reads_the_attempt_status_file(monkeypatch, tmp_path):
    status = tmp_path / "run" / "status.json"
    status.parent.mkdir()
    calls: list[list[str]] = []
    results = iter([(1, "blocked"), (0, "ok")])

    def fake_run_once(command, env):
        calls.append(command)
        if len(calls) == 1:
            Path(env["WORKERS_OPENCODE_STATUS"]).write_text(
                json.dumps({"status_code": 403, "should_fallback": True}),
                encoding="utf-8",
            )
        return next(results)

    monkeypatch.setattr(opencode_runner, "_run_once", fake_run_once)
    env = {"HERMES_KANBAN_TASK": "t_status", "WORKERS_OPENCODE_STATUS": str(status)}
    payload = {
        "attempts": [
            {"command": ["opencode", "--model", "primary"], "env": dict(env)},
            {"command": ["opencode", "--model", "fallback"], "env": dict(env)},
        ]
    }

    assert opencode_runner.run_payload(payload) == 0
    assert [command[2] for command in calls] == ["primary", "fallback"]
    assert not status.exists()


def test_opencode_runner_does_not_replay_task_failure(monkeypatch):
    calls = 0

    def fake_run_once(_command, _env):
        nonlocal calls
        calls += 1
        return 1, "tool execution failed"

    monkeypatch.setattr(opencode_runner, "_run_once", fake_run_once)
    payload = {
        "attempts": [
            {"command": ["opencode", "--model", "primary"]},
            {"command": ["opencode", "--model", "fallback"]},
        ]
    }

    assert opencode_runner.run_payload(payload) == 1
    assert calls == 1


def test_opencode_command_uses_no_agent_unless_configured(tmp_path):
    command = opencode._build_command(fake_task(), str(tmp_path), {}, "provider/model", "prompt")
    assert "--agent" not in command
    command = opencode._build_command(
        fake_task(), str(tmp_path), {"agent": "custom-agent"}, "provider/model", "prompt"
    )
    assert command[command.index("--agent") + 1] == "custom-agent"
    assert opencode.defaults()["agent"] == ""


@pytest.mark.parametrize("inherit_fallbacks", [True, False])
def test_opencode_spawn_passes_hermes_fallback_attempts_to_runner(tmp_path, monkeypatch, inherit_fallbacks):
    from workers_core import process_group
    monkeypatch.setattr(process_group, "supervise", lambda *_args, **_kwargs: None)
    binary = tmp_path / "opencode"
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(0o755)
    document = config.default_config()
    document["inheritance"]["fallback_models"] = inherit_fallbacks
    document["workers"]["opencode"].update(
        {"binary": str(binary), "model": "provider-a/primary", "inherit_project_config": False}
    )
    task = fake_task(skills=[])
    monkeypatch.setattr(
        opencode,
        "fallback_entries_for",
        lambda _provider, _model: [{"provider": "provider-b", "model": "fallback"}] if inherit_fallbacks else pytest.fail("Disabled fallback chain must not be read"),
    )
    monkeypatch.setattr(opencode, "require_credentials", lambda _provider, model=None: {})
    monkeypatch.setattr(
        opencode,
        "_provider_overlay",
        lambda provider, model, _fallback=None: (
            {
                provider: {
                    "npm": "@ai-sdk/openai-compatible",
                    "name": provider,
                    "options": {"baseURL": f"https://{provider}.test/v1"},
                    "models": {model: {"name": model}},
                }
            },
            {
                "api_mode": "chat_completions",
                "base_url": f"https://{provider}.test/v1",
            },
        ),
    )
    monkeypatch.setattr(
        opencode, "_worker_config", lambda *_args, **_kwargs: tmp_path / "oc.json"
    )
    monkeypatch.setattr(opencode, "_source_root", lambda: tmp_path)
    log_path = tmp_path / "worker.log"
    monkeypatch.setattr(opencode, "worker_log", lambda *_args, **_kwargs: log_path)

    class FakeStdin:
        def __init__(self):
            self.data = bytearray()

        def write(self, value):
            self.data.extend(value)

        def close(self):
            pass

    class FakeProcess:
        pid = 4321

        def __init__(self):
            self.stdin = FakeStdin()

        def terminate(self):
            pass

    captured = {}

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["env"] = dict(kwargs["env"])
        captured["process"] = FakeProcess()
        return captured["process"]

    monkeypatch.setattr(opencode.subprocess, "Popen", fake_popen)

    launch = fake_launch(
        task,
        tmp_path,
        env={"HERMES_KANBAN_DB": str(tmp_path / "kanban.db")},
    )
    assert opencode.spawn(launch, config=document) == 4321
    assert captured["env"]["OPENCODE_DISABLE_PROJECT_CONFIG"] == "true"
    if not inherit_fallbacks:
        assert captured["command"][0] == str(binary)
        assert not captured["process"].stdin.data
        return
    assert "workers_core.backends.opencode_runner" in captured["command"][-1]
    payload = json.loads(bytes(captured["process"].stdin.data).decode("utf-8"))
    assert [attempt["model"] for attempt in payload["attempts"]] == [
        "primary",
        "fallback",
    ]


@pytest.mark.parametrize("inherit_user", [True, False])
def test_opencode_user_config_copy_is_optional_and_preserves_task_mcp(tmp_path, monkeypatch, inherit_user):
    user = tmp_path / "user.json"
    user.write_text(json.dumps({"theme": "user-theme", "mcp": {"user-server": {"type": "local", "command": ["user-tool"]}}}))
    monkeypatch.setenv("OPENCODE_CONFIG", str(user))
    monkeypatch.setattr(opencode, "hermes_root", lambda: tmp_path)
    before = user.read_bytes()
    result = opencode._worker_config("p/m", provider_overlays={}, mcp_servers={"hermes-worker": {"command": "bridge"}}, inherit_user_config=inherit_user)
    data = json.loads(result.read_text())
    assert ("theme" in data) is inherit_user
    assert ("user-server" in data["mcp"]) is inherit_user
    assert "hermes-worker" in data["mcp"]
    assert user.read_bytes() == before


def test_claude_native_sources_and_skills_are_explicit_and_fail_when_unsupported(tmp_path, monkeypatch):
    monkeypatch.setattr(claudecode, "_resolve_binary", lambda _: "claude")
    flags = frozenset({"--strict-mcp-config", "--setting-sources", "--disable-slash-commands"})
    monkeypatch.setattr(claudecode, "_cli_flags", lambda _: flags)
    entry = {"model": "provider/model", "strict_mcp_config": True, "settings_sources": [], "native_skills": False}
    command = claudecode.build_command(fake_task(), str(tmp_path), entry, {}, tmp_path / "mcp.json")
    assert "--strict-mcp-config" in command and "--disable-slash-commands" in command
    assert command[command.index("--setting-sources") + 1] == ""
    monkeypatch.setattr(claudecode, "_cli_flags", lambda _: frozenset({"--print"}))
    for selected in [{"strict_mcp_config": True}, {"settings_sources": ["project"]}, {"native_skills": False}]:
        with pytest.raises(RuntimeError, match="cannot"):
            claudecode.build_command(fake_task(), str(tmp_path), {"model": "provider/model", **selected}, {}, tmp_path / "mcp.json")


def test_claude_command_keeps_sessions_unless_opted_out(tmp_path, monkeypatch):
    monkeypatch.setattr(claudecode, "_resolve_binary", lambda _: "claude")
    monkeypatch.setattr(
        claudecode, "_cli_flags", lambda _: frozenset({"--no-session-persistence"})
    )
    entry = {"model": "provider/model"}
    command = claudecode.build_command(fake_task(), str(tmp_path), entry, {}, tmp_path / "mcp.json")
    assert "--no-session-persistence" not in command
    opted_out = claudecode.build_command(
        fake_task(), str(tmp_path), {**entry, "no_session_persistence": True}, {}, tmp_path / "mcp.json"
    )
    assert "--no-session-persistence" in opted_out
    assert claudecode.defaults()["no_session_persistence"] is False


def test_claude_code_command_uses_task_mcp_and_print_mode(tmp_path, monkeypatch):
    binary = tmp_path / "claude"
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(0o755)
    entry = {
        "binary": str(binary),
        "model": "anthropic/sonnet",
        "reasoning_effort": "high",
        "permission_mode": "bypassPermissions",
        "no_session_persistence": True,
        "tools": [],
        "disallowed_tools": ["Bash", "Write"],
    }
    task = fake_task()
    command = claudecode.build_command(
        task,
        str(tmp_path),
        entry,
        {},
        tmp_path / "mcp.json",
        prompt="Call kanban_show()",
    )

    assert command[0] == str(binary)
    assert command[1:3] == ["--print", "--model"]
    assert "sonnet" in command
    assert ["--mcp-config", str(tmp_path / "mcp.json")] == command[command.index("--mcp-config") : command.index("--mcp-config") + 2]
    assert "--strict-mcp-config" not in command
    assert command[command.index("--output-format") + 1] == "stream-json"
    assert "--verbose" in command
    assert "--permission-mode" in command
    assert command[command.index("--tools") + 1] == ""
    assert command[command.index("--disallowedTools") + 1] == "Bash,Write"
    assert "--max-turns" not in command
    assert "--no-session-persistence" in command
    assert command[-1] == "Call kanban_show()"


def claude_help_stub(tmp_path, help_text, name="claude"):
    binary = tmp_path / name
    binary.write_text(
        "#!/bin/sh\nif [ \"$1\" = \"--help\" ]; then cat <<'EOF'\n"
        + help_text
        + "\nEOF\nfi\nexit 0\n",
        encoding="utf-8",
    )
    binary.chmod(0o755)
    return binary


def test_cli_flags_expands_bracketed_help_abbreviations(tmp_path):
    binary = claude_help_stub(
        tmp_path,
        "Usage: claude [options]\n"
        "  --append-system-prompt <prompt>   Append a system prompt\n"
        "  --system-prompt[-file], --append-system-prompt[-file], --add-dir\n",
    )

    flags = claudecode._cli_flags(str(binary))

    assert "--append-system-prompt" in flags
    assert "--append-system-prompt-file" in flags
    assert "--system-prompt-file" in flags
    assert "--append-system-promptfile" not in flags


def test_cli_flags_still_fail_open_when_help_has_no_options(tmp_path):
    binary = claude_help_stub(tmp_path, "claude: no options documented")

    assert claudecode._supports(claudecode._cli_flags(str(binary)), "--effort")


def test_claude_code_command_prefers_system_prompt_file(tmp_path):
    binary = claude_help_stub(
        tmp_path,
        "  --print                    Non-interactive mode\n"
        "  --model <model>           Model for the session\n"
        "  --mcp-config <file>       MCP config\n"
        "  --output-format <format>  Output format\n"
        "  --verbose                 Verbose logging\n"
        "  --permission-mode <mode>  Permission mode\n"
        "  --append-system-prompt[-file]\n",
    )
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("PROFILE SECRET PROMPT BODY", encoding="utf-8")
    entry = {"binary": str(binary), "model": "anthropic/sonnet"}

    command = claudecode.build_command(
        fake_task(),
        str(tmp_path),
        entry,
        {},
        tmp_path / "mcp.json",
        prompt="Work the card",
        system_prompt_path=prompt_file,
    )

    assert command[command.index("--append-system-prompt-file") + 1] == str(prompt_file)
    assert "--append-system-prompt" not in command
    assert "PROFILE SECRET PROMPT BODY" not in command


def test_claude_code_command_inlines_system_prompt_without_file_flag(tmp_path):
    binary = claude_help_stub(
        tmp_path,
        "  --print                    Non-interactive mode\n"
        "  --model <model>           Model for the session\n"
        "  --mcp-config <file>       MCP config\n"
        "  --append-system-prompt <prompt>  Append a system prompt\n",
    )
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("INLINE BODY", encoding="utf-8")
    entry = {"binary": str(binary), "model": "anthropic/sonnet"}

    command = claudecode.build_command(
        fake_task(),
        str(tmp_path),
        entry,
        {},
        tmp_path / "mcp.json",
        prompt="Work the card",
        system_prompt_path=prompt_file,
    )

    assert "--append-system-prompt-file" not in command
    assert command[command.index("--append-system-prompt") + 1] == "INLINE BODY"


def test_cli_flags_detects_camel_case_aliases(tmp_path):
    binary = claude_help_stub(
        tmp_path,
        "  --allowedTools, --allowed-tools <tools...>\n"
        "  --disallowedTools, --disallowed-tools <tools...>\n",
    )

    flags = claudecode._cli_flags(str(binary))

    assert "--allowedTools" in flags
    assert "--disallowedTools" in flags
    # The old lowercase-only pattern truncated these at the capital, leaving keys
    # that no CLI accepts.
    assert "--allowed" not in flags
    assert "--disallowed" not in flags


def test_cli_flags_refresh_after_in_place_upgrade(tmp_path):
    binary = claude_help_stub(tmp_path, "  --effort <level>  Effort level\n")
    assert "--new-flag" not in claudecode._cli_flags(str(binary))

    binary.write_text(
        "#!/bin/sh\nif [ \"$1\" = \"--help\" ]; then echo '  --new-flag <x>  New'; fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    binary.chmod(0o755)

    assert "--new-flag" in claudecode._cli_flags(str(binary))


def test_claude_code_command_gates_unsupported_flags(tmp_path):
    full_help = (
        "  --print\n  --model <model>\n  --mcp-config <file>\n"
        "  --strict-mcp-config\n  --tools <tools...>\n"
        "  --disallowedTools, --disallowed-tools <tools...>\n"
        "  --permission-mode <mode>\n  --no-session-persistence\n"
    )
    entry = {
        "binary": str(claude_help_stub(tmp_path, full_help, name="claude-new")),
        "model": "anthropic/sonnet",
        "strict_mcp_config": True,
        "tools": ["Read"],
        "disallowed_tools": ["Bash"],
        "permission_mode": "bypassPermissions",
        "no_session_persistence": True,
    }
    task = fake_task()

    supported = claudecode.build_command(
        task, str(tmp_path), entry, {}, tmp_path / "mcp.json", prompt="go"
    )
    partial = claudecode.build_command(
        task,
        str(tmp_path),
        dict(entry, strict_mcp_config=False, binary=str(claude_help_stub(tmp_path, "  --print\n  --model <m>\n",
                                                name="claude-old"))),
        {},
        tmp_path / "mcp.json",
        prompt="go",
    )

    for flag in ("--strict-mcp-config", "--tools", "--disallowedTools",
                 "--permission-mode", "--no-session-persistence"):
        assert flag in supported
        assert flag not in partial
    assert partial[partial.index("--model") + 1] == "sonnet"


def test_claude_code_provider_env_uses_hermes_credential(monkeypatch):
    monkeypatch.setattr(claudecode, "require_credentials", lambda provider, model=None: {"provider": provider})
    monkeypatch.setattr(
        claudecode,
        "select_credential",
        lambda provider, model=None: {
            "provider": provider,
            "credential_id": "cred-1",
            "key": "secret-test-key",
        },
    )
    import hermes_cli.runtime_provider as runtime_provider

    monkeypatch.setattr(
        runtime_provider,
        "resolve_runtime_provider",
        lambda **_kwargs: {
            "api_mode": "chat_completions",
            "base_url": "https://opencode.ai/zen/go/v1",
            "extra_headers": {"x-provider-route": "worker-test"},
        },
    )

    runtime = claudecode._provider_runtime(
        "opencode-go", "deepseek-v4-flash"
    )
    runtime["claude_capabilities"] = "effort,max_effort,thinking"
    runtime["route"] = "builtin"
    env = {"ANTHROPIC_AUTH_TOKEN": "stale"}
    claudecode._apply_provider_env(env, runtime)

    assert runtime["base_url"] == "https://opencode.ai/zen/go/v1"
    assert runtime["api_mode"] == "chat_completions"
    assert "ANTHROPIC_API_KEY" not in env
    assert env["ANTHROPIC_MODEL"] == "deepseek-v4-flash"
    assert env["WORKERS_CLAUDE_PROVIDER"] == "opencode-go"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "secret-test-key"
    assert runtime["extra_headers"] == {"x-provider-route": "worker-test"}
    assert "ANTHROPIC_DEFAULT_SONNET_MODEL_SUPPORTED_CAPABILITIES" not in env
    assert env["CLAUDE_CODE_SUBAGENT_MODEL"] == "deepseek-v4-flash"


def test_claude_code_direct_route_publishes_both_credential_vars():
    runtime = {
        "provider": "anthropic",
        "model": "claude-x",
        "base_url": "https://gateway.test",
        "api_key": "secret-test-key",
        "credential_id": "cred-1",
        "api_mode": "anthropic_messages",
        "route": "direct",
    }
    env: dict[str, str] = {}
    claudecode._apply_provider_env(env, runtime)

    # Some Anthropic-compatible gateways only accept x-api-key, so an
    # undeclared direct route keeps publishing both variables.
    assert env["ANTHROPIC_API_KEY"] == "secret-test-key"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "secret-test-key"

    runtime["auth_style"] = "api_key"
    env = {}
    claudecode._apply_provider_env(env, runtime)
    assert env["ANTHROPIC_API_KEY"] == "secret-test-key"
    assert "ANTHROPIC_AUTH_TOKEN" not in env


def test_claude_direct_route_passes_provider_extra_headers():
    runtime = {
        "provider": "opencode-go",
        "model": "deepseek-v4.1-flash",
        "base_url": "https://opencode.ai/zen/go",
        "api_key": "secret-test-key",
        "credential_id": "cred-1",
        "api_mode": "anthropic_messages",
        "route": "direct",
        "extra_headers": {"x-opencode-session": "session-test"},
    }
    env = {"ANTHROPIC_CUSTOM_HEADERS": "X-Trace: kept"}
    claudecode._apply_provider_env(env, runtime)
    assert env["ANTHROPIC_CUSTOM_HEADERS"].splitlines() == [
        "X-Trace: kept",
        "x-opencode-session: session-test",
    ]


def test_claude_code_auth_style_override_is_read_from_fallback_entry(monkeypatch):
    monkeypatch.setattr(claudecode, "require_credentials", lambda provider, model=None: {"provider": provider})
    monkeypatch.setattr(
        claudecode,
        "select_credential",
        lambda provider, model=None: {"provider": provider, "credential_id": "cred-1", "key": "k"},
    )
    import hermes_cli.runtime_provider as runtime_provider

    monkeypatch.setattr(
        runtime_provider,
        "resolve_runtime_provider",
        lambda **_kwargs: {
            "api_mode": "anthropic_messages",
            "base_url": "https://gateway.test",
        },
    )

    runtime = claudecode._provider_runtime(
        "anthropic",
        "claude-x",
        fallback_entry={"auth_style": "bearer"},
    )
    assert runtime["auth_style"] == "bearer"


def test_claude_code_route_selects_direct_or_builtin():
    direct = claudecode._route_runtime(
        {
            "provider": "opencode-go",
            "model": "qwen3.7-plus",
            "api_mode": "anthropic_messages",
            "base_url": "https://opencode.ai/zen/go",
            "api_key": "key",
            "credential_id": "cred",
        }
    )
    converted = claudecode._route_runtime(
        {
            "provider": "opencode-go",
            "model": "kimi-k2.6",
            "api_mode": "chat_completions",
            "base_url": "https://opencode.ai/zen/go/v1",
            "api_key": "key",
            "credential_id": "cred",
        }
    )
    responses = claudecode._route_runtime(
        {
            "provider": "opencode-go",
            "model": "muse-spark-1.2-contributor",
            "api_mode": "codex_responses",
            "base_url": "https://opencode.ai/zen/go/v1",
            "api_key": "key",
            "credential_id": "cred",
        }
    )

    assert direct["route"] == "direct"
    assert direct["base_url"] == "https://opencode.ai/zen/go"
    assert direct["client_model"] == "qwen3.7-plus"
    assert converted["route"] == "builtin"
    assert converted["base_url"] == "https://opencode.ai/zen/go/v1"
    assert converted["api_key"] == "key"
    assert converted["client_model"] == "kimi-k2.6"
    assert responses["route"] == "builtin"
    assert responses["client_model"] == "muse-spark-1.2-contributor"


@pytest.mark.parametrize(
    ("provider", "model", "api_mode", "base_url"),
    [
        ("provider-a", "deepseek-chat", "chat_completions", "https://provider-a.test/v1"),
        (
            "commandcode",
            "deepseek/deepseek-v4.1-flash",
            "chat_completions",
            "https://commandcode.test/v1",
        ),
        (
            "opencode-go",
            "muse-spark-1.3-contributor",
            "codex_responses",
            "https://opencode.ai/zen/go/v1",
        ),
        (
            "opencode-go",
            "deepseek-v4-flash",
            "chat_completions",
            "https://opencode.ai/zen/go/v1",
        ),
    ],
)
def test_core_api_mode_and_base_url_are_adopted_verbatim(
    monkeypatch, provider, model, api_mode, base_url
):
    """Mappings with deepseek/opencode names still come straight from Hermes."""
    import hermes_cli.runtime_provider as runtime_provider

    resolved = {
        "provider": provider,
        "api_mode": api_mode,
        "base_url": base_url,
    }
    monkeypatch.setattr(
        runtime_provider,
        "resolve_runtime_provider",
        lambda **_kwargs: dict(resolved),
    )

    assert provider_catalog.resolve_model_runtime(provider, model) == resolved

    monkeypatch.setattr(
        claudecode,
        "require_credentials",
        lambda requested, model=None: {"provider": requested},
    )
    monkeypatch.setattr(
        claudecode,
        "select_credential",
        lambda requested, model=None: {
            "provider": requested,
            "credential_id": "cred-1",
            "key": "k",
        },
    )
    runtime = claudecode._provider_runtime(provider, model)
    assert runtime["api_mode"] == api_mode
    assert runtime["base_url"] == base_url


def test_external_workers_share_configured_hermes_provider_models(monkeypatch):
    import hermes_cli.inventory as inventory
    import hermes_cli.runtime_provider as runtime_provider

    monkeypatch.setattr(inventory, "load_picker_context", lambda: object())
    monkeypatch.setattr(inventory, "build_models_payload", lambda *_a, **_kw: {
        "providers": [{"slug": "provider-a", "name": "Provider A", "models": ["model-a", "model-b"]}]
    })
    monkeypatch.setattr(runtime_provider, "resolve_runtime_provider", lambda **kw: {
        "api_mode": "chat_completions" if kw["target_model"] == "model-a" else "google_genai",
        "base_url": "https://provider.test/v1",
    })
    monkeypatch.setattr(provider_catalog, "model_capabilities", lambda *_a: {
        "known": True, "supportsTools": True, "supportsReasoning": True,
        "supportsVision": False, "supportsInterleavedThinking": False,
    })
    rows = claudecode.list_models(config.default_config())
    assert [row["id"] for row in rows] == ["provider-a/model-a"]
    assert rows[0]["providerDisplayName"] == "Provider A"
    assert rows == opencode.list_models(config.default_config())


def test_claude_code_plain_model_override_is_rejected():
    with pytest.raises(RuntimeError, match="provider/model"):
        claudecode._effective_target(
            fake_task(model_override="sonnet"),
            {"model": "opencode-go/qwen3.7-plus"},
        )


def test_claude_runner_rotates_once_and_restarts(monkeypatch):
    child_keys: list[str] = []
    results = iter([(1, '{"type":"result","api_error_status":429}'), (0, "ok")])

    def fake_run_once(_command, env):
        child_keys.append(env["ANTHROPIC_AUTH_TOKEN"])
        return next(results)

    rotation_payloads: list[dict] = []
    monkeypatch.setattr(claude_runner, "_run_once", fake_run_once)
    monkeypatch.setattr(
        claude_runner,
        "rotate_credential",
        lambda payload: (
            rotation_payloads.append(dict(payload))
            or {"credential_id": "cred-2", "key": "key-2"}
        ),
    )
    payload = {
        "command": ["claude", "--print", "hello"],
        "env": {"PATH": "/usr/bin"},
        "runtime": {
            "provider": "opencode-go",
            "model": "qwen3.7-plus",
            "api_mode": "anthropic_messages",
            "base_url": "https://opencode.ai/zen/go",
            "api_key": "key-1",
            "credential_id": "cred-1",
        },
    }

    assert claude_runner.run_payload(payload) == 0
    assert child_keys == ["key-1", "key-2"]
    assert len(rotation_payloads) == 1
    assert rotation_payloads[0]["status_code"] == 429
    assert rotation_payloads[0]["source"] == "workers-claude-code"


def test_claude_runner_walks_fallback_after_key_rotation_is_exhausted(monkeypatch):
    commands: list[list[str]] = []
    # 429 rotates the pool and is fallback-eligible; transient 5xx stays on the
    # same route for the core retry.
    results = iter([(1, '{"type":"result","api_error_status":429}'), (0, "ok")])

    def fake_run_routed(command, _env, _runtime, _hooks=None, _proxy=None):
        commands.append(command)
        return next(results)

    monkeypatch.setattr(claude_runner, "_run_routed", fake_run_routed)
    monkeypatch.setattr(claude_runner, "rotate_credential", lambda _payload: None)
    payload = {
        "attempts": [
            {
                "command": ["claude", "--model", "primary"],
                "runtime": {
                    "provider": "provider-a",
                    "model": "primary",
                    "base_url": "https://provider-a.test/v1",
                    "api_key": "key-a",
                },
            },
            {
                "command": ["claude", "--model", "fallback"],
                "runtime": {
                    "provider": "provider-b",
                    "model": "fallback",
                    "base_url": "https://provider-b.test/v1",
                    "api_key": "key-b",
                },
            },
        ],
        "env": {"PATH": "/usr/bin"},
    }

    assert claude_runner.run_payload(payload) == 0
    assert commands == [
        ["claude", "--model", "primary"],
        ["claude", "--model", "fallback"],
    ]


def test_claude_runner_builtin_proxy_preserves_hermes_environment(monkeypatch):
    child_envs: list[dict[str, str]] = []

    def fake_run_once(_command, env):
        child_envs.append(dict(env))
        return 1, "API Error: 401"

    monkeypatch.setattr(claude_runner, "_run_once", fake_run_once)
    class FakeProxy:
        base_url = "http://127.0.0.1:32123"

        def __init__(self, runtime):
            assert runtime["api_key"] == "worker-key"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(claude_runner, "BuiltinClaudeProxy", FakeProxy)
    monkeypatch.setattr(claude_runner, "rotate_credential", lambda _payload: None)
    payload = {
        "command": ["claude", "--print", "hello"],
        "env": {
            "PATH": "/usr/bin",
            "HERMES_HOME": "/tmp/hermes-profile",
            "HERMES_DOWNSTREAM_SETTING": "preserved",
            "ANTHROPIC_API_KEY": "parent-hermes-key",
        },
        "runtime": {
            "provider": "opencode-go",
            "model": "kimi-k2.6",
            "api_mode": "chat_completions",
            "route": "builtin",
            "base_url": "https://opencode.ai/zen/go/v1",
            "api_key": "worker-key",
            "credential_id": "cred-1",
            "client_model": "kimi-k2.6",
        },
    }

    assert claude_runner.run_payload(payload) == 1
    assert len(child_envs) == 1
    child_env = child_envs[0]
    assert child_env["HERMES_HOME"] == "/tmp/hermes-profile"
    assert child_env["HERMES_DOWNSTREAM_SETTING"] == "preserved"
    assert child_env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:32123"
    assert child_env["ANTHROPIC_AUTH_TOKEN"] == "WORKERS_BUILTIN"
    assert "ANTHROPIC_API_KEY" not in child_env
    assert child_env["ANTHROPIC_MODEL"] == "kimi-k2.6"
    assert "CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT" not in child_env
    assert child_env["WORKERS_CLAUDE_PROVIDER"] == "opencode-go"
    assert child_env["WORKERS_CLAUDE_ROUTE"] == "builtin"


def test_claude_runner_merges_hermes_hooks_into_settings(monkeypatch):
    captured = {}

    def fake_run_once(command, _env):
        settings_path = command[command.index("--settings") + 1]
        captured["settings"] = json.loads(
            Path(settings_path).read_text(encoding="utf-8")
        )
        return 0, "ok"

    monkeypatch.setattr(claude_runner, "_run_once", fake_run_once)
    payload = {
        "command": ["claude", "--print", "hello"],
        "env": {"PATH": "/usr/bin"},
        "runtime": {
            "provider": "provider-a",
            "model": "model-a",
            "base_url": "https://provider-a.test/v1",
            "api_key": "key-a",
            "credential_id": "cred-a",
        },
        "hermes_hook_settings": {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "",
                        "hooks": [
                            {"type": "command", "command": "bridge"}
                        ],
                    }
                ]
            }
        },
    }

    assert claude_runner.run_payload(payload) == 0
    assert (
        captured["settings"]["hooks"]["PreToolUse"][0]["hooks"][0][
            "command"
        ]
        == "bridge"
    )


@pytest.mark.parametrize("inherit_fallbacks", [True, False])
def test_claude_spawn_inherits_hermes_environment(tmp_path, monkeypatch, inherit_fallbacks):
    from workers_core import process_group
    monkeypatch.setattr(process_group, "supervise", lambda *_args, **_kwargs: None)
    binary = tmp_path / "claude"
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(0o755)
    document = config.default_config()
    document["inheritance"]["fallback_models"] = inherit_fallbacks
    monkeypatch.setattr(claudecode, "fallback_entries_for", lambda *_: [] if inherit_fallbacks else pytest.fail("Disabled fallback chain must not be read"))
    document["workers"]["claude-code"].update(
        {"binary": str(binary), "model": "opencode-go/qwen3.7-plus"}
    )
    task = fake_task(skills=[])
    monkeypatch.setattr(
        claudecode, "_mcp_config", lambda *_args, **_kwargs: tmp_path / "mcp.json"
    )
    monkeypatch.setattr(claudecode, "_cli_flags", lambda _binary: frozenset())
    monkeypatch.setattr(claudecode, "_source_root", lambda: tmp_path)
    monkeypatch.setattr(claudecode, "hermes_root", lambda: tmp_path)
    monkeypatch.setattr(claudecode, "worker_log", lambda *_args: tmp_path / "worker.log")
    monkeypatch.setattr(
        claudecode,
        "_provider_runtime",
        lambda *_args: {
            "provider": "opencode-go",
            "model": "qwen3.7-plus",
            "api_mode": "anthropic_messages",
            "base_url": "https://opencode.ai/zen/go",
            "api_key": "worker-key",
            "credential_id": "cred-1",
        },
    )

    class FakeStdin:
        def __init__(self):
            self.data = bytearray()

        def write(self, value):
            self.data.extend(value)

        def close(self):
            pass

    class FakeProcess:
        pid = 1234

        def __init__(self):
            self.stdin = FakeStdin()

        def terminate(self):
            pass

    captured = {}

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["env"] = dict(kwargs["env"])
        captured["process"] = FakeProcess()
        return captured["process"]

    monkeypatch.setattr(claudecode.subprocess, "Popen", fake_popen)

    launch = fake_launch(
        task,
        tmp_path,
        env={
            "PATH": "/usr/bin",
            "HERMES_HOME": str(tmp_path),
            "HERMES_KANBAN_DB": str(tmp_path / "kanban.db"),
            "ANTHROPIC_API_KEY": "parent-hermes-key",
            "HERMES_DOWNSTREAM_SETTING": "preserved",
        },
    )
    assert claudecode.spawn(launch, config=document) == 1234
    assert captured["env"]["ANTHROPIC_API_KEY"] == "parent-hermes-key"
    assert captured["env"]["HERMES_DOWNSTREAM_SETTING"] == "preserved"
    payload = json.loads(bytes(captured["process"].stdin.data).decode("utf-8"))
    assert payload["runtime"]["api_key"] == "worker-key"
    assert "hermes_hook_settings" not in payload
    assert "workers_core.backends.claude_runner" in captured["command"][-1]
    prompt_file = Path(payload["cleanup_paths"][1])
    assert prompt_file.is_file() and tmp_path in prompt_file.parents


def test_claude_spawn_passes_hermes_hook_settings_when_enabled(tmp_path, monkeypatch):
    from workers_core import process_group
    monkeypatch.setattr(process_group, "supervise", lambda *_args, **_kwargs: None)
    binary = tmp_path / "claude"
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(0o755)
    document = config.default_config()
    document["workers"]["claude-code"].update(
        {
            "binary": str(binary),
            "model": "opencode-go/qwen3.7-plus",
            "sync_hermes_hooks": True,
            "hermes_hook_selection": {"hook-1": True},
        }
    )
    task = fake_task(skills=[])
    hook_settings = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "",
                    "hooks": [{"type": "command", "command": "bridge"}],
                }
            ]
        }
    }
    monkeypatch.setattr(
        claudecode,
        "_mcp_config",
        lambda *_args, **_kwargs: tmp_path / "mcp.json",
    )
    monkeypatch.setattr(claudecode, "_cli_flags", lambda _binary: frozenset())
    monkeypatch.setattr(claudecode, "_source_root", lambda: tmp_path)
    monkeypatch.setattr(claudecode, "hermes_root", lambda: tmp_path)
    monkeypatch.setattr(
        claudecode,
        "worker_log",
        lambda *_args: tmp_path / "worker.log",
    )
    monkeypatch.setattr(
        claudecode,
        "_provider_runtime",
        lambda *_args: {
            "provider": "opencode-go",
            "model": "qwen3.7-plus",
            "api_mode": "anthropic_messages",
            "base_url": "https://opencode.ai/zen/go",
            "api_key": "worker-key",
            "credential_id": "cred-1",
        },
    )
    hook_calls = {}

    def fake_build_settings(*args, **kwargs):
        hook_calls.update(kwargs)
        return hook_settings

    from workers_core import claude_hooks
    monkeypatch.setattr(claude_hooks, "build_settings", fake_build_settings)

    class FakeStdin:
        def __init__(self):
            self.data = bytearray()

        def write(self, value):
            self.data.extend(value)

        def close(self):
            pass

    class FakeProcess:
        pid = 1235

        def __init__(self):
            self.stdin = FakeStdin()

        def terminate(self):
            pass

    captured = {}

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["process"] = FakeProcess()
        return captured["process"]

    monkeypatch.setattr(claudecode.subprocess, "Popen", fake_popen)
    launch = fake_launch(
        task,
        tmp_path,
        env={
            "PATH": "/usr/bin",
            "HERMES_HOME": str(tmp_path),
            "HERMES_KANBAN_DB": str(tmp_path / "kanban.db"),
        },
    )

    assert claudecode.spawn(launch, config=document) == 1235
    payload = json.loads(bytes(captured["process"].stdin.data).decode("utf-8"))
    assert payload["hermes_hook_settings"] == hook_settings
    assert hook_calls["selection"] == {"hook-1": True}


def test_claude_proxy_converts_tools_thinking_and_usage():
    request = claude_proxy.anthropic_to_openai(
        {
            "model": "sonnet",
            "system": [{"type": "text", "text": "system"}],
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "hello"}]},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "reason"},
                        {"type": "tool_use", "id": "call_1", "name": "read", "input": {"path": "x"}},
                    ],
                },
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "ok"}]},
            ],
            "tools": [
                {"type": "tool_search_tool_bm25_20251119", "name": "tool_search_tool_bm25"},
                {
                    "name": "read",
                    "description": "Read",
                    "input_schema": {"type": "object"},
                    "defer_loading": True,
                },
            ],
            "tool_choice": {"type": "tool", "name": "read"},
            "thinking": {"type": "enabled", "budget_tokens": 8000},
            "stream": True,
        },
        "upstream-model",
    )
    assert request["model"] == "upstream-model"
    assert request["stream"] is False
    assert request["messages"][0] == {"role": "system", "content": "system"}
    assert request["messages"][2]["tool_calls"][0]["function"]["name"] == "read"
    assert request["messages"][3]["role"] == "tool"
    assert [tool["function"]["name"] for tool in request["tools"]] == ["read"]
    assert request["reasoning_effort"] == "medium"
    assert request["tool_choice"]["function"]["name"] == "read"

    response = claude_proxy.openai_to_anthropic(
        {
            "id": "chat_1",
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "content": "working",
                        "reasoning_content": "think",
                        "tool_calls": [
                            {"id": "call_2", "function": {"name": "write", "arguments": '{"path":"y"}'}}
                        ],
                    },
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 4},
        },
        "sonnet",
    )
    assert response["stop_reason"] == "tool_use"
    assert [block["type"] for block in response["content"]] == ["thinking", "text", "tool_use"]
    assert response["content"][2]["input"] == {"path": "y"}
    assert response["usage"] == {"input_tokens": 10, "output_tokens": 4}
    sse = claude_proxy.anthropic_sse(response).decode("utf-8")
    assert "event: message_start" in sse
    assert '"type":"input_json_delta"' in sse
    assert "event: message_stop" in sse


def test_builtin_claude_proxy_serves_anthropic_messages(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200
        headers: dict = {}
        is_read = True

        def read(self):
            return None

        def json(self):
            return {
                "id": "chat_local",
                "choices": [
                    {"finish_reason": "stop", "message": {"content": "proxy ok"}}
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2},
            }

        def close(self):
            return None

    def fake_send(_client, request, *, stream, auth=None, follow_redirects=None):
        captured.update(
            {
                "url": str(request.url),
                "headers": dict(request.headers),
                "json": json.loads(request.content.decode("utf-8")),
                "stream": stream,
            }
        )
        return FakeResponse()

    monkeypatch.setattr(claude_proxy.httpx.Client, "send", fake_send)
    runtime = {
        "provider": "opencode-go",
        "model": "muse-spark-1.2-contributor",
        "api_mode": "chat_completions",
        "base_url": "https://opencode.ai/zen/go/v1",
        "api_key": "secret-key",
        "credential_id": "cred-1",
        "route": "builtin",
        "client_model": "sonnet",
        "extra_headers": {"x-provider-route": "worker-test"},
    }
    with claude_proxy.BuiltinClaudeProxy(runtime) as proxy:
        request = Request(
            f"{proxy.base_url}/v1/messages?beta=true",
            data=json.dumps(
                {
                    "model": "sonnet",
                    "messages": [{"role": "user", "content": "hello"}],
                    "max_tokens": 128,
                    "stream": True,
                }
            ).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            assert response.status == 200
            assert response.headers.get_content_type() == "text/event-stream"
            payload = response.read().decode("utf-8")

    assert captured["url"] == "https://opencode.ai/zen/go/v1/chat/completions"
    assert captured["headers"]["authorization"] == "Bearer secret-key"
    assert captured["headers"]["x-provider-route"] == "worker-test"
    assert captured["json"]["model"] == "muse-spark-1.2-contributor"
    assert captured["json"]["stream"] is True
    assert captured["json"]["stream_options"] == {"include_usage": True}
    assert captured["stream"] is True
    assert "proxy ok" in payload
    assert "event: message_stop" in payload


def test_builtin_claude_proxy_rotates_before_returning_to_claude(monkeypatch):
    captured: list[str] = []

    class FakeResponse:
        headers: dict = {}
        is_read = False

        def __init__(self, status_code, body=None):
            self.status_code = status_code
            self._body = body or {}
            self.closed = False

        def read(self):
            self.is_read = True
            return None

        def json(self):
            return self._body

        def close(self):
            self.closed = True

    responses = iter(
        [
            FakeResponse(429),
            FakeResponse(
                200,
                {
                    "id": "chat_rotated",
                    "choices": [{"finish_reason": "stop", "message": {"content": "rotated ok"}}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 2},
                },
            ),
        ]
    )

    def fake_send(_client, request, *, stream, auth=None, follow_redirects=None):
        captured.append(request.headers["authorization"])
        body = json.loads(request.content.decode("utf-8"))
        assert body["model"] == "muse-spark-1.2-contributor"
        return next(responses)

    monkeypatch.setattr(claude_proxy.httpx.Client, "send", fake_send)
    monkeypatch.setattr(
        credentials,
        "rotate_credential",
        lambda payload: (
            {"credential_id": "cred-2", "key": "next-key"}
            if payload["status_code"] == 429
            else None
        ),
    )
    runtime = {
        "provider": "opencode-go",
        "model": "muse-spark-1.2-contributor",
        "api_mode": "chat_completions",
        "base_url": "https://opencode.ai/zen/go/v1",
        "api_key": "first-key",
        "credential_id": "cred-1",
        "route": "builtin",
        "client_model": "muse-spark-1.2-contributor",
    }
    with claude_proxy.BuiltinClaudeProxy(runtime) as proxy:
        request = Request(
            f"{proxy.base_url}/v1/messages",
            data=json.dumps(
                {
                    "model": "muse-spark-1.2-contributor",
                    "messages": [{"role": "user", "content": "hello"}],
                    "max_tokens": 128,
                }
            ).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))

    assert captured == ["Bearer first-key", "Bearer next-key"]
    assert runtime["api_key"] == "next-key"
    assert payload["content"][0]["text"] == "rotated ok"


def test_builtin_claude_proxy_retries_unsupported_effort(monkeypatch):
    captured: list[str] = []

    class FakeResponse:
        headers: dict = {}
        is_read = False

        def __init__(self, status_code, body=None):
            self.status_code = status_code
            self._body = body or {}

        def read(self):
            self.is_read = True
            return None

        def json(self):
            return self._body

        def close(self):
            return None

    responses = iter(
        [
            FakeResponse(400),
            FakeResponse(
                200,
                {
                    "id": "resp_effort",
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "ok"}],
                        }
                    ],
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                },
            ),
        ]
    )

    def fake_send(_client, request, *, stream, auth=None, follow_redirects=None):
        assert request.headers["authorization"] == "Bearer key"
        body = json.loads(request.content.decode("utf-8"))
        captured.append(body["reasoning"]["effort"])
        return next(responses)

    monkeypatch.setattr(claude_proxy.httpx.Client, "send", fake_send)
    runtime = {
        "provider": "provider-a",
        "model": "model-a",
        "api_mode": "codex_responses",
        "base_url": "https://provider.test/v1",
        "api_key": "key",
        "credential_id": "cred",
        "route": "builtin",
        "client_model": "model-a",
    }
    with claude_proxy.BuiltinClaudeProxy(runtime) as proxy:
        request = Request(
            f"{proxy.base_url}/v1/messages",
            data=json.dumps(
                {
                    "model": "model-a",
                    "messages": [{"role": "user", "content": "hello"}],
                    "output_config": {"effort": "max"},
                }
            ).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            assert json.loads(response.read().decode("utf-8"))["content"][0]["text"] == "ok"

    assert captured == ["max", "high"]


def test_claude_proxy_converts_responses_api():
    request = claude_proxy.anthropic_to_responses(
        {
            "system": "system",
            "messages": [
                {"role": "user", "content": "hello"},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "call_1", "name": "read", "input": {"path": "x"}}
                    ],
                },
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "ok"}]},
            ],
            "tools": [
                {"type": "tool_search_tool_regex_20251119", "name": "tool_search_tool_regex"},
                {
                    "name": "read",
                    "input_schema": {"type": "object"},
                    "defer_loading": True,
                },
            ],
            "thinking": {"type": "enabled", "budget_tokens": 20000},
            "max_tokens": 512,
        },
        "muse-spark-1.2-contributor",
    )
    assert request["model"] == "muse-spark-1.2-contributor"
    assert request["instructions"] == "system"
    assert request["max_output_tokens"] == 512
    assert request["reasoning"] == {"effort": "high"}
    assert [tool["name"] for tool in request["tools"]] == ["read"]
    assert request["input"][1]["type"] == "function_call"
    assert request["input"][2]["type"] == "function_call_output"

    response = claude_proxy.responses_to_anthropic(
        {
            "id": "resp_1",
            "status": "completed",
            "output": [
                {
                    "type": "reasoning",
                    "id": "reasoning",
                    "encrypted_content": "opaque",
                    "summary": [{"type": "summary_text", "text": "think"}],
                },
                {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "working"}]},
                {"type": "function_call", "call_id": "call_2", "name": "write", "arguments": '{"path":"y"}'},
            ],
            "usage": {"input_tokens": 8, "output_tokens": 3},
        },
        "sonnet",
    )
    assert response["stop_reason"] == "tool_use"
    assert [block["type"] for block in response["content"]] == ["thinking", "text", "tool_use"]
    assert response["content"][2]["input"] == {"path": "y"}
    assert response["content"][0]["signature"].startswith("workers-responses-v1:")

    roundtrip = claude_proxy.anthropic_to_responses(
        {
            "messages": [
                {
                    "role": "assistant",
                    "content": response["content"],
                }
            ]
        },
        "muse-spark-1.2-contributor",
    )
    assert roundtrip["input"][0]["type"] == "reasoning"
    assert roundtrip["input"][0]["id"] == "reasoning"


def test_claude_proxy_preserves_extended_effort_until_upstream_rejects_it():
    forwarded = claude_proxy.anthropic_to_responses(
        {
            "messages": [{"role": "user", "content": "hello"}],
            "output_config": {"effort": "max"},
        },
        "model-a",
    )
    assert forwarded["reasoning"] == {"effort": "max"}
    fallback = claude_proxy._fallback_effort_request(forwarded)
    assert fallback["reasoning"] == {"effort": "high"}

    chat = claude_proxy.anthropic_to_openai(
        {
            "messages": [{"role": "user", "content": "hello"}],
            "output_config": {"effort": "xhigh"},
        },
        "model-a",
    )
    assert chat["reasoning_effort"] == "xhigh"


def test_claude_proxy_param_fallback_chain():
    request = claude_proxy.anthropic_to_openai(
        {
            "messages": [
                {
                    "role": "assistant",
                    "content": [{"type": "thinking", "thinking": "reason"}],
                }
            ],
            "max_tokens": 64,
            "thinking": {"type": "enabled", "budget_tokens": 1024},
        },
        "model-a",
        stream=True,
    )
    assert request["reasoning_effort"] == "low"
    chain = claude_proxy._param_fallbacks(request)
    tags = [tag for tag, _ in chain]
    variants = [body for _, body in chain]
    # effort is already standard, so the chain starts at the token remap.
    assert tags == ["max_tokens", "stream_options", "reasoning_content"]
    assert "max_completion_tokens" in variants[0]
    assert "max_tokens" not in variants[0]
    assert "stream_options" not in variants[1]
    assert all(
        "reasoning_content" not in message
        for message in variants[2]["messages"]
    )


def test_claude_proxy_responses_fallback_chain_relaxes_responses_fields():
    request = claude_proxy.anthropic_to_responses(
        {
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 64,
            "tools": [{"name": "read", "input_schema": {"type": "object"}}],
            "thinking": {"type": "enabled", "budget_tokens": 32000},
        },
        "model-a",
        stream=True,
    )
    assert request["max_output_tokens"] == 64
    assert "max_tokens" not in request
    chain = claude_proxy._param_fallbacks(request, responses=True)
    tags = [tag for tag, _ in chain]
    variants = [body for _, body in chain]
    # Responses bodies carry none of the Chat Completions keys, so the chain
    # relaxes the fields Responses actually sends.
    assert tags == ["parallel_tool_calls", "max_output_tokens", "reasoning"]
    assert variants[0]["reasoning"] == {"effort": "high"}
    assert "parallel_tool_calls" not in variants[0]
    assert "max_output_tokens" not in variants[1]
    assert "reasoning" not in variants[2]
    # An extended effort still gets clamped first when the upstream allows it.
    extended = dict(request, reasoning={"effort": "xhigh"})
    assert claude_proxy._param_fallbacks(extended, responses=True)[0][0] == "effort"


def test_claude_proxy_variant_gate_follows_named_parameters():
    assert claude_proxy._should_try_variant(
        "unsupported parameter: stream_options", "stream_options"
    )
    assert not claude_proxy._should_try_variant(
        "unsupported parameter: stream_options", "max_tokens"
    )
    # An opaque error names nothing to match on, so the chain still runs.
    assert claude_proxy._should_try_variant("invalid request", "max_tokens")


def test_claude_proxy_stops_retrying_when_error_names_unrelated_parameter(monkeypatch):
    from urllib.error import HTTPError

    class FakeResponse:
        headers: dict = {}

        def __init__(self, status_code, body=None):
            self.status_code = status_code
            self._body = body or {}

        def read(self):
            return None

        def json(self):
            return self._body

        def close(self):
            return None

    sent: list[dict] = []

    def fake_send(_client, request, *, stream, auth=None, follow_redirects=None):
        sent.append(json.loads(request.content.decode("utf-8")))
        return FakeResponse(
            400, {"error": {"message": "unsupported value for reasoning_effort"}}
        )

    monkeypatch.setattr(claude_proxy.httpx.Client, "send", fake_send)
    runtime = {
        "provider": "provider-a",
        "model": "model-a",
        "api_mode": "chat_completions",
        "base_url": "https://provider.test/v1",
        "api_key": "key",
        "credential_id": "cred",
        "route": "builtin",
        "client_model": "model-a",
    }
    with claude_proxy.BuiltinClaudeProxy(runtime) as proxy:
        request = Request(
            f"{proxy.base_url}/v1/messages",
            data=json.dumps(
                {
                    "model": "model-a",
                    "messages": [{"role": "user", "content": "hello"}],
                    "max_tokens": 64,
                }
            ).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="POST",
        )
        with pytest.raises(HTTPError) as excinfo:
            urlopen(request, timeout=5)

    assert excinfo.value.code == 400
    # The token remap cannot fix a complaint about reasoning_effort, so the
    # error surfaces immediately instead of costing three more round trips.
    assert len(sent) == 1


def test_claude_proxy_next_variant_index_skips_unrelated_rewrites():
    tags = ["", "max_tokens", "stream_options", "reasoning_content"]
    # An error about the third variant must reach it instead of stalling on the
    # second, which is what made the stream_options fix unreachable.
    assert claude_proxy._next_variant_index(
        "unsupported parameter: stream_options", tags, 0
    ) == 2
    assert claude_proxy._next_variant_index("invalid request", tags, 0) == 1
    assert claude_proxy._next_variant_index("max_tokens is not supported", tags, 0) == 1
    assert claude_proxy._next_variant_index("max_tokens is not supported", tags, 1) is None


def test_claude_proxy_reaches_a_later_rewrite_when_error_names_it(monkeypatch):
    class FakeResponse:
        headers: dict = {}

        def __init__(self, status_code, body=None):
            self.status_code = status_code
            self._body = body or {}

        def read(self):
            return None

        def json(self):
            return self._body

        def close(self):
            return None

    sent: list[dict] = []
    upstream = iter(
        [
            FakeResponse(
                400, {"error": {"message": "unsupported parameter: stream_options"}}
            ),
            FakeResponse(
                200,
                {
                    "id": "chat-1",
                    "choices": [{"finish_reason": "stop", "message": {"content": "ok"}}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 1},
                },
            ),
        ]
    )

    def fake_send(_client, request, *, stream, auth=None, follow_redirects=None):
        sent.append(json.loads(request.content.decode("utf-8")))
        return next(upstream)

    monkeypatch.setattr(claude_proxy.httpx.Client, "send", fake_send)
    runtime = {
        "provider": "provider-a",
        "model": "model-a",
        "api_mode": "chat_completions",
        "base_url": "https://provider.test/v1",
        "api_key": "key",
        "credential_id": "cred",
        "route": "builtin",
        "client_model": "model-a",
    }
    with claude_proxy.BuiltinClaudeProxy(runtime) as proxy:
        request = Request(
            f"{proxy.base_url}/v1/messages",
            data=json.dumps(
                {
                    "model": "model-a",
                    "stream": True,
                    "messages": [{"role": "user", "content": "hello"}],
                    "max_tokens": 64,
                }
            ).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            assert response.status == 200

    assert len(sent) == 2
    assert sent[0]["stream_options"] == {"include_usage": True}
    assert "stream_options" not in sent[1]


def test_claude_proxy_recovers_chat_request_after_max_tokens_rejection(monkeypatch):
    class FakeResponse:
        headers: dict = {}

        def __init__(self, status_code, body=None):
            self.status_code = status_code
            self._body = body or {}

        def read(self):
            return None

        def json(self):
            return self._body

        def close(self):
            return None

    sent: list[dict] = []
    upstream = iter(
        [
            FakeResponse(400, {"error": {"message": "unsupported parameter: max_tokens"}}),
            FakeResponse(
                200,
                {
                    "id": "chat-1",
                    "choices": [{"finish_reason": "stop", "message": {"content": "ok"}}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 1},
                },
            ),
        ]
    )

    def fake_send(_client, request, *, stream, auth=None, follow_redirects=None):
        sent.append(json.loads(request.content.decode("utf-8")))
        return next(upstream)

    monkeypatch.setattr(claude_proxy.httpx.Client, "send", fake_send)
    runtime = {
        "provider": "provider-a",
        "model": "model-a",
        "api_mode": "chat_completions",
        "base_url": "https://provider.test/v1",
        "api_key": "key",
        "credential_id": "cred",
        "route": "builtin",
        "client_model": "model-a",
    }
    with claude_proxy.BuiltinClaudeProxy(runtime) as proxy:
        request = Request(
            f"{proxy.base_url}/v1/messages",
            data=json.dumps(
                {
                    "model": "model-a",
                    "messages": [{"role": "user", "content": "hello"}],
                    "max_tokens": 64,
                }
            ).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))

    assert payload["content"][0]["text"] == "ok"
    assert len(sent) == 2
    assert sent[0]["max_tokens"] == 64
    assert sent[1]["max_completion_tokens"] == 64
    assert "max_tokens" not in sent[1]


def test_claude_proxy_recovers_responses_request_after_strict_tool_flag(monkeypatch):
    class FakeResponse:
        headers: dict = {}

        def __init__(self, status_code, body=None):
            self.status_code = status_code
            self._body = body or {}

        def read(self):
            return None

        def json(self):
            return self._body

        def close(self):
            return None

    sent: list[dict] = []
    upstream = iter(
        [
            FakeResponse(
                400, {"error": {"message": "unsupported parameter: parallel_tool_calls"}}
            ),
            FakeResponse(
                200,
                {
                    "id": "resp-1",
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "ok"}],
                        }
                    ],
                    "usage": {"input_tokens": 2, "output_tokens": 1},
                },
            ),
        ]
    )

    def fake_send(_client, request, *, stream, auth=None, follow_redirects=None):
        sent.append(json.loads(request.content.decode("utf-8")))
        return next(upstream)

    monkeypatch.setattr(claude_proxy.httpx.Client, "send", fake_send)
    runtime = {
        "provider": "provider-a",
        "model": "model-a",
        "api_mode": "responses",
        "base_url": "https://provider.test/v1",
        "api_key": "key",
        "credential_id": "cred",
        "route": "builtin",
        "client_model": "model-a",
    }
    with claude_proxy.BuiltinClaudeProxy(runtime) as proxy:
        request = Request(
            f"{proxy.base_url}/v1/messages",
            data=json.dumps(
                {
                    "model": "model-a",
                    "messages": [{"role": "user", "content": "hello"}],
                    "tools": [{"name": "read", "input_schema": {"type": "object"}}],
                    "max_tokens": 64,
                }
            ).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))

    assert payload["content"][0]["text"] == "ok"
    assert "parallel_tool_calls" in sent[0]
    assert "parallel_tool_calls" not in sent[1]
    # The Responses chain must not invent Chat Completions keys.
    assert "max_completion_tokens" not in sent[1]
    assert sent[1]["max_output_tokens"] == 64


def test_claude_proxy_sse_parser_survives_chunk_boundaries():
    import httpx

    events = [
        json.dumps(
            {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "Hello "},
            }
        ),
        json.dumps(
            {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "world"},
            }
        ),
    ]
    payload = "".join(f"event: content_block_delta\ndata: {item}\n\n" for item in events)
    encoded = payload.encode("utf-8")
    # Split mid-field, mid-line and mid-event: framing must come from the
    # buffered line iterator, never from the transport's chunk boundaries.
    chunks = [encoded[:11], encoded[11:40], encoded[40:41], encoded[41:]]

    def handler(_request):
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=iter(chunks),
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    response = client.send(
        client.build_request("POST", "https://upstream.test/v1/messages"),
        stream=True,
    )
    try:
        assert list(claude_proxy._iter_sse_data(response)) == events
    finally:
        response.close()
        client.close()


def test_claude_proxy_token_estimate_does_not_undercount_cjk():
    latin = claude_proxy._estimate_tokens("a" * 300)
    cjk = claude_proxy._estimate_tokens("中" * 300)
    assert 90 <= latin <= 110
    # A flat chars//3 ratio reports 100 here and makes Claude Code defer
    # auto-compact until the upstream context limit is already blown.
    assert cjk >= 3 * latin


def test_claude_sse_preserves_reasoning_signature():
    signature = claude_proxy._encode_reasoning_signature(
        {"type": "reasoning", "id": "reasoning", "encrypted_content": "opaque"}
    )
    payload = claude_proxy.anthropic_sse(
        {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": "model-a",
            "content": [
                {"type": "thinking", "thinking": "summary", "signature": signature}
            ],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
    ).decode("utf-8")

    assert '"type":"signature_delta"' in payload
    assert signature in payload


def test_claude_code_mcp_config_scopes_kanban_env(tmp_path, monkeypatch):
    monkeypatch.setattr(claudecode, "hermes_root", lambda: tmp_path)
    servers = {
        "hermes-worker": {
            "command": "/usr/bin/python3",
            "args": ["-m", "workers_core.capability_mcp"],
            "cwd": str(tmp_path / "workers"),
            "env": {
                "HERMES_HOME": str(tmp_path),
                "HERMES_KANBAN_TASK": "task-1",
                "HERMES_KANBAN_DB": str(tmp_path / "kanban.db"),
                "WORKERS_HERMES_TOOLSETS": "[]",
            },
        }
    }

    path = claudecode._mcp_config(tmp_path, servers)
    value = json.loads(path.read_text(encoding="utf-8"))
    server = value["mcpServers"]["hermes-worker"]

    assert server["args"] == ["-m", "workers_core.capability_mcp"]
    assert server["cwd"] == str(tmp_path / "workers")
    assert server["env"] == servers["hermes-worker"]["env"]


def test_claude_stream_result_and_resume_command():
    output = "\n".join(
        [
            '{"type":"assistant","message":{"content":[]}}',
            '{"type":"result","result":"finished","session_id":"session-1"}',
        ]
    )
    assert claude_runner._final_text(output) == "finished"
    resumed = claude_runner._resume_command(
        ["claude", "--print", "--session-id", "session-1", "first"],
        "session-1",
        "continue",
    )
    assert resumed == ["claude", "--print", "--resume", "session-1", "continue"]


def test_capability_mcp_reads_dynamic_toolsets(monkeypatch):
    monkeypatch.setenv("WORKERS_HERMES_TOOLSETS", '["kanban","compute_submit"]')
    assert capability_mcp._toolsets() == ["kanban", "compute_submit"]


def test_claude_runner_enters_goal_loop_after_success(monkeypatch):
    calls = []

    monkeypatch.setattr(
        claude_runner,
        "_run_candidate",
        lambda command, runtime, env, hooks=None, proxy=None: (
            0,
            '{"type":"result","result":"done"}',
        ),
    )
    monkeypatch.setattr(
        claude_runner,
        "_run_goal",
        lambda command, runtime, env, output, goal, hooks=None, proxy=None: (
            calls.append(goal) or 0
        ),
    )
    payload = {
        "command": ["claude", "--print", "task"],
        "runtime": {
            "provider": "anthropic",
            "model": "sonnet",
            "base_url": "https://example.test",
            "api_key": "key",
        },
        "env": {},
        "goal": {"enabled": True, "task_id": "t_test"},
    }

    assert claude_runner.run_payload(payload) == 0
    assert calls == [{"enabled": True, "task_id": "t_test"}]


def test_claude_worker_heartbeats_through_core_bridge_until_silence_weds(monkeypatch):
    import threading
    import time as clock

    import tools.kanban_tools as kanban_tools

    beats: list[float] = []
    release = threading.Event()
    first_line_seen = threading.Event()

    class _SilentChild:
        """Emits one stream-json line, then stays alive while producing nothing."""

        def __init__(self) -> None:
            self.stdout = self
            self._sent = False

        def readline(self):
            if not self._sent:
                self._sent = True
                first_line_seen.set()
                return b'{"type":"system","subtype":"init"}\n'
            release.wait(5)
            return b""

        def poll(self):
            return None if not release.is_set() else 0

        def wait(self):
            release.wait(5)
            return 0

    monkeypatch.setattr(
        kanban_tools,
        "heartbeat_current_worker_from_env",
        lambda: beats.append(clock.monotonic()) or True,
    )
    monkeypatch.setattr(
        claude_runner, "_apply_pre_llm_context", lambda command, _env: command
    )
    monkeypatch.setattr(claude_runner, "_HEARTBEAT_INTERVAL", 0.05)
    monkeypatch.setattr(claude_runner, "_liveness_grace_seconds", lambda: 0.2)
    monkeypatch.setattr(
        claude_runner.subprocess, "Popen", lambda *_a, **_kw: _SilentChild()
    )

    results: list = []
    worker = threading.Thread(
        target=lambda: results.append(
            claude_runner._run_once(["claude", "--print", "task"], {})
        )
    )
    worker.start()
    assert first_line_seen.wait(2)
    clock.sleep(1.0)
    release.set()
    worker.join(5)

    assert results and results[0][0] == 0
    assert beats, "an active worker must keep its claim alive"
    assert clock.monotonic() - beats[-1] >= 0.3, (
        "heartbeats must stop past the wedge threshold so the board can reclaim"
    )
