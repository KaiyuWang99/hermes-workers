from __future__ import annotations

import json
import shlex
from pathlib import Path

from agent.shell_hooks import ShellHookSpec
from workers_core import claude_hook_bridge, claude_hooks


def named_spec(event: str, command: str, name: str) -> ShellHookSpec:
    """Build a spec carrying the optional ``name`` current Hermes dropped."""
    spec = ShellHookSpec(event=event, command=command)
    spec.name = name
    return spec


def test_unnamed_spec_survives_missing_name_field():
    spec = ShellHookSpec(event="pre_llm_call", command="/tmp/x.py")
    assert claude_hooks.spec_name(spec) == ""
    assert claude_hooks.display_name(spec) == "pre_llm_call"
    assert claude_hooks.hook_id(spec).startswith("hook-")


def test_settings_from_specs_uses_task_manifest_and_native_events(tmp_path):
    spec = ShellHookSpec(
        event="pre_tool_call",
        command="/tmp/check-hermes-hook.sh",
        matcher="terminal|write_file",
        timeout=12,
        fail_closed=True,
    )

    settings = claude_hooks.settings_from_specs(
        [spec], state_dir=tmp_path, task_id="task/1"
    )

    assert settings is not None
    entry = settings["hooks"]["PreToolUse"][0]
    assert entry["matcher"] == ""
    assert entry["hooks"][0]["type"] == "command"
    assert entry["hooks"][0]["timeout"] == 12
    command = shlex.split(entry["hooks"][0]["command"])
    # The bridge launches through Hermes' dependency bootstrap (-I -c).
    assert command[1:3] == ["-I", "-c"]
    assert "claude_hook_bridge" in command[3]
    manifest = Path(command[command.index("--manifest") + 1])
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["schema"] == "hermes-claude-hook-sync-v1"
    assert payload["hooks"] == [
        {
            "event": "pre_tool_call",
            "command": "/tmp/check-hermes-hook.sh",
            "matcher": "terminal|write_file",
            "timeout": 12,
            "fail_closed": True,
        }
    ]


def test_build_settings_filters_explicit_hook_selection(monkeypatch, tmp_path):
    selected = ShellHookSpec(event="pre_tool_call", command="/tmp/selected.sh")
    skipped = ShellHookSpec(event="post_tool_call", command="/tmp/skipped.sh")
    monkeypatch.setattr(
        claude_hooks,
        "_approved_specs",
        lambda _home: ([selected, skipped], []),
    )

    settings = claude_hooks.build_settings(
        str(tmp_path),
        state_dir=tmp_path,
        task_id="task-selection",
        selection={claude_hooks.hook_id(selected): True},
    )

    assert settings is not None
    assert list(settings["hooks"]) == ["PreToolUse"]


def test_approved_specs_preserve_hermes_consent_boundary(monkeypatch, tmp_path):
    config = {
        "hooks": {
            "pre_tool_call": [
                {"command": "/tmp/approved.sh", "matcher": "terminal"},
                {"command": "/tmp/not-approved.sh", "matcher": "terminal"},
            ],
            "transform_llm_output": [
                {"command": "/tmp/unsupported.sh"},
            ],
        }
    }
    monkeypatch.setattr(
        claude_hooks,
        "_load_profile_config",
        lambda _hermes_home: config,
    )
    monkeypatch.setattr(
        claude_hooks,
        "allowlist_entry_for",
        lambda _event, command: {} if command == "/tmp/approved.sh" else None,
    )

    specs, unsupported = claude_hooks._approved_specs(str(tmp_path))

    assert [spec.command for spec in specs] == ["/tmp/approved.sh"]
    assert unsupported == ["transform_llm_output"]


def test_list_hooks_exposes_selection_and_capability(monkeypatch, tmp_path):
    config = {
        "hooks": {
            "pre_tool_call": [{"command": "/tmp/check.sh", "matcher": "terminal"}],
            "transform_llm_output": [{"command": "/tmp/unsupported.sh"}],
        }
    }
    monkeypatch.setattr(claude_hooks, "_load_profile_config", lambda _home: config)
    monkeypatch.setattr(
        claude_hooks,
        "allowlist_entry_for",
        lambda _event, _command: {},
    )
    monkeypatch.setattr(claude_hooks, "script_is_executable", lambda _command: True)

    rows = claude_hooks.list_hooks(
        str(tmp_path),
        selection=None,
        sync_enabled=True,
    )

    assert len(rows) == 2
    assert rows[0]["event"] == "pre_tool_call"
    assert rows[0]["display_name"] == "pre_tool_call"
    assert rows[0]["name"] == ""
    assert rows[0]["selected"] is True
    assert rows[0]["syncable"] is True
    assert rows[1]["syncable"] is False
    assert rows[1]["claude_event"] is None


