# Hermes upgrade checklist

Run this before adopting a Hermes core release. Workers uses private Hermes
APIs, not a stable SDK: an upstream rename can fail at import, silently fall
back to one model/key, or start a worker without tools. Every item is a gate;
adapt the plugin, never copy core logic. Run Python checks with the Hermes
runtime interpreter and the test command from README's Development section.

## Managed Worker construction (Hermes-owned)

Workers no longer patches `agent.managed_worker._construct_agent`. Current
Hermes resolves the Kanban worker route (including `api_mode`) inside
`_construct_agent` via `worker_route`; the plugin only replaces the dispatch
`spawn_fn`. Upgrade check: confirm `_construct_agent` still merges
`worker_route` for Kanban workers. If Hermes moves that resolution, native
workers may lose `api_mode`, but the plugin must not re-implement the route.
Verify: `python -c "import inspect; from agent.managed_worker import _construct_agent; print(inspect.getsource(_construct_agent))"`.

## Desktop settings

- Confirm every named export imported at the top of `desktop/plugin.js` still
  exists in `@hermes/plugin-sdk`; `ModelCatalogMenu`, `ListRow`, `ToggleRow`,
  `ModelMenuCloseContext`, `COMPOSER_AREAS`, `PALETTE_AREA`, `ROUTES_AREA` and
  `host` are load-bearing. One missing export aborts module evaluation and
  blanks the page.
  Verify: `node --check desktop/plugin.js`, then load the page in Desktop. The
  VM tests stub every imported name, so they cannot catch a removed SDK export.
- Check each `backend.id` branch against real CLI-specific settings. Keep Claude
  hooks/settings, OpenCode configuration controls and Codex service tiers
  explicit; do not add a generic settings schema for options only one CLI
  supports. Verify: `node --experimental-vm-modules tests/test_ui_settings.mjs`.
- Confirm common model and option rows still come from `/state` -> `backends`
  and `/options/{backend}`, with stale requests scoped to the active profile and
  connection. Otherwise a profile switch can show another profile's catalog.
  Verify: `pytest -q tests/test_dashboard_api.py`.
- Add new visible strings to the `LOCALES` maps in `desktop/plugin.js` (`en`,
  `zh`, `zh-hant`, `ja`); missing keys surface raw translation IDs.
  Verify: `grep -n "LOCALES" desktop/plugin.js`.
- Review the page once in Desktop after an SDK upgrade; component tests do not
  verify rendered spacing or responsive layout.

## Release gate

- Add/update `requires_hermes` in `plugin.yaml` before publishing. The dev
  manifest currently pins `>=0.22.5`; treat that as a conservative floor, not a
  verified boundary. Set it to the earliest stable release that ships the APIs
  above; if another API below shipped later, raise the floor and note why. A
  local source checkout is not a release boundary.
  Verify: `grep -n '^requires_hermes:' plugin.yaml` prints a value, and
  `hermes_cli.plugins_manifest.requires_hermes_error` returns a reason on an
  older version.
- Recompute the boundary whenever an entry in `workers_core/compat.py`
  `REQUIRED` changes: find the earliest core commit that exports that symbol
  (`git -C <core> log --reverse -S'def <symbol>'`), then
  `git -C <core> tag --contains <sha> --sort=version:refname`. Use the first
  stable tag, not the checkout's version.
- Keep `version` in `plugin.yaml` and `dashboard/manifest.json` aligned.
  Verify: compare both files before tagging a release.

## Kanban dispatch and profile scope

- `hermes_cli.kanban_db_dispatch.dispatch_once(conn, *, spawn_fn=None, ...)`
  must keep the `spawn_fn` keyword default; the entrypoint mutates
  `__kwdefaults__`, so a rename or positional change can leave the router
  uninstalled and send workers to the native spawn.
  Verify: `python -c "import inspect, hermes_cli.kanban_db_dispatch as d; print(inspect.signature(d.dispatch_once)); print(d.dispatch_once.__kwdefaults__)"`.
