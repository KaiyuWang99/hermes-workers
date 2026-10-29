"""Backend API for the unified Workers desktop plugin."""

from __future__ import annotations

import copy
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException


PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from workers_core.config import hermes_root, load_config, public_config, save_config, validate_config  # noqa: E402
from workers_core.credentials import credential_status  # noqa: E402
from workers_core.registry import doctor_backend, list_backend_models  # noqa: E402
from workers_core.backend_registry import EXTERNAL_BACKENDS, get_backend  # noqa: E402
from workers_core.compat import check as check_compatibility  # noqa: E402
from workers_core.options import get_backend_options  # noqa: E402


router = APIRouter()
_MODEL_CACHE_TTL = 60.0
_model_cache: dict[tuple[str, str], tuple[float, list[dict[str, Any]]]] = {}
_cache_lock = threading.Lock()


def _profile_cache_key() -> str:
    """Keep model catalogs isolated when one gateway serves multiple profiles."""
    try:
        from hermes_constants import get_hermes_home

        return str(get_hermes_home().expanduser().resolve())
    except Exception:
        return "default"


def _provider(model: str) -> str:
    provider, separator, _model = str(model or "").strip().partition("/")
    if not separator or not provider or not _model:
        raise ValueError("Worker model must use provider/model format")
    return provider


def _models(backend: str, *, refresh: bool = False) -> list[dict[str, Any]]:
    try:
        spec = get_backend(backend)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Unknown worker backend") from exc
    if spec.model_source == "none":
        raise HTTPException(status_code=404, detail="Unknown worker backend")
    now = time.monotonic()
    cache_key = (backend, _profile_cache_key())
    with _cache_lock:
        cached = _model_cache.get(cache_key)
        if not refresh and cached and now - cached[0] < _MODEL_CACHE_TTL:
            return copy.deepcopy(cached[1])
    try:
        value = list_backend_models(backend, refresh=refresh)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    with _cache_lock:
        _model_cache[cache_key] = (now, copy.deepcopy(value))
    return value


def _cached_models(backend: str) -> list[dict[str, Any]] | None:
    """Return already-loaded catalog rows without starting another CLI/RPC request."""
    cache_key = (backend, _profile_cache_key())
    with _cache_lock:
        cached = _model_cache.get(cache_key)
        if not cached or time.monotonic() - cached[0] >= _MODEL_CACHE_TTL:
            return None
        return copy.deepcopy(cached[1])


def _light_status(document: dict[str, Any]) -> dict[str, Any]:
    workers = document.get("workers") or {}

    def status(worker: dict[str, Any]) -> dict[str, Any]:
        try:
            target = str(worker.get("model") or "")
            return credential_status(_provider(target), model=target.partition("/")[2])
        except ValueError as exc:
            return {
                "configured": False,
                "available": False,
                "mode": "missing",
                "error": str(exc),
            }

    return {
        spec.id: {"credential": status(workers.get(spec.id) or {})}
        for spec in EXTERNAL_BACKENDS
        if spec.model_source == "hermes"
    }


@router.get("/state")
def get_state() -> dict[str, Any]:
    document = load_config()
    claude = ((document.get("workers") or {}).get("claude-code") or {})
    claude_capabilities: dict[str, Any] = {}
    if bool(claude.get("enabled")):
        from workers_core.backends import claudecode

        try:
            claude_capabilities = claudecode.capabilities(
                str(claude.get("binary") or "")
            )
        except Exception:
            claude_capabilities = {}
    return {
        "ok": True,
        "configuration": public_config(document),
        "status": _light_status(document),
        "backends": [spec.public() for spec in EXTERNAL_BACKENDS],
        "claude_capabilities": claude_capabilities,
    }


@router.get("/hooks")
def get_hooks() -> dict[str, Any]:
    """The optional hook panel loads its inventory independently of launch settings."""
    from workers_core import claude_hooks

    document = load_config()
    claude_code = (document.get("workers") or {}).get("claude-code") or {}
    selection = claude_code.get("hermes_hook_selection")
    return {
        "ok": True,
        "hermes_hooks": claude_hooks.list_hooks(
            str(hermes_root()),
            selection=selection,
            sync_enabled=bool(claude_code.get("sync_hermes_hooks")),
        ),
        # Saved choices whose hook was renamed or removed; otherwise invisible.
        "orphaned_selections": claude_hooks.orphan_selections(
            str(hermes_root()), selection
        ),
    }


@router.get("/models/{backend}")
def get_models(backend: str, refresh: bool = False) -> dict[str, Any]:
    return {"ok": True, "backend": backend, "models": _models(backend, refresh=refresh)}


@router.get("/options/{backend}")
def get_options(backend: str, model: str | None = None, refresh: bool = False) -> dict[str, Any]:
    """Backend-specific picker options; model catalogs stay at their native source."""
    try:
        spec = get_backend(backend)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Unknown worker backend") from exc

    try:
        document = load_config()
        entry = (document.get("workers") or {}).get(spec.id) or spec.load().defaults()
        return get_backend_options(
            spec.id,
            entry,
            model=model,
            refresh=refresh,
            scope=_profile_cache_key(),
            models_loader=lambda backend_id, force_refresh: _models(
                backend_id, refresh=force_refresh
            ),
        )
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Worker backend options are unavailable.",
        ) from None


