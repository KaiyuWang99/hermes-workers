from __future__ import annotations

import os
import sys
from pathlib import Path


# This fork's plugin root (workers-optimized/) holds workers_core as a
# top-level package.  Its directory name is not a valid module name, so the
# original ``workers`` package path cannot be reused here.
WORKSPACE = Path(__file__).resolve().parents[1]
HERMES_SOURCE = Path(
    os.environ.get("HERMES_SOURCE", Path.home() / ".hermes" / "hermes-agent")
).expanduser()
for path in (WORKSPACE, HERMES_SOURCE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))



import pytest

from workers_core import provider_errors


_REASONS = {
    400: "format_error",
    401: "auth",
    402: "billing",
    403: "auth",
    404: "model_not_found",
    408: "timeout",
    429: "rate_limit",
    500: "server_error",
    502: "server_error",
    503: "overloaded",
    504: "server_error",
}
# Mirrors core's verdict: deterministic client errors abort to the fallback
# chain; transient 408/5xx are retried on the same route first.
_CORE_FALLBACK = frozenset({400, 401, 402, 403, 404, 429})


@pytest.fixture(autouse=True)
def core_classifier(monkeypatch):
    """Stand-in for ``agent.error_classifier.classify_api_error``.

    The plugin tests run on the venv, which lacks the runtime-only ``utils``
    dependencies core imports on some paths. This keeps the decision contract
    testable; the real core is exercised by the runtime integration probe.
    """
    from types import SimpleNamespace

    def fake(error):
        reason = _REASONS.get(error.status_code, "unknown")
        return SimpleNamespace(
            reason=SimpleNamespace(value=reason),
            retryable=reason not in {"auth", "billing", "format_error"},
            should_fallback=error.status_code in _CORE_FALLBACK,
            error_context={},
            billing_unverified=False,
        )

    monkeypatch.setattr(provider_errors, "_core_classify", fake)


@pytest.fixture(autouse=True)
def isolated_hermes_home(tmp_path, monkeypatch):
    """Tests never read or persist the user's Hermes configuration."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
