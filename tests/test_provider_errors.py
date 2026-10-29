from __future__ import annotations

import json
import threading
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import httpx
import pytest

from workers_core import provider_errors
from workers_core.backends import claude_proxy, claude_runner


COUNTRY = "This model is not available in your country"
REGION = {"error": {"type": "RegionError", "message": COUNTRY}}


# --- region is the one Workers correction ------------------------------------


@pytest.mark.parametrize("provider", ["opencode-go", "anthropic", "custom:fixture"])
@pytest.mark.parametrize(
    "fields",
    [
        {"error_body": REGION},
        {"error_body": {"name": "RegionError", "data": {"message": COUNTRY}}},
        {"error_type": "RegionError"},
        {"error_code": "RegionError"},
        {"error_message": COUNTRY},
        {"error_message": f"API Error: 403 {json.dumps(REGION)}"},
        # Claude Code's real CLI failure line wraps the body; this exact form used
        # to slip the guard and bench both pool entries for an hour (2026-10-28).
        {"error_message": f"Failed to authenticate. API Error: 403 {COUNTRY}"},
        {"error_message": f"Failed to authenticate. API Error: 403 {json.dumps(REGION)}"},
    ],
)
def test_region_403_is_fallback_without_credential_bench(provider, fields):
    decision = provider_errors.classify(
        provider_errors.normalize_error(
            provider=provider,
            model="muse-spark-1.3-contributor",
            status_code=403,
            **fields,
        )
    )
    assert decision.reason == "upstream_blocked"
    assert decision.retryable is False
    assert decision.should_rotate_credential is False
    assert decision.should_fallback is True


def test_region_never_reaches_the_core_classifier(monkeypatch):
    monkeypatch.setattr(
        provider_errors, "_core_classify", lambda error: pytest.fail("core classified a region error")
    )
    decision = provider_errors.classify(
        provider_errors.normalize_error(status_code=403, error_body=REGION)
    )
    assert decision.reason == "upstream_blocked"


@pytest.mark.parametrize(
    "payload",
    [
        {"status_code": 401, "error_body": REGION},
        {"status_code": 402, "error_body": REGION},
        {"status_code": 429, "error_body": REGION},
        {"status_code": "401", "error_body": REGION},
        {"status_code": 403, "error_body": {"error": {"type": "authentication_error", "message": "Invalid API key"}}},
        {"status_code": 403, "error_body": {"message": "Spending limit exceeded"}},
        {"status_code": 403, "error_body": {"prompt": {"type": "RegionError", "message": COUNTRY}}},
        {"status_code": 403, "error_message": f"A document quotes: {COUNTRY}"},
    ],
)
def test_unrelated_failures_and_conversation_fields_are_not_region(payload):
    assert provider_errors.correct_api_error_classification(**payload) is None


def test_status_strings_are_normalized_at_the_boundary():
    assert provider_errors.from_payload({"status_code": "403"}).status_code == 403
    assert provider_errors.from_payload({"status_code": True}).status_code is None


# --- every other reason comes from Hermes ------------------------------------


def test_core_owns_the_reason_and_hint_inputs(monkeypatch):
    seen = []

    def fake(error):
        seen.append(error)
        return SimpleNamespace(
            reason=SimpleNamespace(value="billing"),
            retryable=False,
            should_fallback=True,
            error_context={"billing_unverified": False},
            billing_unverified=True,
        )

    monkeypatch.setattr(provider_errors, "_core_classify", fake)
    decision = provider_errors.classify(
        provider_errors.normalize_error(status_code=403, error_message="spending limit exceeded")
    )
    assert decision.reason == "billing"
    assert decision.retryable is False
    assert decision.should_rotate_credential is True
    assert decision.error_context == {"billing_unverified": False}
    assert decision.billing_unverified is True
    assert seen[0].status_code == 403


# Core's verdict: deterministic client errors abort to the fallback chain;
# transient 408/5xx are retried on the same route first.
_CORE_FALLBACK = frozenset({400, 401, 402, 403, 404, 429})


@pytest.mark.parametrize(
    "status, reason, rotate, fallback",
    [
        (401, "auth", True, True),
        (402, "billing", True, True),
        (403, "auth", True, True),
        (429, "rate_limit", True, True),
        (408, "timeout", False, False),
        (500, "server_error", False, False),
        (503, "overloaded", False, False),
        (400, "format_error", False, True),
        (404, "model_not_found", False, True),
    ],
)
def test_worker_actions_follow_core_reason_and_status(status, reason, rotate, fallback, monkeypatch):
    monkeypatch.setattr(
        provider_errors,
        "_core_classify",
        lambda error: SimpleNamespace(
            reason=SimpleNamespace(value=reason),
            retryable=True,
            should_fallback=error.status_code in _CORE_FALLBACK,
            error_context={},
            billing_unverified=False,
        ),
    )
    decision = provider_errors.classify(provider_errors.normalize_error(status_code=status))
    assert decision.should_rotate_credential is rotate
    assert decision.should_fallback is fallback


