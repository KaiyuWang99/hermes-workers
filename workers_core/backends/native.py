"""Hermes native profile worker backend."""

from __future__ import annotations

from typing import Any, Mapping


def defaults() -> dict[str, Any]:
    return {}


def validate_settings(entry: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(entry, Mapping):
        raise ValueError("workers.native must be an object")
    return dict(entry)


def validate_selection(
    entry: dict[str, Any], *, models: list[dict[str, Any]] | None = None
) -> None:
    del entry, models


def spawn(task: Any, workspace: str, *, board: str | None, config: dict[str, Any]) -> int:
    from hermes_cli.kanban_db_dispatch import _default_spawn

    pid = _default_spawn(task, workspace, board=board)
    if not pid:
        raise RuntimeError("Hermes native worker did not return a PID")
    return int(pid)


def doctor(config: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "backend": "native"}


def list_models(
    config: dict[str, Any], *, refresh: bool = False
) -> list[dict[str, Any]]:
    del refresh
    return []
