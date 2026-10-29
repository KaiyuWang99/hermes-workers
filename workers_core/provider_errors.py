"""Normalize each adapter's error, then let Hermes classify it.

The CLI boundary destroys the exception object, so the only adapter work left is
rebuilding the fields Hermes' classifier needs (status, body, message) and
stripping Claude Code's wrapper. Everything else is core's decision.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any


logger = logging.getLogger(__name__)
_COUNTRY_MESSAGE = "this model is not available in your country"
# Claude Code wraps an upstream error as "Failed to authenticate. API Error: 403 <body>".
# Strip only these known wrappers so the message compares exactly, keeping an
# unrelated sentence that merely quotes the country text (injection) declined.
_CLI_ERROR_PREFIXES = ("failed to authenticate.", "api error: 403", "api error:")
_REGION_TYPES = frozenset({"regionerror", "region_error"})
_PAYLOAD_FIELDS = (
    "provider",
    "model",
    "status_code",
    "error_type",
    "error_code",
    "error_message",
    "error_body",
    "error",
    "source",
)
# Same recovery branches as ``recover_with_credential_pool``. Workers have no
# refresh step, so an auth failure rotates instead of refreshing first.
_ROTATE_REASONS = frozenset({"auth", "billing", "rate_limit", "model_entitlement"})
# Fallback is core's decision. Workers deliberately keep no local status table:
# core already marks 400/404 (format_error/model_not_found) for fallback and
# leaves 408/5xx transient, retrying them on the same route first. This is an
# intentional behaviour alignment, so the two paths cannot drift again.


@dataclass(frozen=True)
class ProviderError:
    provider: str = ""
    model: str = ""
    status_code: int | None = None
    error_type: str = ""
    error_code: str = ""
    message: str = ""
    body: dict[str, Any] = field(default_factory=dict)
    source: str = ""


@dataclass(frozen=True)
class Decision:
    reason: str
    retryable: bool = True
    should_rotate_credential: bool = False
    should_fallback: bool = False
    error_context: dict[str, Any] = field(default_factory=dict)
    billing_unverified: bool = False


class _HTTPFailure(Exception):
    """The minimal exception shape ``classify_api_error`` reads."""

    def __init__(self, message: str, *, status_code: int | None, body: dict[str, Any]):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def _status(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _strip_cli_prefixes(value: str) -> str:
    text = value.strip()
    changed = True
    while changed:
        changed = False
        for prefix in _CLI_ERROR_PREFIXES:
            if text.lower().startswith(prefix):
                text = text[len(prefix):].strip()
                changed = True
    return text


def _json_object(value: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(_strip_cli_prefixes(value))
    except (TypeError, ValueError) as exc:
        # Prose error messages are the normal case; debug only, never change
        # how an unparseable message is treated.
        logger.debug("provider_errors._json_object: message is not JSON: %s", exc)
        return None
    return parsed if isinstance(parsed, dict) else None


def _is_country_message(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    return _strip_cli_prefixes(value).rstrip(".").lower() == _COUNTRY_MESSAGE


def _error_objects(body: Any):
    """Visit only documented error/data wrappers, never prompt/content trees."""
    if not isinstance(body, dict):
        return
    yield body
    for key in ("error", "data"):
        child = body.get(key)
        if isinstance(child, dict):
            yield child
            for nested_key in ("error", "data"):
                nested = child.get(nested_key)
                if isinstance(nested, dict):
                    yield nested


def normalize_error(
    *,
    provider: Any = "",
    model: Any = "",
    status_code: Any = None,
    error_type: Any = "",
    error_code: Any = "",
    error_message: Any = "",
    error_body: Any = None,
    error: Any = None,
    source: Any = "",
) -> ProviderError:
    """Normalize one adapter's error fields without reading conversation text."""
    body = error_body if isinstance(error_body, dict) else {}
    message = error_message if isinstance(error_message, str) else ""
    if not body and error is not None:
        nested = getattr(error, "body", None)
        if isinstance(nested, dict):
            body = nested
    if not body and message:
        parsed = _json_object(message)
        if parsed is not None:
            body = parsed
    return ProviderError(
        provider=str(provider or "").strip(),
        model=str(model or "").strip(),
        status_code=_status(status_code),
        error_type=str(error_type or "").strip(),
        error_code=str(error_code or "").strip(),
        message=message,
        body=body,
        source=str(source or "").strip(),
    )


def from_payload(payload: dict[str, Any] | None = None) -> ProviderError:
    values = payload or {}
    return normalize_error(
        **{name: values.get(name) for name in _PAYLOAD_FIELDS if name in values}
    )


def _is_region_error(error: ProviderError) -> bool:
    if error.status_code != 403:
        return False
    names = [error.error_type, error.error_code]
    messages = [error.message]
    for item in _error_objects(error.body):
        names.extend(item.get(key) for key in ("name", "type", "code"))
        messages.append(item.get("message"))
    if any(
        isinstance(value, str) and value.strip().lower() in _REGION_TYPES
        for value in names
    ):
        return True
    return any(_is_country_message(value) for value in messages)


def _core_classify(error: ProviderError):
    from agent.error_classifier import classify_api_error

    return classify_api_error(
        _HTTPFailure(
            error.message,
            status_code=error.status_code,
            body=error.body,
        ),
        provider=error.provider,
        model=error.model,
    )


def classify(error: ProviderError) -> Decision:
    """Classify one provider failure; region is the only Workers correction.

    A geographic 403 is invisible to the HTTP-status rule (which reads it as
    auth), so it is decided here. Every other reason comes from core.
    """
    if _is_region_error(error):
        # A geographic 403 never touches the credential; another model can serve
        # it, so the fallback chain is the only recovery.
        return Decision(
            "upstream_blocked",
            retryable=False,
            should_fallback=True,
        )
    classified = _core_classify(error)
    reason = classified.reason.value
    has_status = error.status_code is not None
    return Decision(
        reason=reason,
        retryable=bool(classified.retryable),
        should_rotate_credential=has_status and reason in _ROTATE_REASONS,
        should_fallback=has_status and bool(classified.should_fallback),
        error_context=dict(getattr(classified, "error_context", {}) or {}),
        billing_unverified=bool(getattr(classified, "billing_unverified", False)),
    )


def correct_api_error_classification(**payload: Any) -> dict[str, Any] | None:
    """Return only the correction Hermes' own classifier cannot see.

    A typed/known geographic 403 is the case a CLI or aggregator exposes but the
    403 default reads as auth. Every other error returns ``None`` so Hermes
    keeps its own richer classification.
    """
    if not _is_region_error(from_payload(payload)):
        return None
    return {
        "reason": "upstream_blocked",
        "retryable": False,
        "should_rotate_credential": False,
        "should_fallback": True,
    }
