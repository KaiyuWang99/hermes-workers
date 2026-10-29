"""Anthropic SSE encoding and upstream-stream to SSE event translation."""

from __future__ import annotations

import json
import uuid
from typing import Any, Iterator

import httpx

from .common import (
    _chat_usage,
    _encode_reasoning_signature,
    _json_text,
    _responses_usage,
    _stop_reason,
)


def _sse_bytes(events: list[tuple[str, dict[str, Any]]]) -> bytes:
    return b"".join(
        f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n".encode("utf-8")
        for name, data in events
    )


def anthropic_sse(message: dict[str, Any]) -> bytes:
    events: list[tuple[str, dict[str, Any]]] = []
    start = dict(message)
    start["content"] = []
    start["stop_reason"] = None
    start["usage"] = {
        "input_tokens": int((message.get("usage") or {}).get("input_tokens") or 0),
        "output_tokens": 0,
    }
    events.append(("message_start", {"type": "message_start", "message": start}))
    for index, block in enumerate(message.get("content") or []):
        kind = str(block.get("type") or "text")
        deltas: list[dict[str, Any]]
        if kind == "tool_use":
            initial = {"type": "tool_use", "id": block["id"], "name": block["name"], "input": {}}
            deltas = [{"type": "input_json_delta", "partial_json": _json_text(block.get("input") or {})}]
        elif kind == "thinking":
            initial = {"type": "thinking", "thinking": "", "signature": ""}
            deltas = [
                {"type": "thinking_delta", "thinking": str(block.get("thinking") or "")}
            ]
            signature = str(block.get("signature") or "")
            if signature:
                deltas.append({"type": "signature_delta", "signature": signature})
        else:
            initial = {"type": "text", "text": ""}
            deltas = [{"type": "text_delta", "text": str(block.get("text") or "")}]
        events.append(("content_block_start", {"type": "content_block_start", "index": index, "content_block": initial}))
        for delta in deltas:
            events.append(("content_block_delta", {"type": "content_block_delta", "index": index, "delta": delta}))
        events.append(("content_block_stop", {"type": "content_block_stop", "index": index}))
    events.append(
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": message.get("stop_reason"), "stop_sequence": None},
                "usage": {"output_tokens": int((message.get("usage") or {}).get("output_tokens") or 0)},
            },
        )
    )
    events.append(("message_stop", {"type": "message_stop"}))
    return _sse_bytes(events)


def _iter_sse_data(response: httpx.Response) -> Iterator[str]:
    """Yield the joined ``data:`` payload of each upstream SSE event."""
    data_lines: list[str] = []
    for line in response.iter_lines():
        if not line:
            if data_lines:
                yield "\n".join(data_lines)
                data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if data_lines:
        yield "\n".join(data_lines)


class _StreamState:
    """Tracks Anthropic content-block layout while relaying one upstream stream."""

    def __init__(self, client_model: str):
        self.client_model = client_model
        self.block_index = -1
        self.current_kind: str | None = None
        self.pending_tools: dict[int, dict[str, str]] = {}
        self.saw_tool = False
        self.stop_reason: str | None = None
        self.error: str | None = None
        self.completed = False
        self.usage: dict[str, Any] = {"input_tokens": 0, "output_tokens": 0}

    def start_message(self) -> dict[str, Any]:
        return {
            "id": f"msg_{uuid.uuid4().hex}",
            "type": "message",
            "role": "assistant",
            "model": self.client_model,
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }


def _state_open_block(state: _StreamState, initial: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    state.block_index += 1
    state.current_kind = str(initial.get("type") or "text")
    return [
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": state.block_index,
                "content_block": initial,
            },
        )
    ]


def _state_close_block(state: _StreamState) -> list[tuple[str, dict[str, Any]]]:
    if state.current_kind is None or state.block_index < 0:
        return []
    index = state.block_index
    state.current_kind = None
    return [("content_block_stop", {"type": "content_block_stop", "index": index})]


