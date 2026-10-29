"""Regression coverage for the seven configuration/path audit findings."""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from workers_core import common, config, process_group
from workers_core.backends import codex, opencode


def test_opencode_runs_keep_independent_private_configs(tmp_path, monkeypatch):
    monkeypatch.setattr(opencode, "hermes_root", lambda: tmp_path)
    paths = [opencode._worker_config("p/m", provider_overlays={}, inherit_user_config=False,
             mcp_servers={"server": {"command": command}}) for command in ("first", "second")]
    assert paths[0] != paths[1]
    for path, command in zip(paths, ("first", "second")):
        assert json.loads(path.read_text())["mcp"]["server"]["command"] == [command]
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    monkeypatch.setattr(process_group, "_leader_pgid", lambda _pid: None)
    monkeypatch.setattr(process_group, "_group_empty", lambda _pid: True)
    monkeypatch.setattr(process_group.signal, "signal", lambda *_args: None)
    assert process_group.main(["999999", str(paths[0].parent)]) == 0
    assert not paths[0].exists()
    assert paths[1].exists()


@pytest.mark.parametrize("failure", [False, True])
def test_opencode_launch_owns_run_cleanup(tmp_path, monkeypatch, failure):
    binary = tmp_path / "opencode"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    document = {"inheritance": {"fallback_models": False}, "workers": {"opencode": {
        "binary": str(binary), "model": "p/m", "inherit_user_config": False,
    }}}
    monkeypatch.setattr(opencode, "hermes_root", lambda: tmp_path)
    monkeypatch.setattr(opencode, "require_credentials", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(opencode, "_provider_overlay", lambda *_args: ({}, {"api_mode": "chat_completions"}))
    monkeypatch.setattr(opencode, "ensure_opencode_session_header", lambda *_args: None)
    monkeypatch.setattr(opencode, "worker_log", lambda *_args: tmp_path / "worker.log")
    owned = []
    monkeypatch.setattr(process_group, "supervise", lambda pid, **kwargs: owned.append((pid, kwargs)))
    captured = {}

    def popen(_command, **kwargs):
        captured.update(kwargs)
        if failure:
            raise OSError("synthetic spawn failure")
        return SimpleNamespace(pid=987654)

    monkeypatch.setattr(opencode.subprocess, "Popen", popen)
    task = SimpleNamespace(id="test", model_override=None, provider_override=None, reasoning_effort=None)
    launch = common.WorkerLaunchContext(task=task, workspace=str(tmp_path), board=None,
        session_id="audit", env={}, mcp_servers={}, prompt="test")
    if failure:
        with pytest.raises(OSError, match="synthetic"):
            opencode.spawn(launch, config=document)
        assert not list((tmp_path / "state").glob("workers-opencode-*"))
        assert not owned
    else:
        assert opencode.spawn(launch, config=document) == 987654
        path = Path(captured["env"]["OPENCODE_CONFIG"])
        assert path.exists()
        assert owned == [(987654, {"task_id": "test", "board": None, "cleanup_dir": path.parent})]


def test_codex_stdio_env_isolated_and_secrets_stay_out_of_argv(tmp_path):
    import tomllib

    env = {"PATH": os.environ["PATH"], "AUDIT_TOKEN": "parent"}
    command = [sys.executable, "-c", "import os,json;print(json.dumps({'token':os.environ['AUDIT_TOKEN'],'envelopes':[k for k in os.environ if k.startswith('WORKERS_MCP_STDIO_')]}))"]
    args = codex._mcp_args({name: {"command": command[0], "args": command[1:],
        "env": {"AUDIT_TOKEN": value}, "cwd": str(tmp_path)} for name, value in (
        ("one", "FIRST_PRIVATE_VALUE"), ("two", "SECOND_PRIVATE_VALUE"))}, env)
    assert env["AUDIT_TOKEN"] == "parent"
    assert "FIRST_PRIVATE_VALUE" not in str(args) and "SECOND_PRIVATE_VALUE" not in str(args)
    entries = tomllib.loads("\n".join(args[1::2]))["mcp_servers"]
    for name, expected in (("one", "FIRST_PRIVATE_VALUE"), ("two", "SECOND_PRIVATE_VALUE")):
        entry = entries[name]
        forwarded = {key: env[key] for key in entry["env_vars"]}
        result = subprocess.run([entry["command"], *entry["args"]], env=forwarded,
            cwd=entry["cwd"], capture_output=True, text=True, check=True, timeout=10)
        assert json.loads(result.stdout) == {"token": expected, "envelopes": []}


def test_jsonc_preserves_strings_and_inline_comments(tmp_path):
    path = tmp_path / "opencode.jsonc"
    path.write_text(r'''{
      "url": "https://example.test/a/*literal*/", // inline comment
      "quoted": "a\"//still string", /* block comment */
      "list": ["value,}", /* comment */],
    }''')
    assert opencode._read_jsonc(path) == {"url": "https://example.test/a/*literal*/",
        "quoted": 'a"//still string', "list": ["value,}"]}


@pytest.mark.parametrize("text", ['{"secret":"PRIVATE_VALUE",broken}', '{/* broken', '[]'])
def test_invalid_existing_jsonc_reports_error_without_source_secrets(tmp_path, text):
    path = tmp_path / "opencode.jsonc"
    path.write_text(text)
    with pytest.raises(ValueError) as result:
        opencode._read_jsonc(path)
    assert str(path) in str(result.value)
    assert "PRIVATE_VALUE" not in str(result.value)
    assert opencode._read_jsonc(tmp_path / "missing.json") == {}


def test_opencode_discovers_and_merges_profile_xdg_and_explicit_files(tmp_path, monkeypatch):
    directory = tmp_path / "xdg/opencode"
    directory.mkdir(parents=True)
    (directory / "opencode.json").write_text('{"model":"json","provider":{"a":{"name":"A"}}}')
    (directory / "opencode.jsonc").write_text('{"model":"jsonc",/*inline*/"provider":{"b":{"name":"B"}}}')
    explicit = tmp_path / "explicit.json"
    explicit.write_text('{"model":"explicit"}')
    # The child's environment takes precedence over unrelated parent values.
    monkeypatch.setenv("OPENCODE_CONFIG", str(tmp_path / "unrelated-invalid.json"))
    (tmp_path / "unrelated-invalid.json").write_text("invalid")
    env = {"XDG_CONFIG_HOME": str(tmp_path / "xdg"), "HOME": str(tmp_path / "home")}
    assert opencode._user_config(env) == {"model": "jsonc", "provider": {"a": {"name": "A"}, "b": {"name": "B"}}}
    env["OPENCODE_CONFIG"] = str(explicit)
    assert opencode._user_config(env)["model"] == "explicit"
    env["OPENCODE_CONFIG"] = "explicit.json"
    assert opencode._user_config(env, str(tmp_path))["model"] == "explicit"


def test_exited_worker_cleanup_does_not_depend_on_live_leader(tmp_path, monkeypatch):
    run = tmp_path / "run"
    run.mkdir()
    monkeypatch.setattr(process_group.os, "getpgid", lambda _pid: (_ for _ in ()).throw(ProcessLookupError()))
    monkeypatch.setattr(process_group, "_group_empty", lambda _pid: True)
    assert process_group.supervise(987654, task_id="audit", cleanup_dir=run) is None
    assert not run.exists()


@pytest.mark.parametrize("selection", [["audit"], ["mcp-audit"], ["audit-composite"], ["all"], ["*"]])
@pytest.mark.parametrize("registered", [False, True])
def test_direct_mcp_uses_native_alias_and_composite_selection(tmp_path, monkeypatch, selection, registered):
    import toolsets
    from tools.registry import ToolRegistry
    from tools import mcp_tool_config

    registry = ToolRegistry()
    if registered:
        registry.register("audit_tool", "mcp-audit", {}, lambda: "{}")
        registry.register_toolset_alias("audit", "mcp-audit")
    monkeypatch.setattr(toolsets, "_registry", lambda: registry)
    monkeypatch.setitem(toolsets.TOOLSETS, "audit-composite", {"tools": [], "includes": ["mcp-audit"]})
    monkeypatch.setattr(mcp_tool_config, "_load_mcp_config", lambda: {
        "audit": {"command": sys.executable}, "disabled": {"command": sys.executable, "enabled": False}})
    assert set(common.worker_mcp_servers(str(tmp_path), selection)) == {"audit"}


def test_codex_default_prefers_native_config_then_path_and_discovers_bundles(tmp_path, monkeypatch):
    from hermes_cli import config as native_config

    monkeypatch.setattr(native_config, "load_config_readonly", lambda: {"model": {"codex_bin": "/chosen/codex"}})
    monkeypatch.setattr(codex.shutil, "which", lambda name: "/path/codex" if name == "codex" else None)
    assert codex._codex_binary() == "/chosen/codex"
    monkeypatch.setattr(native_config, "load_config_readonly", lambda: {})
    assert codex._codex_binary() == "/path/codex"
    monkeypatch.setattr(codex.shutil, "which", lambda _name: None)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    binary = tmp_path / "Applications/Example.app/Contents/Resources/codex-release/Embedded.app/Contents/MacOS/codex"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    assert codex._codex_binary() == str(binary)
