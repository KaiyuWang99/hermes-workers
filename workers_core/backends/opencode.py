"""Direct OpenCode backend using OpenCode models and Hermes credentials."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Mapping

from ..common import (
    WorkerLaunchContext,
    source_root as _source_root,
    worker_log,
    worker_runner_command,
)
from ..config import hermes_root, plugin_root, inheritance_policy
from ..credentials import credential_status, pool_provider, require_credentials
from ..fallbacks import fallback_entries_for
from ..provider_catalog import (
    SUPPORTED_API_MODES,
    configured_model_rows,
    ensure_opencode_session_header,
    resolve_model_runtime,
)


logger = logging.getLogger(__name__)


def defaults() -> dict[str, Any]:
    from ..config import _which

    return {
        "enabled": False,
        "binary": _which("opencode", str(Path.home() / ".local/bin/opencode")),
        "model": "",
        "reasoning_effort": "",
        # Empty keeps OpenCode's own default agent; a named agent must exist in
        # the user's OpenCode configuration or CLI runs fall back to that default.
        "agent": "",
    }


def validate_settings(entry: dict[str, Any]) -> dict[str, Any]:
    """Normalize OpenCode settings and preserve unknown user fields."""
    if not isinstance(entry, Mapping):
        raise ValueError("workers.opencode must be an object")
    result = dict(entry)
    if not str(result.get("binary") or "").strip():
        raise ValueError("workers.opencode.binary is required")
    if bool(result.get("enabled")) and not str(result.get("model") or "").strip():
        raise ValueError("workers.opencode.model is required when enabled")
    for key in ("inherit_user_config", "inherit_project_config"):
        if key in result and not isinstance(result[key], bool):
            raise ValueError(f"workers.opencode.{key} must be a boolean")
    agent = result.get("agent")
    if agent is not None and not isinstance(agent, str):
        raise ValueError("workers.opencode.agent must be a string")
    return result


def validate_selection(
    entry: dict[str, Any], *, models: list[dict[str, Any]] | None = None
) -> None:
    """Validate an enabled model's Hermes provider credential binding."""
    del models
    if not bool(entry.get("enabled")):
        return
    provider, _model = _split_model(str(entry.get("model") or ""))
    status = credential_status(provider, model=_model)
    if not status.get("configured"):
        raise ValueError(
            f"OpenCode provider {provider!r} has no matching Hermes credential."
        )


def model_options(
    entry: dict[str, Any],
    *,
    model: str,
    refresh: bool = False,
    scope: str = "",
    models_loader: Any = None,
) -> dict[str, Any]:
    from ..options import _opencode_options

    return _opencode_options(
        entry,
        model=model,
        refresh=refresh,
        scope=scope,
        models_loader=models_loader,
    )


def _entry(config: dict[str, Any]) -> dict[str, Any]:
    return dict((config.get("workers") or {}).get("opencode") or {})