@router.get("/doctor")
def doctor(backend: str = "native") -> dict[str, Any]:
    """Diagnose one backend; default to Native so no external CLI is implied."""
    document = load_config()
    response = _doctor_response(backend, document, scope="saved")
    return response


def _diagnostic_checks(backend: str, result: dict[str, Any], *, scope: str) -> dict[str, Any]:
    """Expose local readiness checks without raw CLI output or account details."""
    cli: dict[str, Any] = {
        "status": "not_applicable" if backend == "native" else "not_checked"
    }
    if backend != "native":
        cli["binary"] = str(result.get("binary") or "")
        version = re.search(r"\b\d+\.\d+(?:\.\d+)?(?:[-+][A-Za-z0-9.-]+)?", str(result.get("version") or ""))
        if version:
            cli["version"] = version.group(0)
        if result.get("binary_ok") is False:
            cli.update(status="failed", message="Executable is unavailable.")
        elif result.get("version_ok") is True:
            cli["status"] = "passed"
        elif result.get("version_ok") is False or result.get("error"):
            cli.update(status="failed", message="The local CLI check did not complete successfully.")
        capabilities = result.get("capabilities") or {}
        if capabilities:
            cli["features"] = capabilities.get("features") or {}
            if capabilities.get("version") and not cli.get("version"):
                cli["version"] = capabilities["version"]

    auth: dict[str, Any] = {
        "status": "not_applicable" if backend == "native" else "not_checked"
    }
    if "login_ok" in result:
        auth["source"] = f"{backend}-login"
        auth["status"] = "passed" if result["login_ok"] else "failed"
        if not result["login_ok"]:
            auth["message"] = "Codex login is unavailable."
    elif backend != "native":
        credential = result.get("credential") or {}
        auth.update(source="hermes-credential-pool", configured=bool(credential.get("configured")))
        if "available" in credential:
            auth["available"] = bool(credential["available"])
        if not auth["configured"]:
            auth.update(status="failed", message="No matching Hermes credential is configured.")
        elif "available" in auth:
            auth["status"] = "passed" if auth["available"] else "failed"
            if not auth["available"]:
                auth["message"] = "A Hermes credential is configured but currently unavailable."

    return {
        "backend": backend,
        "scope": scope,
        "cli": cli,
        "authentication": auth,
        "execution": {"status": "not_tested"},
    }


def _doctor_response(backend: str, document: dict[str, Any], *, scope: str) -> dict[str, Any]:
    try:
        get_backend(backend)
    except ValueError:
        raise HTTPException(status_code=404, detail="Unknown worker backend")
    try:
        result = doctor_backend(backend, document)
    except Exception:
        raise HTTPException(status_code=503, detail="The local environment check could not complete.") from None
    checks = _diagnostic_checks(backend, result, scope=scope)
    response = {
        "ok": backend == "native" or all(checks[key]["status"] == "passed" for key in ("cli", "authentication")),
        "backend": backend,
        "diagnostics": checks,
        # Top level so native and external backends share the same plugin
        # compatibility report; it never changes the backend verdict.
        "compatibility": check_compatibility(),
    }
    if scope == "saved":
        # Keep the pre-existing GET contract for existing callers.
        response["backends"] = {backend: result}
    return response


@router.post("/doctor")
def doctor_draft(body: dict[str, Any]) -> dict[str, Any]:
    """Check an unsaved draft locally; never persist it or launch a model task."""
    backend = str(body.get("backend") or "native")
    try:
        document = validate_config(body.get("configuration"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _doctor_response(backend, document, scope="draft")


def _validate_backend_choices(document: dict[str, Any], previous: dict[str, Any]) -> None:
    workers = document["workers"]
    previous_workers = previous.get("workers") or {}
    for spec in EXTERNAL_BACKENDS:
        entry = workers[spec.id]
        old_entry = previous_workers.get(spec.id) or {}
        if not bool(entry.get("enabled")):
            continue
        was_enabled = bool(old_entry.get("enabled"))
        # The registry owns which settings invalidate a saved selection; a
        # backend with no fields (native) never needs selection validation.
        if was_enabled and all(
            entry.get(field, "") == old_entry.get(field, "")
            for field in spec.selection_fields
        ):
            continue
        models = _cached_models(spec.id) if spec.model_source == "cli" else None
        try:
            spec.load().validate_selection(entry, models=models)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/configuration")
def put_configuration(body: dict[str, Any]) -> dict[str, Any]:
    payload = body.get("configuration") if isinstance(body.get("configuration"), dict) else body
    payload = {key: value for key, value in payload.items() if key != "config_path"}
    try:
        document = validate_config(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _validate_backend_choices(document, load_config())
    try:
        save_config(document)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return get_state()
