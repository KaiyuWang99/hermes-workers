"""Local CLI/config/process probes. Never submit a model request."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

PLUGIN = Path(__file__).resolve().parents[1]
CORE = Path.home() / ".hermes/hermes-agent"
for directory in (PLUGIN, CORE):
    sys.path.insert(0, str(directory))

from workers_core.backends import codex, opencode
from workers_core import process_group


def wait_removed(path: Path) -> None:
    deadline = time.monotonic() + 20
    while path.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not path.exists(), f"Run directory was not cleaned: {path}"


def main() -> None:
    saved_env = dict(os.environ)
    results = {}
    try:
        with tempfile.TemporaryDirectory(prefix="workers-audit-native-") as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            directory = root / "xdg/opencode"
            directory.mkdir(parents=True)
            env = {key: value for key, value in saved_env.items() if key in {"PATH", "TMPDIR", "LANG", "SHELL"}}
            env.update({
                "HOME": str(root / "home"), "XDG_CONFIG_HOME": str(root / "xdg"),
                "XDG_DATA_HOME": str(root / "data"), "XDG_CACHE_HOME": str(root / "cache"),
                "XDG_STATE_HOME": str(root / "state"), "HERMES_HOME": str(root / "hermes"),
                "CODEX_HOME": str(root / "codex"), "OPENCODE_DISABLE_MODELS_FETCH": "true",
                "OPENCODE_DISABLE_DEFAULT_PLUGINS": "true", "OPENCODE_DISABLE_AUTOUPDATE": "true",
                "OPENCODE_DISABLE_PROJECT_CONFIG": "true",
            })
            os.environ.clear()
            os.environ.update(env)
            Path(env["CODEX_HOME"]).mkdir()
            (Path(env["CODEX_HOME"]) / "config.toml").write_text('[mcp_servers.native]\ncommand="echo"\n')

            binary = shutil.which("opencode")
            assert binary, "OpenCode is required for the native config probe"

            def native_config(overrides=None):
                run = subprocess.run([binary, "debug", "config"], cwd=workspace,
                    env={**env, **(overrides or {})}, capture_output=True, text=True, timeout=30)
                assert run.returncode == 0, "Synthetic OpenCode config was rejected"
                return json.loads(run.stdout)

            for filename, expected in [("config.json", "legacy"), ("opencode.json", "json"), ("opencode.jsonc", "jsonc")]:
                (directory / filename).write_text('{"model":"audit/' + expected + '", // inline\n "instructions":["' + expected + '"]}')
                assert native_config()["model"] == opencode._user_config(env)["model"] == f"audit/{expected}"
            explicit = root / "explicit.jsonc"
            explicit.write_text('{"model":"audit/explicit"}')
            override = {"OPENCODE_CONFIG": str(explicit)}
            assert native_config(override)["model"] == opencode._user_config({**env, **override})["model"] == "audit/explicit"
            (workspace / "opencode.jsonc").write_text('{"model":"audit/project"}')
            assert native_config({"OPENCODE_DISABLE_PROJECT_CONFIG": "false"})["model"] == "audit/project"
            assert native_config()["model"] == "audit/jsonc"
            results["opencode_native_discovery_and_project_switch"] = "passed"

            codex_binary = shutil.which("codex")
            assert codex_binary, "Codex is required for the config-schema probe"
            args = codex._mcp_args({
                "audit-one": {"command": sys.executable, "args": ["-V"], "env": {"AUDIT_TOKEN": "FIRST_SYNTHETIC"}},
                "audit.two": {"command": sys.executable, "args": ["-V"], "env": {"AUDIT_TOKEN": "SECOND_SYNTHETIC"}},
            }, env)
            run = subprocess.run([codex_binary, *args, "mcp", "list", "--json"],
                env=env, capture_output=True, text=True, timeout=20)
            assert run.returncode == 0, "Codex rejected synthetic MCP config"
            listed = json.loads(run.stdout)
            assert {row["name"] for row in listed} == {"audit-one", "audit.two", "native"}
            results["codex_native_mcp_config_schema"] = "passed"

            paths = [opencode._worker_config("audit/model", provider_overlays={}, inherit_user_config=False,
                mcp_servers={"audit": {"command": marker}}, env=env) for marker in ("first", "second", "third")]
            assert len(set(paths)) == 3
            for path in paths:
                assert path.is_file()
            # Exercise the actual watchdog against real process groups, using
            # synthetic local leaders rather than a subscribed model CLI.
            normal = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(0.5)"], env=env, start_new_session=True)
            process_group.supervise(normal.pid, task_id="audit-normal", cleanup_dir=paths[0].parent)
            normal.wait(timeout=5)
            wait_removed(paths[0].parent)
            assert paths[1].is_file()
            killed = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(60)"], env=env, start_new_session=True)
            try:
                process_group.supervise(killed.pid, task_id="audit-killed", cleanup_dir=paths[1].parent)
                killed.kill()
                killed.wait(timeout=5)
                wait_removed(paths[1].parent)
            finally:
                if killed.poll() is None:
                    killed.kill()
                    killed.wait()
            assert paths[2].is_file()
            fast = subprocess.Popen([sys.executable, "-c", "pass"], env=env, start_new_session=True)
            fast.wait(timeout=5)
            process_group.supervise(fast.pid, task_id="audit-fast", cleanup_dir=paths[2].parent)
            wait_removed(paths[2].parent)
            results["opencode_distinct_configs_normal_sigkill_fast_exit_cleanup"] = "passed"
    finally:
        os.environ.clear()
        os.environ.update(saved_env)
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
