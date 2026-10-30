from __future__ import annotations

import importlib
import sys
from types import ModuleType, SimpleNamespace


def _reload_lane():
    from workers_core import lane

    return importlib.reload(lane)


def _fake_lane_api(monkeypatch):
    module = ModuleType("hermes_cli.kanban_worker_lanes")

    class KanbanWorkerLane:
        name = ""
        assignees = ()
        boards = ()

        def accepts(self, task, *, board, lane):
            return True

        def spawn(self, claim):
            raise NotImplementedError

    module.KanbanWorkerLane = KanbanWorkerLane
    monkeypatch.setitem(sys.modules, "hermes_cli.kanban_worker_lanes", module)
    return module


def test_lane_adapter_is_inert_without_the_hermes_api():
    lane = _reload_lane()
    try:
        assert lane._WorkersLane is None
        assert lane.register_lane(SimpleNamespace(register_kanban_worker_lane=lambda _lane: None)) is False
    finally:
        sys.modules.pop("hermes_cli.kanban_worker_lanes", None)
        _reload_lane()


def test_lane_adapter_registers_and_routes_to_the_existing_router(monkeypatch):
    _fake_lane_api(monkeypatch)
    lane = _reload_lane()
    try:
        registered = []
        ctx = SimpleNamespace(register_kanban_worker_lane=registered.append)
        assert lane.register_lane(ctx) is True
        assert len(registered) == 1
        worker_lane = registered[0]
        assert worker_lane.name == "workers"
        assert worker_lane.assignees == ("*",)
        assert worker_lane.boards == ()

        from workers_core import config, registry

        monkeypatch.setattr(config, "load_config", lambda: {"workers": {}})
        monkeypatch.setattr(registry, "enabled_backends", lambda _document: [])
        assert worker_lane.accepts(SimpleNamespace(), board=None, lane="ready") is False

        monkeypatch.setattr(registry, "enabled_backends", lambda _document: ["codex"])
        assert worker_lane.accepts(SimpleNamespace(), board=None, lane="ready") is True

        calls = []
        monkeypatch.setattr(
            registry,
            "spawn_task",
            lambda task, workspace, board=None: calls.append((task, workspace, board)) or 4321,
        )
        task = SimpleNamespace(id="t_lane")
        claim = SimpleNamespace(task=task, workspace="/tmp/work", board="default")
        assert worker_lane.spawn(claim) == 4321
        assert calls == [(task, "/tmp/work", "default")]
    finally:
        sys.modules.pop("hermes_cli.kanban_worker_lanes", None)
        _reload_lane()
