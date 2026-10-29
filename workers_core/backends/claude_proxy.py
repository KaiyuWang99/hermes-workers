"""Small per-worker Anthropic Messages to OpenAI Chat Completions bridge.

Request/response/SSE conversion lives in :mod:`claude_proxy_convert` and the
parameter fallback ladder in :mod:`claude_proxy_params`; this module keeps the
HTTP handler and the stateful upstream lifecycle.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from contextlib import AbstractContextManager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

import httpx

from ..provider_errors import classify, normalize_error
from .claude_proxy_convert import (  # noqa: F401 — re-exported for callers and tests
    _StreamState,
    _chat_stream_events,
    _chat_url,
    _encode_reasoning_signature,
    _error_message,
    _estimate_tokens,
    _estimate_value_tokens,
    _iter_sse_data,
    _responses_stream_events,
    _responses_url,
    _sse_bytes,
    _state_error_events,
    _state_finish_events,
    anthropic_sse,
    anthropic_to_openai,
    anthropic_to_responses,
    openai_to_anthropic,
    responses_to_anthropic,
)
# Re-exported so callers (and tests) can keep addressing the bridge module.
from .claude_proxy_params import (  # noqa: F401 — re-exported for callers and tests
    _fallback_effort_request,
    _next_variant_index,
    _param_fallbacks,
    _should_try_variant,
)


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, runtime: dict[str, Any]):
        # One Claude worker makes many model/tool turns. Keep one upstream
        # client for its lifetime so CONNECT/TLS connections can be reused.
        self.runtime = runtime
        self.runtime_lock = threading.Lock()
        self.client = httpx.Client(
            timeout=httpx.Timeout(900.0, connect=20.0),
            follow_redirects=True,
        )
        super().__init__(("127.0.0.1", 0), _Handler)


class _Handler(BaseHTTPRequestHandler):
    server: _Server
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _send_json(self, status: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _write_events(self, events: list[tuple[str, dict[str, Any]]]) -> None:
        if not events:
            return
        self.wfile.write(_sse_bytes(events))
        self.wfile.flush()

    def _headers(self, runtime: dict[str, Any]) -> dict[str, str]:
        headers = {"content-type": "application/json", "accept": "application/json"}
        extra = runtime.get("extra_headers")
        if isinstance(extra, dict):
            headers.update(
                {
                    str(key): str(value)
                    for key, value in extra.items()
                    if str(key).strip() and value is not None
                }
            )
        auth_headers = {key.lower() for key in headers}
        if runtime.get("api_key") and not auth_headers.intersection(
            {"authorization", "x-api-key", "api-key"}
        ):
            headers["authorization"] = f"Bearer {runtime['api_key']}"
        return headers

    def _send_upstream(
        self, runtime: dict[str, Any], url: str, request_body: dict[str, Any]
    ) -> httpx.Response:
        headers = self._headers(runtime)
        if request_body.get("stream"):
            headers["accept"] = "text/event-stream"
        if os.environ.get("WORKERS_CLAUDE_PROXY_DEBUG"):
            parsed = urlsplit(url)
            sys.stderr.write(
                "[workers] claude-proxy upstream "
                f"{parsed.scheme}://{parsed.netloc}{parsed.path} "
                f"model={request_body.get('model', '')}\n"
            )
            sys.stderr.flush()
        request = self.server.client.build_request(
            "POST", url, headers=headers, json=request_body
        )
        return self.server.client.send(request, stream=True, follow_redirects=True)

    def _rotate(
        self, runtime: dict[str, Any], attempted_key: str, response: httpx.Response
    ) -> bool:
        from ..credentials import rotate_credential

        try:
            error_body = response.json()
        except ValueError:
            error_body = None
        error = normalize_error(
            provider=runtime.get("provider", ""),
            model=runtime.get("model", ""),
            status_code=response.status_code,
            error_body=error_body,
            error_message=_error_message(response),
            source="workers-claude-proxy",
        )
        verdict = classify(error)
        if not verdict.should_rotate_credential:
            return False
        payload = {
            "provider": error.provider,
            "model": error.model,
            "credential_id": runtime.get("credential_id", ""),
            "api_key": attempted_key,
            "status_code": error.status_code,
            "source": error.source,
            "error_body": error.body,
            "error_message": error.message,
        }
        with self.server.runtime_lock:
            # Another concurrent request may already have rotated this worker.
            if str(runtime.get("api_key") or "") != attempted_key:
                return True
            next_credential = rotate_credential(payload)
            if not next_credential or not next_credential.get("key"):
                return False
            next_key = str(next_credential["key"])
            if not next_key or next_key == attempted_key:
                return False
            runtime["api_key"] = next_key
            runtime["credential_id"] = str(
                next_credential.get("credential_id") or ""
            )
            return True

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path.rstrip("/")
        if path.endswith("health"):
            self._send_json(200, {"status": "healthy"})
        else:
            self._send_json(404, {"type": "error", "error": {"type": "not_found_error", "message": "Not found"}})

    def _count_tokens(self, body: dict[str, Any]) -> None:
        # This endpoint is an estimate. Never tokenize image base64 as text;
        # the encoded bytes can otherwise dominate the context calculation.
        tokens = (
            _estimate_value_tokens(body.get("system") or "")
            + _estimate_value_tokens(body.get("messages") or [])
            + _estimate_value_tokens(body.get("tools") or [])
        )
        self._send_json(200, {"input_tokens": max(1, tokens)})

    def do_POST(self) -> None:  # noqa: N802
        try:
            length = int(self.headers.get("content-length") or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(body, dict):
                raise ValueError("request body must be an object")
        except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
            self._send_json(400, {"type": "error", "error": {"type": "invalid_request_error", "message": str(exc)}})
            return
        path = urlsplit(self.path).path.rstrip("/")
        if path.endswith("count_tokens"):
            self._count_tokens(body)
            return
        if not path.endswith("messages"):
            self._send_json(404, {"type": "error", "error": {"type": "not_found_error", "message": "Not found"}})
            return
        runtime = self.server.runtime
        mode = str(runtime.get("api_mode") or "")
        client_stream = bool(body.get("stream"))
        is_responses = mode in {"responses", "codex_responses"}
        if is_responses:
            request_body = anthropic_to_responses(
                body,
                runtime["model"],
                stream=client_stream,
            )
            upstream_url = _responses_url(runtime["base_url"])
        else:
            request_body = anthropic_to_openai(body, runtime["model"], stream=client_stream)
            upstream_url = _chat_url(runtime["base_url"])
        attempted_key = str(runtime.get("api_key") or "")
        error_text = ""
        response: httpx.Response | None = None
        try:
            response = self._send_upstream(runtime, upstream_url, request_body)
            if response.status_code >= 400:
                response.read()
                if self._rotate(runtime, attempted_key, response):
                    response.close()
                    response = self._send_upstream(runtime, upstream_url, request_body)
            while response.status_code in {400, 422}:
                response.read()
                error_text = _error_message(response)
                fallbacks = _param_fallbacks(request_body, responses=is_responses)
                next_index = _next_variant_index(
                    error_text, ["", *(tag for tag, _variant in fallbacks)], 0
                )
                if next_index is None:
                    break
                response.close()
                request_body = fallbacks[next_index - 1][1]
                response = self._send_upstream(runtime, upstream_url, request_body)
        except httpx.HTTPError as exc:
            if response is not None:
                response.close()
            self._send_json(502, {"type": "error", "error": {"type": "api_error", "message": str(exc)}})
            return
        if response.status_code >= 400:
            response.read()
            error_text = _error_message(response)
            try:
                error_body = response.json()
            except ValueError:
                error_body = None
            # Preserve the classification across the Claude CLI boundary. A
            # generic api_error would discard a typed RegionError whose message
            # differs from the one known phrase, letting the runner rotate it.
            error = normalize_error(
                provider=runtime.get("provider", ""),
                model=runtime.get("model", ""),
                status_code=response.status_code,
                error_body=error_body,
                error_message=error_text,
                source="workers-claude-proxy",
            )
            error_type = (
                "RegionError"
                if classify(error).reason == "upstream_blocked"
                else "api_error"
            )
            self._send_json(
                response.status_code,
                {"type": "error", "error": {"type": error_type, "message": error_text}},
            )
            response.close()
            return
        content_type = response.headers.get("content-type", "").lower()
        if client_stream and "text/event-stream" in content_type:
            self._relay_stream(response, mode=mode, runtime=runtime)
            return
        try:
            response.read()
            upstream = response.json()
            if not isinstance(upstream, dict):
                raise ValueError("upstream response must be an object")
            if upstream.get("error"):
                error = upstream["error"]
                raise ValueError(
                    str(error.get("message") or error) if isinstance(error, dict) else str(error)
                )
            if is_responses and upstream.get("status") in {"failed", "cancelled"}:
                raise ValueError(f"upstream response {upstream['status']}")
            if is_responses and upstream.get("status") == "incomplete":
                incomplete = upstream.get("incomplete_details")
                reason = incomplete.get("reason") if isinstance(incomplete, dict) else None
                if reason not in {"max_output_tokens", "max_tokens"}:
                    raise ValueError(f"upstream response incomplete: {reason or 'unknown reason'}")
            if is_responses:
                converted = responses_to_anthropic(
                    upstream, str(runtime.get("client_model") or runtime["model"])
                )
            else:
                converted = openai_to_anthropic(
                    upstream, str(runtime.get("client_model") or runtime["model"])
                )
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json(502, {"type": "error", "error": {"type": "api_error", "message": str(exc)}})
            return
        finally:
            response.close()
        if client_stream:
            self._send_pseudo_sse(converted)
        else:
            self._send_json(200, converted)

    def _send_pseudo_sse(self, message: dict[str, Any]) -> None:
        """Fallback for upstreams that answer a stream request with plain JSON."""
        payload = anthropic_sse(message)
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("cache-control", "no-cache")
        self.send_header("connection", "close")
        self.end_headers()
        self.wfile.write(payload)
        self.wfile.flush()

    def _relay_stream(self, response: httpx.Response, *, mode: str, runtime: dict[str, Any]) -> None:
        """Convert one upstream SSE stream into Anthropic SSE incrementally."""
        client_model = str(runtime.get("client_model") or runtime["model"])
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("cache-control", "no-cache")
        self.send_header("connection", "close")
        self.end_headers()
        state = _StreamState(client_model)
        convert = (
            _responses_stream_events
            if mode in {"responses", "codex_responses"}
            else _chat_stream_events
        )
        try:
            # message_start+ping go out as soon as the upstream accepted the
            # request so Claude Code sees a live stream during slow thinking.
            self._write_events(
                [
                    ("message_start", {"type": "message_start", "message": state.start_message()}),
                    ("ping", {"type": "ping"}),
                ]
            )
            for data in _iter_sse_data(response):
                if data == "[DONE]":
                    break
                payload = json.loads(data)
                if not isinstance(payload, dict):
                    continue
                self._write_events(convert(state, payload))
                if state.error is not None:
                    break
            if not state.completed and state.error is None:
                state.error = "upstream stream ended without a completion event"
            if state.error is not None:
                self._write_events(_state_error_events(state))
            else:
                self._write_events(_state_finish_events(state))
        except (BrokenPipeError, ConnectionResetError):
            # Claude Code aborted the turn; closing the response below also
            # aborts the upstream generation instead of paying for it fully.
            pass
        except (httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
            try:
                self._write_events(
                    [("error", {"type": "error", "error": {"type": "api_error", "message": str(exc)}})]
                )
            except (BrokenPipeError, ConnectionResetError):
                pass
        finally:
            response.close()


class BuiltinClaudeProxy(AbstractContextManager["BuiltinClaudeProxy"]):
    """One loopback-only bridge whose lifetime matches one Claude worker attempt.

    A goal-mode card keeps the same instance across every resume turn, so the
    bridge must stay free of per-request state: anything cached here would
    accumulate for the whole card and outlive the turn that produced it.
    """

    def __init__(self, runtime: dict[str, Any]):
        self._server = _Server(runtime)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}"

    def __enter__(self) -> "BuiltinClaudeProxy":
        self._thread.start()
        return self

    def __exit__(self, *_args: Any) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)
        self._server.client.close()
