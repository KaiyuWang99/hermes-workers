# Adding a Worker backend

Workers has a static backend registry, not a runtime discovery protocol. A new
backend is a Python module plus one `BackendSpec` entry. The settings page and
API render it from registry metadata when it uses an existing `model_source` and
`option_kind`.

## 1. Register the backend

Add a `BackendSpec` to `workers_core/backend_registry.py`:

```python
"my-cli": BackendSpec(
    id="my-cli",
    label="My CLI Worker",
    module="my_cli",              # workers_core/backends/my_cli.py
    model_source="hermes",        # "hermes" | "cli" | "none"
    option_kind="effort",         # "effort" | "variant" | "reasoning_effort" | None
    inherit_credentials=False,
    supervises_children=True,
    selection_fields=("model", "reasoning_effort", "binary"),
),
```

`selection_fields` lists the settings that invalidate an enabled selection and
must be revalidated on save. External backends must declare at least one.

`model_source` controls the model picker:

- `hermes`: the Hermes SDK model menu.
- `cli`: `GET /models/{backend}` backed by the adapter's `list_models()`.
- `none`: no model picker (Native).

`option_kind` controls the reasoning/variant picker through
`GET /options/{backend}`. Reusing an existing kind needs no desktop changes.

## 2. Implement the adapter module

Create `workers_core/backends/my_cli.py` with:

```python
from typing import Any

from ..common import WorkerLaunchContext


def defaults() -> dict[str, Any]:
    return {
        "enabled": False,
        "binary": "my-cli",
        "model": "",
        "reasoning_effort": "",
    }


def validate_settings(entry: dict[str, Any]) -> dict[str, Any]:
    # Preserve unknown user fields; validate types and required values.
    return dict(entry)


def validate_selection(
    entry: dict[str, Any], *, models: list[dict[str, Any]] | None = None
) -> None:
    # Validate the model, effort/variant and credentials for an enabled worker.
    return None


def model_options(
    entry: dict[str, Any],
    *,
    model: str,
    refresh: bool = False,
    scope: str = "",
    models_loader: Any = None,
) -> dict[str, Any]:
    return {"options": [], "default": "", "source": "my-cli"}


def list_models(config: dict[str, Any], *, refresh: bool = False) -> list[dict[str, Any]]:
    return []


def doctor(config: dict[str, Any]) -> dict[str, Any]:
    # Local checks only: binary/version/login/credential. Never start a task.
    return {"backend": "my-cli", "ok": True}


def spawn(launch: WorkerLaunchContext, *, config: dict[str, Any]) -> int:
    # Start the CLI with start_new_session=True and return its PID.
    ...
```

The module contract:

| Function | Purpose |
| --- | --- |
| `defaults()` | Fresh settings for a new install; must include `enabled=False`, `binary`, `model`, `reasoning_effort` for external backends. |
| `validate_settings(entry)` | Validate/normalize persisted settings; preserve unknown fields. |
| `validate_selection(entry, *, models=None)` | Reject an enabled selection the CLI cannot run. |
| `model_options(...)` | Return `options`, `default`, `source`, optional `message`. |
| `list_models(config, *, refresh=False)` | Return CLI catalog rows (`id`, optional `displayName`, `description`, effort/tier metadata). |
| `doctor(config)` | Return local readiness data. |
| `spawn(launch, *, config)` | Launch the CLI and return a PID; detach it from the gateway process. |

Native is the exception: its registry entry has `model_source="none"` and its
spawn signature is `spawn(task, workspace, *, board, config)`; it delegates to
Hermes' original launcher.

## 3. Launch and supervision

- Start the CLI with `start_new_session=True` so it and its MCP children form
  their own process group.
- Use `worker_log(task_id, board)` for the shared worker log.
- If the adapter starts a runner that calls `process_group.supervise(...)`
  itself, set `supervises_children=True`; otherwise set it to `False` and the
  router will supervise the returned PID.
- Use `build_worker_env` / `WorkerLaunchContext` instead of recreating Hermes
  profile, workspace, MCP or credential resolution.
- Write private run files under the Hermes state directory; clean them up on
  exit. Do not scan historical directories or edit the Kanban database.

## 4. Hermes compatibility

Adapters that import private Hermes APIs must add the import to
`workers_core/compat.py` (`REQUIRED` or `OPTIONAL`) and document the migration
gate in `UPGRADE_CHECKLIST.md`. The startup probe turns an upstream rename into a
clear error instead of a first-task failure.

## 5. Validation checklist

- `defaults()` and `validate_settings()` preserve unknown fields.
- `validate_selection()` is enforced when the backend is enabled or a selection
  field changes.
- The command builder has a unit test for flags, model, settings and MCP config.
- `spawn()` has a test for PID handling, environment, log path and run cleanup.
- `doctor()` is read-only and never launches a model task.
- `/state`, `/models/{backend}` and `/options/{backend}` work for the new
  backend; run `tests/test_ui_settings.mjs` if you added a new `model_source` or
  `option_kind`.
- Add the backend to the README table and to the configuration-only settings
  table when it has file-only settings.
- Run the full suite:

```sh
/path/to/hermes/.venv/bin/python -m pytest -q -p no:cacheprovider
node --check desktop/plugin.js
node --experimental-vm-modules tests/test_ui_settings.mjs
node verification/desktop.mjs
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full responsibility boundaries and
the model/option flow.
