from __future__ import annotations

from types import SimpleNamespace

import pytest

from workers_core.backend_registry import EXTERNAL_BACKENDS
from workers_core.config import default_config
from workers_core import registry
from workers_core.registry import resolve_backend


def task(assignee="default"):
    return SimpleNamespace(assignee=assignee)


def test_no_enabled_external_backend_uses_native():
    document = default_config()
    document["default_worker"] = "codex"
    assert resolve_backend(task(), document) == "native"


@pytest.mark.parametrize("backend", [spec.id for spec in EXTERNAL_BACKENDS])
def test_single_enabled_external_backend_is_selected(backend):
    document = default_config()
    document["workers"][backend]["enabled"] = True
    assert resolve_backend(task(), document) == backend


def test_valid_assignee_route_resolves_legacy_multi_enabled_configuration():
    document = default_config()
    document["workers"]["opencode"]["enabled"] = True
    document["workers"]["codex"]["enabled"] = True
    document["routes_by_assignee"] = {"legacy": "opencode"}

    assert resolve_backend(task("legacy"), document) == "opencode"
    with pytest.raises(ValueError, match="enable only one external Worker"):
        resolve_backend(task("default"), document)

    document["routes_by_assignee"]["legacy"] = "claude-code"
    with pytest.raises(ValueError, match="enable only one external Worker"):
        resolve_backend(task("legacy"), document)


def test_spawn_resolves_native_settings_in_the_assignee_profile(tmp_path, monkeypatch):
    from hermes_constants import get_hermes_home, reset_hermes_home_override, set_hermes_home_override
    from workers_core import process_group
    from workers_core import config

    home = tmp_path / "hermes"
    profile = home / "profiles" / "worker-b"
    profile.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    shared = config.default_config()
    shared["workers"]["codex"].update(enabled=True, model="fixture-codex-model")
    config.save_config(shared)
    profile_config = config.default_config()
    token = set_hermes_home_override(profile)
    try:
        config.save_config(profile_config)
    finally:
        reset_hermes_home_override(token)

    observed = {}
    monkeypatch.setattr(
        registry,
        "backend_module",
        lambda backend: (observed.update(backend=backend) or SimpleNamespace(
            spawn=lambda _task, _workspace, **kwargs: observed.update(config=kwargs["config"]) or 4321
        )),
    )
    monkeypatch.setattr(process_group, "supervise", lambda *_args, **_kwargs: None)

    task_b = SimpleNamespace(id="task-b", assignee="worker-b")
    assert registry.spawn_task(task_b, str(tmp_path)) == 4321
    assert observed["backend"] == "native"
    assert "default_worker" not in observed["config"]
    assert observed["config"]["workers"]["codex"]["enabled"] is False
    assert get_hermes_home() == home
