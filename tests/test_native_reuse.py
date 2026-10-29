"""Contract checks for native context, inventory, and owned run cleanup."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from workers_core import common, process_group, provider_catalog


def test_capabilities_do_not_borrow_another_provider(monkeypatch):
    import agent.models_dev as models

    calls = []
    monkeypatch.setattr(models, "get_model_capabilities", lambda provider, model, **kw: (
        calls.append((provider, model, kw)) or None
    ))
    monkeypatch.setattr(models, "fetch_models_dev", lambda **kw: {
        "another-provider": {"models": {"shared-name": {"tool_call": True, "reasoning": True}}}
    })
    row = provider_catalog.model_capabilities("selected-provider", "shared-name")
    assert row["known"] is False
    assert row["supportsTools"] is None
    assert row["supportsReasoning"] is None
    assert row["supportsInterleavedThinking"] is None
    assert calls == [("selected-provider", "shared-name", {"allow_network": False})]


def test_partial_native_capabilities_remain_unknown(monkeypatch):
    import agent.models_dev as models

    monkeypatch.setattr(models, "get_model_capabilities", lambda *a, **kw: SimpleNamespace(
        supports_tools=True, supports_vision=None, supports_reasoning=None
    ))
    row = provider_catalog.model_capabilities("custom", "local")
    assert row["known"] is True
    assert row["supportsTools"] is True
    assert row["supportsVision"] is None
    assert row["supportsReasoning"] is None


def test_inventory_delegates_provider_collection_and_refresh(monkeypatch):
    import hermes_cli.inventory as inventory

    context = object()
    calls = []
    monkeypatch.setattr(inventory, "load_picker_context", lambda: context)
    monkeypatch.setattr(inventory, "build_models_payload", lambda ctx, **kw: (
        calls.append((ctx, kw)) or {"providers": [{"slug": "custom-a", "name": "Custom A", "models": ["model-a"]}]}
    ))
    monkeypatch.setattr(provider_catalog, "resolve_model_runtime", lambda *a: {"api_mode": "anthropic_messages"})
    monkeypatch.setattr(provider_catalog, "model_capabilities", lambda *a: {"known": False})
    rows = provider_catalog.configured_model_rows(force_refresh=True)
    assert rows[0]["id"] == "custom-a/model-a"
    assert rows[0]["providerDisplayName"] == "Custom A"
    assert calls == [(context, {
        "explicit_only": True, "include_unconfigured": True,
        "probe_custom_providers": False, "non_blocking_catalogs": False, "refresh": True,
    })]


def test_watchdog_cleans_only_its_run_after_descendants_exit(tmp_path, monkeypatch):
    first = tmp_path / "first-run"
    second = tmp_path / "second-run"
    first.mkdir()
    second.mkdir()
    (first / "settings.json").write_text("test")
    (second / "system.md").write_text("other worker")
    monkeypatch.setattr(process_group, "_leader_pgid", lambda pid: None)
    monkeypatch.setattr(process_group, "_group_empty", lambda pid: True)
    monkeypatch.setattr(process_group.signal, "signal", lambda *a: None)
    assert process_group.main(["1234", str(first)]) == 0
    assert not first.exists()
    assert (second / "system.md").read_text() == "other worker"


def test_watchdog_keeps_files_when_descendants_survive(tmp_path, monkeypatch):
    run = tmp_path / "run"
    run.mkdir()
    monkeypatch.setattr(process_group, "_leader_pgid", lambda pid: None)
    monkeypatch.setattr(process_group, "_group_empty", lambda pid: False)
    monkeypatch.setattr(process_group, "reap_group", lambda pid: {
        "members": [12], "sigkill": True, "seconds": 0, "survivors": [12],
    })
    monkeypatch.setattr(process_group.signal, "signal", lambda *a: None)
    assert process_group.main(["1234", str(run)]) == 0
    assert run.exists()
