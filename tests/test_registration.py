from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace


def _load_plugin_root():
    path = Path(__file__).resolve().parents[1] / "__init__.py"
    name = "workers_optimized_plugin_root"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


workers = _load_plugin_root()


def native(conn, *, spawn_fn=None, board=None):
    return spawn_fn, board


def test_spawn_patch_is_reversible():
    native.__kwdefaults__ = {"spawn_fn": None, "board": None}
    fake = SimpleNamespace(dispatch_once=native)
    restore = workers._install_spawn_default(fake)
    assert native.__kwdefaults__["spawn_fn"] is workers._router_spawn
    restore()
    assert native.__kwdefaults__["spawn_fn"] is None


def test_real_plugin_discovery(tmp_path, monkeypatch):
    import yaml

    from hermes_cli import kanban_db_dispatch
    from hermes_cli import plugins as plugins_mod

    source = Path(__file__).resolve().parents[1]
    home = tmp_path / "home"
    target = home / "plugins" / "workers"
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    home.mkdir(exist_ok=True)
    (home / "config.yaml").write_text(
        yaml.safe_dump({"plugins": {"enabled": ["workers"], "disabled": []}}),
        encoding="utf-8",
    )
    empty_bundled = tmp_path / "bundled"
    empty_bundled.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(plugins_mod, "get_bundled_plugins_dir", lambda: empty_bundled)
    # Discovery tests the plugin lifecycle, not the PM interpreter bootstrap.
    # Importing the native managed worker must not install a runtime in this
    # synthetic profile or replace the pytest process.
    monkeypatch.setitem(sys.modules, "hermes_bootstrap", SimpleNamespace())

    before = dict(kanban_db_dispatch.dispatch_once.__kwdefaults__ or {})
    manager = plugins_mod.PluginManager()
    try:
        manager.discover_and_load()
        assert manager._plugins["workers"].enabled is True
        spawn = kanban_db_dispatch.dispatch_once.__kwdefaults__["spawn_fn"]
        assert spawn.__name__ == "_router_spawn"
        monkeypatch.setattr(plugins_mod, "_delivery_manager", lambda: manager)
        verdict = plugins_mod.get_plugin_error_classification(
            provider="opencode-go", model="muse-spark-1.3-contributor",
            status_code=403, error_code="RegionError",
            error_body={"error": {"name": "RegionError",
                                  "message": "This model is not available in your country"}},
        )
        assert verdict is not None
        assert verdict["reason"].value == "upstream_blocked"
        assert verdict["should_rotate_credential"] is False
        from agent.error_classifier import classify_api_error

        error = RuntimeError("This model is not available in your country")
        error.status_code = 403
        error.body = {"error": {"name": "RegionError", "message": str(error)}}
        classified = classify_api_error(error, provider="opencode-go",
                                        model="muse-spark-1.3-contributor")
        assert classified.reason.value == "upstream_blocked"
        assert classified.should_rotate_credential is False
    finally:
        manager.unload()
    assert dict(kanban_db_dispatch.dispatch_once.__kwdefaults__ or {}) == before
    assert manager.invoke_hook("transform_api_error_classification", provider="opencode-go",
                               status_code=403, error_code="RegionError") == []
