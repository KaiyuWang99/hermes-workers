"""Restore one Codex MCP server's environment, then replace this process."""

from __future__ import annotations

import json
import os
import sys


def main() -> None:
    envelope = sys.argv[1]
    spec = json.loads(os.environ[envelope])
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("WORKERS_MCP_STDIO_")}
    env.update({str(key): str(value) for key, value in spec["env"].items()})
    command = str(spec["command"])
    os.execvpe(command, [command, *spec.get("args", [])], env)


if __name__ == "__main__":
    main()