def _state_delta(state: _StreamState, delta: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    return (
        "content_block_delta",
        {"type": "content_block_delta", "index": state.block_index, "delta": delta},
    )


def _state_buffer_tool(
    state: _StreamState,
    index: int,
    *,
    call_id: Any = None,
    name: Any = None,
    arguments: Any = None,
    final: bool = False,
) -> None:
    tool = state.pending_tools.setdefault(index, {"id": "", "name": "", "arguments": ""})
    if call_id:
        tool["id"] = str(call_id)
    if name:
        tool["name"] = str(name)
    if arguments is not None:
        if final:
            tool["arguments"] = str(arguments)
        else:
            tool["arguments"] += str(arguments)
    state.saw_tool = True


def _state_emit_tools(state: _StreamState) -> list[tuple[str, dict[str, Any]]]:
    """Emit complete tool blocks in order after interleaved upstream deltas."""
    for index in sorted(state.pending_tools):
        arguments = state.pending_tools[index]["arguments"] or "{}"
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            state.error = f"upstream tool call {index} ended with invalid JSON arguments"
            return []
        if not isinstance(parsed, dict):
            state.error = f"upstream tool call {index} did not produce object arguments"
            return []
    events = _state_close_block(state)
    for index in sorted(state.pending_tools):
        tool = state.pending_tools[index]
        events.extend(
            _state_open_block(
                state,
                {
                    "type": "tool_use",
                    "id": tool["id"] or f"tool_{uuid.uuid4().hex}",
                    "name": tool["name"] or "unknown_tool",
                    "input": {},
                },
            )
        )
        events.append(
            _state_delta(
                state,
                {"type": "input_json_delta", "partial_json": tool["arguments"] or "{}"},
            )
        )
        events.extend(_state_close_block(state))
    state.pending_tools.clear()
    return events


def _state_finish_events(state: _StreamState) -> list[tuple[str, dict[str, Any]]]:
    events = _state_close_block(state)
    stop_reason = state.stop_reason or ("tool_use" if state.saw_tool else "end_turn")
    events.append(
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                # input_tokens is only known once the upstream stream ends;
                # including it here is non-standard but harmless and lets
                # Claude Code reconcile context usage after the turn.
                "usage": dict(state.usage),
            },
        )
    )
    events.append(("message_stop", {"type": "message_stop"}))
    return events


def _state_error_events(state: _StreamState) -> list[tuple[str, dict[str, Any]]]:
    events = _state_close_block(state)
    events.append(
        (
            "error",
            {
                "type": "error",
                "error": {"type": "api_error", "message": str(state.error or "upstream stream failed")},
            },
        )
    )
    return events


