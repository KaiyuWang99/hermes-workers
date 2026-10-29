"""Translate approved Hermes shell hooks into task-scoped Claude settings.

Hermes remains the source of truth.  This module only projects the
config-driven ``hooks:`` entries that have an equivalent Claude Code event;
the original command is executed by ``claude_hook_bridge`` so its Hermes wire
format and fail-closed semantics remain intact.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import tempfile
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Mapping

from agent.shell_hooks import (
    ShellHookSpec,
    allowlist_entry_for,
    iter_configured_hooks,
    script_is_executable,
)


logger = logging.getLogger(__name__)


# These events have close Claude Code equivalents.  ``pre_llm_call`` is
# projected as ``UserPromptSubmit`` so config-driven Hermes shell hooks keep
# working inside the external Claude process.  The runner separately keeps
# its Hermes-side pre_llm_call path for Python plugin hooks.
HERMES_TO_CLAUDE_EVENTS = {
    "pre_tool_call": "PreToolUse",
    "post_tool_call": "PostToolUse",
    "pre_llm_call": "UserPromptSubmit",
    "on_session_start": "SessionStart",
    "on_session_end": "SessionEnd",
    "pre_verify": "Stop",
    "subagent_start": "SubagentStart",
    "subagent_stop": "SubagentStop",
}


def spec_name(spec: ShellHookSpec) -> str:
    """Return the optional hook name, tolerating installs that dropped it.

    Current Hermes ``ShellHookSpec`` carries no ``name`` field, so reading the
    attribute directly raises AttributeError and breaks the whole hook
    projection.  Named hooks still work where the field exists.
    """
    return str(getattr(spec, "name", "") or "").strip()


def _hashed_hook_id(spec: ShellHookSpec) -> str:
    raw = "\x1f".join(
        (
            spec.event,
            spec.matcher or "",
            spec.command,
        )
    ).encode("utf-8")
    return f"hook-{sha256(raw).hexdigest()[:16]}"


def hook_id(spec: ShellHookSpec) -> str:
    """Return a stable id for one Hermes hook definition."""
    name = spec_name(spec)
    if name:
        return f"hook-{name}"
    return _hashed_hook_id(spec)


def hook_ids(spec: ShellHookSpec) -> frozenset[str]:
    """Every id this hook can be stored under.

    Hermes has added and dropped ``ShellHookSpec.name`` between versions, which flips
    :func:`hook_id`.  A selection saved under the other form still has to resolve, or
    a hook the operator switched off silently re-enters the projection.
    """
    return frozenset({hook_id(spec), _hashed_hook_id(spec)})


def stored_selection(
    selection: Mapping[str, Any] | None, spec: ShellHookSpec
) -> bool | None:
    """The saved choice for this hook, reached through either id form.

    ``None`` means nothing was ever saved for it, which callers read as
    "fall back to the global switch".
    """
    if not isinstance(selection, Mapping):
        return None
    primary = hook_id(spec)
    for identifier in (primary, *sorted(hook_ids(spec) - {primary})):
        if identifier in selection:
            return bool(selection[identifier])
    return None


def orphan_selections(
    hermes_home: str, selection: Mapping[str, Any] | None
) -> list[str]:
    """Stored hook ids that match no configured Hermes hook, in either id form.

    An id that matches nothing is how a hook rename or a ``ShellHookSpec`` field change
    shows up: the selection silently stops governing anything.
    """
    if not isinstance(selection, Mapping) or not selection:
        return []
    known: set[str] = set()
    for spec in iter_configured_hooks(_load_profile_config(hermes_home)):
        known |= set(hook_ids(spec))
    return sorted(str(key) for key in selection if str(key) not in known)


def display_name(spec: ShellHookSpec) -> str:
    """Return a compact functional label for the Workers UI."""
    name = spec_name(spec)
    if name:
        words = name.replace("_", " ").replace("-", " ").split()
        if words:
            connectors = {"and", "or", "of", "the", "to", "in", "for"}
            return " ".join(
                word.lower()
                if index > 0 and word.lower() in connectors
                else word[:1].upper() + word[1:]
                for index, word in enumerate(words)
            )
    return spec.event


def _load_profile_config(hermes_home: str) -> dict[str, Any]:
    """Load the Hermes config belonging to this Worker profile."""
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from hermes_cli.config import load_config

    token = set_hermes_home_override(hermes_home)
    try:
        return load_config()
    finally:
        reset_hermes_home_override(token)


def _approved_specs(
    hermes_home: str,
    *,
    config: dict[str, Any] | None = None,
) -> tuple[list[ShellHookSpec], list[str]]:
    """Return approved compatible specs and unsupported event names.

    Hermes' normal startup only registers allowlisted shell hooks.  Syncing a
    hook must not bypass that consent boundary, so unapproved entries are
    excluded even when the Worker sync switch is enabled.
    """
    profile_config = config if config is not None else _load_profile_config(hermes_home)
    specs = iter_configured_hooks(profile_config)
    compatible: list[ShellHookSpec] = []
    unsupported: list[str] = []

    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    token = set_hermes_home_override(hermes_home)
    try:
        for spec in specs:
            if spec.event not in HERMES_TO_CLAUDE_EVENTS:
                unsupported.append(spec.event)
                continue
            if allowlist_entry_for(spec.event, spec.command) is None:
                logger.warning(
                    "Hermes hook is configured but not approved; skipping Claude "
                    "Worker sync: event=%s command=%s",
                    spec.event,
                    spec.command,
                )
                continue
            compatible.append(spec)
    finally:
        reset_hermes_home_override(token)
    return compatible, sorted(set(unsupported))


def list_hooks(
    hermes_home: str,
    *,
    selection: Mapping[str, Any] | None = None,
    sync_enabled: bool = False,
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return a sanitized hook inventory for the Workers UI.

    Hook definitions come from Hermes.  Workers stores only the per-hook
    selection; command text is returned for display and is never copied into
    the durable Workers configuration.
    """
    profile_config = config if config is not None else _load_profile_config(hermes_home)
    specs = iter_configured_hooks(profile_config)
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    selected_map = selection if isinstance(selection, Mapping) else None
    token = set_hermes_home_override(hermes_home)
    try:
        rows: list[dict[str, Any]] = []
        for spec in specs:
            identifier = hook_id(spec)
            approved = allowlist_entry_for(spec.event, spec.command) is not None
            syncable = spec.event in HERMES_TO_CLAUDE_EVENTS
            stored = stored_selection(selected_map, spec)
            if stored is None:
                # Preserve the previous all-approved behavior for an existing
                # config that enabled the old global switch before per-hook
                # selections were introduced.  Once the UI saves a selection
                # map, its explicit values take precedence.
                selected = bool(sync_enabled and approved and syncable)
            else:
                selected = stored
            rows.append(
                {
                    "id": identifier,
                    "name": spec_name(spec),
                    "display_name": display_name(spec),
                    "event": spec.event,
                    "command": spec.command,
                    "matcher": spec.matcher,
                    "timeout": spec.timeout,
                    "fail_closed": spec.fail_closed,
                    "approved": approved,
                    "executable": script_is_executable(spec.command),
                    "syncable": syncable,
                    "claude_event": HERMES_TO_CLAUDE_EVENTS.get(spec.event),
                    "selected": selected,
                }
            )
        return rows
    finally:
        reset_hermes_home_override(token)