def test_display_names_derive_from_configured_hook_names():
    assert claude_hooks.display_name(
        named_spec(
            "pre_llm_call",
            "/Users/example/graphify_project_context.py",
            "graphify_context",
        )
    ) == "Graphify Context"
    assert claude_hooks.display_name(
        named_spec(
            "pre_llm_call",
            "/Users/example/project_change_skills.py",
            "project_change_skills",
        )
    ) == "Project Change Skills"
    assert claude_hooks.display_name(
        named_spec(
            "post_tool_call",
            "/Users/example/project_change_skills.py",
            "project_skill_tracking",
        )
    ) == "Project Skill Tracking"
    assert claude_hooks.display_name(
        named_spec(
            "pre_tool_call",
            "/Users/example/project_change_skills.py",
            "experiment_records_and_file_management",
        )
    ) == "Experiment Records and File Management"


def test_named_hook_uses_canonical_id():
    spec = named_spec(
        "pre_llm_call",
        "/Users/example/graphify_project_context.py",
        "graphify_context",
    )
    assert claude_hooks.hook_id(spec) == "hook-graphify_context"


def test_native_result_translates_block_and_modify():
    blocked = claude_hook_bridge._native_result(
        "pre_tool_call",
        {"action": "block", "message": "protected"},
        {"tool_input": {"command": "rm -rf /"}},
    )
    assert blocked == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "protected",
        }
    }

    modified = claude_hook_bridge._native_result(
        "pre_tool_call",
        {"action": "modify", "args": {"command": "git status"}},
        {"tool_input": {"command": "git diff", "description": "inspect"}},
    )
    assert modified["hookSpecificOutput"]["updatedInput"] == {
        "command": "git status",
        "description": "inspect",
    }


def test_bridge_runs_hermes_hook_and_maps_claude_tool_name(tmp_path, monkeypatch, capsys):
    hook = tmp_path / "hook.py"
    hook.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "payload = json.load(sys.stdin)\n"
        "assert payload['hook_event_name'] == 'pre_tool_call'\n"
        "assert payload['tool_name'] == 'terminal'\n"
        "assert payload['tool_input']['command'] == 'rm -rf /'\n"
        "print(json.dumps({'action': 'block', 'message': 'blocked by test'}))\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "hermes-claude-hook-sync-v1",
                "hooks": [
                    {
                        "event": "pre_tool_call",
                        "command": str(hook),
                        "matcher": "terminal",
                        "timeout": 10,
                        "fail_closed": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_KANBAN_TASK", "task-1")

    assert claude_hook_bridge.run_once(
        manifest,
        0,
        {
            "hook_event_name": "PreToolUse",
            "session_id": "session-1",
            "cwd": str(tmp_path),
            "tool_name": "Bash",
            "tool_input": {"command": "rm -rf /"},
            "tool_use_id": "tool-1",
        },
    ) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert output["hookSpecificOutput"]["permissionDecisionReason"] == "blocked by test"


def test_stored_selection_survives_the_name_field_flipping():
    """A saved choice must still reach the hook after Hermes re-exposes ``name``."""
    gate = ShellHookSpec(event="pre_tool_call", command="/tmp/gate.sh", matcher="write_file")
    hashed = claude_hooks.hook_id(gate)
    gate.name = "gate"

    assert claude_hooks.hook_id(gate) == "hook-gate"
    assert hashed in claude_hooks.hook_ids(gate)
    assert claude_hooks.stored_selection({hashed: False}, gate) is False
    assert claude_hooks.stored_selection({}, gate) is None


def test_build_settings_uses_a_selection_saved_under_the_other_id_form(
    monkeypatch, tmp_path
):
    gate = ShellHookSpec(event="pre_tool_call", command="/tmp/gate.sh")
    skipped = ShellHookSpec(event="post_tool_call", command="/tmp/skipped.sh")
    hashed = claude_hooks.hook_id(gate)
    gate.name = "gate"
    monkeypatch.setattr(
        claude_hooks, "_approved_specs", lambda _home: ([gate, skipped], [])
    )

    settings = claude_hooks.build_settings(
        str(tmp_path),
        state_dir=tmp_path,
        task_id="task-alias",
        selection={hashed: True},
    )

    assert settings is not None
    assert list(settings["hooks"]) == ["PreToolUse"]


def test_orphan_selections_reports_ids_no_hook_reaches(monkeypatch, tmp_path):
    gate = ShellHookSpec(event="pre_tool_call", command="/tmp/gate.sh")
    monkeypatch.setattr(claude_hooks, "_load_profile_config", lambda _home: {})
    monkeypatch.setattr(claude_hooks, "iter_configured_hooks", lambda _cfg: [gate])

    assert (
        claude_hooks.orphan_selections(
            str(tmp_path), {claude_hooks.hook_id(gate): False}
        )
        == []
    )
    assert claude_hooks.orphan_selections(
        str(tmp_path), {"hook-renamed_gate": True}
    ) == ["hook-renamed_gate"]
