"""Parameter fallback ladder for rejected upstream Claude bridge requests.

Each helper here is a pure rewrite of one request body; the handler decides
which rewrite to try and performs the upstream round trips.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .claude_proxy_convert import _EXTENDED_EFFORTS


def _fallback_effort_request(body: dict[str, Any]) -> dict[str, Any] | None:
    """Clamp only non-standard effort levels after an upstream 4xx."""
    result = deepcopy(body)
    value = str(result.get("reasoning_effort") or "").strip().lower()
    if value:
        if value == "minimal":
            result["reasoning_effort"] = "low"
            return result
        if value in _EXTENDED_EFFORTS:
            result["reasoning_effort"] = "high"
            return result
        return None
    reasoning = result.get("reasoning")
    if not isinstance(reasoning, dict):
        return None
    value = str(reasoning.get("effort") or "").strip().lower()
    if value == "minimal":
        reasoning["effort"] = "low"
        return result
    if value in _EXTENDED_EFFORTS:
        reasoning["effort"] = "high"
        return result
    return None


def _param_fallbacks(
    body: dict[str, Any], *, responses: bool = False
) -> list[tuple[str, dict[str, Any]]]:
    """Independent, one-field rewrites for a rejected request."""
    variants: list[tuple[str, dict[str, Any]]] = []
    effort_variant = _fallback_effort_request(body)
    if effort_variant is not None:
        variants.append(("effort", effort_variant))
    if responses:
        for key in ("parallel_tool_calls", "max_output_tokens", "reasoning"):
            if key in body:
                variant = deepcopy(body)
                variant.pop(key)
                variants.append((key, variant))
        return variants
    if "max_tokens" in body and "max_completion_tokens" not in body:
        variant = deepcopy(body)
        variant["max_completion_tokens"] = variant.pop("max_tokens")
        variants.append(("max_tokens", variant))
    if body.get("stream_options"):
        variant = deepcopy(body)
        variant.pop("stream_options")
        variants.append(("stream_options", variant))
    messages = body.get("messages")
    if isinstance(messages, list) and any(
        isinstance(message, dict) and message.get("reasoning_content")
        for message in messages
    ):
        variant = deepcopy(body)
        for message in variant.get("messages") or []:
            if isinstance(message, dict):
                message.pop("reasoning_content", None)
        variants.append(("reasoning_content", variant))
    return variants


_PARAM_KEYWORDS: dict[str, tuple[str, ...]] = {
    "effort": ("reasoning_effort", "effort"),
    "max_tokens": ("max_tokens", "max_completion_tokens"),
    "stream_options": ("stream_options", "include_usage"),
    "reasoning_content": ("reasoning_content", "reasoning"),
    "parallel_tool_calls": ("parallel_tool_calls", "parallel tool"),
    "max_output_tokens": ("max_output_tokens", "max tokens"),
    "reasoning": ("reasoning",),
}


def _should_try_variant(error_text: str, tag: str) -> bool:
    """Whether a rewrite is worth another upstream round trip.

    When the error names parameters we recognise, only the matching rewrite is
    attempted: retrying the rest just adds latency and hides the real problem.
    An opaque error gives us nothing to match on, so the whole chain still runs.
    """
    lowered = error_text.lower()
    mentioned = {
        name
        for name, words in _PARAM_KEYWORDS.items()
        if any(word in lowered for word in words)
    }
    if not mentioned:
        return True
    return tag in mentioned


def _next_variant_index(error_text: str, tags: list[str], current: int) -> int | None:
    """Pick the next rewrite named by this error, if any."""
    for index in range(current + 1, len(tags)):
        if _should_try_variant(error_text, tags[index]):
            return index
    return None
