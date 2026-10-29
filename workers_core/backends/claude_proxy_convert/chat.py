"""Anthropic <-> OpenAI chat-completions request and response conversion."""

from __future__ import annotations

import uuid
from typing import Any

from .common import (
    _arguments,
    _chat_usage,
    _content_parts,
    _is_client_tool,
    _openai_content,
    _reasoning_effort,
    _stop_reason,
    _tool_choice,
)


def anthropic_to_openai(
    body: dict[str, Any], upstream_model: str, *, stream: bool = False
) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    system = body.get("system")
    if isinstance(system, str) and system:
        messages.append({"role": "system", "content": system})
    elif isinstance(system, list):
        text = "\n".join(
            str(block.get("text") or "")
            for block in system
            if isinstance(block, dict) and block.get("type") == "text"
        )
        if text:
            messages.append({"role": "system", "content": text})

    for raw in body.get("messages") or []:
        if not isinstance(raw, dict):
            continue
        role = str(raw.get("role") or "user")
        content, tool_calls, tool_results, thinking = _content_parts(raw.get("content"))
        if role == "assistant":
            message: dict[str, Any] = {"role": "assistant", "content": _openai_content(content)}
            if tool_calls:
                message["tool_calls"] = tool_calls
            if thinking:
                message["reasoning_content"] = "\n".join(thinking)
            messages.append(message)
        else:
            # OpenAI requires tool messages to directly follow the assistant
            # tool_calls message, so results precede any sibling text.
            image_content: list[dict[str, Any]] = []
            for tool_result in tool_results:
                images = tool_result["image_parts"]
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_result["tool_call_id"],
                        "content": tool_result["content"] or (
                            "[Image from this tool result follows.]" if images else ""
                        ),
                    }
                )
                if images:
                    image_content.append(
                        {
                            "type": "text",
                            "text": f"Images returned by tool call {tool_result['tool_call_id']}:",
                        }
                    )
                    image_content.extend(images)
            if image_content or content:
                messages.append(
                    {"role": "user", "content": _openai_content(image_content + content)}
                )

    result: dict[str, Any] = {
        "model": upstream_model,
        "messages": messages,
        "stream": stream,
    }
    if stream:
        result["stream_options"] = {"include_usage": True}
    if body.get("max_tokens") is not None:
        result["max_tokens"] = body["max_tokens"]
    for key in ("temperature", "top_p"):
        if body.get(key) is not None:
            result[key] = body[key]
    if body.get("stop_sequences"):
        result["stop"] = body["stop_sequences"]
    tools = body.get("tools")
    if isinstance(tools, list):
        converted = []
        for tool in tools:
            if not _is_client_tool(tool):
                continue
            converted.append(
                {
                    "type": "function",
                    "function": {
                        "name": str(tool["name"]),
                        "description": str(tool.get("description") or ""),
                        "parameters": tool.get("input_schema") or {"type": "object"},
                    },
                }
            )
        if converted:
            result["tools"] = converted
    if body.get("tool_choice") is not None:
        result["tool_choice"] = _tool_choice(body["tool_choice"])
    effort = _reasoning_effort(body)
    if effort:
        result["reasoning_effort"] = effort
    return result


def openai_to_anthropic(body: dict[str, Any], client_model: str) -> dict[str, Any]:
    choices = body.get("choices") or []
    choice = choices[0] if choices and isinstance(choices[0], dict) else {}
    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    content: list[dict[str, Any]] = []
    reasoning = message.get("reasoning_content") or message.get("reasoning")
    if reasoning:
        content.append({"type": "thinking", "thinking": str(reasoning), "signature": ""})
    text = message.get("content")
    if isinstance(text, str) and text:
        content.append({"type": "text", "text": text})
    elif isinstance(text, list):
        for part in text:
            if isinstance(part, dict) and part.get("type") == "text" and part.get("text"):
                content.append({"type": "text", "text": str(part["text"])})
    for tool in message.get("tool_calls") or []:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function") if isinstance(tool.get("function"), dict) else {}
        content.append(
            {
                "type": "tool_use",
                "id": str(tool.get("id") or f"tool_{uuid.uuid4().hex}"),
                "name": str(function.get("name") or "unknown_tool"),
                "input": _arguments(function.get("arguments")),
            }
        )
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    return {
        "id": str(body.get("id") or f"msg_{uuid.uuid4().hex}"),
        "type": "message",
        "role": "assistant",
        "model": client_model,
        "content": content,
        "stop_reason": _stop_reason(choice.get("finish_reason")),
        "stop_sequence": None,
        "usage": _chat_usage(usage) if usage else {"input_tokens": 0, "output_tokens": 0},
    }