def _read_jsonc(path: Path) -> dict[str, Any]:
    """Read JSON with comments/trailing commas without altering string literals."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    chars = list(text)
    quoted = escaped = False
    index = 0
    while index < len(text):
        char = text[index]
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif text[index:index + 2] in {"//", "/*"}:
            block = text[index + 1] == "*"
            end = text.find("*/" if block else "\n", index + 2)
            if block and end < 0:
                raise ValueError(f"Invalid OpenCode configuration at {path}: unterminated comment")
            end = len(text) if end < 0 else end + (2 if block else 0)
            for pos in range(index, end):
                if chars[pos] not in "\r\n":
                    chars[pos] = " "
            index = end - 1
        index += 1
    # Strip a comma only outside a string and immediately before a closing
    # delimiter after whitespace/comments have been removed.
    quoted = escaped = False
    for index, char in enumerate(chars):
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char == ",":
            following = index + 1
            while following < len(chars) and chars[following].isspace():
                following += 1
            if following < len(chars) and chars[following] in "}]":
                chars[index] = " "
    try:
        value = json.loads("".join(chars))
    except json.JSONDecodeError as exc:
        # Do not include source snippets: configuration can contain secrets.
        raise ValueError(f"Invalid OpenCode configuration at {path} (line {exc.lineno}, column {exc.colno})") from None
    if not isinstance(value, dict):
        raise ValueError(f"OpenCode configuration must be an object: {path}")
    return value


def _merge_config(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    for key, value in overlay.items():
        if isinstance(base.get(key), dict) and isinstance(value, dict):
            _merge_config(base[key], value)
        else:
            base[key] = value
    return base


def _user_config(env: dict[str, str], workspace: str | None = None) -> dict[str, Any]:
    home = Path(env.get("HOME") or str(Path.home())).expanduser()
    directory = Path(env.get("XDG_CONFIG_HOME") or home / ".config") / "opencode"
    sources = [directory / "config.json", directory / "opencode.json", directory / "opencode.jsonc"]
    configured = str(env.get("OPENCODE_CONFIG") or "").strip()
    if configured:
        path = home / configured[2:] if configured.startswith("~/") else Path(configured)
        sources.append(path if path.is_absolute() else Path(workspace or os.getcwd()) / path)
    base: dict[str, Any] = {}
    for path in dict.fromkeys(sources):
        _merge_config(base, _read_jsonc(path))
    return base


def _split_model(value: str) -> tuple[str, str]:
    provider, separator, model = str(value or "").strip().partition("/")
    if not separator or not provider or not model:
        raise RuntimeError(f"OpenCode model must use provider/model format: {value!r}")
    return provider, model


def _provider_overlay(
    provider: str,
    model: str,
    fallback_entry: dict[str, Any] | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Create the selected provider's transient OpenCode definition."""
    if fallback_entry:
        from hermes_cli.fallback_config import resolve_entry_api_key
        from hermes_cli.runtime_provider import resolve_runtime_provider

        explicit_key = str(resolve_entry_api_key(fallback_entry) or "").strip()
        runtime = resolve_runtime_provider(
            requested=provider,
            explicit_api_key=explicit_key or "workers-opencode-runtime",
            explicit_base_url=str(fallback_entry.get("base_url") or "").strip()
            or None,
            target_model=model,
        )
        if str(fallback_entry.get("api_mode") or "").strip():
            runtime["api_mode"] = str(fallback_entry["api_mode"]).strip()
        if isinstance(fallback_entry.get("extra_headers"), dict):
            runtime["extra_headers"] = dict(fallback_entry["extra_headers"])
        if explicit_key:
            runtime["credential_override"] = explicit_key
    else:
        runtime = resolve_model_runtime(
            provider, model, api_key="workers-opencode-runtime"
        )
    api_mode = str(runtime.get("api_mode") or "").strip()
    if api_mode not in SUPPORTED_API_MODES:
        raise RuntimeError(
            f"OpenCode worker does not support {provider}/{model} with API mode {api_mode or 'unknown'}"
        )
    from hermes_cli.auth import PROVIDER_REGISTRY

    profile = PROVIDER_REGISTRY.get(provider)
    npm = (
        "@ai-sdk/anthropic"
        if api_mode == "anthropic_messages"
        else "@ai-sdk/openai-compatible"
    )
    return (
        {
            provider: {
                "npm": npm,
                "name": str(getattr(profile, "name", "") or provider),
                "options": {"baseURL": str(runtime["base_url"]).rstrip("/")},
                "models": {model: {"name": model}},
            }
        },
        runtime,
    )


def _merge_provider_overlays(
    base: dict[str, Any], overlays: dict[str, dict[str, Any]]
) -> None:
    """Merge Hermes-owned provider fields without touching top-level ``model``."""
    providers = base.get("provider")
    if not isinstance(providers, dict):
        providers = {}
        base["provider"] = providers

    for provider_id, overlay in overlays.items():
        current = providers.get(provider_id)
        merged = dict(current) if isinstance(current, dict) else {}
        merged["npm"] = overlay["npm"]
        merged["name"] = overlay["name"]

        options = merged.get("options")
        options = dict(options) if isinstance(options, dict) else {}
        options.update(dict(overlay.get("options") or {}))
        merged["options"] = options

        models = merged.get("models")
        models = dict(models) if isinstance(models, dict) else {}
        models.update(dict(overlay.get("models") or {}))
        merged["models"] = models
        providers[provider_id] = merged


def _effective_model(task: Any, entry: dict[str, Any]) -> str:
    task_model = str(getattr(task, "model_override", "") or "").strip()
    task_provider = str(getattr(task, "provider_override", "") or "").strip()
    raw = (
        f"{task_provider}/{task_model}"
        if task_model and task_provider
        else task_model
        if task_model and "/" in task_model
        else str(entry.get("model") or "").strip()
    )
    provider, model = _split_model(raw)
    return f"{pool_provider(provider)}/{model}"


def _build_command(
    task: Any,
    workspace: str,
    entry: dict[str, Any],
    model: str,
    prompt: str,
) -> list[str]:
    binary = Path(str(entry.get("binary") or "")).expanduser()
    command = [
        str(binary),
        "run",
        prompt,
        "--model",
        model,
        "--format",
        "default",
        "--title",
        f"Hermes Kanban {task.id}",
        "--dir",
        workspace,
    ]
    agent = str(entry.get("agent") or "").strip()
    if agent:
        command.extend(["--agent", agent])
    reasoning = str(
        getattr(task, "reasoning_effort", "") or entry.get("reasoning_effort") or ""
    ).strip()
    if reasoning:
        command.extend(["--variant", reasoning])
    return command


