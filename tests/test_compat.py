from __future__ import annotations

import importlib.util
import json
import logging
import sys
from pathlib import Path

import pytest

from workers_core import compat


def _load_dashboard():
    path = Path(__file__).resolve().parents[1] / "dashboard" / "plugin_api.py"
    name = "workers_dashboard_compat_test"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_check_reports_current_environment():
    result = compat.check()

    assert result["ok"] is True
    assert result["missing_required"] == []
    assert isinstance(result["missing_optional"], list)
    assert isinstance(result["hermes_version"], str) and result["hermes_version"]
    assert isinstance(result["workers_version"], str) and result["workers_version"]
    assert result["checked_at"].endswith("Z")
    assert compat.require()["ok"] is True


def test_check_is_json_serializable():
    result = compat.check()

    assert json.loads(json.dumps(result)) == result


def test_require_raises_with_missing_symbol_and_versions(monkeypatch):
    real_import = compat._import_module

    def fake_import(module_name):
        if module_name == "agent.error_classifier":
            raise ImportError("simulated missing module")
        return real_import(module_name)

    monkeypatch.setattr(compat, "_import_module", fake_import)

    result = compat.check()
    assert result["ok"] is False
    assert "agent.error_classifier.classify_api_error" in result["missing_required"]
    assert "agent.error_classifier.FailoverReason" in result["missing_required"]

    with pytest.raises(RuntimeError) as excinfo:
        compat.require()

    message = str(excinfo.value)
    assert "agent.error_classifier.classify_api_error" in message
    assert "agent.error_classifier.FailoverReason" in message
    assert result["hermes_version"] in message
    assert result["workers_version"] in message
    assert "Upgrade Hermes" in message


def test_dispatch_once_must_accept_spawn_fn_keyword(monkeypatch):
    real_resolve = compat._resolve

    def fake_resolve(module_name, attribute):
        if module_name == compat._DISPATCH and attribute == "dispatch_once":
            return lambda conn, **kwargs: None
        return real_resolve(module_name, attribute)

    monkeypatch.setattr(compat, "_resolve", fake_resolve)

    result = compat.check()

    assert (
        "hermes_cli.kanban_db_dispatch.dispatch_once(spawn_fn)"
        in result["missing_required"]
    )


def test_require_warns_once_for_missing_optional(monkeypatch, caplog):
    real_import = compat._import_module

    def fake_import(module_name):
        if module_name == "hermes_cli.inventory":
            raise ImportError("simulated missing module")
        return real_import(module_name)

    monkeypatch.setattr(compat, "_import_module", fake_import)
    monkeypatch.setattr(compat, "_warned_optional", set())

    with caplog.at_level(logging.WARNING, logger="workers_core.compat"):
        first = compat.require()
        second = compat.require()

    assert first["ok"] is True
    assert second["ok"] is True
    assert "hermes_cli.inventory.build_models_payload" in first["missing_optional"]
    assert "hermes_cli.inventory.load_picker_context" in first["missing_optional"]
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "hermes_cli.inventory.build_models_payload" in warnings[0].getMessage()


def test_doctor_exposes_compatibility_on_native_and_external(monkeypatch):
    api = _load_dashboard()
    compatibility = compat.check()
    monkeypatch.setattr(api, "check_compatibility", lambda: compatibility)
    monkeypatch.setattr(
        api,
        "doctor_backend",
        lambda *_: {
            "binary_ok": True,
            "version_ok": True,
            "credential": {"configured": True, "available": True},
        },
    )
    from workers_core.config import default_config

    document = default_config()
    external = api._doctor_response("claude-code", document, scope="draft")
    native = api._doctor_response("native", document, scope="saved")

    assert external["compatibility"] is compatibility
    assert native["compatibility"] is compatibility
    assert external["diagnostics"]["backend"] == "claude-code"
    assert native["diagnostics"]["backend"] == "native"
