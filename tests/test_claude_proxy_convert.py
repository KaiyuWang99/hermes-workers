from __future__ import annotations

from workers_core.backends import (
    claude_proxy,
    claude_proxy_convert,
    claude_proxy_params,
)


def test_anthropic_to_openai_keeps_request_shape():
    request = claude_proxy_convert.anthropic_to_openai(
        {
            "system": [{"type": "text", "text": "system"}],
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "hello"}]},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "reason"},
                        {
                            "type": "tool_use",
                            "id": "call_1",
                            "name": "read",
                            "input": {"path": "x"},
                        },
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "call_1", "content": "ok"}
                    ],
                },
            ],
            "tools": [
                {"type": "tool_search_tool_bm25_20251119", "name": "tool_search_tool_bm25"},
                {"name": "read", "description": "Read", "input_schema": {"type": "object"}},
            ],
            "tool_choice": {"type": "tool", "name": "read"},
            "thinking": {"type": "enabled", "budget_tokens": 8000},
            "max_tokens": 256,
            "temperature": 0.2,
            "stop_sequences": ["END"],
        },
        "upstream-model",
    )
    assert request == {
        "model": "upstream-model",
        "messages": [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "hello"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "read", "arguments": '{"path":"x"}'},
                    }
                ],
                "reasoning_content": "reason",
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
        ],
        "stream": False,
        "max_tokens": 256,
        "temperature": 0.2,
        "stop": ["END"],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "read",
                    "description": "Read",
                    "parameters": {"type": "object"},
                },
            }
        ],
        "tool_choice": {"type": "function", "function": {"name": "read"}},
        "reasoning_effort": "medium",
    }


def test_anthropic_to_responses_keeps_request_shape():
    request = claude_proxy_convert.anthropic_to_responses(
        {
            "system": "system",
            "messages": [
                {"role": "user", "content": "hello"},
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call_1",
                            "name": "read",
                            "input": {"path": "x"},
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "call_1", "content": "ok"}
                    ],
                },
            ],
            "tools": [
                {"type": "tool_search_tool_regex_20251119", "name": "tool_search_tool_regex"},
                {"name": "read", "input_schema": {"type": "object"}},
            ],
            "tool_choice": {"type": "any"},
            "thinking": {"type": "enabled", "budget_tokens": 20000},
            "max_tokens": 512,
        },
        "model-x",
    )
    assert request == {
        "model": "model-x",
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "hello"}],
            },
            {
                "type": "function_call",
                "call_id": "call_1",
                "name": "read",
                "arguments": '{"path":"x"}',
            },
            {"type": "function_call_output", "call_id": "call_1", "output": "ok"},
        ],
        "store": False,
        "stream": False,
        "instructions": "system",
        "max_output_tokens": 512,
        "tools": [
            {
                "type": "function",
                "name": "read",
                "description": "",
                "parameters": {"type": "object"},
            }
        ],
        "parallel_tool_calls": True,
        "tool_choice": "required",
        "reasoning": {"effort": "high"},
    }


def test_openai_to_anthropic_keeps_message_shape_and_usage():
    response = claude_proxy_convert.openai_to_anthropic(
        {
            "id": "chat_1",
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "content": "working",
                        "reasoning_content": "think",
                        "tool_calls": [
                            {
                                "id": "call_2",
                                "function": {"name": "write", "arguments": '{"path":"y"}'},
                            }
                        ],
                    },
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 4,
                "prompt_tokens_details": {"cached_tokens": 3},
            },
        },
        "sonnet",
    )
    assert response == {
        "id": "chat_1",
        "type": "message",
        "role": "assistant",
        "model": "sonnet",
        "content": [
            {"type": "thinking", "thinking": "think", "signature": ""},
            {"type": "text", "text": "working"},
            {"type": "tool_use", "id": "call_2", "name": "write", "input": {"path": "y"}},
        ],
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {"input_tokens": 7, "output_tokens": 4, "cache_read_input_tokens": 3},
    }


def test_responses_to_anthropic_keeps_message_shape_and_signature():
    reasoning_item = {
        "type": "reasoning",
        "id": "reasoning",
        "encrypted_content": "opaque",
        "summary": [{"type": "summary_text", "text": "think"}],
    }
    response = claude_proxy_convert.responses_to_anthropic(
        {
            "id": "resp_1",
            "status": "completed",
            "output": [
                reasoning_item,
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "working"}],
                },
                {
                    "type": "function_call",
                    "call_id": "call_2",
                    "name": "write",
                    "arguments": '{"path":"y"}',
                },
            ],
            "usage": {
                "input_tokens": 8,
                "output_tokens": 3,
                "input_tokens_details": {"cached_tokens": 2},
            },
        },
        "sonnet",
    )
    assert response == {
        "id": "resp_1",
        "type": "message",
        "role": "assistant",
        "model": "sonnet",
        "content": [
            {
                "type": "thinking",
                "thinking": "think",
                "signature": claude_proxy_convert._encode_reasoning_signature(reasoning_item),
            },
            {"type": "text", "text": "working"},
            {"type": "tool_use", "id": "call_2", "name": "write", "input": {"path": "y"}},
        ],
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {"input_tokens": 6, "output_tokens": 3, "cache_read_input_tokens": 2},
    }

    incomplete = claude_proxy_convert.responses_to_anthropic(
        {
            "id": "resp_2",
            "incomplete_details": {"reason": "max_output_tokens"},
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "cut"}],
                },
                {
                    "type": "function_call",
                    "call_id": "call_3",
                    "name": "write",
                    "arguments": '{"path":"z"}',
                },
            ],
        },
        "sonnet",
    )
    assert incomplete["stop_reason"] == "max_tokens"
    assert [block["type"] for block in incomplete["content"]] == ["text"]
    assert incomplete["usage"] == {"input_tokens": 0, "output_tokens": 0}


