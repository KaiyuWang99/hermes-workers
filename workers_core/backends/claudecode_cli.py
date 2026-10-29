"""Claude Code binary resolution and CLI ``--help`` metadata probing."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

_FLAG_RE = re.compile(r"(?<!\w)--[a-z][A-Za-z0-9-]*")
# Claude Code renders a flag family as one abbreviated entry (`--x[-file]`) in its help
# output, so a literal scan reports `--x` and loses the `-file` variant.
_BRACKETED_FLAG_RE = re.compile(r"(--[a-z][A-Za-z0-9-]*)\[-([A-Za-z0-9][A-Za-z0-9-]*)\]")
_OPTION_LINE = re.compile(
    r"(?m)^[ ]{0,4}(?:-[A-Za-z],\s*)?--(?P<name>[A-Za-z][A-Za-z0-9-]*)\b"
)
_TOKEN = r"[A-Za-z][A-Za-z0-9_-]*"
_CHOICE_FLAGS = (
    "effort",
    "permission-mode",
    "setting-sources",
)


def _resolve_binary(value: str) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if path.is_file() and os.access(path, os.X_OK):
        return str(path.resolve())
    return shutil.which(raw)


def _binary_stamp(binary: str) -> int:
    """Cache key component for a probed binary.

    Claude Code replaces itself in place, so the path alone would keep a long-lived
    dispatcher serving a pre-upgrade flag list.
    """
    try:
        return os.stat(binary).st_mtime_ns
    except OSError:
        return 0


@lru_cache(maxsize=8)
def _probe_help_text(binary: str, stamp: int) -> str:
    try:
        result = subprocess.run(
            [binary, "--help"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout or ""


@lru_cache(maxsize=8)
def _probe_cli_flags(binary: str, stamp: int) -> frozenset[str]:
    help_text = _probe_help_text(binary, stamp)
    flags = set(_FLAG_RE.findall(help_text))
    flags.update(
        f"{base}-{suffix}" for base, suffix in _BRACKETED_FLAG_RE.findall(help_text)
    )
    return frozenset(flags)


@lru_cache(maxsize=8)
def _probe_cli_version(binary: str, stamp: int) -> str:
    try:
        result = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    text = (result.stdout or result.stderr or "").strip()
    return text.splitlines()[0] if text else ""


def _help_sections(help_text: str) -> dict[str, str]:
    headers = list(_OPTION_LINE.finditer(help_text))
    sections: dict[str, str] = {}
    for index, header in enumerate(headers):
        end = headers[index + 1].start() if index + 1 < len(headers) else len(help_text)
        sections.setdefault(header.group("name"), help_text[header.start() : end])
    return sections


def _choices_from_section(section: str) -> tuple[str, ...]:
    flat = re.sub(r"\s+", " ", section).strip()
    explicit = re.search(
        r"(?:choices?|allowed\s+values?|one\s+of)\s*:?\s*(.{0,240})",
        flat,
        re.IGNORECASE,
    )
    if explicit:
        quoted = [
            first or second
            for first, second in re.findall(r'"([^"]+)"|\'([^\']+)\'', explicit.group(1))
        ]
        if len(quoted) >= 2:
            return tuple(dict.fromkeys(quoted))
        tokens = re.findall(_TOKEN, explicit.group(1))
        if len(tokens) >= 2:
            return tuple(dict.fromkeys(tokens))
    quoted = [
        first or second
        for first, second in re.findall(r'"([^"]+)"|\'([^\']+)\'', flat)
    ]
    if len(quoted) >= 2:
        return tuple(dict.fromkeys(quoted))
    for group in re.findall(r"\(([^()]*)\)", flat):
        parts = [part.strip() for part in re.split(r"[,|]", group)]
        if len(parts) >= 2 and all(re.fullmatch(_TOKEN, part) for part in parts):
            return tuple(dict.fromkeys(parts))
    return ()


@lru_cache(maxsize=8)
def _help_info(binary: str, stamp: int) -> dict[str, Any]:
    help_text = _probe_help_text(binary, stamp)
    sections = _help_sections(help_text)
    choices: dict[str, tuple[str, ...]] = {}
    for name in _CHOICE_FLAGS:
        if name not in sections:
            continue
        values = _choices_from_section(sections[name])
        if values:
            choices[name.replace("-", "_")] = values
    return {
        "flags": _probe_cli_flags(binary, stamp),
        "version": _probe_cli_version(binary, stamp),
        "choices": choices,
    }


def _cli_flags(binary: str) -> frozenset[str]:
    return _probe_cli_flags(binary, _binary_stamp(binary))


def capabilities(binary_value: str) -> dict[str, Any]:
    """Return the Worker-relevant CLI capabilities without importing Hermes."""
    binary = _resolve_binary(binary_value)
    if binary is None:
        return {
            "binary": "",
            "version": "",
            "features": {},
            "choices": {},
        }
    info = _help_info(binary, _binary_stamp(binary))
    flags = info["flags"]
    features = {
        "strict_mcp_config": "--strict-mcp-config" in flags,
        "settings_sources": "--setting-sources" in flags,
        "native_skills": "--disable-slash-commands" in flags,
        "permission_mode": "--permission-mode" in flags,
        "effort": "--effort" in flags,
    } if flags else {}
    return {
        "binary": binary,
        "version": info["version"],
        "features": features,
        "choices": info["choices"],
    }


def _supports(flags: frozenset[str], name: str) -> bool:
    return not flags or name in flags