def _chat_stream_events(state: _StreamState, chunk: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    error = chunk.get("error")
    if error:
        state.error = str(error.get("message") or error) if isinstance(error, dict) else str(error)
        return events
    usage = chunk.get("usage")
    if isinstance(usage, dict) and usage:
        state.usage = _chat_usage(usage)
    choices = chunk.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return events
    choice = choices[0]
    delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
    if reasoning:
        if state.current_kind != "thinking":
            events.extend(_state_close_block(state))
            events.extend(_state_open_block(state, {"type": "thinking", "thinking": "", "signature": ""}))
        events.append(_state_delta(state, {"type": "thinking_delta", "thinking": str(reasoning)}))
    text = delta.get("content")
    if isinstance(text, str) and text:
        if state.current_kind != "text":
            events.extend(_state_close_block(state))
            events.extend(_state_open_block(state, {"type": "text", "text": ""}))
        events.append(_state_delta(state, {"type": "text_delta", "text": text}))
    for tool in delta.get("tool_calls") or []:
        if not isinstance(tool, dict):
            continue
        upstream_index = int(tool.get("index") or 0)
        function = tool.get("function") if isinstance(tool.get("function"), dict) else {}
        _state_buffer_tool(
            state,
            upstream_index,
            call_id=tool.get("id"),
            name=function.get("name"),
            arguments=function.get("arguments"),
        )
    finish = choice.get("finish_reason")
    if finish:
        state.stop_reason = "tool_use" if state.saw_tool and finish == "stop" else _stop_reason(finish)
        state.completed = True
        if finish == "length":
            # A cut-off function call is not safe to execute.
            state.pending_tools.clear()
        else:
            events.extend(_state_emit_tools(state))
    return events


def _responses_stream_events(state: _StreamState, payload: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    kind = str(payload.get("type") or "")
    if kind == "response.output_item.added":
        index = int(payload.get("output_index") or 0)
        item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
        item_type = str(item.get("type") or "")
        if item_type == "reasoning":
            events.extend(_state_close_block(state))
            events.extend(_state_open_block(state, {"type": "thinking", "thinking": "", "signature": ""}))
        elif item_type == "function_call":
            _state_buffer_tool(
                state, index, call_id=item.get("call_id") or item.get("id"), name=item.get("name")
            )
        # message items open lazily on their first output_text delta.
    elif kind == "response.reasoning_summary_text.delta":
        if state.current_kind != "thinking":
            events.extend(_state_close_block(state))
            events.extend(_state_open_block(state, {"type": "thinking", "thinking": "", "signature": ""}))
        events.append(
            _state_delta(state, {"type": "thinking_delta", "thinking": str(payload.get("delta") or "")})
        )
    elif kind == "response.output_text.delta":
        if state.current_kind != "text":
            events.extend(_state_close_block(state))
            events.extend(_state_open_block(state, {"type": "text", "text": ""}))
        events.append(
            _state_delta(state, {"type": "text_delta", "text": str(payload.get("delta") or "")})
        )
    elif kind == "response.function_call_arguments.delta":
        _state_buffer_tool(
            state, int(payload.get("output_index") or 0), arguments=payload.get("delta") or ""
        )
    elif kind == "response.output_item.done":
        item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
        item_type = str(item.get("type") or "")
        if item_type == "reasoning" and state.current_kind == "thinking":
            events.append(
                _state_delta(
                    state,
                    {"type": "signature_delta", "signature": _encode_reasoning_signature(item)},
                )
            )
            events.extend(_state_close_block(state))
        elif item_type in {"message", "function_call"}:
            if item_type == "function_call":
                _state_buffer_tool(
                    state,
                    int(payload.get("output_index") or 0),
                    call_id=item.get("call_id") or item.get("id"),
                    name=item.get("name"),
                    arguments=item.get("arguments"),
                    final=item.get("arguments") is not None,
                )
            else:
                events.extend(_state_close_block(state))
    elif kind in {"response.completed", "response.incomplete"}:
        response = payload.get("response") if isinstance(payload.get("response"), dict) else {}
        usage = response.get("usage")
        if isinstance(usage, dict) and usage:
            state.usage = _responses_usage(usage)
        has_tool = any(
            isinstance(item, dict) and item.get("type") == "function_call"
            for item in response.get("output") or []
        )
        incomplete = response.get("incomplete_details")
        reason = incomplete.get("reason") if isinstance(incomplete, dict) else None
        if kind == "response.incomplete" and reason not in {"max_output_tokens", "max_tokens"}:
            state.error = f"upstream response incomplete: {reason or 'unknown reason'}"
            return events
        for index, item in enumerate(response.get("output") or []):
            if isinstance(item, dict) and item.get("type") == "function_call":
                _state_buffer_tool(
                    state,
                    index,
                    call_id=item.get("call_id") or item.get("id"),
                    name=item.get("name"),
                    arguments=item.get("arguments"),
                    final=item.get("arguments") is not None,
                )
        state.saw_tool = state.saw_tool or has_tool
        state.stop_reason = state.stop_reason or (
            "max_tokens"
            if kind == "response.incomplete"
            else "tool_use"
            if has_tool
            else "end_turn"
        )
        state.completed = True
        if kind == "response.incomplete":
            state.pending_tools.clear()
        else:
            events.extend(_state_emit_tools(state))
    elif kind == "response.failed":
        response = payload.get("response") if isinstance(payload.get("response"), dict) else {}
        error = response.get("error") if isinstance(response.get("error"), dict) else {}
        state.error = str(error.get("message") or "upstream response failed")
    elif kind == "error":
        error = payload.get("error")
        state.error = str(
            payload.get("message")
            or (error.get("message") if isinstance(error, dict) else error)
            or "upstream stream error"
        )
    return events
