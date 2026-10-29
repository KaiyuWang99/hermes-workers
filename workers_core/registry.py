"""Small internal backend registry and deterministic worker selection."""

from __future__ import annotations

from typing import Any

from .backend_registry import EXTERNAL_BACKENDS, get_backend
from .config import load_config, normalize_assignee


def enabled_backends(document: dict[str, Any]) -> list[str]:
    workers = document.get("workers") or {}
    return [
        spec.id
        for spec in EXTERNAL_BACKENDS
        if bool((workers.get(spec.id) or {}).get("enabled"))
    ]


def resolve_backend(task: Any, document: dict[str, Any] | None = None) -> str:
    config = document if document is not None else load_config()
    enabled = enabled_backends(config)
    assignee = normalize_assignee(getattr(task, "assignee", ""))
    routed = str((config.get("routes_by_assignee") or {}).get(assignee) or "")
    if routed in enabled:
        return routed
    if len(enabled) == 1:
        return enabled[0]
    if not enabled:
        return "native"
    raise ValueError(
        "Multiple external Workers are enabled and no valid route matches "
        f"assignee {assignee!r}; enable only one external Worker or add a "
        "valid assignee route."
    )


def backend_module(backend: str):
    try:
        return get_backend(backend).load()
    except ValueError as exc:
        raise RuntimeError(f"unknown worker backend: {backend!r}") from exc


def spawn_task(task: Any, workspace: str, *, board: str | None = None) -> int:
    # Assignee is the Hermes profile boundary. Resolve both the profile and its
    # native plugin settings before choosing a backend so profile B cannot
    # inherit the gateway profile's Workers route.
    from hermes_cli.profiles import normalize_profile_name, resolve_profile_env
    from hermes_cli.kanban_db_dispatch import _worker_profile_scope

    assignee = str(getattr(task, "assignee", "") or "default")
    profile = normalize_profile_name(assignee)
    profile_home = resolve_profile_env(profile)
    with _worker_profile_scope(profile_home):
        document = load_config()
        backend = resolve_backend(task, document)
    spec = get_backend(backend)
    module = backend_module(spec.id)
    if spec.id == "native":
        pid = int(module.spawn(task, workspace, board=board, config=document))
    else:
        from agent import runtime_cwd

        from .common import build_launch_context, build_worker_env

        # Resolve board paths before binding the assignee's home: the task
        # belongs to the dispatching board, which can live in another profile.
        env = build_worker_env(
            task, workspace, board, inherit_credentials=spec.inherit_credentials
        )
        with _worker_profile_scope(env["HERMES_HOME"]):
            cwd_token = runtime_cwd.set_session_cwd(workspace)
            try:
                launch = build_launch_context(
                    task, workspace, board, document, env=env
                )
                pid = int(module.spawn(launch, config=document))
            finally:
                runtime_cwd.reset_session_cwd(cwd_token)
    # The detached worker's CLI and MCP servers share one supervised group.
    from .process_group import supervise

    if not spec.supervises_children:
        supervise(pid, task_id=str(task.id), board=board)
    return pid


def doctor_backend(backend: str, document: dict[str, Any] | None = None) -> dict[str, Any]:
    config = document or load_config()
    return backend_module(backend).doctor(config)


def list_backend_models(
    backend: str,
    document: dict[str, Any] | None = None,
    *,
    refresh: bool = False,
) -> list[dict[str, Any]]:
    config = document or load_config()
    return backend_module(backend).list_models(config, refresh=refresh)
