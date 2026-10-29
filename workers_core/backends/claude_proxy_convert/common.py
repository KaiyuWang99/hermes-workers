"""Shared primitives for the Claude bridge conversions.

Token estimation, Anthropic content/tool parsing, reasoning signatures,
usage accounting and URL/error helpers live here.  Nothing in this module
keeps per-request state or performs I/O.
"""

from __future__ import annotations

import base64
import json
import uuid
from typing import Any

import httpx


_REASONING_SIGNATURE_PREFIX = "workers-responses-v1:"
_EXTENDED_EFFORTS = {"xhigh", "max", "ultra"}


def _json_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _estimate_tokens(text: str) -> int:
    """Estimate prompt tokens without under-counting CJK.

    Claude's tokenizer spends roughly one token per CJK character but about one
    per three latin characters.  A single flat ratio therefore under-reports
    Chinese/Japanese prompts by about half, and Claude Code uses this number to
    decide when to auto-compact -- underestimating means compaction fires too
    late and the turn dies on the upstream context limit instead.
    """
    wide = 0
    for char in text:
        code = ord(char)
        if (
            0x3000 <= code <= 0x30FF
            or 0x3400 <= code <= 0x4DBF
            or 0x4E00 <= code <= 0x9FFF
            or 0xAC00 <= code <= 0xD7AF
            or 0xF900 <= code <= 0xFAFF
            or 0xFF00 <= code <= 0xFFEF
            or 0x20000 <= code <= 0x2FA1F
        ):
            wide += 1
    return wide + (len(text) - wide + 2) // 3


def _estimate_value_tokens(value: Any) -> int:
    """Count prompt text without treating encoded image bytes as prompt text."""
    if isinstance(value, dict):
        if value.get("type") == "image":
            return 1100
        return sum(_estimate_tokens(str(key)) + _estimate_value_tokens(item) for key, item in value.items())
    if isinstance(value, list):
        return sum(_estimate_value_tokens(item) for item in value)
    if isinstance(value, str):
        return _estimate_tokens(value)
    return _estimate_tokens(_json_text(value))


def _image_part(block: dict[str, Any]) -> dict[str, Any] | None:
    source = block.get("source")
    if not isinstance(source, dict):
        return None
    source_type = str(source.get("type") or "")
    if source_type == "url" and source.get("url"):
        return {"type": "image_url", "image_url": {"url": str(source["url"])}}
    if source_type == "base64" and source.get("data"):
        media = str(source.get("media_type") or "application/octet-stream")
        return {
            "type": "image_url",
            "image_url": {"url": f"data:{media};base64,{source['data']}"},
        }
    return None


def _tool_result_parts(raw: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    value = raw.get("content", "")
    if not isinstance(value, list):
        text = _json_text(value)
        return (f"[Tool error] {text}" if raw.get("is_error") else text), []
    texts: list[str] = []
    images: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict) and item.get("type") == "text":
            texts.append(str(item.get("text") or ""))
        elif isinstance(item, dict) and item.get("type") == "image":
            image = _image_part(item)
            if image:
                images.append(image)
            else:
                texts.append(_json_text(item))
        else:
            # Other Anthropic result blocks have no matching function-output
            # type. Preserve their data and type instead of dropping them.
            texts.append(_json_text(item))
    text = "\n".join(part for part in texts if part)
    return (f"[Tool error] {text}" if raw.get("is_error") else text), images


