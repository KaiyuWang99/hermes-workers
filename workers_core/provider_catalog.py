"""Shared Hermes provider/model catalog for OpenCode and Claude Code."""

from __future__ import annotations

from typing import Any


SUPPORTED_API_MODES = {
    "anthropic_messages",
    "chat_completions",
    "responses",
    "codex_responses",
}


def model_capabilities(provider: str, model: str) -> dict[str, Any]:
    """Use Hermes metadata for this provider; absent fields stay unknown."""
    from agent.models_dev import get_model_capabilities

    metadata = get_model_capabilities(provider, model, allow_network=False)
    return {
        "known": metadata is not None,
        "supportsTools": metadata.supports_tools if metadata is not None else None,
        "supportsReasoning": metadata.supports_reasoning if metadata is not None else None,
        "supportsVision": metadata.supports_vision if metadata is not None else None,
        # Hermes' structured metadata does not expose this capability.
        "supportsInterleavedThinking": None,
    }


def resolve_model_runtime(
    provider: str, model: str, *, api_key: str = "workers-model-catalog"
) -> dict[str, Any]:
    """Resolve Hermes protocol and endpoint metadata without persisting a key."""
    from hermes_cli.runtime_provider import resolve_runtime_provider

    runtime = resolve_runtime_provider(
        requested=provider,
        explicit_api_key=api_key,
        target_model=model,
    )
    if not str(runtime.get("base_url") or "").strip():
        raise RuntimeError(f"Hermes provider {provider!r} has no base URL")
    return dict(runtime)


def ensure_opencode_session_header(
    runtime: dict[str, Any], session_id: str
) -> dict[str, Any]:
    """Attach the OpenCode relay's session-affinity header when required."""
    if not session_id:
        return runtime
    from hermes_cli.models import opencode_provider_family

    if opencode_provider_family(runtime.get("provider")) is None:
        return runtime
    extra = runtime.get("extra_headers")
    if not isinstance(extra, dict):
        extra = {}
        runtime["extra_headers"] = extra
    extra.setdefault("x-opencode-session", session_id)
    return runtime


def configured_model_rows(*, force_refresh: bool = False) -> list[dict[str, Any]]:
    """Flatten Hermes' native picker inventory into the Workers display shape."""
    from hermes_cli.inventory import build_models_payload, load_picker_context

    payload = build_models_payload(
        load_picker_context(),
        explicit_only=True,
        include_unconfigured=True,
        probe_custom_providers=False,
        non_blocking_catalogs=not force_refresh,
        refresh=force_refresh,
    )
    rows: list[dict[str, Any]] = []
    for source in payload.get("providers", []):
        provider = str(source.get("slug") or "")
        if not provider:
            continue
        for model in source.get("models") or []:
            try:
                runtime = resolve_model_runtime(provider, model)
            except RuntimeError:
                # Keep native inventory entries visible when configuration is incomplete.
                runtime = {}
            api_mode = str(runtime.get("api_mode") or "")
            if api_mode and api_mode not in SUPPORTED_API_MODES:
                continue
            row_id = f"{provider}/{model}"
            rows.append({
                "id": row_id,
                "model": model,
                "provider": provider,
                "providerDisplayName": str(source.get("name") or provider),
                "displayName": row_id,
                "apiMode": api_mode,
                **model_capabilities(provider, model),
            })
    return rows
