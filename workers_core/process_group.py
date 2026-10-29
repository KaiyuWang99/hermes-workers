"""Own the process group a Worker was launched into.

Every backend starts its Worker with ``start_new_session=True``, so the PID it
returns leads a process group holding the backend CLI and every MCP server that
CLI starts. Hermes signals only that one PID
(``kanban_db_dispatch.enforce_max_runtime`` / ``_terminate_reclaimed_worker``),
so the moment a Worker exits — normally, by SIGTERM, by SIGKILL, or on
``max_runtime_seconds`` — its MCP servers are reparented to init and keep
running. Measured 2026-09-10: orphaned ``codebase-memory-mcp`` processes burned
26-35% CPU each for tens of minutes, and the load they added starved launchd's
background QoS.

:func:`supervise` starts one watchdog per Worker alongside it, in its own session
so it outlives the group it has to reap. The watchdog waits for the leader to
disappear, then signals the whole group: SIGTERM, a bounded grace period,
SIGKILL.
"""

from __future__ import annotations

import os
import signal
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

#: Seconds between leader-liveness probes. One syscall on Darwin, one small
#: ``/proc`` read on Linux; both are far cheaper than a ``ps`` fork.
_POLL_SECONDS = 1.0

#: Seconds the group gets to honour SIGTERM before it is SIGKILLed.
_TERM_GRACE_SECONDS = 5.0

#: Seconds SIGKILL gets to drain the group before the watchdog gives up.
_KILL_GRACE_SECONDS = 2.0

_PS_TIMEOUT_SECONDS = 5


def _leader_pgid(pid: int) -> int | None:
    """Live process-group id of ``pid``, or None once it is no longer running.

    ``os.kill(pid, 0)`` cannot answer this: it still succeeds for a Worker that
    has exited but has not been reaped (verified on this host, Darwin 25.6,
    2026-09-10). Darwin's ``getpgid(2)`` returns ESRCH for exactly that state,
    and Linux keeps a zombie's group but marks it ``Z`` in ``/proc``.
    """
    if sys.platform == "darwin":
        try:
            return os.getpgid(pid)
        except OSError:
            return None
    if sys.platform == "linux":
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError:
            return None
        # "pid (comm) state ppid pgrp ..." — comm may itself contain spaces.
        fields = stat.rsplit(")", 1)[-1].split()
        if len(fields) < 3 or fields[0] == "Z":
            return None
        try:
            return int(fields[2])
        except ValueError:
            return None
    return _ps_pgid(pid)