def _safe_id(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value) or "task"


def _write_manifest(
    specs: Iterable[ShellHookSpec],
    *,
    state_dir: Path,
    task_id: str,
) -> Path:
    state_dir.mkdir(parents=True, exist_ok=True)
    target = state_dir / f"workers-claude-hooks-{_safe_id(task_id)}.json"
    payload = {
        "schema": "hermes-claude-hook-sync-v1",
        "hooks": [
            {
                "event": spec.event,
                "command": spec.command,
                "matcher": spec.matcher,
                "timeout": spec.timeout,
                "fail_closed": spec.fail_closed,
            }
            for spec in specs
        ],
    }
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix="workers-claude-hooks-",
        suffix=".json",
        dir=str(state_dir),
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    finally:
        handle.close()
    os.chmod(temporary, 0o600)
    os.replace(temporary, target)
    return target


def _bridge_command(manifest: Path, hook_index: int) -> str:
    from .common import worker_runner_command

    return shlex.join(
        [
            *worker_runner_command("workers_core.claude_hook_bridge"),
            "--manifest",
            str(manifest),
            "--hook-index",
            str(hook_index),
        ]
    )


def settings_from_specs(
    specs: Iterable[ShellHookSpec],
    *,
    state_dir: Path,
    task_id: str,
) -> dict[str, Any] | None:
    """Build Claude's native settings fragment for a list of Hermes specs."""
    selected = list(specs)
    if not selected:
        return None
    manifest = _write_manifest(selected, state_dir=state_dir, task_id=task_id)
    hooks: dict[str, list[dict[str, Any]]] = {}
    for index, spec in enumerate(selected):
        claude_event = HERMES_TO_CLAUDE_EVENTS[spec.event]
        # The bridge applies Hermes' original matcher after translating the
        # Claude tool name.  Keeping Claude's matcher empty avoids lossy regex
        # rewriting and preserves arbitrary Hermes matchers.
        hooks.setdefault(claude_event, []).append(
            {
                "matcher": "",
                "hooks": [
                    {
                        "type": "command",
                        "command": _bridge_command(manifest, index),
                        "timeout": spec.timeout,
                    }
                ],
            }
        )
    return {"hooks": hooks}


def build_settings(
    hermes_home: str,
    *,
    state_dir: Path,
    task_id: str,
    selection: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Load and project approved Hermes hooks for one Claude Worker task."""
    if os.environ.get("HERMES_SAFE_MODE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        logger.info("HERMES_SAFE_MODE is active; skipping Claude Worker hook sync")
        return None
    specs, unsupported = _approved_specs(hermes_home)
    if isinstance(selection, Mapping):
        specs = [spec for spec in specs if bool(stored_selection(selection, spec))]
    if unsupported:
        logger.info(
            "Hermes hook events are not projected to Claude Code: %s",
            ", ".join(unsupported),
        )
    return settings_from_specs(specs, state_dir=state_dir, task_id=task_id)
