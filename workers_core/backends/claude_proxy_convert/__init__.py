"""Pure request, response and SSE conversions for the Claude bridge.

Nothing in this package keeps per-request state or performs I/O; the HTTP
handler in ``claude_proxy`` owns all of that and imports these helpers.

The implementation is split by protocol surface, and this module is the
single public index so ``workers_core.backends.claude_proxy_convert`` keeps
exposing every historical name (including the private helpers):

- :mod:`.common` -- token estimates, content/tool parsing, reasoning
  signatures, usage mapping, URL and error helpers
- :mod:`.chat` -- Anthropic <-> chat-completions conversion
- :mod:`.responses` -- Anthropic <-> Responses API conversion
- :mod:`.stream` -- SSE encoding and upstream stream event translation
"""

from __future__ import annotations

from .chat import (
    anthropic_to_openai,
    openai_to_anthropic,
)
from .common import (
    _EXTENDED_EFFORTS,
    _REASONING_SIGNATURE_PREFIX,
    _arguments,
    _chat_url,
    _chat_usage,
    _content_parts,
    _decode_reasoning_signature,
    _encode_reasoning_signature,
    _error_message,
    _estimate_tokens,
    _estimate_value_tokens,
    _image_part,
    _is_client_tool,
    _json_text,
    _openai_content,
    _reasoning_effort,
    _responses_reasoning_items,
    _responses_url,
    _responses_usage,
    _stop_reason,
    _tool_choice,
    _tool_result_parts,
)
from .responses import (
    _responses_parts,
    anthropic_to_responses,
    responses_to_anthropic,
)
from .stream import (
    _StreamState,
    _chat_stream_events,
    _iter_sse_data,
    _responses_stream_events,
    _sse_bytes,
    _state_buffer_tool,
    _state_close_block,
    _state_delta,
    _state_emit_tools,
    _state_error_events,
    _state_finish_events,
    _state_open_block,
    anthropic_sse,
)


__all__ = [
    "_EXTENDED_EFFORTS",
    "_REASONING_SIGNATURE_PREFIX",
    "_StreamState",
    "_arguments",
    "_chat_stream_events",
    "_chat_url",
    "_chat_usage",
    "_content_parts",
    "_decode_reasoning_signature",
    "_encode_reasoning_signature",
    "_error_message",
    "_estimate_tokens",
    "_estimate_value_tokens",
    "_image_part",
    "_is_client_tool",
    "_iter_sse_data",
    "_json_text",
    "_openai_content",
    "_reasoning_effort",
    "_responses_parts",
    "_responses_reasoning_items",
    "_responses_stream_events",
    "_responses_url",
    "_responses_usage",
    "_sse_bytes",
    "_state_buffer_tool",
    "_state_close_block",
    "_state_delta",
    "_state_emit_tools",
    "_state_error_events",
    "_state_finish_events",
    "_state_open_block",
    "_stop_reason",
    "_tool_choice",
    "_tool_result_parts",
    "anthropic_sse",
    "anthropic_to_openai",
    "anthropic_to_responses",
    "openai_to_anthropic",
    "responses_to_anthropic",
]
