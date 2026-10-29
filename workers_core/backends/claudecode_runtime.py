"""Claude Code route resolution and provider environment wiring."""

from __future__ import annotations

from typing import Any

from ..credentials import pool_provider, require_credentials, select_credential
from ..provider_catalog import SUPPORTED_API_MODES
from .claudecode_settings import (
    _AUTH_STYLE_VARS,
    _CLAUDE_AUTH_ENV_VARS,
    _CLAUDE_CAPABILITY_ENV_VARS,
)


def _auth_style(runtime: dict[str, Any]) -> str:
    """Which credential variables Claude Code should publish for this route.

    The loopback bridge ignores the value entirely, so it gets a single
    deterministic variable.  Direct Anthropic-compatible gateways differ: some
    only accept ``x-api-key`` and reject a Bearer token, so unless the provider
    metadata says otherwise we publish both, matching the long-standing
    behaviour of the direct route.
    """
    style = str(runtime.get("auth_style") or "").strip().lower()
    if style in _AUTH_STYLE_VARS:
        return style
    return "bearer" if runtime.get("route") == "builtin" else "both"


def _split_target(value: str) -> tuple[str, str]:
    provider, separator, model = str(value or "").strip().partition("/")
    if not separator or not provider or not model:
        raise RuntimeError(
            f"Claude Code worker model must use provider/model format: {value!r}"
        )
    return pool_provider(provider), model


def _effective_target(task: Any, entry: dict[str, Any]) -> tuple[str, str]:
    requested_model = str(getattr(task, "model_override", "") or "").strip()
    requested_provider = str(
        getattr(task, "provider_override", "") or ""
    ).strip().lower()
    if requested_model and requested_provider:
        return pool_provider(requested_provider), requested_model.split("/", 1)[-1]
    if requested_model and "/" in requested_model:
        return _split_target(requested_model)
    if requested_model:
        raise RuntimeError(
            "Card model_override must use provider/model format or be paired "
            "with provider_override"
        )
    configured_provider, configured_model = _split_target(
        str(entry.get("model") or "")
    )
    return configured_provider, requested_model or configured_model


def _provider_runtime(
    provider: str,
    model: str,
    fallback_entry: dict[str, Any] | None = None,
    *,
    credential_check: Any = require_credentials,
    credential_select: Any = select_credential,
) -> dict[str, Any]:
    """Resolve Hermes provider metadata and select a pool credential per spawn.

    The credential hooks are injectable so the backend entry point can keep
    honoring overrides/tests that patch its module-level credential functions.
    """
    explicit_key = ""
    if fallback_entry:
        from hermes_cli.fallback_config import resolve_entry_api_key

        explicit_key = str(resolve_entry_api_key(fallback_entry) or "").strip()
    if explicit_key:
        credential = {"credential_id": "", "key": explicit_key}
    else:
        credential_check(provider, model=model)
        credential = credential_select(provider, model=model)
    if not credential or not str(credential.get("key") or "").strip():
        raise RuntimeError(
            f"No available Hermes credential for Claude Code provider {provider!r}"
        )

    from hermes_cli.runtime_provider import resolve_runtime_provider

    runtime = resolve_runtime_provider(
        requested=provider,
        explicit_api_key=str(credential["key"]),
        explicit_base_url=(
            str(fallback_entry.get("base_url") or "").strip() or None
            if fallback_entry
            else None
        ),
        target_model=model,
    )
    api_mode = str(runtime.get("api_mode") or "").strip()
    base_url = str(runtime.get("base_url") or "").strip().rstrip("/")
    # An explicit fallback entry may pin a route the resolver cannot infer.
    if fallback_entry:
        explicit_api_mode = str(fallback_entry.get("api_mode") or "").strip()
        if explicit_api_mode:
            api_mode = explicit_api_mode
    if not base_url:
        raise RuntimeError(f"Claude Code provider {provider!r} has no base URL")
    result: dict[str, Any] = {
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "api_mode": api_mode,
        "api_key": str(credential["key"]),
        "credential_id": str(credential.get("credential_id") or ""),
    }
    extra_headers = runtime.get("extra_headers")
    if isinstance(extra_headers, dict):
        result["extra_headers"] = dict(extra_headers)
    if fallback_entry and isinstance(fallback_entry.get("extra_headers"), dict):
        result["extra_headers"] = dict(fallback_entry["extra_headers"])
    if fallback_entry and fallback_entry.get("reasoning_echo"):
        result["reasoning_echo"] = True
    auth_style = str(
        (fallback_entry or {}).get("auth_style") or runtime.get("auth_style") or ""
    ).strip().lower()
    if auth_style in _AUTH_STYLE_VARS:
        result["auth_style"] = auth_style
    return result