- `_default_spawn(task, workspace, *, board=None)` must remain the native
  fallback and return a PID (or `None`); the native backend calls it directly.
  Verify: `python -c "import inspect; from hermes_cli.kanban_db_dispatch import _default_spawn; print(inspect.signature(_default_spawn))"`.
- `_worker_profile_scope(hermes_home, *, bind_home=True)` must stay a context
  manager that restores home, secret and terminal scopes on success and on
  exception; otherwise profile B's worker reads A's config, `.env`, or terminal
  policy.
  Verify: `pytest -q tests/test_worker_fixes.py::test_real_profile_scope_restores_a_after_b_and_keeps_dispatch_board tests/test_mcp_inheritance.py::test_connection_error_restores_profile_and_workspace`.
- `_resolve_worker_cli_toolsets(hermes_home)` must return the effective CLI
  toolset names (or `None`); a changed return type silently expands a string
  into characters or drops the profile's tools.
  Verify: `python -c "import inspect; from hermes_cli.kanban_db_dispatch import _resolve_worker_cli_toolsets as f; print(inspect.signature(f))"`,
  then `pytest -q tests/test_worker_context.py`.
- `_worker_terminal_timeout_env(max_runtime_seconds, current_timeout)` must
  return a string or `None`; an int return breaks `Popen(env=...)`, and a
  missing override kills long tasks at the default terminal timeout.
  Verify: `python -c "from hermes_cli.kanban_db_dispatch import _worker_terminal_timeout_env as f; v=f(3600,None); assert v is None or isinstance(v,str); print(v)"`.
- Board and log helpers (`kanban_db_path`, `workspaces_root`, `worker_log_path`,
  `_normalize_board_slug`, `get_current_board`,
  `hermes_cli.kanban_worker_log.rotate_worker_log/worker_log_rotation_config`)
  must keep their signatures; otherwise a worker writes to the wrong board DB
  or log rotation stops.
  Verify: `pytest -q tests/test_worker_fixes.py::test_same_task_on_two_boards_has_independent_files_and_cleanup`.
- Profile environment seams (`hermes_cli.profiles.normalize_profile_name`,
  `resolve_profile_env`, `tools.environments.local.served_profile_child_env`,
  `gateway.session_context._VAR_MAP`,
  `hermes_constants.set/reset_hermes_home_override`) are exercised by
  `pytest -q tests/test_worker_fixes.py tests/test_worker_context.py`.

## Error classification boundary

- `agent.error_classifier.classify_api_error` must keep accepting the exception
  plus keyword-only context (`provider`, `model`, `approx_tokens`,
  `context_length`, `num_messages`, `base_url`, `api_key`); the plugin passes
  only provider/model and relies on the defaults. Its result must keep
  `.reason`, `.retryable`, `.error_context` and `.billing_unverified`.
  Verify: `python -c "import inspect; from agent.error_classifier import classify_api_error; print(inspect.signature(classify_api_error))"`.
- `FailoverReason` must still contain the reasons the plugin maps: `auth`,
  `billing`, `rate_limit` and `model_entitlement` rotate; `upstream_blocked` is
  the region correction; `server_error`, `overloaded`, `timeout`,
  `model_not_found` and `format_error` must remain distinct so fallback policy
  stays meaningful; `auth_permanent` and `upstream_rate_limit` must remain
  non-rotating unless the plugin's recovery policy changes. Rotation is plugin
  policy, not core policy.
  Verify: `python -c "from agent.error_classifier import FailoverReason as F; need={'auth','billing','rate_limit','upstream_blocked','server_error','overloaded','timeout','model_not_found','format_error','model_entitlement'}; missing=need-{x.value for x in F}; print(missing); assert not missing"`.
- `transform_api_error_classification` must still receive provider, model,
  status/error fields, error body/object and token/message context, and must
  accept a dict with a valid `FailoverReason`/string plus optional bool hints.
  Workers returns only the typed or exact-match geographic 403 correction
  (`upstream_blocked`, no rotation, fallback) and `None` for everything else;
  any generic reason would suppress core's richer rules.
  Verify: `pytest -q tests/test_provider_errors.py::test_hook_returns_only_the_region_correction tests/test_provider_errors.py::test_hook_declines_everything_else`.
