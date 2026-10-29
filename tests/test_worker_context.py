from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from workers_core import common, config
from workers_core.backends import claude_runner, claudecode


def _profile(root: Path, label: str):
    home = root / label
    workspace = root / f"workspace-{label}"
    home.mkdir()
    workspace.mkdir()
    (home / "config.yaml").write_text(f"agent:\n  system_prompt: persona-{label}\n")
    (home / "SOUL.md").write_text(f"soul-{label}")
    (workspace / "AGENTS.md").write_text(f"project-{label}")
    (home / "memories").mkdir()
    (home / "memories" / "MEMORY.md").write_text(f"memory-{label}")
    (home / "memories" / "USER.md").write_text(f"user-{label}")
    return home, workspace


def _context(home, workspace):
    return common._profile_context_prompt(
        task_id="context-card", session_id="context-session", hermes_home=str(home),
        workspace=str(workspace), toolsets=[],
    )


def test_identity_persona_project_memory_and_user_are_profile_scoped(tmp_path):
    from hermes_constants import get_hermes_home
    from agent import runtime_cwd

    profiles = {name: _profile(tmp_path, name) for name in ("a", "b")}
    before_home = get_hermes_home()
    before_cwd = runtime_cwd.scoped_session_cwd()
    for label in ("a", "b", "a"):
        prompt = _context(*profiles[label])
        for kind in ("soul", "persona", "project", "memory", "user"):
            assert f"{kind}-{label}" in prompt
            assert f"{kind}-{'b' if label == 'a' else 'a'}" not in prompt
        assert get_hermes_home() == before_home
        assert runtime_cwd.scoped_session_cwd() == before_cwd


def test_snapshot_is_appended_and_reused_after_disk_changes(tmp_path, monkeypatch):
    home, workspace = _profile(tmp_path, "a")
    snapshot = _context(home, workspace)
    run = tmp_path / "run"
    run.mkdir()
    path = claudecode._system_prompt_file(run, snapshot)
    monkeypatch.setattr(claudecode, "_resolve_binary", lambda _value: "claude")
    monkeypatch.setattr(claudecode, "_cli_flags", lambda _binary: frozenset({
        "--append-system-prompt-file", "--session-id", "--resume"
    }))
    task = SimpleNamespace(model_override=None, provider_override=None, reasoning_effort=None, goal_mode=False)
    command = claudecode.build_command(
        task, str(workspace), {"model": "provider/model"}, {}, run / "mcp.json",
        prompt="work context-card", session_id="context-session", system_prompt_path=path,
    )
    assert command[command.index("--append-system-prompt-file") + 1] == str(path)
    assert "--system-prompt" not in command
    (home / "memories" / "MEMORY.md").write_text("changed after worker startup")
    resumed = claude_runner._resume_command(command, "context-session", "continue")
    assert resumed[resumed.index("--append-system-prompt-file") + 1] == str(path)
    assert path.read_text() == snapshot
    assert "changed after worker startup" not in snapshot


