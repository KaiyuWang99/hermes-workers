"""Static registry for the Hermes Native and external CLI worker backends."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from types import ModuleType
from typing import Literal


ModelSource = Literal["hermes", "cli", "none"]
OptionKind = Literal["effort", "variant", "reasoning_effort"]


@dataclass(frozen=True)
class BackendSpec:
    """Stable metadata shared by routing, configuration and model settings."""

    id: str
    label: str
    module: str
    model_source: ModelSource
    option_kind: OptionKind | None
    inherit_credentials: bool = False
    supervises_children: bool = False
    # Settings whose change requires re-validating an enabled backend's selection.
    selection_fields: tuple[str, ...] = ()

    def load(self) -> ModuleType:
        """Import a backend only when a caller needs its implementation."""
        return import_module(f"{__package__}.backends.{self.module}")

    def public(self) -> dict[str, str | None]:
        """Return the backend metadata safe for API/UI serialization."""
        return {
            "id": self.id,
            "label": self.label,
            "model_source": self.model_source,
            "option_kind": self.option_kind,
        }


BACKENDS: dict[str, BackendSpec] = {
    "native": BackendSpec(
        id="native",
        label="Hermes Native",
        module="native",
        model_source="none",
        option_kind=None,
        supervises_children=True,
        selection_fields=(),
    ),
    "opencode": BackendSpec(
        id="opencode",
        label="OpenCode Worker",
        module="opencode",
        model_source="hermes",
        option_kind="variant",
        supervises_children=True,
        selection_fields=("model", "reasoning_effort", "binary"),
    ),
    "codex": BackendSpec(
        id="codex",
        label="Codex",
        module="codex",
        model_source="cli",
        option_kind="reasoning_effort",
        selection_fields=("model", "reasoning_effort", "binary", "codex_home", "service_tier"),
    ),
    "claude-code": BackendSpec(
        id="claude-code",
        label="Claude Code",
        module="claudecode",
        model_source="hermes",
        option_kind="effort",
        inherit_credentials=True,
        supervises_children=True,
        selection_fields=(
            "model",
            "reasoning_effort",
            "binary",
            "strict_mcp_config",
            "settings_sources",
            "native_skills",
            "permission_mode",
        ),
    ),
}

EXTERNAL_BACKENDS: tuple[BackendSpec, ...] = tuple(
    spec for key, spec in BACKENDS.items() if key != "native"
)


def get_backend(backend_id: str) -> BackendSpec:
    """Return the registered backend descriptor or raise a clear error."""
    try:
        return BACKENDS[str(backend_id)]
    except KeyError as exc:
        raise ValueError(f"unknown worker backend: {backend_id!r}") from exc
