"""Backend-specific model option sources for the Workers API."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from json import JSONDecodeError, JSONDecoder
from pathlib import Path
from typing import Any, Callable, Mapping

from .backend_registry import get_backend
from .reasoning import EffortOptionsError, claude_effort_options


logger = logging.getLogger(__name__)


class BackendOptionsError(RuntimeError):
    """A backend's local options source could not be read reliably."""


_OPENCODE_OPTIONS_TTL = 60.0
_OPENCODE_OPTIONS_TIMEOUT = 20.0
_OPENCODE_OUTPUT_LIMIT = 10 * 1024 * 1024
_OPENCODE_CACHE: dict[
    tuple[str, int, int, str, str],
    tuple[float, dict[str, tuple[str, ...]]],
] = {}
_OPENCODE_CACHE_LOCK = threading.Lock()
_OPENCODE_ROW_FIELDS = frozenset({"providerID", "id"})


def _model_id(provider: str, model: str) -> str:
    if model.startswith(f"{provider}/"):
        return model
    return f"{provider}/{model}"


def _split_model(value: str) -> tuple[str, str] | None:
    provider, separator, model = str(value or "").strip().partition("/")
    if not separator or not provider or not model:
        return None
    return provider, model


def parse_opencode_models_verbose(output: str) -> dict[str, tuple[str, ...]]:
    """Read model-scoped variants from OpenCode's verbose model records.

    The command emits human-readable headings around JSON model objects. Parse
    only records with ``providerID``, ``id`` and ``variants``; never retain or
    expose the rest of a model/provider record.
    """
    decoder = JSONDecoder()
    variants_by_model: dict[str, tuple[str, ...]] = {}

    for match in re.finditer(r"\{", output):
        try:
            record, _end = decoder.raw_decode(output, match.start())
        except JSONDecodeError:
            continue
        if not isinstance(record, dict) or not _OPENCODE_ROW_FIELDS.issubset(record):
            continue

        provider = record.get("providerID")
        model = record.get("id")
        if not isinstance(provider, str) or not provider or not isinstance(model, str) or not model:
            continue

        raw_variants = record.get("variants")
        names = (
            tuple(name for name in raw_variants if isinstance(name, str) and name.strip())
            if isinstance(raw_variants, dict)
            else ()
        )
        variants_by_model[_model_id(provider, model)] = names

    return variants_by_model


def _opencode_binary(entry: Mapping[str, Any], module: Any) -> Path:
    configured = str(entry.get("binary") or module.defaults().get("binary") or "").strip()
    if not configured:
        raise BackendOptionsError("OpenCode executable is unavailable.")
    binary = Path(configured).expanduser()
    # Match subprocess launch semantics for explicit paths and PATH commands.
    has_explicit_path = binary.is_absolute() or os.sep in configured or bool(
        os.altsep and os.altsep in configured
    )
    if not has_explicit_path or not binary.is_file() or not os.access(binary, os.X_OK):
        resolved = shutil.which(configured)
        if not resolved:
            raise BackendOptionsError("OpenCode executable is unavailable.")
        binary = Path(resolved)
    try:
        return binary.resolve(strict=True)
    except OSError as exc:
        raise BackendOptionsError("OpenCode executable is unavailable.") from exc


def _binary_identity(binary: Path) -> tuple[str, int, int]:
    try:
        stat = binary.stat()
    except OSError as exc:
        raise BackendOptionsError("OpenCode executable metadata is unavailable.") from exc
    return str(binary), stat.st_mtime_ns, stat.st_size