- Keep the region detector narrow (403 plus typed `RegionError` or the exact
  country message); broad free-text matching turns unrelated failures into
  `upstream_blocked` and skips credential rotation.
  Verify: `pytest -q tests/test_provider_errors.py tests/test_registration.py`.

## Credentials, fallback chain and custom providers

- `agent.credential_pool.load_pool(provider)` and the pool's
  `has_credentials()`, `has_available(model=...)`, `next_available_at(model=...)`
  and `select(model=...)` must keep their names and keyword arguments, and the
  returned entry must keep `id` and `runtime_api_key`/`access_token`; a missing
  method can make the plugin fall back to one API key, so cooldowns and rotation
  appear to work but do not.
  Verify: `python -c "import inspect; from agent.credential_pool import load_pool, CredentialPool; print(inspect.signature(load_pool)); print(inspect.signature(CredentialPool.select))"`,
  then `pytest -q tests/test_credentials.py`.
- `mark_exhausted_and_rotate` must keep the keyword arguments `status_code`,
  `error_context`, `api_key_hint`, `credential_id`, `failure_reason` and `model`;
  `model` scopes a 429/entitlement cooldown to one model, and dropping it
  benches the whole key.
  Verify: `python -c "import inspect; from agent.credential_pool import CredentialPool; print(inspect.signature(CredentialPool.mark_exhausted_and_rotate))"`.
- `agent.credential_pool_model_cooldowns.pool_failure_reason` must accept
  `(reason, error_context, billing_unverified)` with a `FailoverReason` and
  return the pool's string vocabulary (`billing`, `rate_limit`, usage-window and
  unverified variants). A mismatch gives wrong cooldown durations even when
  rotation happens.
  Verify: `python -c "import inspect; from agent.credential_pool_model_cooldowns import pool_failure_reason; print(inspect.signature(pool_failure_reason))"`,
  then `pytest -q tests/test_credentials.py::test_billing_uses_the_pool_vocabulary`.
- `hermes_cli.auth.PROVIDER_REGISTRY`,
  `resolve_api_key_provider_credentials`,
  `hermes_cli.runtime_provider.get_compatible_custom_providers` and
  `hermes_cli.config.get_env_value` are the single-key and `custom:*` fallbacks;
  a rename makes configured providers look missing.
  Verify: `pytest -q tests/test_credentials.py` (the custom-provider case is
  parametrized there).
- `hermes_cli.fallback_config.get_fallback_chain` and `resolve_entry_api_key`
  feed `fallbacks.py`; an unreadable chain is logged as `readable: false` and
  workers run single-model, so check the log instead of assuming no fallbacks
  were configured.
  Verify: `pytest -q tests/test_backends.py::test_fallback_chain_status_distinguishes_unreadable_from_unconfigured`.

## Runtime launcher

- `hermes_cli._launchers.runtime_command` must keep `repo_root`, `args` and the
  keyword-only `module`, `code`, `python`, `home` parameters, and return an argv
  that starts the store Python/bootstrap; `worker_runner_command` builds every
  plugin-owned Python subprocess with it.
  Verify: `python -c "import inspect; from hermes_cli._launchers import runtime_command; print(inspect.signature(runtime_command))"`.
- `workers_core.common.worker_runner_command(module)` must keep resolving the
  source root from `hermes_cli.kanban_db.__file__` and inserting the plugin root;
  if `kanban_db` moves or is frozen, the child cannot import `workers_core` or
  core dependencies.
  Verify: run the printed argv with `workers_core.credential_bridge unknown` and
  expect JSON `unknown_action`, not `ImportError`; then run
  `python verification/native_smoke.py` for the MCP handshake.
- Missing launchers have distinct symptoms: OpenCode's hook cannot spawn the
  credential bridge, so requests return HTTP 503 `No available Hermes credential`
  and key rotation stops (the runner may still fall back on the 503 status); the
  capability MCP `hermes-worker` cannot start, so Claude/Codex workers lose
  Hermes tools or abort MCP initialization; `opencode_runner`, `claude_runner`
  and `claude_hook_bridge` also fail to start.
  Verify: `pytest -q tests/test_mcp_inheritance.py tests/test_worker_fixes.py`
  plus the smoke above.