def test_workers_no_longer_keep_a_local_fallback_status_table():
    assert not hasattr(provider_errors, "_FALLBACK_STATUSES")


def test_statusless_cli_error_never_rotates_or_falls_back(monkeypatch):
    monkeypatch.setattr(
        provider_errors,
        "_core_classify",
        lambda error: SimpleNamespace(
            reason=SimpleNamespace(value="auth"),
            retryable=True,
            # Core would fall back, but without a status there is no trustworthy
            # signal that another model should be tried.
            should_fallback=True,
            error_context={},
            billing_unverified=False,
        ),
    )
    decision = provider_errors.classify(
        provider_errors.normalize_error(error_message=f"Failed to authenticate. API Error: 403 {COUNTRY}")
    )
    assert decision.should_rotate_credential is False
    assert decision.should_fallback is False


# --- native hook: correction only -------------------------------------------


def test_hook_returns_only_the_region_correction():
    verdict = provider_errors.correct_api_error_classification(
        provider="any-provider",
        status_code=403,
        error_code="RegionError",
        error_body={"error": {"message": COUNTRY}},
    )
    assert verdict == {
        "reason": "upstream_blocked",
        "retryable": False,
        "should_rotate_credential": False,
        "should_fallback": True,
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"provider": "opencode-go", "status_code": 403,
         "error_body": {"error": {"message": "Invalid API key"}}},
        {"provider": "opencode-go", "status_code": 401, "error_body": REGION},
        {"provider": "opencode-go", "status_code": 402, "error_body": REGION},
        {"provider": "opencode-go", "status_code": None, "error_body": REGION},
    ],
)
def test_hook_declines_everything_else(payload):
    assert provider_errors.correct_api_error_classification(**payload) is None


def test_hook_ignores_hook_specific_kwargs():
    verdict = provider_errors.correct_api_error_classification(
        provider="opencode-go",
        status_code=403,
        error_body=REGION,
        approx_tokens=1,
        context_length=2,
        num_messages=3,
        error=RuntimeError("x"),
    )
    assert verdict["reason"] == "upstream_blocked"


# --- adapters feed the same classifier --------------------------------------


def _runtime():
    return {
        "provider": "opencode-go",
        "model": "muse-spark-1.3-contributor",
        "base_url": "https://opencode.ai/zen/go/v1",
        "api_key": "test-key",
    }


@pytest.mark.parametrize(
    "result_fields",
    [
        {"api_error_body": REGION},
        {"result": f"API Error: 403 {json.dumps(REGION)}"},
        {"api_error_message": COUNTRY},
    ],
)
def test_claude_runner_never_rotates_for_structured_region_error(monkeypatch, result_fields):
    output = json.dumps(
        {"type": "result", "is_error": True, "api_error_status": 403, **result_fields}
    )
    monkeypatch.setattr(claude_runner, "_session_claim_taken", False)
    monkeypatch.setattr(claude_runner, "_run_routed", lambda *_args: (1, output))
    monkeypatch.setattr(
        claude_runner, "rotate_credential", lambda _payload: pytest.fail("region rotated")
    )
    assert claude_runner._run_candidate(["claude", "prompt"], _runtime(), {}) == (1, output)


@pytest.mark.parametrize("status, reason", [(401, "auth"), (402, "billing"), (403, "auth"), (429, "rate_limit")])
def test_claude_runner_rotates_on_credential_reasons(monkeypatch, status, reason):
    result = {"type": "result", "api_error_status": status, "api_error_body": {"error": {"message": "x"}}}
    output = json.dumps(result)
    seen = []
    monkeypatch.setattr(claude_runner, "_session_claim_taken", False)
    monkeypatch.setattr(claude_runner, "_run_routed", lambda *_args: (1, output))
    monkeypatch.setattr(claude_runner, "rotate_credential", lambda payload: seen.append(payload))
    claude_runner._run_candidate(["claude", "prompt"], _runtime(), {})
    assert len(seen) == 1
    assert seen[0]["status_code"] == status
    assert seen[0]["model"] == _runtime()["model"]
    assert seen[0]["error_body"] == result["api_error_body"]


