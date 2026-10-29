"""Read-only reasoning choices supported by installed worker CLIs."""

from __future__ import annotations

import re
import subprocess
from functools import lru_cache

from workers_core.backends import claudecode


class EffortOptionsError(RuntimeError):
    """The configured Claude CLI could not provide a trustworthy effort list."""


_OPTION_LINE = re.compile(r"(?m)^[ \t]*(?:-[A-Za-z],\s*)?--(?P<name>[A-Za-z][A-Za-z0-9-]*)\b")
_TOKEN = r"[A-Za-z][A-Za-z0-9_-]*"
_TOKEN_RE = re.compile(rf"{_TOKEN}\Z")
_ENUM = rf"{_TOKEN}(?:\s*(?:,|\|)\s*{_TOKEN})+"


def parse_claude_effort_options(help_text: str) -> tuple[str, ...]:
    """Extract the choices described by Claude Code's ``--effort`` help entry.

    Returns an empty tuple when the CLI has no ``--effort`` option. If the flag
    exists but its accepted values are not listed, raise instead of guessing.
    """
    headers = list(_OPTION_LINE.finditer(help_text))
    effort_header = next((item for item in headers if item.group("name") == "effort"), None)

    if effort_header is None:
        return ()

    next_header = next((item for item in headers if item.start() > effort_header.start()), None)
    section = help_text[effort_header.start() : next_header.start() if next_header else len(help_text)]
    flattened = re.sub(r"\s+", " ", section).strip()

    explicit = re.search(
        rf"(?:choices?|allowed\s+values?|one\s+of)\s*:?\s*({_ENUM})",
        flattened,
        re.IGNORECASE,
    )
    if explicit:
        values = tuple(re.split(r"\s*(?:,|\|)\s*", explicit.group(1)))
        return tuple(dict.fromkeys(values))

    for candidate in re.findall(r"\(([^()]*)\)", flattened):
        candidate = re.sub(r"^(?:choices?|values?)\s*:\s*", "", candidate.strip(), flags=re.IGNORECASE)
        parts = tuple(re.split(r"\s*(?:,|\|)\s*", candidate))
        if len(parts) > 1 and all(_TOKEN_RE.fullmatch(value) for value in parts):
            return tuple(dict.fromkeys(parts))

    raise EffortOptionsError("Claude Code lists --effort but not its accepted values.")


@lru_cache(maxsize=8)
def _probe_claude_effort_options(binary: str, stamp: int) -> tuple[str, ...]:
    """Probe once per resolved binary version; a binary replacement changes the stamp."""
    try:
        result = subprocess.run(
            [binary, "--help"], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EffortOptionsError("Claude Code help probe failed.") from exc

    if result.returncode != 0:
        raise EffortOptionsError("Claude Code help probe failed.")

    help_text = "\n".join(part for part in (result.stdout, result.stderr) if part)
    if not help_text.strip():
        raise EffortOptionsError("Claude Code help probe returned no output.")

    return parse_claude_effort_options(help_text)


def claude_effort_options(binary_value: str, *, refresh: bool = False) -> tuple[str, ...]:
    """Resolve the configured executable and return its ``--effort`` choices.

    The resolved path and binary stamp bind cached results to the installed CLI.
    ``refresh`` bypasses the in-process cache for an explicit UI refresh.
    """
    binary = claudecode._resolve_binary(binary_value)
    if binary is None:
        raise EffortOptionsError("Claude Code executable is unavailable.")
    stamp = claudecode._binary_stamp(binary)
    if refresh:
        return _probe_claude_effort_options.__wrapped__(binary, stamp)
    return _probe_claude_effort_options(binary, stamp)
