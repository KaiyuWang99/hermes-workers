"""Hermes credential-pool bridge shared by external Workers backends."""

from __future__ import annotations

import inspect
import logging
import os
from typing import Any


logger = logging.getLogger(__name__)


def pool_provider(value: Any) -> str:
    """Normalize a provider id through Hermes' own alias table."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        from hermes_cli.models import normalize_provider
    except Exception as exc:
        logger.debug("pool_provider(%r): normalize_provider unavailable: %s", raw, exc)
        return raw.lower()
    return normalize_provider(raw)


def _key(entry: Any) -> str:
    return str(
        getattr(entry, "runtime_api_key", "")
        or getattr(entry, "access_token", "")
        or ""
    ).strip()


def _custom_provider_entry(provider: str) -> dict[str, Any] | None:
    raw = str(provider or "").strip().lower()
    bare = raw[7:] if raw.startswith("custom:") else raw
    if not bare:
        return None
    try:
        from hermes_cli.runtime_provider import get_compatible_custom_providers

        entries = get_compatible_custom_providers()
    except Exception as exc:
        logger.debug("_custom_provider_entry(%r): %s", provider, exc)
        return None
    for entry in entries:
        key = str(entry.get("provider_key") or "").strip().lower()
        name = str(entry.get("name") or "").strip().lower()
        if bare in {key, name}:
            return dict(entry)
    return None


def _custom_key(entry: dict[str, Any]) -> str:
    value = str(entry.get("api_key") or "").strip()
    if value and value.lower() not in {"none", "null", "not-needed", "no-key-required"}:
        return value
    key_env = str(entry.get("key_env") or entry.get("api_key_env") or "").strip()
    if not key_env:
        return ""
    try:
        from hermes_cli.config import get_env_value

        value = str(get_env_value(key_env) or "").strip()
    except Exception as exc:
        logger.debug("_custom_key(%r): env lookup failed: %s", key_env, exc)
        value = ""
    return value or str(os.environ.get(key_env) or "").strip()


def credential_status(provider: str, model: str | None = None) -> dict[str, Any]:
    mapped = pool_provider(provider)
    try:
        from agent.credential_pool import load_pool

        pool = load_pool(mapped)
        if pool and pool.has_credentials():
            return {
                "provider": mapped,
                "configured": True,
                "available": bool(pool.has_available(model=model)),
                "mode": "pool",
                "next_available_at": pool.next_available_at(model=model),
            }
    except Exception as exc:
        logger.debug("credential_status(%r): pool lookup failed: %s", provider, exc)
    try:
        from hermes_cli.auth import PROVIDER_REGISTRY, resolve_api_key_provider_credentials

        profile = PROVIDER_REGISTRY.get(mapped)
        if profile and getattr(profile, "auth_type", "") == "api_key":
            value = resolve_api_key_provider_credentials(mapped)
            if str(value.get("api_key") or "").strip():
                return {
                    "provider": mapped,
                    "configured": True,
                    "available": True,
                    "mode": "single",
                    "next_available_at": None,
                }
    except Exception as exc:
        logger.debug("credential_status(%r): single-key lookup failed: %s", provider, exc)
    custom = _custom_provider_entry(provider)
    if custom:
        custom_id = f"custom:{str(custom.get('provider_key') or '').strip().lower()}"
        try:
            from agent.credential_pool import load_pool

            pool = load_pool(custom_id)
            if pool and pool.has_credentials():
                return {
                    "provider": custom_id,
                    "configured": True,
                    "available": bool(pool.has_available(model=model)),
                    "mode": "pool",
                    "next_available_at": pool.next_available_at(model=model),
                }
        except Exception as exc:
            logger.debug("credential_status(%r): custom pool lookup failed: %s", custom_id, exc)
        if _custom_key(custom):
            return {
                "provider": custom_id,
                "configured": True,
                "available": True,
                "mode": "single",
                "next_available_at": None,
            }
    return {
        "provider": mapped,
        "configured": False,
        "available": False,
        "mode": "missing",
        "next_available_at": None,
    }


def require_credentials(provider: str, model: str | None = None) -> dict[str, Any]:
    status = credential_status(provider, model=model)
    if not status["configured"]:
        raise RuntimeError(
            f"Worker provider {provider!r} has no matching Hermes credential"
        )
    if not status["available"]:
        raise RuntimeError(
            f"All Hermes credentials for worker provider {provider!r} are cooling down or exhausted"
        )
    return status


def select_credential(provider: str, model: str | None = None) -> dict[str, Any] | None:
    mapped = pool_provider(provider)
    authoritative_pool = False
    try:
        from agent.credential_pool import load_pool

        pool = load_pool(mapped)
        if pool and pool.has_credentials():
            authoritative_pool = True
            entry = pool.select(model=model)
            key = _key(entry)
            if entry is not None and key:
                return {
                    "provider": mapped,
                    "credential_id": str(getattr(entry, "id", "") or ""),
                    "key": key,
                }
            # An existing pool is authoritative, including its cooldowns.
            return None
    except Exception as exc:
        logger.debug("select_credential(%r): pool selection failed: %s", mapped, exc)
        if authoritative_pool:
            return None
    try:
        from hermes_cli.auth import PROVIDER_REGISTRY, resolve_api_key_provider_credentials

        profile = PROVIDER_REGISTRY.get(mapped)
        if profile and getattr(profile, "auth_type", "") == "api_key":
            value = resolve_api_key_provider_credentials(mapped)
            key = str(value.get("api_key") or "").strip()
            if key:
                return {"provider": mapped, "credential_id": "", "key": key}
    except Exception as exc:
        logger.debug("select_credential(%r): single-key lookup failed: %s", mapped, exc)
    custom = _custom_provider_entry(provider)
    if custom:
        custom_id = f"custom:{str(custom.get('provider_key') or '').strip().lower()}"
        authoritative_pool = False
        try:
            from agent.credential_pool import load_pool

            pool = load_pool(custom_id)
            if pool and pool.has_credentials():
                authoritative_pool = True
                entry = pool.select(model=model)
                key = _key(entry)
                if entry is not None and key:
                    return {
                        "provider": custom_id,
                        "credential_id": str(getattr(entry, "id", "") or ""),
                        "key": key,
                    }
                return None
        except Exception as exc:
            logger.debug(
                "select_credential(%r): custom pool selection failed: %s",
                custom_id,
                exc,
            )
            if authoritative_pool:
                return None
        key = _custom_key(custom)
        if key:
            return {"provider": custom_id, "credential_id": "", "key": key}
    return None


def rotate_credential(payload: dict[str, Any]) -> dict[str, Any] | None:
    from .provider_errors import classify, from_payload

    error = from_payload(payload)
    decision = classify(error)
    # A model's regional availability says nothing about the key's balance or
    # validity. Preserve that error for the caller without poisoning the pool.
    if not decision.should_rotate_credential:
        return None
    status_code = error.status_code
    provider = str(payload.get("provider") or "")
    mapped = pool_provider(provider)
    custom = _custom_provider_entry(provider)
    if custom and mapped == provider.strip().lower():
        mapped = f"custom:{str(custom.get('provider_key') or '').strip().lower()}"
    credential_id = str(payload.get("credential_id") or "").strip()
    api_key = str(payload.get("api_key") or "").strip()
    if not credential_id and not api_key:
        return None
    try:
        from agent.credential_pool import load_pool
        from agent.credential_pool_model_cooldowns import pool_failure_reason
        from agent.error_classifier import FailoverReason

        pool = load_pool(mapped)
        if not pool:
            return None
        failure_reason = pool_failure_reason(
            FailoverReason(decision.reason),
            decision.error_context,
            decision.billing_unverified,
        )
        kwargs = {
            "status_code": status_code,
            "error_context": {
                "source": str(payload.get("source") or "workers-opencode"),
                "reason": decision.reason,
                **{
                    name: payload[name]
                    for name in ("model", "error_type", "error_code", "error_message", "error_body")
                    if payload.get(name) is not None
                },
            },
            "api_key_hint": api_key or None,
            "credential_id": credential_id or None,
            "failure_reason": failure_reason,
            "model": str(payload.get("model") or "").strip() or None,
        }
        parameters = inspect.signature(pool.mark_exhausted_and_rotate).parameters
        if "failure_reason" not in parameters and not any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        ):
            kwargs.pop("failure_reason", None)
        entry = pool.mark_exhausted_and_rotate(**kwargs)
        key = _key(entry)
        if entry is None or not key or key == api_key:
            return None
        return {
            "provider": mapped,
            "credential_id": str(getattr(entry, "id", "") or ""),
            "key": key,
        }
    except Exception as exc:
        logger.debug("rotate_credential(%r): pool rotation failed: %s", mapped, exc)
        return None