def _ps_pgid(pid: int) -> int | None:
    """``ps`` fallback for the remaining POSIX platforms; None when not live."""
    try:
        result = subprocess.run(
            ["ps", "-o", "pgid=,stat=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=_PS_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    fields = (result.stdout or "").split()
    if result.returncode != 0 or len(fields) != 2 or "Z" in fields[1]:
        return None
    try:
        return int(fields[0])
    except ValueError:
        return None


def _group_members(pgid: int) -> list[int] | None:
    """PIDs still running in ``pgid``, or None when the probe itself failed.

    ``killpg(pgid, 0)`` cannot answer this: an unreaped zombie keeps the group
    alive, and waiting out the full grace period for one zombie would delay
    every reap.
    """
    try:
        result = subprocess.run(
            ["ps", "-Ao", "pid=,pgid=,stat="],
            capture_output=True,
            text=True,
            timeout=_PS_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    members: list[int] = []
    for line in (result.stdout or "").splitlines():
        fields = line.split()
        if len(fields) != 3 or "Z" in fields[2]:
            continue
        try:
            pid, group = int(fields[0]), int(fields[1])
        except ValueError:
            continue
        if group == pgid:
            members.append(pid)
    return members


def _group_empty(pgid: int) -> bool:
    """True only when the group is provably cleared. A failed probe reads as
    "not empty": signalling an empty group is a no-op, skipping a live one
    leaks exactly the processes this module exists to reap."""
    members = _group_members(pgid)
    return members is not None and not members


def _signal_group(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except OSError:
        pass


def reap_group(pgid: int) -> dict[str, Any]:
    """Empty ``pgid`` and report what it took, for the Worker log."""
    started = time.monotonic()
    if _group_empty(pgid):
        return {"pgid": pgid, "members": [], "sigkill": False, "seconds": 0.0}
    members = _group_members(pgid) or []
    _signal_group(pgid, signal.SIGTERM)
    deadline = started + _TERM_GRACE_SECONDS
    while time.monotonic() < deadline and not _group_empty(pgid):
        time.sleep(_POLL_SECONDS)
    sigkill = not _group_empty(pgid)
    if sigkill:
        _signal_group(pgid, signal.SIGKILL)
        deadline = time.monotonic() + _KILL_GRACE_SECONDS
        while time.monotonic() < deadline and not _group_empty(pgid):
            time.sleep(_POLL_SECONDS)
    return {
        "pgid": pgid,
        "members": members,
        "sigkill": sigkill,
        "survivors": _group_members(pgid) or [],
        "seconds": round(time.monotonic() - started, 2),
    }


def supervise(
    pid: int, *, task_id: str, board: str | None = None, cleanup_dir: Path | None = None
) -> int | None:
    """Start the watchdog that owns ``pid``'s process group; None when there is none.

    A Worker whose PID does not lead its own process group shares one with the
    dispatcher, and signalling that group would take Hermes down with it — so an
    unowned Worker is left alone rather than guessed at.
    """
    if os.name != "posix":
        return None
    try:
        if os.getpgid(pid) != pid:
            return None
    except OSError:
        if cleanup_dir is None:
            return None
        # A short-lived CLI can exit between Popen and this check. Its
        # descendants may still own the original group; reap them before
        # removing the private run directory, just as for a live leader.
        if _group_empty(pid):
            shutil.rmtree(cleanup_dir, ignore_errors=True)
            return None

    from hermes_cli.kanban_db import worker_log_path

    from .config import plugin_root

    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(plugin_root()), env.get("PYTHONPATH", "")) if part
    )
    log_path = worker_log_path(task_id, board=board)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = open(log_path, "ab")
    command = [sys.executable, "-m", "workers_core.process_group", str(pid)]
    if cleanup_dir is not None:
        command.append(str(cleanup_dir))
    try:
        process = subprocess.Popen(
            command,
            cwd=str(plugin_root()),
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
    except OSError:
        # The Worker is already running; losing its watchdog must not fail the
        # dispatch that started it.
        return None
    finally:
        log_handle.close()
    return int(process.pid)


def _record(pgid: int, outcome: dict[str, Any]) -> None:
    sys.stderr.write(
        f"[workers] reaped process group {pgid} of worker {pgid}: "
        f"{len(outcome['members'])} descendant(s) "
        f"{outcome['members']}, sigkill={outcome['sigkill']}, "
        f"{outcome['seconds']}s\n"
    )
    sys.stderr.flush()


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) not in (1, 2) or not args[0].isdigit():
        sys.stderr.write("usage: python -m workers_core.process_group <worker-pid> [run-dir]\n")
        return 2
    pid = int(args[0])

    stop = False

    def _on_signal(_signum, _frame):
        nonlocal stop
        stop = True

    for name in ("SIGTERM", "SIGINT"):
        handler = getattr(signal, name, None)
        if handler is not None:
            signal.signal(handler, _on_signal)

    # The Worker was launched detached, so its own PID is its process-group id.
    while not stop:
        group = _leader_pgid(pid)
        if group is None:
            break
        if group != pid:
            # The PID was recycled; this group belongs to a stranger now.
            sys.stderr.write(
                f"[workers] worker pid {pid} was recycled by group {group}; "
                "leaving it alone\n"
            )
            sys.stderr.flush()
            return 0
        time.sleep(_POLL_SECONDS)

    _record(pid, reap_group(pid))
    if len(args) == 2 and _group_empty(pid):
        shutil.rmtree(args[1], ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