def test_claude_runner_does_not_rotate_on_5xx(monkeypatch):
    output = json.dumps({"type": "result", "api_error_status": 503})
    monkeypatch.setattr(claude_runner, "_session_claim_taken", False)
    monkeypatch.setattr(claude_runner, "_run_routed", lambda *_args: (1, output))
    monkeypatch.setattr(
        claude_runner, "rotate_credential", lambda _payload: pytest.fail("5xx rotated")
    )
    assert claude_runner._run_candidate(["claude", "prompt"], _runtime(), {}) == (1, output)


def test_claude_runner_ignores_country_text_outside_api_result(monkeypatch):
    output = "\n".join(
        [
            json.dumps({"type": "assistant", "message": {"content": COUNTRY}}),
            json.dumps({"type": "result", "is_error": True}),
        ]
    )
    monkeypatch.setattr(claude_runner, "_session_claim_taken", False)
    monkeypatch.setattr(claude_runner, "_run_routed", lambda *_args: (1, output))
    monkeypatch.setattr(
        claude_runner, "rotate_credential", lambda _payload: pytest.fail("text rotated")
    )
    assert claude_runner._run_candidate(["claude", COUNTRY], _runtime(), {}) == (1, output)


@pytest.mark.parametrize(
    "status, body, expected",
    [
        (403, REGION, False),
        (403, {"error": {"message": "Invalid API key"}}, True),
        (401, {"error": {"message": "Invalid API key"}}, True),
        (402, {"error": {"message": "No credits"}}, True),
        (429, {"error": {"message": "Rate limit"}}, True),
    ],
)
def test_proxy_decides_from_upstream_error_body_before_rotation(monkeypatch, status, body, expected):
    from workers_core import credentials

    seen = []
    monkeypatch.setattr(
        credentials,
        "rotate_credential",
        lambda payload: (seen.append(payload) or {"key": "next-test-key", "credential_id": "next-id"}),
    )
    handler = object.__new__(claude_proxy._Handler)
    handler.server = SimpleNamespace(runtime_lock=threading.Lock())
    runtime = _runtime()
    response = httpx.Response(status, json=body)
    assert handler._rotate(runtime, "test-key", response) is expected
    assert len(seen) == int(expected)
    if expected:
        assert seen[0]["error_body"] == body
        assert seen[0]["model"] == runtime["model"]


@pytest.mark.parametrize(
    "upstream_type,message,expected_rotations",
    [
        ("RegionError", "Access to this model is restricted in your region.", []),
        ("authentication_error", "Invalid API key", ["proxy", "runner"]),
    ],
)
def test_proxy_cli_runner_preserves_typed_region_errors_end_to_end(
    monkeypatch, upstream_type, message, expected_rotations
):
    from workers_core import credentials

    rotations = []
    upstream_requests = []

    def send(_client, request, **_kwargs):
        upstream_requests.append(request)
        return httpx.Response(403, json={"error": {"type": upstream_type, "message": message}})

    monkeypatch.setattr(claude_proxy.httpx.Client, "send", send)
    monkeypatch.setattr(credentials, "rotate_credential", lambda payload: (rotations.append("proxy") or None))
    monkeypatch.setattr(claude_runner, "rotate_credential", lambda payload: (rotations.append("runner") or None))
    monkeypatch.setattr(claude_runner, "_session_claim_taken", False)
    runtime = {**_runtime(), "api_mode": "chat_completions", "route": "builtin"}
    downstream = []
    with claude_proxy.BuiltinClaudeProxy(runtime) as proxy:
        def fake_cli(_command, _env, _runtime, _hooks=None, _proxy=None):
            request = Request(
                f"{proxy.base_url}/v1/messages",
                data=json.dumps(
                    {
                        "model": runtime["model"],
                        "max_tokens": 16,
                        "messages": [{"role": "user", "content": "probe"}],
                    }
                ).encode(),
                headers={"content-type": "application/json"},
                method="POST",
            )
            with pytest.raises(HTTPError) as caught:
                urlopen(request, timeout=5)
            response = caught.value
            with response:
                body = json.loads(response.read())
            downstream.append(body)
            return 1, json.dumps(
                {
                    "type": "result",
                    "is_error": True,
                    "api_error_status": response.code,
                    "result": f"API Error: {response.code} {json.dumps(body)}",
                }
            )

        monkeypatch.setattr(claude_runner, "_run_routed", fake_cli)
        returncode, _output = claude_runner._run_candidate(
            ["claude", "probe"], runtime, {}, proxy=proxy
        )
    assert returncode == 1
    assert len(upstream_requests) == 1
    assert downstream[0]["error"]["message"] == message
    assert downstream[0]["error"]["type"] == (
        "RegionError" if upstream_type == "RegionError" else "api_error"
    )
    assert rotations == expected_rotations
