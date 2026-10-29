# Workers for Hermes

Workers connects Hermes Kanban to external coding CLIs. Hermes keeps ownership
of task claiming, dependencies, workspaces, review transitions, timeouts and
retry policy; Workers only replaces the worker-launch callback and adapts the
resolved launch inputs to each CLI.

## Backends

| Backend | Model picker | Reasoning / variant source | Execution |
| --- | --- | --- | --- |
| Hermes Native | Hermes native settings | Hermes native settings | Original Hermes launcher |
| Claude Code | Hermes SDK model menu | Configured CLI's `--help` | Native Claude loop; direct Anthropic routes or the local protocol bridge |
| OpenCode | Hermes SDK model menu | Selected provider/model's CLI `variants` | Native OpenCode loop; Hermes credential overlays |
| Codex | Codex `model/list` | Selected model's advertised reasoning efforts | `codex exec --json`; failures are recorded, not rotated or fallen back |

## Features

- One routing, context and settings structure across backends.
- Shared Kanban protocol, task context, profile/workspace scope, SOUL,
  personality, memory, project rules, Skills, MCP and Hermes tools.
- Claude Code capability detection from the installed CLI's `--help`: unsupported
  switches are disabled and saved values are normalized before saving.
- Backend-specific option sources instead of one universal effort list.
- Hermes-owned credential pool and provider resolution; no provider aliases or
  endpoint tables in the plugin.
- Shared Hermes fallback chain for Claude Code and OpenCode. Codex keeps its own
  login and fallback behavior.
- Labeled environment checks that never save the draft or run a model task.
- Per-assignee routing through `routes_by_assignee`; the settings page edits one
  external backend at a time.

## Requirements

- Hermes `>=0.22.5` (declared in `plugin.yaml`).
- macOS or Linux.
- At least one external CLI for external workers:
  - Claude Code: a current CLI; optional flags are detected at runtime.
  - OpenCode: a current CLI.
  - Codex CLI `0.165+` for `exec --json`.
- Provider credentials configured in Hermes (credential pool or model config).
  The plugin does not store API keys in this repository.

## Installation

Install from a Git repository:

```sh
hermes plugins install KaiyuWang99/hermes-workers --enable
```

For local development, place the checkout in the plugin directory and enable it:

```sh
git clone <repo-url> ~/.hermes/plugins/workers
hermes plugins enable workers
```

Restart the gateway after installing, and reload Hermes Desktop so the settings
bundle is refreshed. Then open **Settings → Plugins → Workers**.

## Configuration

Workers stores its configuration in native Hermes plugin settings at
`plugins.entries.workers.settings.configuration` (`workers-config-v1`). The legacy
`configs/workers.json` migration remains a compatibility path; there is no second
active configuration store.

### Routing and inheritance

- Enable one external backend on the settings page. Enabling a different backend
  turns the previous external backend off; disabling it returns new tasks to
  Hermes Native.
- `routes_by_assignee` can pin an assignee profile to a backend. It is a
  configuration-file setting for multi-profile boards.
- The inheritance section controls what new external workers receive: identity,
  memory, project rules, Skills, MCP, Hermes tools and supported fallback models.
  A card's task and Kanban protocol are always included.

### Configuration-file-only settings

These adapter settings have no control on the settings page. They are read from
the stored `workers.<backend>` entry and validated by that backend:

| Backend | Settings page | Configuration file only |
| --- | --- | --- |
| Claude Code | model, reasoning, native MCP, native Skills, settings sources, permission mode, hook sync | `binary`, `no_session_persistence`, `tools`, `disallowed_tools`, `silent_timeout_seconds`, `pin_model_aliases`, `pin_subagent_model` |
| OpenCode | model, variant, user/project configuration inheritance | `binary`, `agent` |
| Codex | model, reasoning, fast tier, user configuration | `binary`, `codex_home`, `sandbox`, `network_access`, `ephemeral` |
| Hermes Native | none (uses Hermes profile settings) | none |

Empty `tools: []` is passed as `--tools ""`, which Claude Code documents as
"disable all tools". An empty OpenCode `agent` omits `--agent` and lets OpenCode
use its own default agent; a named agent must exist in the OpenCode configuration.

## Execution details

### Claude Code routes

Claude Code uses the Hermes resolver's `api_mode`:

- `anthropic_messages` → direct connection to the provider's Anthropic endpoint.
- `chat_completions`, `responses`, `codex_responses` → a loopback
  `BuiltinClaudeProxy` converts Anthropic Messages to the provider protocol.

Every fallback entry is routed independently. The bridge is created per attempt
and reused across goal-mode resume turns.

### Codex

Codex runs `codex exec --json` and records `turn.failed`/`error` events to the
worker log and `<worker log>.status.json`. It does not rotate credentials or
switch models; Codex keeps its own login and fallback chain. Its generated MCP
servers use `default_tools_approval_mode="approve"` because an unattended worker
cannot answer an approval prompt under `approval_policy=never`; the Codex
sandbox and network settings still apply to shell commands.

## Adding another backend

Backend identity is static and explicit; there is no dynamic discovery layer.
A new external backend is a small module plus one registry entry. See
[ADDING_A_BACKEND.md](ADDING_A_BACKEND.md) for the contract, a minimal skeleton
and the validation checklist. Existing UI panels render new backends from the
registry metadata when they use the same `model_source` and `option_kind`.

## Development

Use an existing Hermes development environment with pytest:

```sh
/path/to/hermes/.venv/bin/python -m pytest -q -p no:cacheprovider
node --check desktop/plugin.js
node --experimental-vm-modules tests/test_ui_settings.mjs
node verification/desktop.mjs
```

`tests/conftest.py` resolves the Hermes source from `HERMES_SOURCE` when set and
otherwise from `~/.hermes/hermes-agent`. The profile-history integration test is
skipped unless `HERMES_LIVE_PROFILE_TEST=1` is set, because it reexecutes into a
separate Hermes runtime.

Component tests use host stubs; they do not prove visual correctness in the live
desktop. CLI and MCP probes are separate from real provider tasks.

## Architecture and upgrades

- [ARCHITECTURE.md](ARCHITECTURE.md) — responsibility boundaries, backend
  interface, model/option flow and error boundary.
- [UPGRADE_CHECKLIST.md](UPGRADE_CHECKLIST.md) — private Hermes integration points
  to verify after an upstream upgrade.

## License

MIT. See [LICENSE](LICENSE).
