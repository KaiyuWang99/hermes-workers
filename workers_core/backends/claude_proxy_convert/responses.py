"""Anthropic <-> OpenAI Responses API request and response conversion."""

from __future__ import annotations

import uuid
from typing import Any

from .common import (
    _arguments,
    _content_parts,
    _encode_reasoning_signature,
    _is_client_tool,
    _reasoning_effort,
    _responses_reasoning_items,
    _responses_usage,
    _tool_choice,
)


def _responses_parts(parts: list[dict[str, Any]], *, assistant: bool) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for part in parts:
        if part.get("type") == "text":
            result.append(
                {
                    "type": "output_text" if assistant else "input_text",
                    "text": str(part.get("text") or ""),
                }
            )
        elif part.get("type") == "image_url" and not assistant:
            image = part.get("image_url") if isinstance(part.get("image_url"), dict) else {}
            if image.get("url"):
                result.append({"type": "input_image", "image_url": str(image["url"])})
    return result


def anthropic_to_responses(
    body: dict[str, Any],
    upstream_model: str,
    *,
    stream: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "model": upstream_model,
        "input": [],
        "store": False,
        "stream": stream,
    }
    system = body.get("system")
    if isinstance(system, str):
        result["instructions"] = system
    elif isinstance(system, list):
        result["instructions"] = "\n".join(
            str(block.get("text") or "")
            for block in system
            if isinstance(block, dict) and block.get("type") == "text"
        )
    for raw in body.get("messages") or []:
        if not isinstance(raw, dict):
            continue
        role = str(raw.get("role") or "user")
        content, tool_calls, tool_results, _thinking = _content_parts(raw.get("content"))
        result["input"].extend(_responses_reasoning_items(raw.get("content")))
        # function_call_output items must directly follow their function_call,
        # so they precede any sibling text from the same user turn.
        for tool_result in tool_results:
            images = tool_result["image_parts"]
            output: Any = tool_result["content"]
            if images:
                output = [
                    {"type": "input_text", "text": output or "Image returned by tool."},
                    *(
                        {"type": "input_image", "image_url": image["image_url"]["url"]}
                        for image in images
                    ),
                ]
            result["input"].append(
                {
                    "type": "function_call_output",
                    "call_id": str(tool_result.get("tool_call_id") or ""),
                    "output": output,
                }
            )
        message_parts = _responses_parts(content, assistant=role == "assistant")
        if message_parts:
            result["input"].append(
                {"type": "message", "role": role, "content": message_parts}
            )
        for call in tool_calls:
            function = call.get("function") if isinstance(call.get("function"), dict) else {}
            result["input"].append(
                {
                    "type": "function_call",
                    "call_id": str(call.get("id") or f"call_{uuid.uuid4().hex}"),
                    "name": str(function.get("name") or "unknown_tool"),
                    "arguments": str(function.get("arguments") or "{}"),
                }
            )
    if body.get("max_tokens") is not None:
        result["max_output_tokens"] = body["max_tokens"]
    tools = body.get("tools")
    if isinstance(tools, list):
        converted = []
        for tool in tools:
            if not _is_client_tool(tool):
                continue
            converted.append(
                {
                    "type": "function",
                    "name": str(tool["name"]),
                    "description": str(tool.get("description") or ""),
                    "parameters": tool.get("input_schema") or {"type": "object"},
                }
            )
        if converted:
            result["tools"] = converted
            result["parallel_tool_calls"] = True
    if body.get("tool_choice") is not None:
        choice = _tool_choice(body["tool_choice"])
        if isinstance(choice, dict) and choice.get("type") == "function":
            choice = {"type": "function", "name": str((choice.get("function") or {}).get("name") or "")}
        result["tool_choice"] = choice
    effort = _reasoning_effort(body)
    if effort:
        result["reasoning"] = {"effort": effort}
    return result


def responses_to_anthropic(body: dict[str, Any], client_model: str) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    has_tool = False
    incomplete = body.get("incomplete_details")
    incomplete_reason = incomplete.get("reason") if isinstance(incomplete, dict) else None
    for item in body.get("output") or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "")
        if kind == "message":
            for part in item.get("content") or []:
                if not isinstance(part, dict):
                    continue
                if part.get("type") in {"output_text", "text", "refusal"}:
                    text = str(part.get("text") or part.get("refusal") or "")
                    if text:
                        content.append({"type": "text", "text": text})
        elif kind == "function_call":
            if incomplete_reason:
                continue
            has_tool = True
            content.append(
                {
                    "type": "tool_use",
                    "id": str(item.get("call_id") or item.get("id") or f"call_{uuid.uuid4().hex}"),
                    "name": str(item.get("name") or "unknown_tool"),
                    "input": _arguments(item.get("arguments")),
                }
            )
        elif kind == "reasoning":
            summary = item.get("summary") or []
            text = "\n".join(
                str(part.get("text") or "")
                for part in summary
                if isinstance(part, dict)
            )
            content.append(
                {
                    "type": "thinking",
                    "thinking": text,
                    "signature": _encode_reasoning_signature(item),
                }
            )
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    stop_reason = (
        "max_tokens"
        if incomplete_reason in {"max_output_tokens", "max_tokens"}
        else "tool_use"
        if has_tool
        else "end_turn"
    )
    return {
        "id": str(body.get("id") or f"msg_{uuid.uuid4().hex}"),
        "type": "message",
        "role": "assistant",
        "model": client_model,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": _responses_usage(usage) if usage else {"input_tokens": 0, "output_tokens": 0},
    }
