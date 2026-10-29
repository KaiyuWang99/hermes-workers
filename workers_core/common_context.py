"""Worker prompt context: kanban protocol plus selected Hermes context."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Iterable, Mapping


logger = logging.getLogger(__name__)


def protocol_prompt(task_id: str) -> str:
    # Reuse Hermes' own kanban contract verbatim so the CLI worker tracks core's
    # lifecycle/review/orchestrator rules instead of drifting on a hand copy.
    from agent.prompt_builder import KANBAN_GUIDANCE

    parts = [
        f"You are the Hermes Kanban Worker for Card {task_id}.",
        KANBAN_GUIDANCE.strip(),
    ]
    return "\n".join(parts) + "\n"


def _profile_context_prompt(
    *,
    task_id: str,
    session_id: str,
    hermes_home: str,
    workspace: str,
    toolsets: Iterable[str],
    skills: Iterable[str] = (),
    inheritance: Mapping[str, bool] | None = None,
) -> str:
    """Render selected Hermes context; CLI-native context loading is unchanged."""
    from .config import inheritance_policy

    policy = inheritance_policy(
        {"inheritance": inheritance} if inheritance is not None else None
    )
    from agent import runtime_cwd
    from agent.prompt_builder import (
        build_context_files_prompt,
        build_skills_system_prompt,
        load_soul_md,
    )
    from agent.skill_commands import build_auto_load_prompt, build_preloaded_skills_prompt
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from hermes_cli.config import load_config
    from hermes_cli.personality import resolve_ephemeral_system_prompt
    from hermes_cli.plugins import (
        format_system_prompt_sections,
        render_system_prompt_sections,
    )
    from tools.memory_tool import (
        get_builtin_memory_config,
        get_builtin_memory_store_flags,
        load_on_disk_store,
    )

    home = Path(hermes_home).expanduser().resolve()
    cwd = Path(workspace).expanduser().resolve()
    home_token = set_hermes_home_override(str(home))
    cwd_token = runtime_cwd.set_session_cwd(str(cwd))
    try:
        config = load_config() if any(policy[key] for key in (
            "personality", "user_profile", "memory", "external_memory", "auto_skills"
        )) else {}
        parts: list[str] = []

        if policy["soul"]:
            soul = load_soul_md(home_override=home)
            if soul:
                parts.extend(["## Profile identity", soul])

        if policy["personality"]:
            personality = resolve_ephemeral_system_prompt(config)
            if personality:
                parts.extend(["## Profile instructions", personality])

        if policy["project_rules"]:
            project_context = build_context_files_prompt(
                cwd=str(cwd), skip_soul=True, home_override=home
            )
            if project_context:
                parts.append(project_context)

        selected_toolsets = {str(name) for name in toolsets}
        auto_loaded: list[str] = []
        if policy["auto_skills"] and "skills" in selected_toolsets:
            from utils import is_truthy_value

            if not is_truthy_value(os.environ.get("HERMES_IGNORE_RULES")):
                auto_prompt, auto_loaded, missing_auto = build_auto_load_prompt(
                    task_id=session_id, user_config=config, home_override=home
                )
                if auto_prompt:
                    parts.append(auto_prompt)
                if missing_auto:
                    logger.warning("skills.auto_load unavailable: %s", ", ".join(missing_auto))
        if policy["skill_index"] and policy["hermes_tools"] and "skills" in selected_toolsets:
            skills_index = build_skills_system_prompt(
                available_toolsets=selected_toolsets, skills_dir_override=home / "skills"
            )
            if skills_index:
                parts.append(skills_index)
        if policy["card_skills"]:
            skill_prompt, _loaded, missing = build_preloaded_skills_prompt(
                list(skills), task_id=session_id, excluded_loaded_names=set(auto_loaded)
            )
            if missing:
                raise RuntimeError(f"missing or unavailable Skills: {', '.join(missing)}")
            if skill_prompt:
                parts.append(skill_prompt)

        if policy["memory"] or policy["user_profile"]:
            memory_enabled, user_enabled = get_builtin_memory_store_flags(config)
            if (memory_enabled and policy["memory"]) or (user_enabled and policy["user_profile"]):
                memory_store = load_on_disk_store()
                if memory_enabled and policy["memory"]:
                    memory = memory_store.format_for_system_prompt("memory")
                    if memory:
                        parts.extend(["## Profile memory", memory])
                if user_enabled and policy["user_profile"]:
                    user = memory_store.format_for_system_prompt("user")
                    if user:
                        parts.extend(["## User profile", user])

        if policy["external_memory"]:
            provider_name = str(
                get_builtin_memory_config(config).get("provider") or ""
            ).strip()
            if provider_name:
                from plugins.memory import load_memory_provider

                provider = load_memory_provider(provider_name, register_skills=False)
                if provider is not None and provider.is_available():
                    provider_context = provider.system_prompt_block()
                    if provider_context:
                        parts.extend(["## External memory context", provider_context])

        if policy["plugin_context"]:
            plugin_context = format_system_prompt_sections(
                render_system_prompt_sections(
                    {
                        "session_id": session_id,
                        "task_id": task_id,
                        "platform": "cli",
                        "source": "kanban",
                    }
                )
            )
            if plugin_context:
                parts.extend(["## Hermes plugin context", plugin_context])
        return "\n\n".join(parts).strip()
    finally:
        runtime_cwd.reset_session_cwd(cwd_token)
        reset_hermes_home_override(home_token)
