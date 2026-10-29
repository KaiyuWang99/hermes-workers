"""Single authoritative configuration for all Workers backends."""

from __future__ import annotations

import logging
import shutil
from copy import deepcopy
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping

from .backend_registry import EXTERNAL_BACKENDS

SCHEMA = "workers-config-v1"
INHERITANCE_DEFAULTS = {
    "soul": True,
    "personality": True,
    "user_profile": True,
    "memory": True,
    "external_memory": True,
    "plugin_context": True,
    "project_rules": True,
    "skill_index": True,
    "auto_skills": True,
    "card_skills": True,
    "mcp": True,
    "hermes_tools": True,
    "fallback_models": True,
}
logger = logging.getLogger(__name__)


def hermes_root() -> Path:
    from hermes_constants import get_default_hermes_root

    return get_default_hermes_root().expanduser().resolve()


def config_path() -> Path:
    from hermes_constants import get_hermes_home

    return get_hermes_home().expanduser().resolve() / "config.yaml"


@contextmanager
def _home_scope(home: str | Path):
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    token = set_hermes_home_override(home)
    try:
        yield
    finally:
        reset_hermes_home_override(token)


def _native_settings(home: str | Path) -> tuple[bool, Any]:
    """Return whether native settings explicitly contain a value and the value itself."""
    from hermes_cli.config import load_config_readonly

    with _home_scope(home):
        native = load_config_readonly() or {}
    plugins = native.get("plugins") if isinstance(native, Mapping) else None
    entries = plugins.get("entries") if isinstance(plugins, Mapping) else None
    entry = entries.get("workers") if isinstance(entries, Mapping) else None
    settings = entry.get("settings") if isinstance(entry, Mapping) else None
    if not isinstance(settings, Mapping) or "configuration" not in settings:
        return False, None
    return True, settings.get("configuration")


def _nonempty_native_configuration(home: str | Path) -> dict[str, Any] | None:
    present, value = _native_settings(home)
    if not present or value is None or value == {}:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("native Workers configuration must be an object")
    return validate_config(value)


def plugin_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _which(name: str, fallback: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    path = Path(fallback).expanduser()
    return str(path.resolve()) if path.is_file() else name


def default_config() -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "routes_by_assignee": {},
        "inheritance": dict(INHERITANCE_DEFAULTS),
        "workers": {spec.id: spec.load().defaults() for spec in EXTERNAL_BACKENDS},
    }


def normalize_assignee(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        from hermes_cli.profiles import normalize_profile_name

        return normalize_profile_name(raw)
    except Exception as exc:
        logger.debug("config.normalize_assignee: profile normalization failed: %s", exc)
        return raw.lower()


def inheritance_policy(document: Mapping[str, Any] | None = None) -> dict[str, bool]:
    """Resolve external-Worker inheritance without requiring a complete config.

    Version 1.5 group switches supply defaults for their individual descendants.
    An explicit individual choice wins over its legacy group.
    """
    from . import legacy

    raw = document.get("inheritance", {}) if document is not None else {}
    if not isinstance(raw, Mapping):
        raise ValueError("inheritance must be an object")
    policy = dict(INHERITANCE_DEFAULTS)
    legacy.apply_inheritance_groups(raw, policy)
    for key in policy:
        if key in raw:
            if not isinstance(raw[key], bool):
                raise ValueError(f"inheritance.{key} must be a boolean")
            policy[key] = raw[key]
    return policy


def validate_config(document: Mapping[str, Any]) -> dict[str, Any]:
    from . import legacy

    if not isinstance(document, Mapping):
        raise ValueError("workers configuration must be an object")
    if document.get("schema") != SCHEMA:
        raise ValueError(f"workers schema must be {SCHEMA}")
    workers = document.get("workers")
    if not isinstance(workers, Mapping):
        raise ValueError("workers must be an object")
    workers = dict(workers)
    for spec in EXTERNAL_BACKENDS:
        if spec.id not in workers:
            entry = spec.load().defaults()
        else:
            entry = workers[spec.id]
            if not isinstance(entry, Mapping):
                raise ValueError(f"workers.{spec.id} must be an object")
            entry = dict(entry)
        workers[spec.id] = spec.load().validate_settings(entry)
    normalized_routes: dict[str, str] = {}
    routes = document.get("routes_by_assignee") or {}
    if not isinstance(routes, Mapping):
        raise ValueError("routes_by_assignee must be an object")
    for raw_assignee, raw_backend in routes.items():
        assignee = normalize_assignee(raw_assignee)
        backend = str(raw_backend or "").strip().lower()
        if not assignee or backend not in {spec.id for spec in EXTERNAL_BACKENDS}:
            raise ValueError(f"invalid worker route: {raw_assignee!r} -> {raw_backend!r}")
        normalized_routes[assignee] = backend


    policy = inheritance_policy(document)
    group_keys = legacy.inheritance_group_keys()
    result = deepcopy(dict(document))
    result["inheritance"] = {
        **{key: deepcopy(value) for key, value in document.get("inheritance", {}).items()
           if key not in group_keys},
        **policy,
    }
    result["workers"] = workers
    result["routes_by_assignee"] = normalized_routes
    # Retired fields: routing is now enabled backends + routes_by_assignee.
    result.pop("default_worker", None)
    result.pop("state_retention_days", None)
    result.pop("mcp_policy", None)
    # Display-only / retired top-level keys that older documents still carry.
    result.pop("config_path", None)
    result.pop("skill_roots", None)
    return result


def save_config(document: Mapping[str, Any]) -> Path:
    """Save the complete Workers document through Hermes' native plugin-settings writer."""
    validated = validate_config(document)
    from hermes_cli.plugins_state import save_plugin_setting

    save_plugin_setting("workers", ("configuration",), validated)
    return config_path()


def ensure_config() -> Path:
    """Run the one-time migration and return the active native Hermes config path."""
    from . import legacy

    legacy.migrate_legacy_config()
    return config_path()


def load_config() -> dict[str, Any]:
    ensure_config()
    from hermes_constants import get_hermes_home

    active_home = get_hermes_home().expanduser().resolve()
    active = _nonempty_native_configuration(active_home)
    if active is not None:
        return active
    shared = _nonempty_native_configuration(hermes_root())
    return shared if shared is not None else default_config()


def public_config(document: Mapping[str, Any] | None = None) -> dict[str, Any]:
    value = deepcopy(dict(document or load_config()))
    value["config_path"] = str(config_path())
    return value
