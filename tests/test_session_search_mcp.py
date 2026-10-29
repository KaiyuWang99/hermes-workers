from __future__ import annotations

import json
import os

import pytest

from workers_core import capability_mcp


def _server(monkeypatch, names):
    import hermes_cli.plugins as plugins
    import mcp.server
    import model_tools
    from tools.session_search_tool import SESSION_SEARCH_SCHEMA

    class Server:
        def __init__(self, *_args, **_kwargs):
            self.handlers = {}

        def add_tool(self, handler, **kwargs):
            self.handlers[kwargs["name"]] = handler

    definitions = [
        {"type": "function", "function": (
            SESSION_SEARCH_SCHEMA if name == "session_search" else {
                "name": name, "parameters": {"type": "object"}
            }
        )}
        for name in names
    ]
    monkeypatch.setattr(plugins, "discover_plugins", lambda: None)
    monkeypatch.setattr(mcp.server, "MCPServer", Server, raising=False)
    monkeypatch.setattr(model_tools, "get_tool_definitions", lambda **_kwargs: definitions)
    monkeypatch.setenv("WORKERS_SESSION_ID", "claude-session")
    return capability_mcp.create_server()


def test_search_reuses_registered_native_handler_and_defaults(monkeypatch):
    import model_tools
    import tools.session_search_tool as native

    calls = []
    before = set(model_tools._AGENT_LOOP_TOOLS)
    server = _server(monkeypatch, ["session_search", "memory", "todo_list", "delegate_task"])
    monkeypatch.setattr(native, "session_search", lambda **kwargs: (
        calls.append(kwargs) or '{"results":[]}'
    ))
    assert server.handlers["session_search"](query="previous decision") == '{"results":[]}'
    assert calls[0]["query"] == "previous decision"
    assert calls[0]["current_session_id"] == "claude-session"
    assert calls[0]["limit"] == 3
    assert calls[0]["db"] is None  # native search opens/closes its own profile DB
    assert set(server.handlers) == {"session_search"}
    assert model_tools._AGENT_LOOP_TOOLS == before


def test_search_is_not_added_when_native_toolset_does_not_offer_it(monkeypatch):
    server = _server(monkeypatch, ["skill_view"])
    assert "session_search" not in server.handlers


def test_search_error_is_normalized_by_native_registry(monkeypatch):
    import tools.session_search_tool as native

    server = _server(monkeypatch, ["session_search"])

    def fail(**_kwargs):
        raise RuntimeError("isolated DB unavailable")

    monkeypatch.setattr(native, "session_search", fail)
    result = json.loads(server.handlers["session_search"](query="x"))
    assert "error" in result


@pytest.mark.skipif(
    not os.environ.get("HERMES_LIVE_PROFILE_TEST"),
    reason="reads the live profile session store; set HERMES_LIVE_PROFILE_TEST=1 to run",
)
def test_search_reads_the_selected_profiles_real_history(tmp_path, monkeypatch):
    from hermes_constants import get_hermes_home, reset_hermes_home_override, set_hermes_home_override
    from hermes_state import SessionDB

    server = _server(monkeypatch, ["session_search"])
    before = get_hermes_home()
    for label in ("a", "b", "a"):
        home = tmp_path / label
        home.mkdir(exist_ok=True)
        token = set_hermes_home_override(str(home))
        try:
            with SessionDB(home / "state.db") as db:
                db.create_session(f"history-{label}", "cli")
                db.append_message(f"history-{label}", "user", f"native_search_marker_{label}")
            result = server.handlers["session_search"](session_id=f"history-{label}")
            assert f"native_search_marker_{label}" in result
            assert f"native_search_marker_{'b' if label == 'a' else 'a'}" not in result
        finally:
            reset_hermes_home_override(token)
        assert get_hermes_home() == before
