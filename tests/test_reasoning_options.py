from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


def load_api():
    path = Path(__file__).resolve().parents[1] / "dashboard" / "plugin_api.py"
    name = "workers_dashboard_reasoning_test"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_parse_effort_choices_from_wrapped_help_uses_cli_values():
    from workers_core.reasoning import parse_claude_effort_options

    help_text = """Options:
  --effort <level>    Select the session effort
                      (calm, focused,
                       thorough, exhaustive)
  --fallback-model <model>  Use another model
"""

    assert parse_claude_effort_options(help_text) == (
        "calm",
        "focused",
        "thorough",
        "exhaustive",
    )


def test_parse_effort_choices_without_flag_returns_empty():
    from workers_core.reasoning import parse_claude_effort_options

    assert parse_claude_effort_options("Options:\n  --model <model>  Select a model\n") == ()


def test_effort_flag_without_list_is_probe_error():
    from workers_core.reasoning import EffortOptionsError, parse_claude_effort_options

    with pytest.raises(EffortOptionsError):
        parse_claude_effort_options("Options:\n  --effort <level>  Select an effort\n")


def test_probe_cache_is_bound_to_binary_stamp(monkeypatch):
    from workers_core.reasoning import _probe_claude_effort_options

    _probe_claude_effort_options.cache_clear()
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout="Options:\n  --effort <level> (swift, steady, deep)\n",
            stderr="",
        )

    monkeypatch.setattr("workers_core.reasoning.subprocess.run", run)
    assert _probe_claude_effort_options("/test/claude", 1) == ("swift", "steady", "deep")
    assert _probe_claude_effort_options("/test/claude", 1) == ("swift", "steady", "deep")
    assert _probe_claude_effort_options("/test/claude", 2) == ("swift", "steady", "deep")
    assert len(calls) == 2
    assert all(call[0] == ["/test/claude", "--help"] for call in calls)
    _probe_claude_effort_options.cache_clear()


def test_probe_error_is_not_converted_to_guessed_options(monkeypatch):
    from workers_core.reasoning import EffortOptionsError, _probe_claude_effort_options

    _probe_claude_effort_options.cache_clear()
    monkeypatch.setattr(
        "workers_core.reasoning.subprocess.run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired("claude", 10)),
    )
    with pytest.raises(EffortOptionsError):
        _probe_claude_effort_options("/test/claude", 1)
    _probe_claude_effort_options.cache_clear()


def test_read_only_endpoint_returns_choices_without_saving(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    api = load_api()
    monkeypatch.setattr(
        api,
        "load_config",
        lambda: {"workers": {"claude-code": {"binary": "/configured/claude"}}},
    )
    monkeypatch.setattr(api, "save_config", lambda *_args, **_kwargs: pytest.fail("GET must not save config"))
    from workers_core import options as backend_options

    monkeypatch.setattr(
        backend_options,
        "claude_effort_options",
        lambda binary, **_kwargs: ("low", "high")
        if binary == "/configured/claude"
        else pytest.fail("wrong binary"),
    )

    assert api.get_options("claude-code", model="provider/model") == {
        "backend": "claude-code",
        "kind": "effort",
        "options": ["low", "high"],
        "default": "",
        "source": "claude-cli-help",
    }
    assert not (tmp_path / "hermes-home" / "config.yaml").exists()


def test_read_only_endpoint_returns_empty_options_when_flag_is_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    api = load_api()
    monkeypatch.setattr(api, "load_config", lambda: {"workers": {"claude-code": {"binary": "claude"}}})
    from workers_core import options as backend_options

    monkeypatch.setattr(backend_options, "claude_effort_options", lambda _binary, **_kwargs: ())

    assert api.get_options("claude-code") == {
        "backend": "claude-code",
        "kind": "effort",
        "options": [],
        "default": "",
        "source": "claude-cli-help",
        "message": "Claude Code CLI does not expose --effort; its default will be used.",
    }


def test_options_endpoint_returns_503_when_binary_is_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    api = load_api()
    monkeypatch.setattr(api, "load_config", lambda: {"workers": {"claude-code": {"binary": "missing"}}})
    from workers_core.backends import claudecode

    monkeypatch.setattr(claudecode, "_resolve_binary", lambda _binary: None)

    with pytest.raises(HTTPException) as error:
        api.get_options("claude-code")

    assert error.value.status_code == 503
    assert error.value.detail == "Worker backend options are unavailable."


def test_options_endpoint_returns_503_for_failed_probe(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    api = load_api()
    monkeypatch.setattr(api, "load_config", lambda: {"workers": {"claude-code": {"binary": "configured"}}})
    from workers_core import options as backend_options
    from workers_core.reasoning import EffortOptionsError

    monkeypatch.setattr(
        backend_options,
        "claude_effort_options",
        lambda _binary, **_kwargs: (_ for _ in ()).throw(
            EffortOptionsError("private probe output")
        ),
    )

    with pytest.raises(HTTPException) as error:
        api.get_options("claude-code")

    assert error.value.status_code == 503
    assert error.value.detail == "Worker backend options are unavailable."
    assert "private" not in str(error.value.detail)
