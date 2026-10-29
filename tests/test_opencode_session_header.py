"""One canonical OpenCode session-affinity header, applied by every backend.

The OpenCode relay rejects a request that omits ``x-opencode-session``, so the
header has to reach the upstream request of every Worker backend whose route
can land on an OpenCode provider.  ``provider_catalog`` owns the single
implementation; these tests pin its contract and prove both backends that
resolve Hermes provider runtimes actually wire it in.
"""

import json
import sys

from workers_core import provider_catalog
from workers_core.backends import claudecode, opencode


class _Task:
    id = "t_probe"
    assignee = "default"
    model_override = None
    provider_override = None
    reasoning_effort = None
    goal_mode = False


class _Launch:
    task = _Task()
    workspace = "/tmp"
    board = None
    session_id = "sid-wire"
    env = {}
    mcp_servers = {}
    prompt = "p"


class _Stdin:
    def __init__(self):
        self.data = b""

    def write(self, data):
        self.data += data

    def close(self):
        pass


# --- canonical contract ------------------------------------------------------


def test_opencode_provider_receives_the_worker_session_id():
    runtime = {"provider": "opencode-go", "model": "deepseek-flash"}
    assert provider_catalog.ensure_opencode_session_header(runtime, "sid-1") is runtime
    assert runtime["extra_headers"] == {"x-opencode-session": "sid-1"}


def test_pinned_value_wins():
    runtime = {
        "provider": "opencode-zen",
        "extra_headers": {"x-opencode-session": "pinned"},
    }
    provider_catalog.ensure_opencode_session_header(runtime, "sid-2")
    assert runtime["extra_headers"] == {"x-opencode-session": "pinned"}


def test_unrelated_headers_are_preserved():
    runtime = {"provider": "opencode-go", "extra_headers": {"x-trace": "1"}}
    provider_catalog.ensure_opencode_session_header(runtime, "sid-3")
    assert runtime["extra_headers"] == {"x-trace": "1", "x-opencode-session": "sid-3"}


def test_non_opencode_provider_is_untouched():
    runtime = {"provider": "anthropic", "model": "claude"}
    provider_catalog.ensure_opencode_session_header(runtime, "sid-4")
    assert "extra_headers" not in runtime


def test_empty_session_id_is_a_noop():
    runtime = {"provider": "opencode-go", "model": "deepseek-flash"}
    provider_catalog.ensure_opencode_session_header(runtime, "")
    assert "extra_headers" not in runtime


# --- wiring: every backend that resolves a Hermes provider runtime -----------


def _opencode_runtime(provider, model, **_kwargs):
    return {
        "provider": provider,
        "model": model,
        "base_url": "https://opencode.ai/zen/go/v1",
        "api_mode": "chat_completions",
        "api_key": "k",
    }


def test_claudecode_payload_carries_the_header(monkeypatch, tmp_path):
    from workers_core import process_group
    monkeypatch.setattr(process_group, "supervise", lambda *_args, **_kwargs: None)
    import hermes_cli.runtime_provider as runtime_provider

    monkeypatch.setattr(
        claudecode,
        "_entry",
        lambda config: {"binary": sys.executable, "model": "opencode-go/deepseek-flash"},
    )
    monkeypatch.setattr(claudecode, "require_credentials", lambda provider, model=None: {})
    monkeypatch.setattr(
        claudecode,
        "select_credential",
        lambda provider, model=None: {"credential_id": "", "key": "k"},
    )
    monkeypatch.setattr(claudecode, "fallback_entries_for", lambda provider, model: [])
    monkeypatch.setattr(
        runtime_provider,
        "resolve_runtime_provider",
        lambda **kwargs: _opencode_runtime(
            kwargs.get("requested", ""), kwargs.get("target_model", "")
        ),
    )
    monkeypatch.setattr(claudecode, "_mcp_config", lambda *a, **k: tmp_path / "mcp.json")
    monkeypatch.setattr(
        claudecode, "_system_prompt_file", lambda *a, **k: tmp_path / "system.md"
    )
    monkeypatch.setattr(claudecode, "_resolve_binary", lambda value: sys.executable)
    monkeypatch.setattr(claudecode, "_cli_flags", lambda binary: frozenset())
    monkeypatch.setattr(
        claudecode, "worker_log", lambda task_id, board: str(tmp_path / "worker.log")
    )

    started = {}

    class _Proc:
        pid = 4242

        def __init__(self, command, **kwargs):
            self.stdin = _Stdin()
            started["stdin"] = self.stdin

    monkeypatch.setattr(claudecode.subprocess, "Popen", _Proc)

    assert claudecode.spawn(_Launch(), config={}) == 4242
    payload = json.loads(started["stdin"].data.decode("utf-8"))
    assert payload["runtime"]["extra_headers"]["x-opencode-session"] == "sid-wire"


def test_opencode_payload_carries_the_header(monkeypatch, tmp_path):
    from workers_core import process_group
    monkeypatch.setattr(process_group, "supervise", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        opencode,
        "_entry",
        lambda config: {"binary": sys.executable, "model": "opencode-go/deepseek-flash"},
    )
    monkeypatch.setattr(opencode, "require_credentials", lambda provider, model=None: {})
    monkeypatch.setattr(opencode, "fallback_entries_for", lambda provider, model: [])
    monkeypatch.setattr(opencode, "resolve_model_runtime", _opencode_runtime)
    monkeypatch.setattr(
        opencode, "_worker_config", lambda *a, **k: tmp_path / "opencode.json"
    )
    monkeypatch.setattr(
        opencode, "worker_log", lambda task_id, board: str(tmp_path / "worker.log")
    )

    started = {}

    class _Proc:
        pid = 4243

        def __init__(self, command, **kwargs):
            started["env"] = kwargs.get("env") or {}

    monkeypatch.setattr(opencode.subprocess, "Popen", _Proc)

    assert opencode.spawn(_Launch(), config={}) == 4243
    headers = json.loads(started["env"]["WORKERS_OPENCODE_EXTRA_HEADERS"])
    assert headers["x-opencode-session"] == "sid-wire"
