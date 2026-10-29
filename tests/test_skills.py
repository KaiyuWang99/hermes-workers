from __future__ import annotations

from pathlib import Path

import pytest

from workers_core.common import _profile_context_prompt


def _skill(path: Path, name: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'---\nname: {name}\ndescription: "{name} skill"\n---\n\n{body}\n')


def _context(home: Path, workspace: Path, skills=()):
    return _profile_context_prompt(
        task_id="test-card", session_id="test-session", hermes_home=str(home),
        workspace=str(workspace), toolsets=["skills"], skills=skills,
    )


@pytest.fixture
def profile(tmp_path):
    home = tmp_path / "profile"
    workspace = tmp_path / "project"
    home.mkdir()
    workspace.mkdir()
    (home / "config.yaml").write_text("skills:\n  auto_load: []\n")
    return home, workspace


def test_native_preload_includes_full_skill_and_supporting_paths(profile):
    home, workspace = profile
    skill = home / "skills" / "devops" / "review" / "SKILL.md"
    _skill(skill, "review", "FULL REVIEW BODY")
    (skill.parent / "references").mkdir()
    (skill.parent / "references" / "guide.md").write_text("support")
    prompt = _context(home, workspace, ["review"])
    assert "FULL REVIEW BODY" in prompt
    assert str(skill.parent) in prompt
    assert "references/guide.md" in prompt
    assert "## Required Skill:" not in prompt


def test_native_preload_refuses_disabled_and_missing_skills(profile):
    home, workspace = profile
    _skill(home / "skills" / "review" / "SKILL.md", "review", "BODY")
    (home / "config.yaml").write_text("skills:\n  disabled: [review]\n")
    for name in ("review", "does-not-exist"):
        with pytest.raises(RuntimeError, match="missing or unavailable Skills"):
            _context(home, workspace, [name])


def test_native_auto_load_and_card_skill_are_not_duplicated(profile):
    home, workspace = profile
    _skill(home / "skills" / "review" / "SKILL.md", "review", "UNIQUE FULL BODY")
    (home / "config.yaml").write_text("skills:\n  auto_load: [review]\n")
    prompt = _context(home, workspace, ["review"])
    assert prompt.count("UNIQUE FULL BODY") == 1
    assert "auto-loaded via config" in prompt


def test_native_auto_load_typo_does_not_block_worker(profile, caplog):
    home, workspace = profile
    (home / "config.yaml").write_text("skills:\n  auto_load: [does-not-exist]\n")
    _context(home, workspace)
    assert "skills.auto_load unavailable" in caplog.text


def test_native_context_loader_preserves_file_precedence(profile):
    home, workspace = profile
    (workspace / "CLAUDE.md").write_text("CLAUDE PROJECT RULE")
    assert "CLAUDE PROJECT RULE" in _context(home, workspace)
    (workspace / "AGENTS.md").write_text("AGENTS PROJECT RULE")
    prompt = _context(home, workspace)
    assert "AGENTS PROJECT RULE" in prompt
    assert "CLAUDE PROJECT RULE" not in prompt
    (workspace / "HERMES.md").write_text("HERMES PROJECT RULE")
    prompt = _context(home, workspace)
    assert "HERMES PROJECT RULE" in prompt
    assert "AGENTS PROJECT RULE" not in prompt

