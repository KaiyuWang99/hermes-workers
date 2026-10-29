"""Structural checks for the claude_proxy_convert package split.

Behavior stays covered by test_claude_proxy_convert.py; this file only guards
the package layout, the one-way dependency direction and the identity of the
re-exported names.
"""

from __future__ import annotations

import ast
from pathlib import Path

from workers_core.backends import claude_proxy, claude_proxy_convert, claude_proxy_params
from workers_core.backends.claude_proxy_convert import chat, common, responses, stream

SUB_MODULES = (common, chat, responses, stream)
# Names the bridge (claude_proxy) imports from the package.
BRIDGE_NAMES = {
    "_StreamState",
    "_chat_stream_events",
    "_chat_url",
    "_encode_reasoning_signature",
    "_error_message",
    "_estimate_tokens",
    "_estimate_value_tokens",
    "_iter_sse_data",
    "_responses_stream_events",
    "_responses_url",
    "_sse_bytes",
    "_state_error_events",
    "_state_finish_events",
    "anthropic_sse",
    "anthropic_to_openai",
    "anthropic_to_responses",
    "openai_to_anthropic",
    "responses_to_anthropic",
}


def _sibling_imports(module) -> set[str]:
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    return {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level
    }


def test_package_layout_is_preserved():
    assert hasattr(claude_proxy_convert, "__path__")
    package_dir = Path(claude_proxy_convert.__file__).parent
    assert claude_proxy_convert.__file__ == str(package_dir / "__init__.py")
    for module in SUB_MODULES:
        assert Path(module.__file__).parent == package_dir


def test_reexported_names_are_the_submodule_objects():
    assert len(claude_proxy_convert.__all__) == len(set(claude_proxy_convert.__all__))
    for name in claude_proxy_convert.__all__:
        value = getattr(claude_proxy_convert, name)
        assert any(vars(module).get(name) is value for module in SUB_MODULES), name


def test_bridge_names_are_reexported_unchanged():
    assert BRIDGE_NAMES <= set(claude_proxy_convert.__all__)
    for name in BRIDGE_NAMES:
        assert getattr(claude_proxy, name) is getattr(claude_proxy_convert, name), name
    assert (
        claude_proxy_params._EXTENDED_EFFORTS
        is claude_proxy_convert._EXTENDED_EFFORTS
    )


def test_dependency_direction_is_one_way():
    assert _sibling_imports(common) == set()
    for module in (chat, responses, stream):
        assert _sibling_imports(module) <= {"common"}, module.__name__