## Settings and configuration

- `hermes_cli.plugins_state.save_plugin_setting(plugin_id, segments, value)`
  must keep the atomic native writer and write under
  `plugins.entries.workers.settings.configuration`; a changed signature breaks
  every save, and a changed path makes loads silently fall back to defaults.
  Verify: `python -c "import inspect; from hermes_cli.plugins_state import save_plugin_setting; print(inspect.signature(save_plugin_setting))"`,
  then `pytest -q tests/test_native_settings.py tests/test_dashboard_api.py`.
- `hermes_cli.config.load_config_readonly()` must stay no-argument and
  read-only; the plugin also uses `load_config`, never mutates either result,
  and a required argument or path change breaks every per-profile settings read.
  Verify: `python -c "import inspect; from hermes_cli.config import load_config_readonly; print(inspect.signature(load_config_readonly))"`.
- `_native_settings` reads exactly
  `plugins.entries.workers.settings.configuration`; confirm the native schema
  still uses that path and `plugin_settings_fields("workers", ...)` stays empty,
  because the visual page owns the form.
  Verify: `pytest -q tests/test_native_settings.py::test_visual_page_saves_without_an_automatic_json_form`.
- `hermes_constants.get_hermes_home`, `get_default_hermes_root`,
  `set_hermes_home_override` and `reset_hermes_home_override` scope every
  per-profile read; a rename breaks profile isolation and legacy migration.
  Verify: `pytest -q tests/test_config.py tests/test_native_settings.py`.

## Model catalog and runtime resolution

- `hermes_cli.inventory.load_picker_context()` and `build_models_payload` must
  keep the `explicit_only`, `include_unconfigured`, `probe_custom_providers`,
  `non_blocking_catalogs` and `refresh` keywords and return
  `providers[].slug/name/models[]`; a changed row shape empties the picker or
  drops models from selection validation.
  Verify: `python -c "import inspect; from hermes_cli.inventory import build_models_payload, load_picker_context; print(inspect.signature(load_picker_context)); print(inspect.signature(build_models_payload))"`,
  then `pytest -q tests/test_native_reuse.py tests/test_backends.py::test_external_workers_share_configured_hermes_provider_models`.
- `hermes_cli.runtime_provider.resolve_runtime_provider` must keep
  `(*, requested, explicit_api_key, explicit_base_url, target_model)` and return
  `base_url`, `api_mode` and optional `extra_headers`/`auth_style`; OpenCode
  overlays and Claude routes are built from it, so a missing base URL aborts
  spawn.
  Verify: `python -c "import inspect; from hermes_cli.runtime_provider import resolve_runtime_provider; print(inspect.signature(resolve_runtime_provider))"`,
  then `pytest -q tests/test_backends.py -k "runtime or provider_overlay"`.
- `hermes_cli.models.opencode_provider_family`,
  `normalize_opencode_base_url` and `opencode_model_api_mode` must keep their
  semantics; `resolve_runtime_provider` uses them to choose the Anthropic
  endpoint and relay path. Workers now trusts that resolver instead of calling
  them, but a silent change still sends the worker to the wrong protocol.
  Verify: `pytest -q tests/test_backends.py::test_claude_code_provider_env_uses_hermes_credential tests/test_opencode_session_header.py`.
- `agent.models_dev.get_model_capabilities` must keep
  `(provider, model, allow_network=False)`; it enriches picker rows.
  `lookup_models_dev_context` also runs during Claude route setup, so a rename
  degrades metadata at best and can abort Claude spawn at worst.
  Verify: `pytest -q tests/test_native_reuse.py tests/test_backends.py::test_claude_code_route_selects_direct_or_builtin`.

## Codex app-server

- `codex exec --json` must keep `turn.failed.error.message`, top-level
  `error.message` and `item.completed.item.type=agent_message`. Workers records
  those messages (plus the classifier verdict) to `<worker log>.status.json` and
  never parses a status out of the text. Codex keeps its own login and fallback.
  Verify: `pytest -q tests/test_codex_runner.py`.
