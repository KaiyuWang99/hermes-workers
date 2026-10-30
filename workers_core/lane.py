"""Adapter for Hermes' public Kanban worker-lane API.

Hermes 0.22.x has no public spawn seam yet; when the lane API lands, this
module registers Workers as one lane instead of replacing ``dispatch_once``'s
``spawn_fn`` default. The adapter is import-safe on older Hermes versions.
"""

from __future__ import annotations

from typing import Any

try:  # pragma: no cover - exercised by the lane API's own release
    from hermes_cli.kanban_worker_lanes import KanbanWorkerLane
except ImportError:  # Hermes without the public worker-lane API
    KanbanWorkerLane = None  # type: ignore[assignment, misc]


if KanbanWorkerLane is not None:

    class _WorkersLane(KanbanWorkerLane):  # type: ignore[misc]
        """Run every externally routed Kanban task through the existing router."""

        name = "workers"
        # A lane must name at least one assignee or board; empty matches any value.
        assignees = ("*",)
        boards = ()

        def accepts(self, task: Any, *, board: str | None, lane: str) -> bool:
            del task, board, lane
            from .config import load_config
            from .registry import enabled_backends

            return bool(enabled_backends(load_config()))

        def spawn(self, claim: Any) -> int:
            from .registry import spawn_task

            return int(spawn_task(claim.task, claim.workspace, board=claim.board))

else:
    _WorkersLane = None


def register_lane(ctx: Any) -> bool:
    """Register the lane when Hermes exposes the API; return whether it did."""
    if _WorkersLane is None or not hasattr(ctx, "register_kanban_worker_lane"):
        return False
    ctx.register_kanban_worker_lane(_WorkersLane())
    return True
