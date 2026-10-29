from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from agent import credential_pool
from workers_core import credentials


def test_pool_provider_uses_the_hermes_provider_alias_table():
    assert credentials.pool_provider("  OpenCode-Go ") == "opencode-go"
    assert credentials.pool_provider("go") == "opencode-go"
    assert credentials.pool_provider("zen") == "opencode-zen"
    assert credentials.pool_provider("opencode") == "opencode-zen"
    assert credentials.pool_provider("") == ""


def test_model_cooldown_does_not_block_sibling_model(monkeypatch):
    entry = credential_pool.PooledCredential(
        provider="opencode-go",
        id="fixture",
        label="fixture",
        auth_type="api_key",
        priority=0,
        source="manual",
        access_token="fixture-key",
        model_cooldowns={"model-a": time.time() + 600},
    )
    pool = credential_pool.CredentialPool("opencode-go", [entry])
    monkeypatch.setattr(pool, "_persist", lambda *args, **kwargs: None)
    monkeypatch.setattr(credential_pool, "load_pool", lambda provider: pool)

    assert credentials.credential_status("opencode-go", model="model-a")["available"] is False
    assert credentials.credential_status("opencode-go", model="model-a")["next_available_at"] is not None
    with pytest.raises(RuntimeError, match="cooling down"):
        credentials.require_credentials("opencode-go", model="model-a")
    assert credentials.require_credentials("opencode-go", model="model-b")["available"] is True
    assert credentials.select_credential("opencode-go", model="model-a") is None
    assert credentials.select_credential("opencode-go", model="model-b")["credential_id"] == "fixture"


@pytest.mark.parametrize("custom", [False, True])
@pytest.mark.parametrize("raises", [False, True])
def test_pool_without_available_entry_never_falls_back_to_single(monkeypatch, custom, raises):
    import hermes_cli.auth as auth

    def select(*, model):
        assert model == "model-a"
        if raises:
            raise RuntimeError("credential pool is unavailable")
        return None

    pool = SimpleNamespace(has_credentials=lambda: True, select=select)
    monkeypatch.setattr(
        credential_pool,
        "load_pool",
        lambda provider: pool if not custom or provider == "custom:fixture" else None,
    )
    monkeypatch.setattr(
        auth,
        "resolve_api_key_provider_credentials",
        lambda provider: pytest.fail("exhausted pool bypassed through single-key resolver"),
    )
    monkeypatch.setattr(
        credentials,
        "_custom_provider_entry",
        lambda provider: {"provider_key": "fixture", "api_key": "single-key"} if custom else None,
    )
    assert credentials.select_credential("fixture" if custom else "opencode-go", model="model-a") is None


def test_bridge_passes_model_and_error_details_to_native_pool(monkeypatch):
    observed = {}
    entry = SimpleNamespace(id="next", runtime_api_key="next-key")

    def rotate(**kwargs):
        observed.update(kwargs)
        return entry

    pool = SimpleNamespace(mark_exhausted_and_rotate=rotate)
    monkeypatch.setattr(credential_pool, "load_pool", lambda provider: pool)
    monkeypatch.setattr(credentials, "_custom_provider_entry", lambda provider: None)
    result = credentials.rotate_credential({
        "provider": "opencode-go",
        "credential_id": "old",
        "api_key": "old-key",
        "model": "model-a",
        "status_code": 429,
        "reason": "rate_limit",
        "error_type": "RateLimitError",
        "error_message": "request limit reached",
        "error_body": {"type": "RateLimitError"},
    })
    assert result == {"provider": "opencode-go", "credential_id": "next", "key": "next-key"}
    assert observed["model"] == "model-a"
    assert observed["failure_reason"] == "rate_limit"
    assert observed["error_context"]["reason"] == "rate_limit"
    assert observed["error_context"]["error_body"] == {"type": "RateLimitError"}
    assert observed["error_context"]["error_message"] == "request limit reached"
    assert "api_key" not in observed["error_context"]


def test_billing_uses_the_pool_vocabulary(monkeypatch):
    observed = {}
    entry = SimpleNamespace(id="next", runtime_api_key="next-key")
    pool = SimpleNamespace(mark_exhausted_and_rotate=lambda **kwargs: observed.update(kwargs) or entry)
    monkeypatch.setattr(credential_pool, "load_pool", lambda provider: pool)
    monkeypatch.setattr(credentials, "_custom_provider_entry", lambda provider: None)
    assert credentials.rotate_credential({
        "provider": "opencode-go",
        "credential_id": "old",
        "api_key": "old-key",
        "model": "model-a",
        "status_code": 402,
        "error_message": "insufficient credits",
    }) == {"provider": "opencode-go", "credential_id": "next", "key": "next-key"}
    assert observed["failure_reason"] == "billing"


@pytest.mark.parametrize("status_code", [403, "403"])
@pytest.mark.parametrize("provider", ["opencode-go", "anthropic"])
def test_region_error_never_mutates_native_pool(monkeypatch, status_code, provider):
    monkeypatch.setattr(
        credential_pool,
        "load_pool",
        lambda provider: pytest.fail("regional model rejection must not mutate credential pool"),
    )
    assert credentials.rotate_credential({
        "provider": provider,
        "credential_id": "fixture",
        "model": "muse-spark-1.3-contributor",
        "status_code": status_code,
        "error_body": {"type": "error", "error": {
            "type": "RegionError", "message": "This model is not available in your country",
        }},
    }) is None


def test_native_pool_type_error_is_not_retried_without_failure_reason(monkeypatch):
    calls = []

    def rotate(**kwargs):
        calls.append(kwargs)
        raise TypeError("internal provider failure")

    monkeypatch.setattr(credential_pool, "load_pool", lambda provider: SimpleNamespace(
        mark_exhausted_and_rotate=rotate,
    ))
    monkeypatch.setattr(credentials, "_custom_provider_entry", lambda provider: None)
    assert credentials.rotate_credential({
        "provider": "opencode-go", "credential_id": "fixture", "model": "model-a", "status_code": 429,
    }) is None
    assert len(calls) == 1
    assert calls[0]["model"] == "model-a"