def _opencode_config_signature(module: Any, entry: Mapping[str, Any]) -> str:
    """Fingerprint only OpenCode variant metadata through its existing JSONC reader."""
    try:
        effective = module._user_config(dict(os.environ))
    except Exception as exc:
        # The CLI remains the authority. A malformed/unreadable local config is
        # reflected by its own probe; do not include its raw contents here.
        logger.debug("_opencode_config_signature: user config unreadable: %s", exc)
        effective = {}

    providers = effective.get("provider") if isinstance(effective, dict) else None
    definitions: list[tuple[str, str, tuple[str, ...]]] = []
    if isinstance(providers, dict):
        for provider_id, provider in providers.items():
            models = provider.get("models") if isinstance(provider, dict) else None
            if not isinstance(models, dict):
                continue
            for model_id, model in models.items():
                variants = model.get("variants") if isinstance(model, dict) else None
                if isinstance(variants, dict):
                    names = tuple(name for name in variants if isinstance(name, str))
                    definitions.append((str(provider_id), str(model_id), names))

    fingerprint = {
        "inherit_user_config": entry.get("inherit_user_config", True),
        "inherit_project_config": entry.get("inherit_project_config", True),
        "opencode_config": os.environ.get("OPENCODE_CONFIG", ""),
        "home": os.environ.get("HOME", ""),
        "xdg_config_home": os.environ.get("XDG_CONFIG_HOME", ""),
        "configured_variants": sorted(definitions),
    }
    raw = json.dumps(fingerprint, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _probe_opencode_variants(binary: Path, *, refresh: bool) -> dict[str, tuple[str, ...]]:
    command = [str(binary), "models", "--verbose"]
    if refresh:
        command.append("--refresh")
    try:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=_OPENCODE_OPTIONS_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BackendOptionsError("OpenCode model metadata probe failed.") from exc

    output = result.stdout or ""
    if result.returncode != 0:
        raise BackendOptionsError("OpenCode model metadata probe failed.")
    if len(output.encode("utf-8", errors="replace")) > _OPENCODE_OUTPUT_LIMIT:
        raise BackendOptionsError("OpenCode model metadata output exceeded the local limit.")
    if not output.strip():
        raise BackendOptionsError("OpenCode model metadata probe returned no output.")
    records = parse_opencode_models_verbose(output)
    if not records:
        raise BackendOptionsError("OpenCode model metadata format was not recognized.")
    return records


def _opencode_variants(
    module: Any,
    entry: Mapping[str, Any],
    model: str,
    *,
    refresh: bool,
    scope: str,
) -> tuple[str, ...]:
    binary = _opencode_binary(entry, module)
    binary_path, binary_stamp, binary_size = _binary_identity(binary)
    config_signature = _opencode_config_signature(module, entry)
    cache_key = (binary_path, binary_stamp, binary_size, config_signature, scope)

    with _OPENCODE_CACHE_LOCK:
        cached = _OPENCODE_CACHE.get(cache_key)
        if not refresh and cached and time.monotonic() - cached[0] < _OPENCODE_OPTIONS_TTL:
            records = cached[1]
        else:
            records = None

    if records is None:
        fetched = _probe_opencode_variants(binary, refresh=refresh)
        with _OPENCODE_CACHE_LOCK:
            _OPENCODE_CACHE[cache_key] = (time.monotonic(), fetched)
            if len(_OPENCODE_CACHE) > 16:
                oldest = min(_OPENCODE_CACHE, key=lambda key: _OPENCODE_CACHE[key][0])
                _OPENCODE_CACHE.pop(oldest, None)
        records = fetched

    selected = _split_model(model)
    if selected is None:
        return ()
    return records.get(_model_id(*selected), ())


def _codex_effort_options(
    model: str,
    models: list[dict[str, Any]] | None,
) -> tuple[list[str], str, str | None]:
    row = next(
        (item for item in (models or []) if isinstance(item, dict) and item.get("id") == model),
        None,
    )
    if row is None:
        return [], "", "No Codex model metadata is available for this selection."

    values: list[str] = []
    for item in row.get("supportedReasoningEfforts") or []:
        value = item.get("reasoningEffort") if isinstance(item, dict) else item
        if isinstance(value, str) and value and value not in values:
            values.append(value)
    return values, str(row.get("defaultReasoningEffort") or ""), None


def _claude_options(
    entry: Mapping[str, Any],
    *,
    model: str,
    refresh: bool = False,
    scope: str = "",
    models_loader: Callable[[str, bool], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    del model, scope, models_loader
    from .backends import claudecode

    binary = str(entry.get("binary") or claudecode.defaults().get("binary") or "claude")
    try:
        options = list(claude_effort_options(binary, refresh=refresh))
    except EffortOptionsError as exc:
        raise BackendOptionsError("Claude Code CLI effort options are unavailable.") from exc
    result: dict[str, Any] = {
        "options": options,
        "default": "",
        "source": "claude-cli-help",
    }
    if not options:
        result["message"] = "Claude Code CLI does not expose --effort; its default will be used."
    return result


def _opencode_options(
    entry: Mapping[str, Any],
    *,
    model: str,
    refresh: bool = False,
    scope: str = "",
    models_loader: Callable[[str, bool], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    del models_loader
    source = "opencode-models-verbose"
    if _split_model(model) is None:
        return {
            "options": [],
            "default": "",
            "source": source,
            "message": "Select a provider/model to see available OpenCode variants.",
        }

    from .backends import opencode

    options = list(
        _opencode_variants(opencode, entry, model, refresh=refresh, scope=scope)
    )
    result = {"options": options, "default": "", "source": source}
    if not options:
        result["message"] = "No named variants are available for this model; OpenCode will use its default."
    return result


def _codex_options(
    entry: Mapping[str, Any],
    *,
    model: str,
    refresh: bool = False,
    scope: str = "",
    models_loader: Callable[[str, bool], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    del entry, scope
    models = models_loader("codex", refresh) if models_loader else None
    options, default, message = _codex_effort_options(model, models)
    if not options and message is None:
        message = "This Codex model does not advertise selectable reasoning efforts."
    result: dict[str, Any] = {
        "options": options,
        "default": default,
        "source": "codex-model/list",
    }
    if message:
        result["message"] = message
    return result


def get_backend_options(
    backend_id: str,
    entry: Mapping[str, Any],
    *,
    model: str | None = None,
    refresh: bool = False,
    scope: str = "",
    models_loader: Callable[[str, bool], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Resolve one backend's option choices without projecting them onto another backend."""
    try:
        spec = get_backend(backend_id)
    except ValueError as exc:
        raise BackendOptionsError("Unknown worker backend.") from exc

    selected = str(entry.get("model") or "").strip() if model is None else str(model).strip()
    if spec.option_kind is None:
        result: dict[str, Any] = {"options": [], "default": "", "source": "none"}
    else:
        result = spec.load().model_options(
            entry,
            model=selected,
            refresh=refresh,
            scope=scope,
            models_loader=models_loader,
        )
    return {**result, "backend": spec.id, "kind": spec.option_kind}