def _content_parts(value: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    content: list[dict[str, Any]] = []
    tool_calls: list[dict[str, Any]] = []
    tool_results: list[dict[str, Any]] = []
    thinking: list[str] = []
    blocks = value if isinstance(value, list) else [{"type": "text", "text": value}]
    for raw in blocks:
        if not isinstance(raw, dict):
            content.append({"type": "text", "text": _json_text(raw)})
            continue
        kind = str(raw.get("type") or "text")
        if kind == "text":
            text = str(raw.get("text") or "")
            if text:
                content.append({"type": "text", "text": text})
        elif kind == "image":
            part = _image_part(raw)
            if part:
                content.append(part)
        elif kind == "tool_use":
            tool_calls.append(
                {
                    "id": str(raw.get("id") or f"tool_{uuid.uuid4().hex}"),
                    "type": "function",
                    "function": {
                        "name": str(raw.get("name") or "unknown_tool"),
                        "arguments": _json_text(raw.get("input") or {}),
                    },
                }
            )
        elif kind == "tool_result":
            result_content, images = _tool_result_parts(raw)
            tool_results.append(
                {
                    "role": "tool",
                    "tool_call_id": str(raw.get("tool_use_id") or ""),
                    "content": result_content,
                    "image_parts": images,
                }
            )
        elif kind in {"thinking", "redacted_thinking"}:
            text = str(raw.get("thinking") or raw.get("data") or "")
            if text:
                thinking.append(text)
        elif kind in {"server_tool_use", "tool_search_tool_result"}:
            content.append({"type": "text", "text": _json_text(raw)})
    return content, tool_calls, tool_results, thinking


def _openai_content(parts: list[dict[str, Any]]) -> Any:
    if not parts:
        return None
    if all(part.get("type") == "text" for part in parts):
        return "\n".join(str(part.get("text") or "") for part in parts)
    return parts


def _tool_choice(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    kind = str(value.get("type") or "")
    if kind == "tool":
        return {"type": "function", "function": {"name": str(value.get("name") or "")}}
    if kind == "any":
        return "required"
    if kind == "auto":
        return "auto"
    if kind == "none":
        return "none"
    return value


def _is_client_tool(tool: Any) -> bool:
    if not isinstance(tool, dict) or not tool.get("name"):
        return False
    tool_type = str(tool.get("type") or "")
    # The bridge cannot execute Anthropic server tools. MCP search is not
    # needed here: pass every deferred client schema to the upstream directly.
    return tool_type in {"", "custom", "function"} and not str(tool["name"]).startswith(
        "tool_search_tool_"
    )


def _reasoning_effort(body: dict[str, Any]) -> str | None:
    requested = ""
    output_config = body.get("output_config")
    if isinstance(output_config, dict):
        requested = str(output_config.get("effort") or "").strip().lower()
    if requested not in {"minimal", "low", "medium", "high", *_EXTENDED_EFFORTS}:
        requested = ""
    if requested == "ultra":
        requested = "max"
    if not requested:
        thinking = body.get("thinking")
        if not isinstance(thinking, dict) or thinking.get("type") not in {"enabled", "adaptive"}:
            return None
        budget = int(thinking.get("budget_tokens") or 0)
        if budget <= 0:
            # Adaptive reasoning has no fixed budget. Let the upstream select
            # its own default rather than silently translating it to "low".
            return None
        if budget >= 16000:
            requested = "high"
        elif budget >= 4000:
            requested = "medium"
        else:
            requested = "low"
    # Try the actual requested effort first. Only a rejection from this model
    # justifies the targeted downgrade in _fallback_effort_request.
    return requested


def _encode_reasoning_signature(item: dict[str, Any]) -> str:
    payload = json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    return _REASONING_SIGNATURE_PREFIX + base64.urlsafe_b64encode(payload).decode(
        "ascii"
    )


def _decode_reasoning_signature(value: Any) -> dict[str, Any] | None:
    raw = str(value or "")
    if not raw.startswith(_REASONING_SIGNATURE_PREFIX):
        return None
    encoded = raw[len(_REASONING_SIGNATURE_PREFIX) :]
    try:
        item = json.loads(base64.urlsafe_b64decode(encoded.encode("ascii")))
    except (ValueError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(item, dict) or item.get("type") != "reasoning":
        return None
    return item


def _responses_reasoning_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for block in value:
        if not isinstance(block, dict) or block.get("type") != "thinking":
            continue
        item = _decode_reasoning_signature(block.get("signature"))
        if item is not None:
            result.append(item)
    return result


def _arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    except json.JSONDecodeError:
        return {"raw": str(value or "")}


def _stop_reason(value: Any) -> str:
    return {
        "tool_calls": "tool_use",
        "length": "max_tokens",
        "content_filter": "stop_sequence",
    }.get(str(value or ""), "end_turn")


def _chat_usage(usage: dict[str, Any]) -> dict[str, Any]:
    input_tokens = int(usage.get("prompt_tokens") or 0)
    details = usage.get("prompt_tokens_details")
    cached = int(details.get("cached_tokens") or 0) if isinstance(details, dict) else 0
    if usage.get("prompt_cache_hit_tokens") is not None:
        cached = int(usage.get("prompt_cache_hit_tokens") or 0)
    result: dict[str, Any] = {
        "input_tokens": max(0, input_tokens - cached),
        "output_tokens": int(usage.get("completion_tokens") or 0),
    }
    if cached:
        result["cache_read_input_tokens"] = min(cached, input_tokens)
    return result


def _responses_usage(usage: dict[str, Any]) -> dict[str, Any]:
    input_tokens = int(usage.get("input_tokens") or 0)
    details = usage.get("input_tokens_details")
    cached = int(details.get("cached_tokens") or 0) if isinstance(details, dict) else 0
    result: dict[str, Any] = {
        "input_tokens": max(0, input_tokens - cached),
        "output_tokens": int(usage.get("output_tokens") or 0),
    }
    if cached:
        result["cache_read_input_tokens"] = min(cached, input_tokens)
    return result


def _chat_url(base_url: str) -> str:
    value = str(base_url or "").rstrip("/")
    if value.endswith("/chat/completions"):
        return value
    if value.endswith("/v1"):
        return f"{value}/chat/completions"
    return f"{value}/v1/chat/completions"


def _responses_url(base_url: str) -> str:
    value = str(base_url or "").rstrip("/")
    if value.endswith("/responses"):
        return value
    if value.endswith("/v1"):
        return f"{value}/responses"
    return f"{value}/v1/responses"


def _error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:2000] or f"HTTP {response.status_code}"
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        if body.get("message"):
            return str(body["message"])
    return _json_text(body)[:2000]