- `agent.transports.codex_app_server.CodexAppServerClient` must keep
  `(codex_bin=..., codex_home=...)`, `initialize(timeout=...)`,
  `request("model/list", {}, timeout=...)` and `close()`; `list_models` calls
  them directly.
  Verify: `python -c "import inspect; from agent.transports.codex_app_server import CodexAppServerClient as C; print(inspect.signature(C)); print(inspect.signature(C.request))"`.
- `model/list` must return `data` rows with `id`, `supportedReasoningEfforts`
  (`reasoningEffort`), `defaultReasoningEffort` and `serviceTiers`. Missing `id`
  rows are dropped, missing efforts disable effort validation, and missing tiers
  hide the priority toggle.
  Verify: `pytest -q tests/test_backend_contract.py tests/test_backend_options.py::test_backend_option_kinds_use_their_own_sources`,
  and inspect `GET /models/codex` against a logged-in Codex.
- `hermes_cli.codex_runtime_switch.get_configured_codex_binary(load_config_readonly())`
  chooses Hermes' configured Codex binary; a rename breaks Codex defaults and
  discovery before the PATH/app-bundle fallback can run.
  Verify: `pytest -q tests/test_backends.py::test_codex_command_is_isolated`.

## Profile context, MCP bridge and Claude helpers

- `agent.runtime_cwd`, `agent.prompt_builder.KANBAN_GUIDANCE`,
  `build_context_files_prompt`, `build_skills_system_prompt`, `load_soul_md`,
  `agent.skill_commands.build_auto_load_prompt/build_preloaded_skills_prompt`,
  `hermes_cli.personality.resolve_ephemeral_system_prompt`,
  `hermes_cli.plugins.format_system_prompt_sections/render_system_prompt_sections`,
  `tools.memory_tool` and `plugins.memory.load_memory_provider` supply profile
  context; a rename loses identity, memory or Skills silently or fails spawn.
  Verify: `pytest -q tests/test_worker_context.py`.
- `hermes_cli.plugins.discover_plugins`,
  `tools.mcp_tool_config._load_mcp_config`,
  `tools.mcp_tool_transport._connect_inputs`,
  `tools.mcp_tool_common.mcp_server_enabled` and
  `toolsets.get_toolset/resolve_toolset` resolve MCP servers inside the active
  profile scope; a change duplicates or omits servers or prevents the bridge
  from starting.
  Verify: `pytest -q tests/test_mcp_inheritance.py`.
- `model_tools.get_tool_definitions/handle_function_call`,
  `_AGENT_LOOP_TOOLS`,
  `agent.transports.hermes_tools_mcp_server._signature_from_schema` and
  `mcp.server.MCPServer` build the `hermes-worker` capability bridge; a
  signature mismatch makes the MCP server fail to start or exposes wrong tool
  schemas.
  Verify: `python verification/native_smoke.py` and
  `pytest -q tests/test_session_search_mcp.py -k "not real_history"`.
- `agent.shell_hooks.ShellHookSpec/run_once`, `hermes_cli.plugins.invoke_hook`,
  `hermes_cli.goals.DEFAULT_MAX_TURNS/run_kanban_goal_loop`,
  `tools.kanban_tools.heartbeat_current_worker_from_env`,
  `_COMMENT_POLL_MIN_INTERVAL_SECONDS`,
  `inject_new_comments_from_env`,
  `hermes_cli.kanban_db.DEFAULT_CLAIM_HEARTBEAT_MAX_STALE_SECONDS` and
  `hermes_cli.kanban_db_connect` power Claude hooks, live comments and goal
  mode; a rename stops heartbeats, comments or goal continuation.
  Verify: `pytest -q tests/test_claude_hooks.py tests/test_claude_comments.py tests/test_backends.py::test_claude_runner_enters_goal_loop_after_success`.
- `utils.is_truthy_value` and the remaining direct imports above are exercised
  by the full suite; run the whole test command, not only the named tests.
