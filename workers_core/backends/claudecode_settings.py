"""Constants and pure settings helpers for the Claude Code backend."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

_CLAUDE_AUTH_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_OAUTH_TOKEN",
)
_CLAUDE_CAPABILITY_ENV_VARS = (
    "ANTHROPIC_DEFAULT_FABLE_MODEL_SUPPORTED_CAPABILITIES",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL_SUPPORTED_CAPABILITIES",
    "ANTHROPIC_DEFAULT_OPUS_MODEL_SUPPORTED_CAPABILITIES",
    "ANTHROPIC_DEFAULT_SONNET_MODEL_SUPPORTED_CAPABILITIES",
    "ANTHROPIC_CUSTOM_MODEL_OPTION_SUPPORTED_CAPABILITIES",
)
_CLAUDE_SETTINGS_ENV_VARS = (
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_MODEL",
    "ANTHROPIC_DEFAULT_FABLE_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_CUSTOM_MODEL_OPTION",
    "ANTHROPIC_CUSTOM_MODEL_OPTION_NAME",
    "ANTHROPIC_CUSTOM_HEADERS",
    "CLAUDE_CODE_SUBAGENT_MODEL",
    "CLAUDE_CODE_MAX_CONTEXT_TOKENS",
)
_AUTH_STYLE_VARS = {
    "bearer": ("ANTHROPIC_AUTH_TOKEN",),
    "api_key": ("ANTHROPIC_API_KEY",),
    "both": ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"),
}


def defaults() -> dict[str, Any]:
    from ..config import _which

    return {
        "enabled": False,
        "binary": _which("claude", str(Path.home() / ".local/bin/claude")),
        "model": "",
        "reasoning_effort": "",
        "permission_mode": "",
        "no_session_persistence": False,
        "strict_mcp_config": False,
        "pin_model_aliases": True,
        "pin_subagent_model": True,
        "silent_timeout_seconds": 0,
        "sync_hermes_hooks": False,
    }


def validate_settings(entry: dict[str, Any]) -> dict[str, Any]:
    """Normalize Claude Code settings without discarding future user fields."""
    if not isinstance(entry, Mapping):
        raise ValueError("workers.claude-code must be an object")
    result = dict(entry)
    result.setdefault("strict_mcp_config", False)
    for key in (
        "strict_mcp_config",
        "pin_model_aliases",
        "pin_subagent_model",
        "native_skills",
        "no_session_persistence",
    ):
        if key in result and not isinstance(result[key], bool):
            raise ValueError(f"workers.claude-code.{key} must be a boolean")
    sources = result.get("settings_sources")
    if sources is not None:
        if not isinstance(sources, list) or any(
            not isinstance(source, str) or not source.strip() for source in sources
        ):
            raise ValueError(
                "workers.claude-code.settings_sources must be a list of non-empty source names"
            )
        # Accepted values are checked against the installed CLI's --help choices
        # in validate_selection; this keeps the config layer CLI-version agnostic.
        result["settings_sources"] = list(
            dict.fromkeys(source.strip() for source in sources)
        )
    silent_timeout = result.get("silent_timeout_seconds", 0)
    if (
        isinstance(silent_timeout, bool)
        or not isinstance(silent_timeout, (int, float))
        or not 0 <= silent_timeout < float("inf")
    ):
        raise ValueError(
            "workers.claude-code.silent_timeout_seconds must be a nonnegative number"
        )
    for key in ("tools", "disallowed_tools"):
        values = result.get(key)
        if values is not None and (
            not isinstance(values, list)
            or any(not isinstance(item, str) or not item.strip() for item in values)
        ):
            raise ValueError(
                f"workers.claude-code.{key} must be a list of non-empty tool names"
            )
    sync_hermes_hooks = result.get("sync_hermes_hooks", False)
    if not isinstance(sync_hermes_hooks, bool):
        raise ValueError("workers.claude-code.sync_hermes_hooks must be a boolean")
    result["sync_hermes_hooks"] = sync_hermes_hooks
    if "hermes_hook_selection" in result:
        selection = result["hermes_hook_selection"]
        if not isinstance(selection, Mapping):
            raise ValueError(
                "workers.claude-code.hermes_hook_selection must be an object"
            )
        normalized_selection: dict[str, bool] = {}
        for hook_id, selected in selection.items():
            if not isinstance(selected, bool):
                raise ValueError(
                    "workers.claude-code.hermes_hook_selection values must be booleans"
                )
            normalized_selection[str(hook_id)] = selected
        result["hermes_hook_selection"] = normalized_selection
    if not str(result.get("binary") or "").strip():
        raise ValueError("workers.claude-code.binary is required")
    if bool(result.get("enabled")) and not str(result.get("model") or "").strip():
        raise ValueError("workers.claude-code.model is required when enabled")
    return result


def model_options(
    entry: dict[str, Any],
    *,
    model: str,
    refresh: bool = False,
    scope: str = "",
    models_loader: Any = None,
) -> dict[str, Any]:
    from ..options import _claude_options

    return _claude_options(
        entry,
        model=model,
        refresh=refresh,
        scope=scope,
        models_loader=models_loader,
    )