@pytest.mark.parametrize("disabled", [None, "profile_context", "skills", *config.INHERITANCE_DEFAULTS])
def test_inheritance_switches_independently_control_injected_context(tmp_path, monkeypatch, disabled):
    policy = config.inheritance_policy({"inheritance": {} if disabled is None else {disabled: False}})
    """Real profile and project loaders retain their boundaries when one switch is off."""
    from agent import prompt_builder, skill_commands
    import hermes_cli.plugins as plugins
    import plugins.memory as memory_plugins
    import tools.memory_tool as memory_tool
    from hermes_constants import get_hermes_home
    from agent import runtime_cwd

    home, workspace = _profile(tmp_path, "selected")
    monkeypatch.setattr(plugins, "render_system_prompt_sections", lambda _ctx: "PLUGIN CONTEXT")
    monkeypatch.setattr(plugins, "format_system_prompt_sections", lambda value: value)
    monkeypatch.setattr(memory_tool, "get_builtin_memory_config", lambda _cfg: {"provider": "test"})
    monkeypatch.setattr(memory_tool, "get_builtin_memory_store_flags", lambda _cfg: (True, True))
    monkeypatch.setattr(memory_plugins, "load_memory_provider", lambda *a, **k: SimpleNamespace(
        is_available=lambda: True, system_prompt_block=lambda: "EXTERNAL MEMORY",
    ))
    skill_calls = []

    def auto(**kwargs):
        skill_calls.append("auto")
        return "AUTO-LOADED SKILL", ["auto"], []

    def index(**kwargs):
        skill_calls.append("index")
        return "SKILL INDEX"

    def preloaded(names, **kwargs):
        skill_calls.append("preloaded")
        assert names == ["card-skill"]
        assert kwargs["excluded_loaded_names"] == ({"auto"} if policy["auto_skills"] else set())
        return "CARD SKILL BODY", ["card-skill"], []

    monkeypatch.setattr(skill_commands, "build_auto_load_prompt", auto)
    monkeypatch.setattr(prompt_builder, "build_skills_system_prompt", index)
    monkeypatch.setattr(skill_commands, "build_preloaded_skills_prompt", preloaded)
    monkeypatch.delenv("HERMES_IGNORE_RULES", raising=False)
    before_home, before_cwd = get_hermes_home(), runtime_cwd.scoped_session_cwd()
    prompt = common._profile_context_prompt(
        task_id="card", session_id="session", hermes_home=str(home),
        workspace=str(workspace), toolsets=["skills"], skills=["card-skill"],
        inheritance={} if disabled is None else {disabled: False},
    )
    markers = {"soul": "soul-selected", "personality": "persona-selected", "memory": "memory-selected", "user_profile": "user-selected", "plugin_context": "PLUGIN CONTEXT", "external_memory": "EXTERNAL MEMORY", "project_rules": "project-selected", "auto_skills": "AUTO-LOADED SKILL", "skill_index": "SKILL INDEX", "card_skills": "CARD SKILL BODY"}
    for key, marker in markers.items():
        active = policy[key] and (policy["hermes_tools"] if key == "skill_index" else True)
        assert (marker in prompt) is active, key
    assert skill_calls == [name for name, enabled in (("auto", policy["auto_skills"]), ("index", policy["skill_index"] and policy["hermes_tools"]), ("preloaded", policy["card_skills"])) if enabled]
    assert get_hermes_home() == before_home
    assert runtime_cwd.scoped_session_cwd() == before_cwd


def test_skills_off_skips_unavailable_card_preload_without_removing_discovery_tools(tmp_path):
    home, workspace = _profile(tmp_path, "selected")
    prompt = common._profile_context_prompt(
        task_id="card", session_id="session", hermes_home=str(home),
        workspace=str(workspace), toolsets=["skills"], skills=["missing-skill"],
        inheritance={"skills": False},
    )
    assert "project-selected" in prompt
    assert "missing-skill" not in prompt


def test_all_prompt_inheritance_off_does_not_call_injected_context_loaders(tmp_path, monkeypatch):
    from agent import prompt_builder, skill_commands
    import hermes_cli.plugins as plugins
    import tools.memory_tool as memory_tool
    from hermes_constants import get_hermes_home
    from agent import runtime_cwd

    home, workspace = _profile(tmp_path, "selected")

    def fail(*args, **kwargs):
        raise AssertionError("Disabled inherited context must not be loaded")

    for module, names in [
        (prompt_builder, ["load_soul_md", "build_context_files_prompt", "build_skills_system_prompt"]),
        (skill_commands, ["build_auto_load_prompt", "build_preloaded_skills_prompt"]),
        (plugins, ["render_system_prompt_sections"]),
        (memory_tool, ["load_on_disk_store", "get_builtin_memory_config"]),
    ]:
        for name in names:
            monkeypatch.setattr(module, name, fail)
    before_home, before_cwd = get_hermes_home(), runtime_cwd.scoped_session_cwd()
    assert common._profile_context_prompt(
        task_id="card", session_id="session", hermes_home=str(home),
        workspace=str(workspace), toolsets=["skills"], skills=["missing-skill"],
        inheritance={"profile_context": False, "project_rules": False, "skills": False},
    ) == ""
    assert get_hermes_home() == before_home
    assert runtime_cwd.scoped_session_cwd() == before_cwd
