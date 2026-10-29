# Legacy migration; remove after 1.8.
"""Pre-1.0 compatibility for the Workers configuration.

Only two things still need this module: importing the former shared
``configs/workers.json`` once, and reading the 1.5 inheritance group switches.
``config.py`` calls into it at runtime instead of owning the code, so deleting
it after 1.8 means removing those calls plus this file.

Imports of ``config`` stay inside functions: ``config`` calls this module and a
module-level import either way would form a cycle.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Mapping

logger = logging.getLogger(__name__)
_MIGRATION_LOCK = threading.RLock()

# 1.5 group switches: each group is shorthand for its individual descendants.
LEGACY_INHERITANCE_GROUPS = {
    "profile_context": ("soul", "personality", "user_profile", "memory", "external_memory", "plugin_context"),
    "skills": ("skill_index", "auto_skills", "card_skills"),
}


def inheritance_group_keys() -> frozenset[str]:
    """Names reserved for group switches; they never appear in a resolved policy."""
    return frozenset(LEGACY_INHERITANCE_GROUPS)


def apply_inheritance_groups(raw: Mapping[str, Any], policy: dict[str, bool]) -> None:
    """Overlay 1.5 group switches onto ``policy``.

    Groups only supply defaults: an explicit individual choice applied later
    wins over its legacy group.
    """
    for group, children in LEGACY_INHERITANCE_GROUPS.items():
        if group in raw:
            if not isinstance(raw[group], bool):
                raise ValueError(f"inheritance.{group} must be a boolean")
            policy.update({key: raw[group] for key in children})


def _legacy_config_path() -> Path:
    from .config import hermes_root

    return hermes_root() / "configs" / "workers.json"


def _archive_legacy(path: Path) -> None:
    archive = path.with_name(f"{path.name}.migrated")
    try:
        # A hard link is an atomic, no-overwrite archive on the same filesystem.
        # If an archive already exists, leave the source untouched.
        os.link(path, archive)
    except FileExistsError:
        logger.warning("Workers migration archive already exists; retaining %s", path)
        return
    except OSError as exc:
        logger.warning("Workers migrated but could not archive %s: %s", path, exc)
        return
    try:
        path.unlink()
    except OSError as exc:
        logger.warning("Workers migrated but could not remove legacy file %s: %s", path, exc)


def migrate_legacy_config() -> bool:
    """Import the former shared JSON once into default-root native plugin settings."""
    from .config import (
        _home_scope,
        _nonempty_native_configuration,
        hermes_root,
        save_config,
        validate_config,
    )

    legacy_path = _legacy_config_path()
    if not legacy_path.exists():
        return False
    with _MIGRATION_LOCK:
        if not legacy_path.exists():
            return False
        root = hermes_root()
        # Native settings always win. Keep the old file as a recoverable copy when
        # there is already an explicit native document; migration did not write it.
        if _nonempty_native_configuration(root) is not None:
            return False
        try:
            value = json.loads(legacy_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Cannot read legacy Workers configuration {legacy_path}") from exc
        if not isinstance(value, Mapping):
            raise RuntimeError(f"Legacy Workers configuration {legacy_path} must be an object")
        document = validate_config(value)
        with _home_scope(root):
            save_config(document)
        migrated = _nonempty_native_configuration(root)
        if migrated != document:
            raise RuntimeError(
                f"Native Workers configuration did not match the legacy file; retained {legacy_path}"
            )
        _archive_legacy(legacy_path)
        return True
