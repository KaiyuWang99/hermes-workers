"""Startup compatibility probe for the private Hermes APIs Workers depends on.

The plugin is not a pure public-API plugin: routing, credential rotation, model
inventory and launch commands reach into Hermes internals.  Every module is
resolved inside :func:`check`, so importing this module has no Hermes side
effects and a missing API becomes a readable startup error instead of a random
``ImportError`` during the first task.
"""

from __future__ import annotations

import importlib
import inspect
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DISPATCH = "hermes_cli.kanban_db_dispatch"

# (module, dotted attribute) pairs that routing/startup cannot work without.
REQUIRED: tuple[tuple[str, str], ...] = (
    (_DISPATCH, "dispatch_once"),
    (_DISPATCH, "_default_spawn"),
    (_DISPATCH, "_worker_profile_scope"),
    ("hermes_cli._launchers", "runtime_command"),
    ("agent.error_classifier", "classify_api_error"),
    ("agent.error_classifier", "FailoverReason"),
)

# Missing entries only disable the matching capability.
OPTIONAL: tuple[tuple[str, str], ...] = (
    ("agent.credential_pool", "load_pool"),
    ("agent.credential_pool", "CredentialPool.mark_exhausted_and_rotate"),
    ("agent.credential_pool_model_cooldowns", "pool_failure_reason"),
    ("hermes_cli.plugins_state", "save_plugin_setting"),
    ("hermes_cli.inventory", "build_models_payload"),
    ("hermes_cli.inventory", "load_picker_context"),
    ("agent.transports.codex_app_server", "CodexAppServerClient"),
    ("hermes_cli.kanban_db", "DEFAULT_CLAIM_HEARTBEAT_MAX_STALE_SECONDS"),
)

_warned_optional: set[str] = set()


def _import_module(module_name: str) -> Any:
    """Indirection so tests can simulate a Hermes layout without it."""
    return importlib.import_module(module_name)


def _hermes_version() -> str:
    try:
        import hermes_cli

        return str(hermes_cli.__version__ or "unknown")
    except Exception as exc:
        # Version is decoration; never fail the probe because of it.
        logger.debug("_hermes_version: version probe failed: %s", exc)
        return "unknown"


def _workers_version() -> str:
    try:
        text = (Path(__file__).resolve().parents[1] / "plugin.yaml").read_text(
            encoding="utf-8"
        )
        match = re.search(r"^version:\s*['\"]?([^'\"\s#]+)", text, re.MULTILINE)
    except Exception as exc:
        logger.debug("_workers_version: plugin manifest unreadable: %s", exc)
        return "unknown"
    return match.group(1) if match else "unknown"


def _resolve(module_name: str, attribute: str) -> Any:
    target: Any = _import_module(module_name)
    for part in attribute.split("."):
        target = getattr(target, part)
    return target


def _is_missing(module_name: str, attribute: str) -> bool:
    try:
        value = _resolve(module_name, attribute)
    except Exception as exc:
        logger.debug("_is_missing: %s.%s unavailable: %s", module_name, attribute, exc)
        return True
    if module_name == _DISPATCH and attribute == "dispatch_once":
        # The router replaces the native spawn default; it needs a real keyword
        # parameter (a **kwargs-only signature cannot be patched that way).
        try:
            parameter = inspect.signature(value).parameters.get("spawn_fn")
        except (TypeError, ValueError):
            return True
        return parameter is None or parameter.kind not in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        )
    return False


def _label(module_name: str, attribute: str) -> str:
    if module_name == _DISPATCH and attribute == "dispatch_once":
        return f"{module_name}.{attribute}(spawn_fn)"
    return f"{module_name}.{attribute}"


def check() -> dict[str, Any]:
    """Probe the private API surface without raising or caching results."""
    missing_required = [
        _label(module, attribute)
        for module, attribute in REQUIRED
        if _is_missing(module, attribute)
    ]
    missing_optional = [
        _label(module, attribute)
        for module, attribute in OPTIONAL
        if _is_missing(module, attribute)
    ]
    return {
        "ok": not missing_required,
        "workers_version": _workers_version(),
        "hermes_version": _hermes_version(),
        "missing_required": missing_required,
        "missing_optional": missing_optional,
        "checked_at": datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
    }


def require() -> dict[str, Any]:
    """Fail startup with an actionable message when a required API is gone."""
    result = check()
    if result["missing_required"]:
        raise RuntimeError(
            f"Workers plugin {result['workers_version']} is incompatible with "
            f"Hermes {result['hermes_version']}: missing required API(s): "
            f"{', '.join(result['missing_required'])}. "
            "Upgrade Hermes or install a compatible Workers release."
        )
    new_optional = [
        label for label in result["missing_optional"] if label not in _warned_optional
    ]
    if new_optional:
        _warned_optional.update(new_optional)
        logger.warning(
            "Workers plugin: optional Hermes API(s) unavailable; related "
            "features are disabled: %s",
            ", ".join(new_optional),
        )
    return result
