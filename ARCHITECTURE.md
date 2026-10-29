# Architecture

## Responsibility boundaries

```text
Hermes Kanban dispatch
  → Workers router (assignee profile, explicit route/default)
  → Hermes-native context resolution
  → WorkerLaunchContext
  → selected backend adapter
  → native CLI
```

Hermes owns the board lifecycle and runtime services. The router selects an
adapter and resolves one context snapshot; adapters serialize it for their CLI.
Native workers call the original launcher. Claude, OpenCode and Codex are peer
adapters, not subtypes of the Claude implementation.

| Module | Responsibility |
| --- | --- |
| `backend_registry.py` | Backend ID, label, module, model source, option kind, launch capability metadata |
| `config.py` | Native configuration storage, profile inheritance, migration, shared schema |
| `registry.py` | Routing, profile binding, shared context resolution, dispatch and supervision boundary |
| `contracts.py` | Backend-neutral launch inputs and resolved MCP transport types |
| `common.py` | Native Hermes context/environment/MCP/toolset integration |
| `backends/*` | CLI defaults, settings/selection validation, options, catalog, launch, diagnostics and serialization |
| `options.py` | Thin dispatch plus existing CLI-specific metadata readers/caches |
| `dashboard/plugin_api.py` | Profile-scoped HTTP transport, native save, catalog cache and diagnostics |
| `desktop/plugin.js` | Shared backend panels, local draft, native model menu and resource retry guards |

`BackendSpec.load()` imports the selected module lazily. There is no backend base
class, service container, dynamic discovery protocol or second scheduler.
Necessary transport/protocol differences stay in the adapters.

## Backend interface

An external backend exposes:

- `defaults()` and pure `validate_settings(entry)` for persisted settings.
- `validate_selection(entry, *, models=None)` for explicit selection changes.
- `model_options(entry, *, model, refresh=False, scope='', models_loader=None)`.
- `list_models(config, *, refresh=False)` and `doctor(config)`.
- `spawn(launch: WorkerLaunchContext, *, config) -> int`.

`model_options` returns `options`, `default`, `source`, and an optional `message`.
The generic options dispatcher attaches `backend` and `kind`. `option_kind`
labels the UI semantics; it never selects a particular CLI implementation. A new
backend can have the same option kind and its own discovery mechanism.

`WorkerLaunchContext` carries task, workspace, board, session ID, scoped environment,
resolved MCP descriptions and prompt. MCP descriptions use stdio command/args/env/
cwd or a remote transport URL/headers; they are not a Claude configuration file.
Each adapter owns JSON/TOML serialization and credential forwarding. The previous
`common.WorkerLaunchContext` import remains an alias for compatibility.

## Model and options flow

Hermes-backed models use the exported SDK `ModelCatalogMenu` and a local
controller. The host owns catalog state, names and visibility. Codex models come
from its existing `model/list` adapter. The page does not project arbitrary
Hermes model IDs onto Codex.

`GET /state` returns configuration, status and backend metadata.
`GET /options/{backend}?model=...&refresh=...` reads that backend's actual option
source; `/models/{backend}` remains the CLI catalog endpoint.
Options are scoped by backend/model and guarded against obsolete async responses.
CLI model catalogs load before their option request; option lookup reuses the
loaded catalog. No duplicate frontend effort enum is maintained.

Default is represented by a nonempty UI sentinel and persisted as `''`; the CLI
flag is omitted. Codex metadata may advertise the CLI's effective default effort,
but that does not turn an unset worker preference into an explicit override.
Unsupported saved values are displayed separately. Saves revalidate a backend
only when enabled or when selection-related fields change. Expired catalog rows
are not used to reject selections, and saving never starts a catalog request.

## Error boundary

External CLIs destroy the exception object, so each adapter only normalizes what
its CLI exposed (`workers_core/provider_errors.py`: status, error body, message,
and the known Claude Code wrapper). `classify` then calls Hermes'
`agent.error_classifier.classify_api_error`; native and external workers share
one classifier. The plugin adds exactly one correction: a typed or exact-match
geographic 403 becomes `upstream_blocked` (no credential rotation, fall back).

Native workers receive that correction through the
`transform_api_error_classification` hook; every other error returns `None` so
Hermes keeps its richer rules. The OpenCode hook spawns
`workers_core/credential_bridge.py` through Hermes' runtime command, so the
bridge imports the same core classifier instead of the plugin's Python
environment.

Codex exposes no structured upstream status in `exec --json`; its runner records
`turn.failed`/`error` messages and the classifier verdict, but never rotates or
falls back. Codex keeps its own login and fallback chain.

## Compatibility and remaining coupling

The configuration schema and unknown user fields are retained. Explicit existing
backend settings, protocol bridges, credential pools, fallback rules and
default/assignee routes remain in place. Provider routing itself follows Hermes'
resolver; an explicit fallback entry may pin `api_mode`/`base_url` for one
route. These are deliberate policy, not model-picker allowlists.

The entrypoint installs one reversible wrapper: the dispatch `spawn_fn`. Hermes
resolves the managed Kanban worker route (including `api_mode`) inside
`agent.managed_worker._construct_agent` via `worker_route`; Workers does not
patch that constructor and keeps no provider-to-protocol table. The wrapper is a
private integration point, so an upstream rename can break this adapter. See
[`UPGRADE_CHECKLIST.md`](UPGRADE_CHECKLIST.md) for the migration gates.

Supporting another CLI still requires an actual adapter and appropriate local
verification. Registry-driven UI does not imply compatibility with every CLI.