def _route_runtime(runtime: dict[str, Any]) -> dict[str, Any]:
    mode = str(runtime.get("api_mode") or "").strip()
    from agent.models_dev import lookup_models_dev_context

    context_window = lookup_models_dev_context(
        runtime["provider"], runtime["model"], allow_network=False
    )
    if isinstance(context_window, int) and context_window > 0:
        runtime["context_window"] = context_window
    if mode == "anthropic_messages":
        runtime["route"] = "direct"
        runtime["client_model"] = runtime["model"]
        return runtime
    if mode not in SUPPORTED_API_MODES:
        raise RuntimeError(
            f"Claude Code worker does not support {runtime['provider']}/{runtime['model']} "
            f"with API mode {mode or 'unknown'}"
        )
    runtime["route"] = "builtin"
    runtime["client_model"] = runtime["model"]
    return runtime


def _apply_provider_env(env: dict[str, str], runtime: dict[str, Any]) -> None:
    for key in _CLAUDE_AUTH_ENV_VARS:
        env.pop(key, None)
    for key in _CLAUDE_CAPABILITY_ENV_VARS:
        env.pop(key, None)
    env.pop("CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT", None)
    env.pop("CLAUDE_CODE_MAX_CONTEXT_TOKENS", None)
    env["ANTHROPIC_BASE_URL"] = runtime["base_url"]
    for key in _AUTH_STYLE_VARS[_auth_style(runtime)]:
        env[key] = runtime["api_key"]
    client_model = str(runtime.get("client_model") or runtime["model"])
    env["ANTHROPIC_MODEL"] = client_model
    env["ANTHROPIC_DEFAULT_MODEL"] = client_model
    if runtime.get("pin_model_aliases", True):
        env["ANTHROPIC_DEFAULT_FABLE_MODEL"] = client_model
        env["ANTHROPIC_DEFAULT_OPUS_MODEL"] = client_model
        env["ANTHROPIC_DEFAULT_SONNET_MODEL"] = client_model
        env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = client_model
        env["ANTHROPIC_CUSTOM_MODEL_OPTION"] = client_model
        env["ANTHROPIC_CUSTOM_MODEL_OPTION_NAME"] = client_model
    if runtime.get("pin_subagent_model", True):
        env["CLAUDE_CODE_SUBAGENT_MODEL"] = client_model
    if runtime.get("context_window"):
        env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] = str(runtime["context_window"])
    custom_headers: dict[str, str] = {}
    for line in str(env.get("ANTHROPIC_CUSTOM_HEADERS") or "").splitlines():
        name, separator, value = line.partition(":")
        if separator and name.strip():
            custom_headers[name.strip().lower()] = f"{name.strip()}: {value.strip()}"
    extra_headers = runtime.get("extra_headers")
    if isinstance(extra_headers, dict):
        for name, value in extra_headers.items():
            if str(name).strip() and value is not None:
                custom_headers[str(name).strip().lower()] = f"{str(name).strip()}: {value}"
    if custom_headers:
        env["ANTHROPIC_CUSTOM_HEADERS"] = "\n".join(custom_headers.values())
    env["WORKERS_CLAUDE_PROVIDER"] = runtime["provider"]
    env["WORKERS_CLAUDE_CREDENTIAL_ID"] = runtime["credential_id"]
    env["WORKERS_CLAUDE_ROUTE"] = runtime.get("route", "direct")
