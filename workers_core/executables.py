"""Shared executable resolution for external CLI backends."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def resolve_executable(value: str) -> str | None:
    """Resolve an explicit path or a PATH command; None when neither exists."""
    raw = str(value or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if path.is_file() and os.access(path, os.X_OK):
        return str(path.resolve())
    return shutil.which(raw)