def test_anthropic_sse_empty_content_has_no_content_blocks():
    payload = claude_proxy_convert.anthropic_sse(
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "m",
            "content": [],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 5, "output_tokens": 0},
        }
    ).decode("utf-8")

    assert "event: message_start" in payload
    assert '"content":[]' in payload
    assert '"stop_reason":null' in payload
    assert "content_block_start" not in payload
    assert "content_block_delta" not in payload
    assert "event: message_delta" in payload
    assert '"stop_reason":"end_turn"' in payload
    assert '"output_tokens":0' in payload
    assert payload.endswith('event: message_stop\ndata: {"type":"message_stop"}\n\n')


def test_anthropic_sse_encodes_thinking_signature_and_tool_input():
    payload = claude_proxy_convert.anthropic_sse(
        {
            "id": "msg_2",
            "type": "message",
            "role": "assistant",
            "model": "m",
            "content": [
                {"type": "thinking", "thinking": "why", "signature": "sig-1"},
                {"type": "tool_use", "id": "call_1", "name": "read", "input": {"path": "x"}},
            ],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 1, "output_tokens": 2},
        }
    ).decode("utf-8")

    assert '"type":"thinking_delta","thinking":"why"' in payload
    assert '"type":"signature_delta","signature":"sig-1"' in payload
    assert '"type":"input_json_delta","partial_json":"{\\"path\\":\\"x\\"}"' in payload
    assert payload.count("event: content_block_start") == 2
    assert payload.count("event: content_block_stop") == 2


def test_chat_stream_events_empty_text_finish_and_error():
    state = claude_proxy_convert._StreamState("m")
    assert claude_proxy_convert._chat_stream_events(state, {"choices": []}) == []

    events = claude_proxy_convert._chat_stream_events(
        state, {"choices": [{"delta": {"content": "hi"}, "finish_reason": "stop"}]}
    )
    assert [name for name, _data in events] == [
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
    ]
    assert events[0][1]["content_block"] == {"type": "text", "text": ""}
    assert events[1][1]["delta"] == {"type": "text_delta", "text": "hi"}
    assert state.completed is True

    finish = claude_proxy_convert._state_finish_events(state)
    assert finish[0][1]["delta"] == {"stop_reason": "end_turn", "stop_sequence": None}
    assert finish[0][1]["usage"] == {"input_tokens": 0, "output_tokens": 0}
    assert finish[-1] == ("message_stop", {"type": "message_stop"})

    failed = claude_proxy_convert._StreamState("m")
    assert claude_proxy_convert._chat_stream_events(
        failed, {"error": {"message": "boom"}}
    ) == []
    assert failed.error == "boom"
    assert failed.completed is False
    assert claude_proxy_convert._state_error_events(failed) == [
        (
            "error",
            {"type": "error", "error": {"type": "api_error", "message": "boom"}},
        )
    ]


def test_responses_stream_events_completion_usage_and_invalid_tool_arguments():
    state = claude_proxy_convert._StreamState("m")
    assert (
        claude_proxy_convert._responses_stream_events(
            state,
            {
                "type": "response.completed",
                "response": {
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 4,
                        "input_tokens_details": {"cached_tokens": 2},
                    },
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "ok"}],
                        }
                    ],
                },
            },
        )
        == []
    )
    assert state.completed is True
    assert state.stop_reason == "end_turn"
    assert state.usage == {
        "input_tokens": 8,
        "output_tokens": 4,
        "cache_read_input_tokens": 2,
    }
    finish = claude_proxy_convert._state_finish_events(state)
    assert finish[0][1]["delta"]["stop_reason"] == "end_turn"
    assert finish[0][1]["usage"] == {
        "input_tokens": 8,
        "output_tokens": 4,
        "cache_read_input_tokens": 2,
    }

    broken = claude_proxy_convert._StreamState("m")
    claude_proxy_convert._responses_stream_events(
        broken,
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {"type": "function_call", "call_id": "c1", "name": "read"},
        },
    )
    claude_proxy_convert._responses_stream_events(
        broken,
        {
            "type": "response.function_call_arguments.delta",
            "output_index": 0,
            "delta": "{",
        },
    )
    assert (
        claude_proxy_convert._responses_stream_events(
            broken,
            {
                "type": "response.completed",
                "response": {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "c1",
                            "name": "read",
                            "arguments": "{",
                        }
                    ]
                },
            },
        )
        == []
    )
    assert broken.error == "upstream tool call 0 ended with invalid JSON arguments"
    assert broken.completed is True


def test_claude_proxy_reexports_moved_names():
    conversion_names = [
        "anthropic_to_openai",
        "anthropic_to_responses",
        "openai_to_anthropic",
        "responses_to_anthropic",
        "anthropic_sse",
        "_estimate_tokens",
        "_estimate_value_tokens",
        "_chat_stream_events",
        "_responses_stream_events",
        "_StreamState",
        "_iter_sse_data",
        "_encode_reasoning_signature",
        "_sse_bytes",
        "_chat_url",
        "_responses_url",
        "_error_message",
    ]
    for name in conversion_names:
        assert getattr(claude_proxy, name) is getattr(claude_proxy_convert, name), name

    fallback_names = [
        "_param_fallbacks",
        "_fallback_effort_request",
        "_should_try_variant",
        "_next_variant_index",
    ]
    for name in fallback_names:
        assert getattr(claude_proxy, name) is getattr(claude_proxy_params, name), name