def _mcp_entry(server: dict[str, Any]) -> dict[str, Any]:
    if server.get("command"):
        entry: dict[str, Any] = {
            "type": "local",
            "command": [str(server["command"]), *map(str, server.get("args") or [])],
            "enabled": True,
        }
        if server.get("env"):
            entry["environment"] = dict(server["env"])
        if server.get("cwd"):
            entry["cwd"] = str(server["cwd"])
        return entry
    entry = {"type": "remote", "url": str(server["url"]), "enabled": True}
    if server.get("headers"):
        entry["headers"] = dict(server["headers"])
    return entry


def _worker_config(
    model: str,
    *,
    provider_overlays: dict[str, dict[str, Any]],
    mcp_servers: dict[str, dict[str, Any]] | None = None,
    inherit_user_config: bool = True,
    env: dict[str, str] | None = None,
    workspace: str | None = None,
) -> Path:
    base = _user_config(dict(os.environ) if env is None else env, workspace) if inherit_user_config else {}
    _merge_provider_overlays(base, provider_overlays)
    # Do not synchronize the active model. Workers passes its own model through
    # ``opencode run --model`` below; the user's OpenCode default remains intact.
    mcp = dict(base.get("mcp") or {})
    mcp.update(
        {name: _mcp_entry(server) for name, server in (mcp_servers or {}).items()}
    )
    base["mcp"] = mcp
    plugins = [item for item in (base.get("plugin") or []) if isinstance(item, str)]
    runtime_plugin = (plugin_root() / "opencode" / "index.js").resolve().as_uri()
    if runtime_plugin not in plugins:
        plugins.append(runtime_plugin)
    base["plugin"] = plugins

    state_dir = hermes_root() / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix="workers-opencode-", dir=state_dir))
    target = run_dir / "opencode.json"
    try:
        with open(target, "x", encoding="utf-8", opener=lambda path, flags: os.open(path, flags, 0o600)) as handle:
            json.dump(base, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except BaseException:
        shutil.rmtree(run_dir, ignore_errors=True)
        raise
    return target


def spawn(
    launch: WorkerLaunchContext,
    *,
    config: dict[str, Any],
) -> int:
    task = launch.task
    workspace = launch.workspace
    board = launch.board
    entry = _entry(config)
    binary = Path(str(entry.get("binary") or "")).expanduser()
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise RuntimeError(f"OpenCode executable is unavailable: {binary}")
    model = _effective_model(task, entry)
    provider, model_id = _split_model(model)
    require_credentials(provider, model=model_id)
    targets = [(model, None)]
    inherited_fallbacks = fallback_entries_for(provider, model_id) if inheritance_policy(config)["fallback_models"] else []
    for fallback_entry in inherited_fallbacks:
        targets.append(
            (
                f"{fallback_entry['provider']}/{fallback_entry['model']}",
                fallback_entry,
            )
        )

    resolved: list[tuple[str, str, dict[str, Any]]] = []
    overlays: dict[str, dict[str, Any]] = {}
    for target, fallback_entry in targets:
        target_provider, target_model = _split_model(target)
        try:
            if fallback_entry:
                from hermes_cli.fallback_config import resolve_entry_api_key

                has_explicit_key = bool(resolve_entry_api_key(fallback_entry))
                if not has_explicit_key:
                    require_credentials(target_provider, model=target_model)
            else:
                require_credentials(target_provider, model=target_model)
            target_overlays, target_runtime = _provider_overlay(
                target_provider, target_model, fallback_entry
            )
            ensure_opencode_session_header(target_runtime, launch.session_id)
        except Exception as exc:
            if fallback_entry:
                logger.debug(
                    "opencode: skipped fallback %s/%s: %s",
                    target_provider,
                    target_model,
                    exc,
                )
                continue
            raise
        overlays.update(target_overlays)
        resolved.append((target_provider, target_model, target_runtime))

    config_path = _worker_config(
        model,
        provider_overlays=overlays,
        mcp_servers=launch.mcp_servers,
        inherit_user_config=bool(entry.get("inherit_user_config", True)),
        env=dict(launch.env),
        workspace=workspace,
    )
    process = None
    try:
        base_env = dict(launch.env)
        base_env["WORKERS_OPENCODE_BRIDGE"] = json.dumps(
            worker_runner_command("workers_core.credential_bridge"),
            ensure_ascii=False,
        )
        # The hook reports its decision into this run's private directory,
        # which the watchdog removes with the rest of the run.
        base_env["WORKERS_OPENCODE_STATUS"] = str(config_path.parent / "status.json")
        if "inherit_project_config" in entry:
            base_env["OPENCODE_DISABLE_PROJECT_CONFIG"] = "false" if entry["inherit_project_config"] else "true"
        attempts: list[dict[str, Any]] = []
        for target_provider, target_model, runtime in resolved:
            target = f"{target_provider}/{target_model}"
            attempt_env = dict(base_env)
            attempt_env["OPENCODE_CONFIG"] = str(config_path)
            attempt_env["WORKERS_OPENCODE_PROVIDER"] = target_provider
            attempt_env["WORKERS_OPENCODE_API_MODE"] = str(runtime["api_mode"])
            attempt_env.pop("WORKERS_OPENCODE_API_KEY", None)
            attempt_env.pop("WORKERS_OPENCODE_EXTRA_HEADERS", None)
            extra_headers = runtime.get("extra_headers")
            if isinstance(extra_headers, dict):
                attempt_env["WORKERS_OPENCODE_EXTRA_HEADERS"] = json.dumps(
                    extra_headers, ensure_ascii=False
                )
            credential_override = str(runtime.get("credential_override") or "").strip()
            if credential_override:
                attempt_env["WORKERS_OPENCODE_API_KEY"] = credential_override
            attempts.append(
                {
                    "command": _build_command(
                        task,
                        workspace,
                        entry,
                        target,
                        launch.prompt,
                    ),
                    "env": attempt_env,
                    "provider": target_provider,
                    "model": target_model,
                }
            )

        if not attempts:
            raise RuntimeError("OpenCode worker has no usable model target")

        # Keep the no-fallback path as a direct child.  When Hermes has fallback
        # candidates, a small plugin-owned runner supervises the CLI and switches
        # models only after a recognized provider failure.
        if len(attempts) == 1:
            command = attempts[0]["command"]
            env = attempts[0]["env"]
        else:
            source_root = _source_root()
            env = dict(attempts[0]["env"])
            env["PYTHONPATH"] = os.pathsep.join((str(plugin_root()), str(source_root)))
            command = worker_runner_command("workers_core.backends.opencode_runner")
        log_handle = open(worker_log(str(task.id), board), "ab")
        try:
            process = subprocess.Popen(
                command,
                cwd=workspace,
                stdin=subprocess.PIPE if len(attempts) > 1 else subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                env=env,
                start_new_session=True,
            )
            from ..process_group import supervise

            supervise(process.pid, task_id=str(task.id), board=board, cleanup_dir=config_path.parent)
            if len(attempts) > 1:
                if process.stdin is None:
                    raise RuntimeError("OpenCode worker runner stdin pipe was not created")
                payload = json.dumps({"attempts": attempts}, ensure_ascii=False).encode("utf-8")
                try:
                    process.stdin.write(payload)
                    process.stdin.close()
                except (BrokenPipeError, OSError) as exc:
                    try:
                        process.terminate()
                    except OSError:
                        pass
                    raise RuntimeError(
                        f"OpenCode worker runner rejected launch payload: {exc}"
                    ) from exc
        finally:
            log_handle.close()
        return int(process.pid)
    except BaseException:
        if process is None:
            shutil.rmtree(config_path.parent, ignore_errors=True)
        else:
            try:
                process.terminate()
            except OSError:
                pass
        raise


def doctor(config: dict[str, Any]) -> dict[str, Any]:
    entry = _entry(config)
    binary = Path(str(entry.get("binary") or "")).expanduser()
    model = str(entry.get("model") or "")
    try:
        provider, model_id = _split_model(model)
        credentials = credential_status(pool_provider(provider), model=model_id)
    except RuntimeError as exc:
        credentials = {"configured": False, "available": False, "error": str(exc)}
    result = {
        "backend": "opencode",
        "binary": str(binary),
        "binary_ok": binary.is_file() and os.access(binary, os.X_OK),
        "model": model,
        "credential": credentials,
    }
    if result["binary_ok"]:
        try:
            run = subprocess.run(
                [str(binary), "--version"], capture_output=True, text=True, timeout=10
            )
            result["version"] = (run.stdout or run.stderr).strip()
            result["version_ok"] = run.returncode == 0
            result["ok"] = run.returncode == 0 and bool(credentials.get("configured"))
        except (OSError, subprocess.SubprocessError) as exc:
            result.update({"ok": False, "error": str(exc)})
    else:
        result.update({"ok": False, "error": "OpenCode executable is unavailable"})
    return result


def list_models(
    config: dict[str, Any], *, refresh: bool = False
) -> list[dict[str, Any]]:
    del config
    return configured_model_rows(force_refresh=refresh)
