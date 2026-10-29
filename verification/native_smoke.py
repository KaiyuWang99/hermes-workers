"""Run with Hermes' dependency bootstrap; use only synthetic local inputs."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import runpy
import signal
import subprocess
import sys
import tempfile


PLUGIN = Path(__file__).resolve().parents[1]
SOURCE = Path.home() / ".hermes" / "hermes-agent"
sys.path.insert(0, str(PLUGIN))


async def check_mcp(command, env, task_id):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    parameters = StdioServerParameters(command=command[0], args=command[1:], env=env)
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            definitions = await session.list_tools()
            names = {tool.name for tool in definitions.tools}
            assert {"kanban_show", "skill_view"} <= names
            result = await session.call_tool("kanban_show", {"task_id": task_id})
            payload = json.loads(next(item.text for item in result.content if hasattr(item, "text")))
            assert payload["task"]["title"] == "Native adapter smoke"
            assert "SYNTHETIC CARD BODY" in payload["worker_context"]
            return len(names)


def main():
    from hermes_cli._launchers import runtime_command
    from hermes_cli import kanban_db, kanban_db_connect
    from workers_core.common import _profile_context_prompt

    launch_env = dict(os.environ)
    with tempfile.TemporaryDirectory(prefix="workers-native-smoke-") as temporary:
        root = Path(temporary)
        home, workspace = root / "home", root / "project"
        home.mkdir()
        workspace.mkdir()
        (home / "config.yaml").write_text("toolsets: [skills, kanban]\nskills:\n  auto_load: [smoke]\n")
        skill = home / "skills" / "smoke" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text('---\nname: smoke\ndescription: "Native smoke skill"\n---\n\nUNIQUE NATIVE SKILL BODY\n')
        (workspace / "CLAUDE.md").write_text("NATIVE PROJECT RULE")
        os.environ["HERMES_HOME"] = str(home)
        database = root / "kanban.db"
        with kanban_db_connect.connect_closing(database) as connection:
            task_id = kanban_db.create_task(connection, title="Native adapter smoke",
                body="SYNTHETIC CARD BODY", assignee="default", initial_status="blocked")
        context = _profile_context_prompt(task_id=task_id, session_id="native-smoke",
            hermes_home=str(home), workspace=str(workspace), toolsets=["skills"], skills=["smoke"])
        assert context.count("UNIQUE NATIVE SKILL BODY") == 1
        assert "NATIVE PROJECT RULE" in context

        child_env = {
            "HERMES_HOME": str(home), "HERMES_PROFILE": "default",
            "HERMES_KANBAN_TASK": task_id, "HERMES_KANBAN_DB": str(database),
            "TERMINAL_CWD": str(workspace), "WORKERS_HERMES_TOOLSETS": '["skills", "kanban"]',
        }
        code = (f"import os,sys,runpy; os.environ.update({child_env!r}); "
                f"sys.path.insert(0,{str(PLUGIN)!r}); "
                "runpy.run_module('workers_core.capability_mcp',run_name='__main__')")
        command = runtime_command(SOURCE, code=code)
        count = asyncio.run(check_mcp(command, launch_env, task_id))

        owned = root / "owned-run"
        other = root / "other-run"
        owned.mkdir()
        other.mkdir()
        (owned / "settings.json").write_text("synthetic settings")
        leader = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
        code = (f"import sys,runpy; sys.path.insert(0,{str(PLUGIN)!r}); "
                f"sys.argv=['process_group',{str(leader.pid)!r},{str(owned)!r}]; "
                "runpy.run_module('workers_core.process_group',run_name='__main__')")
        watchdog = subprocess.Popen(runtime_command(SOURCE, code=code), env=launch_env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            os.kill(leader.pid, signal.SIGKILL)
            leader.wait(timeout=5)
            output, errors = watchdog.communicate(timeout=20)
            assert watchdog.returncode == 0, errors.decode()
            assert not owned.exists()
            assert other.is_dir()
        finally:
            for process in (leader, watchdog):
                if process.poll() is None:
                    process.kill()
                    process.wait()
        print(json.dumps({"python": sys.version.split()[0], "native_context": "pass",
            "mcp_tools": count, "kanban_show_roundtrip": "pass", "sigkill_cleanup": "pass"}))
    os.environ.clear()
    os.environ.update(launch_env)


if __name__ == "__main__":
    main()
