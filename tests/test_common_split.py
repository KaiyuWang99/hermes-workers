"""Structural checks for the common.py split.

Only re-export identity and signatures live here; the moved behavior stays
covered by test_worker_context.py and test_mcp_inheritance.py.
"""

from __future__ import annotations

import ast
import inspect
import subprocess
import sys
from pathlib import Path

from workers_core import common, common_context, common_env, common_mcp
from workers_core.contracts import WorkerLaunchContext


MOVED_HELPERS = {
    "build_worker_env": common_env,
    "worker_toolsets": common_env,
    "worker_mcp_env": common_env,
    "worker_mcp_servers": common_mcp,
    "inherited_mcp_toolsets": common_mcp,
    "protocol_prompt": common_context,
    "_profile_context_prompt": common_context,
}
ORCHESTRATION = ("build_launch_context", "worker_log", "worker_runner_command")
PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _shape(function):
    return [
        (name, parameter.kind.name, parameter.default is inspect.Parameter.empty)
        for name, parameter in inspect.signature(function).parameters.items()
    ]


def test_moved_helpers_are_reexported_from_common():
    for name, owner in MOVED_HELPERS.items():
        reexported = getattr(common, name)
        assert reexported is getattr(owner, name), name
        assert reexported.__module__ == owner.__name__, name
    public = {name for name in MOVED_HELPERS if not name.startswith("_")}
    assert public | set(ORCHESTRATION) | {"WorkerLaunchContext"} <= set(common.__all__)


def test_orchestration_stays_in_common():
    for name in ORCHESTRATION:
        assert getattr(common, name).__module__ == "workers_core.common", name
    assert common.WorkerLaunchContext is WorkerLaunchContext


def test_launch_context_signature_is_unchanged():
    assert _shape(common.build_launch_context) == [
        ("task", "POSITIONAL_OR_KEYWORD", True),
        ("workspace", "POSITIONAL_OR_KEYWORD", True),
        ("board", "POSITIONAL_OR_KEYWORD", True),
        ("config", "POSITIONAL_OR_KEYWORD", True),
        ("env", "KEYWORD_ONLY", True),
    ]
    assert _shape(common.worker_log) == [
        ("task_id", "POSITIONAL_OR_KEYWORD", True),
        ("board", "POSITIONAL_OR_KEYWORD", True),
    ]
    assert _shape(common.worker_runner_command) == [
        ("module", "POSITIONAL_OR_KEYWORD", True),
    ]


def test_environment_signatures_are_unchanged():
    assert _shape(common.build_worker_env) == [
        ("task", "POSITIONAL_OR_KEYWORD", True),
        ("workspace", "POSITIONAL_OR_KEYWORD", True),
        ("board", "POSITIONAL_OR_KEYWORD", True),
        ("inherit_credentials", "KEYWORD_ONLY", False),
    ]
    assert _shape(common.worker_toolsets) == [("hermes_home", "POSITIONAL_OR_KEYWORD", True)]
    assert _shape(common.worker_mcp_env) == [("env", "POSITIONAL_OR_KEYWORD", True)]


def test_mcp_signatures_are_unchanged():
    assert _shape(common.worker_mcp_servers) == [
        ("hermes_home", "POSITIONAL_OR_KEYWORD", True),
        ("toolsets", "POSITIONAL_OR_KEYWORD", True),
        ("workspace", "KEYWORD_ONLY", False),
    ]
    assert _shape(common.inherited_mcp_toolsets) == [
        ("hermes_home", "POSITIONAL_OR_KEYWORD", True),
    ]


def test_context_signatures_are_unchanged():
    assert _shape(common.protocol_prompt) == [("task_id", "POSITIONAL_OR_KEYWORD", True)]
    assert _shape(common._profile_context_prompt) == [
        ("task_id", "KEYWORD_ONLY", True),
        ("session_id", "KEYWORD_ONLY", True),
        ("hermes_home", "KEYWORD_ONLY", True),
        ("workspace", "KEYWORD_ONLY", True),
        ("toolsets", "KEYWORD_ONLY", True),
        ("skills", "KEYWORD_ONLY", False),
        ("inheritance", "KEYWORD_ONLY", False),
    ]


def test_new_modules_do_not_import_common_at_module_level():
    for module in (common_env, common_mcp, common_context):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        imported = {
            node.module
            for node in tree.body
            if isinstance(node, ast.ImportFrom)
        }
        assert "common" not in imported, module.__name__


def test_modules_import_in_a_fresh_interpreter():
    code = (
        "import workers_core.common_env\n"
        "import workers_core.common_mcp\n"
        "import workers_core.common_context\n"
        "import workers_core.common as c\n"
        "assert c.build_launch_context.__module__ == 'workers_core.common'\n"
    )
    subprocess.run(
        [sys.executable, "-c", code], cwd=str(PLUGIN_ROOT), check=True, timeout=120
    )
