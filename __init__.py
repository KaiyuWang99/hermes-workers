"""Install one reversible worker router without changing Hermes source."""

from __future__ import annotations

from typing import Any, Callable


_PATCH_STATE = "_workers_spawn_default"


def _router_spawn(task: Any, workspace: str, *, board: str | None = None):
    from .workers_core.registry import spawn_task

    return spawn_task(task, workspace, board=board)


def _install_spawn_default(kanban_dispatch: Any) -> Callable[[], None]:
    native = kanban_dispatch.dispatch_once
    current = dict(native.__kwdefaults__ or {})
    if current.get("spawn_fn") is _router_spawn:
        return lambda: None

    previous = dict(current)
    current["spawn_fn"] = _router_spawn
    native.__kwdefaults__ = current
    setattr(
        kanban_dispatch,
        _PATCH_STATE,
        {"native": native, "previous_kwdefaults": previous, "spawn_fn": _router_spawn},
    )

    def restore() -> None:
        active = dict(native.__kwdefaults__ or {})
        if active.get("spawn_fn") is _router_spawn:
            native.__kwdefaults__ = dict(previous)

    return restore


def register(ctx) -> None:
    from .workers_core.compat import require
    from .workers_core.config import ensure_config
    from .workers_core.provider_errors import correct_api_error_classification

    require()

    from hermes_cli import kanban_db_dispatch

    ensure_config()
    ctx.register_hook(
        "transform_api_error_classification", correct_api_error_classification
    )
    ctx.on_unload(_install_spawn_default(kanban_db_dispatch))
