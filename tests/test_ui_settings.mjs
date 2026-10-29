import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import vm from 'node:vm'

const source = await readFile(new URL('../desktop/plugin.js', import.meta.url), 'utf8')
const sdkNames = ['Button', 'Codicon', 'COMPOSER_AREAS', 'DropdownMenu', 'DropdownMenuContent', 'DropdownMenuItem', 'DropdownMenuTrigger', 'ErrorState', 'Loader', 'ListRow', 'Input', 'ModelCatalogMenu', 'ModelMenuCloseContext', 'PALETTE_AREA', 'ROUTES_AREA', 'SearchField', 'Select', 'SelectContent', 'SelectItem', 'SelectTrigger', 'SelectValue', 'StatusDot', 'Switch', 'ToggleRow', 'host', 'usePluginI18n', 'useValue']
const sdk = Object.fromEntries(sdkNames.map(name => [name, { name }]))
const navigations = [], replacements = [], notices = []
let activeProfile = 'test-profile', activeConnection = 'local', locales = {}
let nativeHandler = () => { throw new Error('Unexpected native request') }
const browser = { location: { hash: '#/kanban', replace: hash => { replacements.push(hash); browser.location.hash = hash } } }
Object.assign(sdk, {
  ModelMenuCloseContext: { Provider: { name: 'ModelMenuCloseContext.Provider' } },
  REASONING_EFFORTS: ['minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra', 'future-native-effort'],
  COMPOSER_AREAS: { contextMenu: 'composer.contextMenu' }, PALETTE_AREA: 'palette', ROUTES_AREA: 'routes', usePluginI18n: () => key => locales.en[key] || key, useValue: atom => atom.get(),
  host: { navigate: path => { navigations.push(path); browser.location.hash = `#${path}` }, notify: value => notices.push(value),
    state: { profile: { get: () => activeProfile }, connectionId: { get: () => activeConnection } },
    request: (method, params) => nativeHandler(method, params) }
})

// Commit effects after render, compare dependency arrays, and clean up on changes
// and unmount. Memo/ref/effect/state slots share React's hook ordering.
function createHooks() {
  const slots = []
  let cursor = 0, alive = true, dirty = true, effects = []
  const changed = (a, b) => !a || !b || a.length !== b.length || b.some((value, i) => !Object.is(value, a[i]))
  const slot = kind => { const index = cursor++; slots[index] ??= { kind }; assert.equal(slots[index].kind, kind, 'stable hook ordering'); return slots[index] }
  return {
    get dirty() { return dirty },
    begin() { assert.ok(alive); cursor = 0; dirty = false; effects = [] },
    commit() { for (const run of effects) run(); effects = [] },
    unmount() { alive = false; for (const entry of slots) if (entry.kind === 'effect') entry.cleanup?.() },
    useState(initial) {
      const entry = slot('state')
      if (!entry.initialized) { entry.value = typeof initial === 'function' ? initial() : initial; entry.initialized = true }
      return [entry.value, next => { if (!alive) return; const value = typeof next === 'function' ? next(entry.value) : next; if (!Object.is(value, entry.value)) { entry.value = value; dirty = true } }]
    },
    useRef(initial) { const entry = slot('ref'); return entry.value ??= { current: initial } },
    useMemo(factory, deps) { const entry = slot('memo'); if (!entry.initialized || changed(entry.deps, deps)) { entry.value = factory(); entry.deps = deps; entry.initialized = true } return entry.value },
    useEffect(effect, deps) { const entry = slot('effect'); if (!entry.initialized || changed(entry.deps, deps)) effects.push(() => { entry.cleanup?.(); entry.cleanup = effect(); entry.deps = deps; entry.initialized = true }) }
  }
}
let hookRuntime
const react = Object.fromEntries(['useEffect', 'useMemo', 'useRef', 'useState'].map(name => [name, (...args) => hookRuntime[name](...args)]))
const jsxRuntime = Object.fromEntries(['jsx', 'jsxs'].map(name => [name, (type, props = {}, key) => ({ type, props, key })]))
const context = vm.createContext({ window: browser, console, setTimeout, clearTimeout, URLSearchParams })
const synthetic = (names, values) => new vm.SyntheticModule(names, function () { for (const name of names) this.setExport(name, values[name]) }, { context })
const module = new vm.SourceTextModule(source + '\nexport { WorkersPage, WorkerToggle, HookToggle, Field, ResourceStatus, DiagnosticResult, NativeModelPicker, ModelPicker, OptionSelect, BackendPanel }', { context })
await module.link(specifier => {
  if (specifier === '@hermes/plugin-sdk') return synthetic(sdkNames, sdk)
  if (specifier === 'react') return synthetic(Object.keys(react), react)
  if (specifier === 'react/jsx-runtime') return synthetic(Object.keys(jsxRuntime), jsxRuntime)
  throw new Error('Unexpected import: ' + specifier)
})
await module.evaluate()
const { default: plugin, WorkersPage, WorkerToggle, HookToggle, Field, ResourceStatus, DiagnosticResult, NativeModelPicker, ModelPicker, OptionSelect, BackendPanel } = module.namespace
function withHooks(runtime, callback) { hookRuntime = runtime; runtime.begin(); let value; try { value = callback() } finally { hookRuntime = null } runtime.commit(); return value }
// A harness regression must not silently turn every useEffect into a mount-only effect.
{
  const runtime = createHooks(), events = []
  let dependency = 1
  const render = () => withHooks(runtime, () => { const ref = react.useRef('stable'); react.useEffect(() => { const captured = dependency; events.push(`effect:${captured}`); return () => events.push(`cleanup:${captured}`) }, [dependency]); return ref })
  const ref = render(); assert.equal(render(), ref); dependency = 2; assert.equal(render(), ref); runtime.unmount()
  assert.deepEqual(events, ['effect:1', 'cleanup:1', 'effect:2', 'cleanup:2'])
}
function register(overrides = {}) {
  const registrations = [], settingsPages = []
  plugin.register({ i18n: { register: value => { locales = value }, t: key => locales.en[key] || key }, registerMany: items => registrations.push(...items), registerSettingsPage: page => settingsPages.push(page), rest: async () => { throw new Error('Registration must not fetch') }, ...overrides })
  return { registrations, settingsPages }
}
const { registrations, settingsPages } = register()
assert.equal(plugin.id, 'workers'); assert.equal(settingsPages.length, 1); assert.equal(settingsPages[0].id, 'settings'); assert.equal(settingsPages[0].title, 'Workers'); assert.equal(settingsPages[0].children, undefined)
assert.ok(registrations.every(item => !item.area.startsWith('titleBar.')))
assert.ok(registrations.some(item => item.id === 'page' && item.data.path === '/workers'))
const settingsEntry = settingsPages[0].render(), scopedPage = settingsEntry.type(settingsEntry.props), pageScope = scopedPage.props.scope
assert.equal(settingsEntry.props.embedded, true); assert.equal(scopedPage.type, WorkersPage)

const original = { schema: 'workers-config-v1', config_path: '/display-only', default_worker: 'opencode', routes_by_assignee: { legacy: 'codex' }, unknown_setting: { preserved: true }, workers: {
  opencode: { enabled: true, binary: '/test/opencode', model: 'legacy/retired-open', reasoning_effort: 'high', retained: 'open' },
  codex: { enabled: true, binary: '/test/codex', model: 'retired-codex', reasoning_effort: 'high', service_tier: 'flex', retained: 'codex' },
  'claude-code': { enabled: true, binary: '/test/claude', model: 'custom/retired-claude', reasoning_effort: 'high', sync_hermes_hooks: true, hermes_hook_selection: { approved: true, unknownHook: false }, pin_model_aliases: true, pin_subagent_model: true, retained: 'claude' }
} }
const backendMetadata = [
  { id: 'claude-code', label: 'Claude Code', model_source: 'hermes', option_kind: 'effort' },
  { id: 'opencode', label: 'OpenCode Worker', model_source: 'hermes', option_kind: 'variant' },
  { id: 'codex', label: 'Codex', model_source: 'cli', option_kind: 'reasoning_effort' }
]
const nativeCatalog = [
  { slug: 'anthropic', name: 'Anthropic', aliases: ['claude'], models: ['available-native'] },
  { slug: 'custom:shared', name: 'Shared custom', aliases: ['custom'], models: ['shared-model', 'new-model', 'during-refresh-model'] },
  { slug: 'credential-only', name: 'Credential only', authenticated: true, aliases: [], models: [] },
  { slug: 'empty-native', name: 'Empty provider', aliases: [], models: [] }
]
const hookRows = [
  { id: 'approved', name: 'Approved', command: 'echo approved', matcher: '*.py', approved: true, syncable: true, executable: true, selected: true },
  { id: 'unsupported', name: 'Unsupported', command: 'echo unsupported', approved: true, syncable: false, executable: true, selected: false }
]
const clone = value => JSON.parse(JSON.stringify(value))
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }
function collect(node, predicate, found = [], seen = new Set()) {
  if (!node || typeof node !== 'object' || seen.has(node)) return found
  seen.add(node)
  if (Array.isArray(node)) { for (const item of node) collect(item, predicate, found, seen); return found }
  if (node.type !== undefined && predicate(node)) found.push(node)
  for (const value of Object.values(node.props || {})) collect(value, predicate, found, seen)
  return found
}
const one = (page, predicate, message = 'Expected unique control') => { const found = collect(page, predicate); assert.equal(found.length, 1, message); return found[0] }
const section = (page, title) => one(page, node => node.type === 'section' && collect(node, child => child.type === 'h2' && child.props.children === title).length > 0)
const routingGroup = page => one(page, node => node.type === 'section' && node.props.id === 'workers-routing')
const field = (page, label) => one(page, node => node.type === Field && node.props.label === label).props.children
const backendToggle = (page, name) => one(page, node => node.type === WorkerToggle && node.props.label === locales.en[name])
const saveButton = page => one(page, node => node.type === sdk.Button && node.props.children === locales.en.save)
const resource = (page, id) => one(page, node => node.type === ResourceStatus && node.props.id === id)
const nativeFallbackHints = page => collect(page, node => node.type === 'p' && node.props.children === locales.en.nativeFallback)
const assertNoDefaultWorkerSelector = page => assert.equal(collect(page, node => node.type === sdk.ListRow && node.props.title === 'Default worker').length, 0)
const nativeMenu = (page, backend = 'claudeCode') => {
  const picker = field(section(page, locales.en[backend]), locales.en.model)
  assert.equal(picker.type, NativeModelPicker)
  const rendered = withHooks(createHooks(), () => NativeModelPicker(picker.props))
  return one(rendered, node => node.type === sdk.ModelCatalogMenu).props
}
const currentModel = (page, backend = 'claudeCode') => nativeMenu(page, backend).controller.current.model
const chooseModel = (page, model, provider = 'custom:shared') => nativeMenu(page).controller.select(model, provider)
const openInheritance = page => one(page, node => node.type === 'details' && node.props.id === 'workers-inheritance').props.onToggle({ currentTarget: { open: true } })
function createPage(configuration = original, handlers = {}, options = {}) {
  const calls = [], requests = []; let stored = clone(configuration)
  const descriptors = options.backends || backendMetadata
  const response = () => ({
    configuration: clone(stored),
    status: {
      opencode: { credential: { configured: true, available: true } },
      'claude-code': { credential: { configured: true, available: false } },
      ...(options.status || {})
    },
    backends: clone(descriptors),
    claude_capabilities: clone(options.claudeCapabilities || {})
  })
  const rest = async (path, opts = {}) => {
    calls.push({ path, options: opts })
    const basePath = path.split('?')[0]
    const handler = handlers[path] || handlers[basePath]
    if (handler) return handler(opts, path)
    if (path === '/state') return response()
    if (basePath.startsWith('/options/')) {
      const backend = basePath.slice('/options/'.length)
      const kind = descriptors.find(item => item.id === backend)?.option_kind
      const values = backend === 'opencode'
        ? ['low', 'default', 'medium', 'high', 'future-native-effort']
        : backend === 'claude-code'
          ? ['low', 'medium', 'high', 'xhigh', 'max', 'future-cli-effort']
          : ['minimal', 'low', 'medium', 'high', 'xhigh']
      return { backend, kind, options: values, default: '', source: `${backend}-cli` }
    }
    if (path === '/hooks') return { hermes_hooks: clone(hookRows) }
    if (basePath.startsWith('/models/')) {
      const backend = basePath.slice('/models/'.length)
      if (backend === 'codex') return { models: [{ id: 'available-codex', displayName: 'Available Codex', supportedReasoningEfforts: [{ reasoningEffort: 'high' }], defaultReasoningEffort: 'high' }] }
      return { models: [{ id: 'available-fake', displayName: `Available ${backend}` }] }
    }
    if (path === '/configuration') { stored = clone(opts.body.configuration); return response() }
    throw new Error('Unexpected API path: ' + path)
  }
  nativeHandler = async (method, params) => { requests.push({ method, params }); assert.equal(method, 'model.options'); if (handlers.providers) return handlers.providers(params); return { provider: 'native-current-provider', model: 'native-current-model', providers: clone(nativeCatalog) } }
  const hooks = createHooks(), backendHooks = new Map(), props = { rest, embedded: true, scope: pageScope, ...options }; let page
  const expand = node => {
    if (!node || typeof node !== 'object') return node
    if (Array.isArray(node)) return node.map(expand)
    if (node.type === BackendPanel) {
      const id = node.props.backend.id
      if (!backendHooks.has(id)) backendHooks.set(id, createHooks())
      return expand(withHooks(backendHooks.get(id), () => BackendPanel(node.props)))
    }
    if (!node.props || typeof node.props !== 'object') return node
    return { ...node, props: Object.fromEntries(Object.entries(node.props).map(([key, value]) => [key, expand(value)])) }
  }
  const render = () => { page = expand(withHooks(hooks, () => WorkersPage(props))); return page }
  const settle = async () => {
    for (let i = 0; i < 20; i++) {
      await new Promise(resolve => setImmediate(resolve))
      if (hooks.dirty || [...backendHooks.values()].some(runtime => runtime.dirty) || !page) render()
    }
    return page
  }
  const unmount = () => { hooks.unmount(); for (const runtime of backendHooks.values()) runtime.unmount() }
  return { calls, requests, hooks, unmount, props, response, render, settle, get page() { return page }, get stored() { return stored } }
}

// Configuration is usable even when optional catalogs remain pending.
{
  const pending = deferred(), test = createPage(original, { '/options/claude-code': () => pending.promise, '/models/codex': () => pending.promise })
  test.render(); const page = await test.settle()
  assert.equal(page.props.className, 'flex min-w-0 flex-col pb-6')
  assert.ok(collect(page, node => node.type === WorkerToggle).length >= 3)
  assert.equal(currentModel(page), 'retired-claude')
  assert.equal(test.calls[0].path, '/state'); assert.equal(test.calls.filter(call => call.path === '/hooks').length, 0)
  assert.equal(resource(page, 'codex').props.resource.status, 'loading')
  pending.resolve({ providers: [], models: [] }); await test.settle(); test.unmount()
}
// Credential cards are rendered by the same metadata-driven panel and only
// appear when state contains a credential status for that backend.
{
  const test = createPage(); test.render(); const page = await test.settle()
  for (const [backend, tone, label] of [
    ['openCode', 'good', locales.en.credentialReady],
    ['claudeCode', 'warn', locales.en.credentialCooling]
  ]) {
    const row = one(section(page, locales.en[backend]), node => node.type === sdk.ListRow && node.props.title === locales.en.savedCredentials)
    assert.equal(one(row, node => node.type === sdk.StatusDot).props.tone, tone)
    assert.equal(row.props.action.props.children[1], label)
  }
  assert.equal(collect(section(page, locales.en.codex), node => node.type === sdk.ListRow && node.props.title === locales.en.savedCredentials).length, 0)
  test.unmount()
}
// External backend switches are mutually exclusive. Turning the selected
// backend off restores Hermes Native, and saving drops the legacy route field
// while preserving backend settings and unrelated configuration.
{
  const configuration = clone(original)
  for (const worker of Object.values(configuration.workers)) worker.enabled = false
  const test = createPage(configuration); test.render(); let page = await test.settle()
  assert.equal(Object.hasOwn(locales.en, 'defaultWorker'), false)
  assertNoDefaultWorkerSelector(page)
  assert.equal(nativeFallbackHints(page).length, 1)

  backendToggle(page, 'codex').props.onChange(true); page = test.render(); page = await test.settle()
  assert.equal(backendToggle(page, 'codex').props.checked, true)
  assert.equal(backendToggle(page, 'openCode').props.checked, false)
  assert.equal(backendToggle(page, 'claudeCode').props.checked, false)
  assertNoDefaultWorkerSelector(page)
  assert.equal(nativeFallbackHints(page).length, 0)

  backendToggle(page, 'claudeCode').props.onChange(true); page = test.render(); page = await test.settle()
  assert.equal(backendToggle(page, 'codex').props.checked, false)
  assert.equal(backendToggle(page, 'openCode').props.checked, false)
  assert.equal(backendToggle(page, 'claudeCode').props.checked, true)
  assertNoDefaultWorkerSelector(page)
  assert.equal(nativeFallbackHints(page).length, 0)

  backendToggle(page, 'claudeCode').props.onChange(false); page = test.render(); page = await test.settle()
  for (const name of ['codex', 'openCode', 'claudeCode']) assert.equal(backendToggle(page, name).props.checked, false)
  assert.equal(nativeFallbackHints(page).length, 1)
  saveButton(page).props.onClick(); await test.settle()
  const saved = test.stored
  assert.equal(Object.hasOwn(saved, 'default_worker'), false)
  assert.equal(Object.hasOwn(saved, 'config_path'), false)
  assert.deepEqual(saved.routes_by_assignee, original.routes_by_assignee)
  assert.deepEqual(saved.unknown_setting, original.unknown_setting)
  for (const [backend, worker] of Object.entries(original.workers)) {
    assert.equal(saved.workers[backend].enabled, false)
    for (const [key, value] of Object.entries(worker)) {
      if (key === 'enabled') continue
      if (key === 'hermes_hook_selection') {
        for (const [hook, selected] of Object.entries(value)) assert.deepEqual(saved.workers[backend][key][hook], selected)
      } else assert.deepEqual(saved.workers[backend][key], value, `${backend}.${key} is preserved`)
    }
  }
  const savedConfiguration = test.calls.find(call => call.path === '/configuration').options.body.configuration
  assert.equal(Object.hasOwn(savedConfiguration, 'default_worker'), false)
  assert.equal(Object.hasOwn(savedConfiguration, 'config_path'), false)
  test.unmount()
}
// Routing controls stay together at the top of the section, in the backend
// metadata order, while each enabled backend's existing configuration panel
// follows the complete switch group.
{
  const orderedBackends = ['opencode', 'codex', 'claude-code'].map(id => backendMetadata.find(backend => backend.id === id))
  for (const activeBackend of ['codex', 'claude-code']) {
    const configuration = clone(original)
    for (const worker of Object.values(configuration.workers)) worker.enabled = false
    configuration.workers[activeBackend].enabled = true
    const test = createPage(configuration, {}, { backends: orderedBackends })
    test.render(); const page = await test.settle()

    const routing = routingGroup(page)
    const toggles = collect(routing, node => node.type === WorkerToggle)
    assert.deepEqual(toggles.map(node => node.props.label), orderedBackends.map(backend => backend.label))
    assert.ok(toggles.every(node => node.props.description === undefined))
    assert.equal(collect(routing, node => node.type === 'p' && node.props.children === locales.en.backendHelp).length, 1)
    assert.equal(collect(routing, node => node !== routing && node.type === 'section').length, 0)
    assert.equal(collect(routing, node => node.type === Field).length, 0)

    const active = section(page, orderedBackends.find(backend => backend.id === activeBackend).label)
    const children = page.props.children
    assert.ok(children.indexOf(routing) >= 0)
    assert.ok(children.indexOf(active) > children.indexOf(routing), `${activeBackend} configuration follows the routing group`)
    test.unmount()
  }
}
// Fast tier only overrides the CLI default while enabled. Turning it off
// clears the override instead of synthesizing an unsupported `flex` tier.
{
  const configuration = clone(original)
  for (const worker of Object.values(configuration.workers)) worker.enabled = false
  configuration.workers.codex.enabled = true
  configuration.workers.codex.model = 'fixture-fast-tier-model'
  assert.equal(configuration.workers.codex.service_tier, 'flex')
  const test = createPage(configuration, {
    '/models/codex': () => ({ models: [{ id: 'fixture-fast-tier-model', displayName: 'Fixture model', serviceTiers: [{ id: 'priority' }] }] })
  })
  test.render(); let page = await test.settle()
  let fast = one(page, node => node.type === sdk.ToggleRow && node.props.label === locales.en.fast)
  assert.equal(fast.props.checked, false)

  fast.props.onChange(true); page = test.render()
  fast = one(page, node => node.type === sdk.ToggleRow && node.props.label === locales.en.fast)
  assert.equal(fast.props.checked, true)
  saveButton(page).props.onClick(); page = await test.settle()
  const prioritySnapshot = clone(test.stored)
  assert.equal(prioritySnapshot.workers.codex.service_tier, 'priority')

  fast = one(page, node => node.type === sdk.ToggleRow && node.props.label === locales.en.fast)
  fast.props.onChange(false); page = test.render()
  fast = one(page, node => node.type === sdk.ToggleRow && node.props.label === locales.en.fast)
  assert.equal(fast.props.checked, false)
  saveButton(page).props.onClick(); page = await test.settle()
  const disabledSnapshot = test.stored
  assert.equal(disabledSnapshot.workers.codex.service_tier, '')
  const expectedSnapshot = clone(prioritySnapshot)
  expectedSnapshot.workers.codex.service_tier = ''
  assert.deepEqual(disabledSnapshot, expectedSnapshot, 'disabling Fast changes no other saved configuration')
  assert.notEqual(disabledSnapshot.workers.codex.service_tier, 'flex')
  test.unmount()
}
// Switching to a Codex model without the priority tier clears the hidden tier.
{
  const configuration = clone(original)
  for (const worker of Object.values(configuration.workers)) worker.enabled = false
  configuration.workers.codex.enabled = true
  configuration.workers.codex.model = 'fixture-fast-tier-model'
  configuration.workers.codex.service_tier = 'priority'
  const test = createPage(configuration, {
    '/models/codex': () => ({ models: [
      { id: 'fixture-fast-tier-model', displayName: 'Fast model', serviceTiers: [{ id: 'priority' }] },
      { id: 'fixture-no-tier-model', displayName: 'No-tier model' }
    ] })
  })
  test.render(); let page = await test.settle()
  const codexModel = field(section(page, locales.en.codex), locales.en.model)
  codexModel.props.onChange('fixture-no-tier-model')
  page = test.render(); page = await test.settle()
  saveButton(page).props.onClick(); page = await test.settle()
  assert.equal(test.stored.workers.codex.model, 'fixture-no-tier-model')
  assert.equal(test.stored.workers.codex.service_tier, '')
  test.unmount()
}
// CLI capabilities gray out unsupported Claude switches and drive source choices.
{
  const test = createPage(clone(original), {}, {
    claudeCapabilities: {
      binary: '/test/claude',
      version: '9.9.9',
      features: { strict_mcp_config: false, native_skills: false, settings_sources: false },
      choices: { setting_sources: ['user', 'project'] }
    }
  })
  test.render(); let page = await test.settle()
  openInheritance(page); page = test.render()
  const toggle = id => one(page, node => node.type === sdk.ToggleRow && node.props.id === id)
  assert.equal(toggle('native-claude-mcp').props.disabled, true)
  assert.equal(toggle('native-claude-skills').props.disabled, true)
  assert.equal(toggle('native-claude-source-user').props.disabled, true)
  assert.equal(collect(page, node => node.props?.id === 'native-claude-source-local').length, 0)
  test.unmount()
}
// Saved values the CLI cannot honor are normalized to safe defaults before save.
{
  const configuration = clone(original)
  configuration.workers['claude-code'].strict_mcp_config = true
  configuration.workers['claude-code'].native_skills = false
  configuration.workers['claude-code'].settings_sources = ['user', 'future']
  configuration.workers['claude-code'].permission_mode = 'bypassPermissions'
  const test = createPage(configuration, {}, {
    claudeCapabilities: {
      binary: '/test/claude', version: '9.9.9',
      features: { strict_mcp_config: false, native_skills: false, settings_sources: false, permission_mode: false },
      choices: { setting_sources: ['user', 'project'] }
    }
  })
  test.render(); let page = await test.settle()
  saveButton(page).props.onClick(); page = await test.settle()
  assert.equal(test.stored.workers['claude-code'].strict_mcp_config, false)
  assert.equal(test.stored.workers['claude-code'].native_skills, true)
  assert.deepEqual(test.stored.workers['claude-code'].settings_sources, [])
  assert.equal(test.stored.workers['claude-code'].permission_mode, '')
  test.unmount()
}
// Permission mode is a select driven by CLI help choices.
{
  const configuration = clone(original)
  configuration.workers['claude-code'].permission_mode = 'bypassPermissions'
  const test = createPage(configuration, {}, {
    claudeCapabilities: {
      binary: '/test/claude', version: '9.9.9',
      features: { permission_mode: true },
      choices: { permission_mode: ['auto', 'bypassPermissions'] }
    }
  })
  test.render(); let page = await test.settle()
  const permission = field(page, locales.en.permissionMode)
  assert.equal(permission.type, sdk.Select)
  assert.equal(permission.props.value, 'bypassPermissions')
  permission.props.onValueChange('auto')
  page = test.render(); page = await test.settle()
  saveButton(page).props.onClick(); page = await test.settle()
  assert.equal(test.stored.workers['claude-code'].permission_mode, 'auto')
  test.unmount()
}
// Doctor output shows the CLI feature summary and only reports missing Hermes APIs.
{
  const result = {
    cli: {
      status: 'passed', binary: '/test/claude', version: '9.9.9',
      features: { strict_mcp_config: false, native_skills: true }
    },
    authentication: { status: 'passed' },
    compatibility: { missing_required: [], missing_optional: ['agent.credential_pool'] }
  }
  const rendered = withHooks(createHooks(), () => DiagnosticResult({ result }))
  assert.equal(collect(rendered, node => node.type === sdk.ListRow && node.props.title === locales.en.cliFeatures).length, 1)
  assert.equal(collect(rendered, node => node.type === sdk.ListRow && node.props.title === locales.en.hermesCompatibility).length, 1)
}
// Disabled Codex does not start a CLI catalog query; enabling it loads on demand.
{
  const configuration = clone(original); configuration.workers.codex.enabled = false
  const test = createPage(configuration); test.render(); let page = await test.settle()
  assert.equal(test.calls.filter(call => call.path.startsWith('/models/codex')).length, 0)
  backendToggle(page, 'codex').props.onChange(true); test.render(); page = await test.settle()
  assert.equal(test.calls.filter(call => call.path.startsWith('/models/codex')).length, 1)
  const codexOptionCalls = test.calls.filter(call => call.path.startsWith('/options/codex'))
  assert.equal(codexOptionCalls.length, 1, 'CLI options load only after the model catalog is ready')
  assert.equal(new URLSearchParams(codexOptionCalls[0].path.split('?')[1]).get('refresh'), 'false')
  assert.ok(test.calls.findIndex(call => call.path.startsWith('/models/codex')) < test.calls.findIndex(call => call.path.startsWith('/options/codex')))
  assert.ok(section(page, locales.en.codex)); test.unmount()
}
// A new backend appears from state metadata without adding a UI-specific
// branch, and its CLI models and variant candidates use their own endpoints.
{
  const fake = { id: 'test-engine', label: 'Test Engine', model_source: 'cli', option_kind: 'variant' }
  const configuration = clone(original)
  for (const worker of Object.values(configuration.workers)) worker.enabled = false
  configuration.default_worker = 'native'
  configuration.workers[fake.id] = { enabled: false, binary: '/test/engine', model: 'legacy/private-model', reasoning_effort: 'legacy-variant', retained: true }
  const test = createPage(configuration, {
    '/models/test-engine': () => ({ models: [{ id: 'engine-v2', displayName: 'Engine v2' }] }),
    '/options/test-engine': (_opts, path) => {
      const query = new URLSearchParams(path.split('?')[1])
      return { backend: fake.id, kind: 'variant', options: [query.get('model') === 'engine-v2' ? 'new-variant' : 'legacy-variant'], default: '', source: 'engine-cli' }
    }
  }, {
    backends: [...backendMetadata, fake],
    status: { [fake.id]: { credential: { configured: true, available: true } } }
  })
  test.render(); let page = await test.settle()
  const toggle = one(page, node => node.type === WorkerToggle && node.props.label === fake.label)
  assert.equal(toggle.props.checked, false)
  toggle.props.onChange(true); page = test.render(); page = await test.settle()
  assert.ok(test.calls.some(call => call.path.startsWith('/models/test-engine')))
  assert.equal(test.calls.filter(call => call.path.startsWith('/models/test-engine')).length, 1)
  assert.ok(test.calls.some(call => call.path.startsWith('/options/test-engine?model=legacy%2Fprivate-model')))
  const fakeCredentials = one(section(page, fake.label), node => node.type === sdk.ListRow && node.props.title === locales.en.savedCredentials)
  assert.equal(collect(fakeCredentials, node => node.type === sdk.StatusDot).length, 1)
  let model = field(section(page, fake.label), locales.en.model)
  assert.equal(model.type, ModelPicker)
  assert.equal(model.props.models.some(item => item.id === 'legacy/private-model'), false)
  model.props.onChange('engine-v2'); page = test.render(); page = await test.settle()
  const optionField = one(section(page, fake.label), node => node.type === Field && node.props.label === locales.en.variant)
  const optionControl = optionField.props.children
  assert.equal(optionControl.type, OptionSelect)
  assert.equal(optionField.props.description, `${locales.en.optionUnavailable} legacy-variant`)
  assert.equal(resource(page, 'options-test-engine').props.resource.status, 'ready')
  assert.notEqual(ResourceStatus(resource(page, 'options-test-engine').props).props.description, 'engine-cli')
  assert.ok(test.calls.some(call => call.path.startsWith('/options/test-engine?model=engine-v2')))
  assert.equal(test.calls.filter(call => call.path.startsWith('/models/test-engine')).length, 1, 'changing the worker model reuses the loaded CLI catalog')
  const optionSelect = OptionSelect(optionControl.props)
  assert.ok(collect(optionSelect, node => node.type === sdk.SelectItem && node.props.value === 'new-variant').length)
  optionSelect.props.onValueChange('new-variant'); page = test.render()
  saveButton(page).props.onClick(); await test.settle()
  assert.equal(test.stored.workers[fake.id].reasoning_effort, 'new-variant')
  assert.equal(test.stored.workers[fake.id].retained, true)
  assert.equal(test.stored.unknown_setting.preserved, true)
  test.unmount()
}
// A response for the prior model cannot replace the option list for the
// current model, even when the old request finishes last.
{
  const staleOptions = deferred()
  const test = createPage(original, {
    '/options/claude-code': (_opts, path) => {
      const model = new URLSearchParams(path.split('?')[1]).get('model')
      if (model === 'custom/retired-claude') return staleOptions.promise
      return { backend: 'claude-code', kind: 'effort', options: ['current-model-effort'], default: '', source: model }
    }
  })
  test.render(); let page = await test.settle()
  assert.equal(resource(page, 'options-claude-code').props.resource.status, 'loading')
  chooseModel(page, 'newer-model', 'custom:shared'); page = test.render(); page = await test.settle()
  let control = OptionSelect(field(section(page, locales.en.claudeCode), locales.en.reasoning).props)
  assert.ok(collect(control, node => node.type === sdk.SelectItem && node.props.value === 'current-model-effort').length)
  staleOptions.resolve({ backend: 'claude-code', kind: 'effort', options: ['stale-model-effort'], default: '', source: 'old-model' })
  page = await test.settle()
  control = OptionSelect(field(section(page, locales.en.claudeCode), locales.en.reasoning).props)
  assert.ok(collect(control, node => node.type === sdk.SelectItem && node.props.value === 'current-model-effort').length)
  assert.equal(collect(control, node => node.type === sdk.SelectItem && node.props.value === 'stale-model-effort').length, 0)
  assert.ok(test.calls.some(call => call.path.startsWith('/options/claude-code?model=custom%3Ashared%2Fnewer-model')))
  test.unmount()
}
// A failed resource retries independently and keeps unsaved edits intact.
{
  let fail = true
  const test = createPage(original, { '/models/codex': () => { if (fail) throw new Error('Codex catalog unavailable'); return { models: [{ id: 'recovered-codex' }] } } })
  test.render(); let page = await test.settle()
  assert.equal(resource(page, 'codex').props.resource.status, 'error')
  assert.equal(test.calls.filter(call => call.path.startsWith('/options/codex')).length, 0)
  const codexOptions = field(section(page, locales.en.codex), locales.en.reasoning)
  assert.equal(codexOptions.type, OptionSelect)
  assert.equal(codexOptions.props.disabled, false, 'catalog failure leaves Default selectable')
  chooseModel(page, 'new-model'); page = test.render()
  const before = { state: test.calls.filter(call => call.path === '/state').length, providers: test.requests.length, hooks: test.calls.filter(call => call.path === '/hooks').length }
  fail = false; resource(page, 'codex').props.onRefresh(); test.render(); page = await test.settle()
  assert.equal(resource(page, 'codex').props.resource.status, 'ready')
  assert.equal(test.calls.filter(call => call.path.startsWith('/options/codex')).length, 1)
  assert.equal(currentModel(page), 'new-model')
  assert.equal(test.calls.filter(call => call.path === '/state').length, before.state); assert.equal(test.requests.length, before.providers); assert.equal(test.calls.filter(call => call.path === '/hooks').length, before.hooks)
  saveButton(page).props.onClick(); await test.settle()
  assert.equal(test.stored.workers['claude-code'].model, 'custom:shared/new-model'); assert.equal(test.stored.unknown_setting.preserved, true)
  assert.deepEqual(test.stored.routes_by_assignee, original.routes_by_assignee); assert.equal(Object.hasOwn(test.stored, 'config_path'), false)
  test.unmount()
}
// A CLI option failure is retried without rebuilding its ready model catalog;
// option queries reuse the catalog and never request a refresh themselves.
{
  let fail = true
  const test = createPage(original, {
    '/options/codex': (_opts, path) => {
      assert.equal(new URLSearchParams(path.split('?')[1]).get('refresh'), 'false')
      if (fail) throw new Error('Codex options unavailable')
      return { backend: 'codex', kind: 'reasoning_effort', options: ['medium', 'high'], default: '', source: 'private-source-tag' }
    }
  })
  test.render(); let page = await test.settle()
  assert.equal(test.calls.filter(call => call.path.startsWith('/models/codex')).length, 1)
  assert.equal(test.calls.filter(call => call.path.startsWith('/options/codex')).length, 1)
  assert.equal(resource(page, 'options-codex').props.resource.status, 'error')
  assert.equal(field(section(page, locales.en.codex), locales.en.reasoning).props.disabled, false)
  fail = false
  resource(page, 'options-codex').props.onRefresh(); test.render(); page = await test.settle()
  assert.equal(resource(page, 'options-codex').props.resource.status, 'ready')
  assert.equal(test.calls.filter(call => call.path.startsWith('/models/codex')).length, 1, 'retrying options does not rebuild the catalog')
  assert.equal(test.calls.filter(call => call.path.startsWith('/options/codex')).length, 2)
  test.unmount()
}
// Option and hook failures stay in their own rows, with independent retries.
for (const kind of ['reasoning', 'hooks']) {
  let fail = true
  const handlers = kind === 'reasoning'
    ? { '/options/claude-code': () => { if (fail) throw new Error('Reasoning options unavailable'); return { backend: 'claude-code', kind: 'effort', options: ['high', 'max'], default: '', source: 'test-cli' } } }
    : { '/hooks': () => { if (fail) throw new Error('Hooks inventory unavailable'); return { hermes_hooks: clone(hookRows) } } }
  const test = createPage(original, handlers)
  test.render(); let page = await test.settle()
  if (kind === 'hooks') { openInheritance(page); test.render(); page = await test.settle() }
  const resourceId = kind === 'reasoning' ? 'options-claude-code' : 'hooks'
  const failed = resource(page, resourceId)
  assert.equal(failed.props.resource.status, 'error')
  assert.match(ResourceStatus(failed.props).props.description, /unavailable/)
  assert.ok(saveButton(page), 'optional resource failure leaves configuration usable')
  chooseModel(page, 'new-model'); page = test.render()
  const stateCalls = test.calls.filter(call => call.path === '/state').length
  const codexCalls = test.calls.filter(call => call.path.startsWith('/models/codex')).length
  const nativeCalls = test.requests.length
  fail = false; resource(page, resourceId).props.onRefresh(); test.render(); page = await test.settle()
  assert.equal(resource(page, resourceId).props.resource.status, 'ready')
  assert.equal(currentModel(page), 'new-model')
  assert.equal(test.calls.filter(call => call.path === '/state').length, stateCalls)
  assert.equal(test.calls.filter(call => call.path.startsWith('/models/codex')).length, codexCalls)
  assert.equal(test.requests.length, nativeCalls)
  test.unmount()
}
// No external backend means no optional catalog calls; an open disclosure with
// hook sync disabled also stays idle until that setting is enabled.
{
  const configuration = clone(original)
  for (const entry of Object.values(configuration.workers)) entry.enabled = false
  configuration.default_worker = 'native'; configuration.workers['claude-code'].sync_hermes_hooks = false
  const test = createPage(configuration); test.render(); let page = await test.settle()
  assert.deepEqual(test.calls.map(call => call.path), ['/state']); assert.equal(test.requests.length, 0)
  backendToggle(page, 'claudeCode').props.onChange(true); test.render(); page = await test.settle()
  assert.equal(test.calls.filter(call => call.path.startsWith('/options/claude-code')).length, 1); assert.equal(test.calls.filter(call => call.path.startsWith('/models/codex')).length, 0)
  openInheritance(page); test.render(); page = await test.settle()
  assert.equal(test.calls.filter(call => call.path === '/hooks').length, 0)
  one(page, node => node.type === WorkerToggle && node.props.label === locales.en.syncHooks).props.onChange(true)
  test.render(); await test.settle(); assert.equal(test.calls.filter(call => call.path === '/hooks').length, 1)
  test.unmount()
}
// The host's actual catalog component owns visibility, aliases and refresh.
// Workers only owns its local selection; loading/saving never changes main chat.
{
  const test = createPage(); test.render(); let page = await test.settle()
  assert.equal(test.requests.length, 0, 'the native menu owns its lazy catalog query')
  for (const [backend, provider, model] of [['openCode', 'legacy', 'retired-open'], ['claudeCode', 'custom', 'retired-claude']]) {
    const menu = nativeMenu(page, backend)
    assert.equal(menu.controller.current.provider, provider)
    assert.equal(menu.controller.current.model, model)
    assert.equal(menu.profile, pageScope.profile)
    assert.equal(menu.ownerConnectionId, pageScope.connectionId)
    assert.equal(menu.controller.allowSpeed, false)
    assert.equal(menu.models, undefined, 'no local list to reinsert hidden models')
  }
  const menu = nativeMenu(page)
  await menu.request('model.options', { explicit_only: true })
  assert.equal(test.requests.length, 1)
  const codex = field(section(page, locales.en.codex), locales.en.model); assert.equal(codex.type, ModelPicker); assert.equal(codex.props.value, 'retired-codex'); assert.equal(codex.props.models.some(item => item.id === 'retired-codex'), false)
  openInheritance(page); test.render(); page = await test.settle()
  assert.equal(test.calls.filter(call => call.path === '/hooks').length, 1)
  const hook = one(page, node => node.type === HookToggle && node.props.hook.id === 'approved'), nativeHook = HookToggle(hook.props)
  assert.equal(nativeHook.type, sdk.ToggleRow); assert.match(nativeHook.props.hint, /echo approved/); assert.match(nativeHook.props.hint, /matcher: \*\.py/)
  hook.props.onChange(false); page = test.render()
  one(page, node => (node.type === WorkerToggle || node.type === sdk.ToggleRow) && node.props.label === locales.en.syncHooks).props.onChange(false); page = test.render()
  const inheritanceKeys = ['soul', 'personality', 'user_profile', 'memory', 'external_memory', 'plugin_context', 'project_rules', 'skill_index', 'auto_skills', 'card_skills', 'mcp', 'hermes_tools', 'fallback_models']
  for (const key of inheritanceKeys) {
    const toggle = one(page, node => node.type === sdk.ToggleRow && node.props.id === `inheritance-${key}`)
    assert.equal(toggle.props.checked, true, 'absent inheritance settings preserve existing behavior')
    toggle.props.onChange(false)
    page = test.render()
  }
  assert.equal(one(page, node => node.type === sdk.ToggleRow && node.props.id === 'inheritance-skill_index').props.disabled, true)
  for (const [id, checked] of [['native-claude-mcp', false], ['native-claude-skills', false], ['native-claude-source-user', false], ['native-claude-source-project', false], ['native-claude-source-local', false], ['native-codex-user-config', true], ['native-opencode-user-config', false], ['native-opencode-project-config', false]]) {
    one(page, node => node.type === sdk.ToggleRow && node.props.id === id).props.onChange(checked)
    page = test.render()
  }
  for (const [label, tab] of [['manageSkills', 'skills'], ['manageMcp', 'connectors']]) {
    one(page, node => node.type === sdk.Button && node.props.children === locales.en[label]).props.onClick()
    const [path, query] = navigations.at(-1).split('?'), params = new URLSearchParams(query)
    assert.equal(path, '/capabilities'); assert.equal(params.get('tab'), tab)
    assert.equal(params.get('profile'), pageScope.profile); assert.equal(params.get('connection'), pageScope.connectionId)
  }
  saveButton(page).props.onClick(); await test.settle(); const saved = test.stored
  assert.equal(saved.workers['claude-code'].sync_hermes_hooks, false); assert.equal(saved.workers['claude-code'].hermes_hook_selection.approved, false); assert.equal(saved.workers['claude-code'].hermes_hook_selection.unknownHook, false)
  assert.equal(saved.workers['claude-code'].pin_model_aliases, true); assert.equal(saved.workers['claude-code'].pin_subagent_model, true); assert.equal(saved.workers.opencode.retained, 'open'); assert.equal(saved.workers.codex.retained, 'codex')
  assert.deepEqual(saved.inheritance, Object.fromEntries(inheritanceKeys.map(key => [key, false])))
  assert.equal(saved.workers['claude-code'].strict_mcp_config, true)
  assert.equal(saved.workers['claude-code'].native_skills, false)
  assert.deepEqual(saved.workers['claude-code'].settings_sources, [])
  assert.equal(saved.workers.codex.ignore_user_config, false)
  assert.equal(saved.workers.opencode.inherit_user_config, false)
  assert.equal(saved.workers.opencode.inherit_project_config, false)
  assert.equal(WorkerToggle({ label: 'test', checked: true }).type, sdk.ToggleRow); assert.equal(Field({ label: 'test', children: 'control' }).type, sdk.ListRow)
  test.unmount()
}
// A 1.5 grouped preference remains off until an individual child is chosen.
{
  const configuration = clone(original)
  configuration.inheritance = { profile_context: false, skills: false, future: 'preserve' }
  const test = createPage(configuration); test.render(); let page = await test.settle()
  openInheritance(page); page = test.render()
  for (const key of ['soul', 'personality', 'user_profile', 'memory', 'external_memory', 'plugin_context', 'skill_index', 'auto_skills', 'card_skills']) {
    assert.equal(one(page, node => node.type === sdk.ToggleRow && node.props.id === `inheritance-${key}`).props.checked, false, key)
  }
  one(page, node => node.type === sdk.ToggleRow && node.props.id === 'inheritance-user_profile').props.onChange(true)
  page = test.render()
  assert.equal(one(page, node => node.type === sdk.ToggleRow && node.props.id === 'inheritance-memory').props.checked, false)
  saveButton(page).props.onClick(); await test.settle()
  assert.equal(test.stored.inheritance.user_profile, true)
  assert.equal(test.stored.inheritance.profile_context, false)
  assert.equal(test.stored.inheritance.future, 'preserve')
  test.unmount()
}
// Local diagnostics inspect the unsaved draft, never save it, and keep CLI,
// authentication, and actual task execution as distinct results.
{
  const diagnostics = { backend: 'claude-code', scope: 'draft', cli: { status: 'passed', binary: '/test/claude', version: '1.2.3' }, authentication: { status: 'failed', source: 'hermes-credential-pool' }, execution: { status: 'not_tested' } }
  const test = createPage(original, { '/doctor': options => {
    assert.equal(options.method, 'POST')
    return { ok: false, backend: options.body.backend, diagnostics: clone(diagnostics) }
  } })
  test.render(); let page = await test.settle()
  assert.equal(test.calls.filter(call => call.path === '/doctor').length, 0, 'diagnostics are explicitly requested')
  chooseModel(page, 'new-model'); page = test.render()
  one(page, node => node.type === sdk.ListRow && node.props.id === 'doctor-claude-code').props.action.props.onClick()
  test.render(); page = await test.settle()
  const call = test.calls.find(call => call.path === '/doctor')
  assert.equal(call.options.body.backend, 'claude-code')
  assert.equal(call.options.body.configuration.workers['claude-code'].model, 'custom:shared/new-model')
  assert.equal(Object.hasOwn(call.options.body.configuration, 'config_path'), false)
  assert.equal(test.calls.filter(call => call.path === '/configuration').length, 0, 'diagnosis cannot save the draft')
  assert.equal(test.stored.workers['claude-code'].model, original.workers['claude-code'].model)
  const row = one(page, node => node.type === sdk.ListRow && node.props.id === 'doctor-claude-code')
  assert.equal(row.props.below.type, DiagnosticResult)
  const result = DiagnosticResult(row.props.below.props)
  assert.equal(one(result, node => node.type === sdk.ListRow && node.props.title === locales.en.cliReadiness).props.action, locales.en.checkPassed)
  assert.equal(one(result, node => node.type === sdk.ListRow && node.props.title === locales.en.authentication).props.action, locales.en.checkFailed)
  assert.equal(one(result, node => node.type === sdk.ListRow && node.props.title === locales.en.realTask).props.action, locales.en.taskNotTested)
  chooseModel(page, 'during-refresh-model'); page = test.render()
  const stale = one(page, node => node.type === sdk.ListRow && node.props.id === 'doctor-claude-code')
  assert.equal(stale.props.below, undefined); assert.equal(stale.props.description, locales.en.diagnosticsChanged)
  test.unmount()
}
// Native diagnostics report the Hermes runtime instead of pretending to check a CLI.
{
  const result = { backend: 'native', cli: { status: 'not_applicable' }, authentication: { status: 'not_applicable' }, execution: { status: 'not_tested' } }
  const rendered = withHooks(createHooks(), () => DiagnosticResult({ result }))
  assert.equal(collect(rendered, node => node.type === sdk.ListRow && node.props.title === locales.en.cliReadiness).length, 0)
  assert.equal(collect(rendered, node => node.type === sdk.ListRow && node.props.title === locales.en.authentication).length, 0)
  assert.equal(one(rendered, node => node.type === sdk.ListRow && node.props.title === locales.en.nativeRuntime).props.action, locales.en.checkPassed)
}
// A late configuration refresh cannot replace edits made while it was pending,
// or roll back a newer save response.
{
  let held
  const test = createPage(original, { '/state': () => held ? held.promise : test.response() })
  test.render(); let page = await test.settle()
  held = deferred()
  one(page, node => node.type === sdk.Button && node.props.id === 'workers-global-refresh').props.onClick()
  page = test.render()
  chooseModel(page, 'during-refresh-model'); page = test.render()
  held.resolve({ configuration: clone(original), status: {}, backends: clone(backendMetadata) }); page = await test.settle()
  assert.equal(currentModel(page), 'during-refresh-model')
  held = deferred()
  one(page, node => node.type === sdk.Button && node.props.id === 'workers-global-refresh').props.onClick(); page = test.render()
  saveButton(page).props.onClick(); page = await test.settle()
  held.resolve({ configuration: clone(original), status: {}, backends: clone(backendMetadata) }); page = await test.settle()
  assert.equal(currentModel(page), 'during-refresh-model')
  assert.equal(test.stored.workers['claude-code'].model, 'custom:shared/during-refresh-model')
  test.unmount()
}
// Stale drafts cannot write into a newly selected profile or connection.
{
  const test = createPage(); test.render(); const page = await test.settle(), before = test.calls.filter(call => call.options.method).length
  activeProfile = 'another-profile'; assert.notEqual(settingsEntry.type(settingsEntry.props).key, scopedPage.key); saveButton(page).props.onClick(); assert.equal(test.calls.filter(call => call.options.method).length, before)
  activeProfile = pageScope.profile; activeConnection = 'remote'; assert.notEqual(settingsEntry.type(settingsEntry.props).key, scopedPage.key); saveButton(page).props.onClick(); assert.equal(test.calls.filter(call => call.options.method).length, before)
  activeConnection = pageScope.connectionId; test.unmount()
}
const settingsHref = '/settings?tab=plugins&plugin=workers'
registrations.find(item => item.id === 'open').data.run(); assert.equal(navigations.at(-1), settingsHref)
browser.location.hash = '#/workers'; const redirect = registrations.find(item => item.id === 'page').render()
withHooks(createHooks(), () => redirect.type(redirect.props)); assert.equal(replacements.at(-1), `#${settingsHref}`)
delete browser.location.replace; browser.location.hash = '#/workers'; withHooks(createHooks(), () => redirect.type(redirect.props)); assert.equal(navigations.at(-1), settingsHref)
const old = register({ registerSettingsPage: undefined }); assert.equal(old.registrations.find(item => item.id === 'page').render().props.embedded, false)
old.registrations.find(item => item.id === 'open').data.run(); assert.equal(navigations.at(-1), '/workers'); assert.ok(old.registrations.every(item => !item.area.startsWith('titleBar.')))
{
  const test = createPage(original, {}, { embedded: false }); test.render(); const page = await test.settle()
  assert.equal(page.props.className, 'flex h-full min-h-0 flex-col overflow-y-auto px-5 pb-20 pt-2')
  test.unmount()
  const nativeState = sdk.host.state
  delete sdk.host.state
  const oldest = register({ registerSettingsPage: undefined })
  assert.equal(oldest.registrations.find(item => item.id === 'page').render().type, WorkersPage)
  assert.ok(oldest.registrations.every(item => !item.area.startsWith('titleBar.')))
  sdk.host.state = nativeState
}

// The option row uses backend-provided candidates for both native and CLI
// sources, and a native model-menu preset cannot silently rewrite a saved value.
{
  const test = createPage(original); test.render(); let page = await test.settle()
  for (const [backend, expected] of [['openCode', 'future-native-effort'], ['claudeCode', 'future-cli-effort'], ['codex', 'high']]) {
    const label = backend === 'openCode' ? locales.en.variant : locales.en.reasoning
    const control = field(section(page, locales.en[backend]), label)
    assert.equal(control.type, OptionSelect)
    const select = OptionSelect(control.props)
    assert.equal(select.type, sdk.Select)
    assert.ok(collect(select, node => node.type === sdk.SelectItem && node.props.value === expected).length)
    assert.equal(collect(select, node => node.type === sdk.Input).length, 0)
    select.props.onValueChange(expected); page = test.render()
  }
  const chosenEffort = field(section(page, locales.en.claudeCode), locales.en.reasoning).props.value
  const beforeNotices = notices.length
  nativeMenu(page).controller.setOptions({ effort: 'none' }, { provider: 'custom:shared', model: 'shared-model', isActive: true })
  assert.equal(notices.length, beforeNotices + 1)
  assert.equal(field(section(test.render(), locales.en.claudeCode), locales.en.reasoning).props.value, chosenEffort)
  nativeMenu(page).controller.select('shared-model', 'custom:shared')
  nativeMenu(page).controller.applyPreset({ effort: 'medium' }, { model: 'shared-model', provider: 'custom:shared' })
  page = test.render()
  assert.equal(field(section(page, locales.en.claudeCode), locales.en.reasoning).props.value, chosenEffort)
  saveButton(page).props.onClick(); await test.settle()
  assert.equal(test.stored.workers['claude-code'].reasoning_effort, 'future-cli-effort')
  assert.equal(test.stored.workers.opencode.reasoning_effort, 'future-native-effort')
  assert.ok(test.requests.every(request => request.method === 'model.options'))
  test.unmount()
}
// `default` is a valid OpenCode variant. It remains distinct from the empty
// Default sentinel in both the shared row and the SDK model-menu controller.
{
  const test = createPage(original); test.render(); let page = await test.settle()
  const optionField = one(section(page, locales.en.openCode), node => node.type === Field && node.props.label === locales.en.variant)
  assert.equal(optionField.props.description, locales.en.optionDefaultHelp)
  const select = OptionSelect(optionField.props.children.props)
  const variant = one(select, node => node.type === sdk.SelectItem && node.props.value === 'default')
  assert.equal(variant.props.children, 'default')
  assert.equal(collect(select, node => node.type === sdk.SelectItem && node.props.children === locales.en.reasoningDefault).length, 1)
  assert.equal(one(select, node => node.type === sdk.SelectValue).props.children, 'high')
  select.props.onValueChange(variant.props.value); page = test.render()
  assert.equal(one(OptionSelect(one(section(page, locales.en.openCode), node => node.type === Field && node.props.label === locales.en.variant).props.children.props), node => node.type === sdk.SelectValue).props.children, 'default')
  const beforeNotices = notices.length
  const modelMenu = nativeMenu(page, 'openCode')
  assert.equal(modelMenu.controller.setOptions({ variant: 'default' }, { provider: 'custom:shared', model: 'shared-model', isActive: true }), true)
  assert.equal(notices.length, beforeNotices)
  page = test.render(); saveButton(page).props.onClick(); await test.settle()
  assert.equal(test.stored.workers.opencode.reasoning_effort, 'default')
  assert.equal(test.stored.workers.opencode.model, 'custom:shared/shared-model')
  test.unmount()
}
{
  const configuration = clone(original)
  configuration.workers['claude-code'].reasoning_effort = 'legacy-custom-effort'
  const test = createPage(configuration); test.render(); let page = await test.settle()
  const optionField = one(section(page, locales.en.claudeCode), node => node.type === Field && node.props.label === locales.en.reasoning)
  const control = optionField.props.children
  assert.equal(control.props.value, 'legacy-custom-effort')
  assert.equal(optionField.props.description, `${locales.en.optionUnavailable} legacy-custom-effort`)
  const select = OptionSelect(control.props)
  assert.equal(collect(select, node => node.type === sdk.SelectItem && node.props.value === 'legacy-custom-effort').length, 0)
  saveButton(page).props.onClick(); page = await test.settle()
  assert.equal(test.stored.workers['claude-code'].reasoning_effort, 'legacy-custom-effort')
  const defaultSelect = OptionSelect(field(section(page, locales.en.claudeCode), locales.en.reasoning).props)
  const defaultValue = one(defaultSelect, node => node.type === sdk.SelectItem && node.props.children === locales.en.reasoningDefault).props.value
  defaultSelect.props.onValueChange(defaultValue)
  page = test.render(); saveButton(page).props.onClick(); await test.settle()
  assert.equal(test.stored.workers['claude-code'].reasoning_effort, '')
  test.unmount()
}

// A previously saved Claude default remains visible and unchanged until the
// user picks another value; selecting Default later writes the omitted value.
{
  const configuration = clone(original)
  configuration.workers['claude-code'].reasoning_effort = 'default'
  const test = createPage(configuration); test.render(); let page = await test.settle()
  const control = field(section(page, locales.en.claudeCode), locales.en.reasoning)
  const select = OptionSelect(control.props)
  assert.equal(one(select, node => node.type === sdk.SelectValue).props.children, locales.en.reasoningDefault)
  saveButton(page).props.onClick(); page = await test.settle()
  assert.equal(test.stored.workers['claude-code'].reasoning_effort, 'default')
  const afterSave = OptionSelect(field(section(page, locales.en.claudeCode), locales.en.reasoning).props)
  const defaultValue = one(afterSave, node => node.type === sdk.SelectItem && node.props.children === locales.en.reasoningDefault).props.value
  afterSave.props.onValueChange(defaultValue)
  page = test.render(); saveButton(page).props.onClick(); await test.settle()
  assert.equal(test.stored.workers['claude-code'].reasoning_effort, '')
  test.unmount()
}

// A real backend can keep its old HTTP router after a plugin-only reload.
// Once the backend is updated, the page Refresh must recover choices too.
{
  let endpointReady = false
  const test = createPage(original, { '/options/claude-code': () => {
    if (!endpointReady) throw new Error('404: Not Found')
    return { backend: 'claude-code', kind: 'effort', options: ['high', 'max'], default: '', source: 'test-cli' }
  } })
  test.render(); let page = await test.settle()
  const failedControl = field(section(page, locales.en.claudeCode), locales.en.reasoning)
  assert.equal(failedControl.type, OptionSelect)
  assert.equal(failedControl.props.disabled, false, 'Default stays selectable after an option load failure')
  endpointReady = true
  one(page, node => node.type === sdk.Button && node.props.id === 'workers-global-refresh').props.onClick()
  page = await test.settle()
  const control = field(section(page, locales.en.claudeCode), locales.en.reasoning)
  assert.equal(control.props.disabled, false)
  OptionSelect(control.props).props.onValueChange('max')
  page = test.render(); saveButton(page).props.onClick(); await test.settle()
  assert.equal(test.stored.workers['claude-code'].reasoning_effort, 'max')
  assert.equal(test.calls.filter(call => call.path.startsWith('/options/claude-code')).length, 2)
  const refreshedOptions = test.calls.filter(call => call.path.startsWith('/options/claude-code')).at(-1)
  assert.equal(new URLSearchParams(refreshedOptions.path.split('?')[1]).get('refresh'), 'true')
  assert.ok(test.calls.some(call => call.path === '/models/codex?refresh=true'))
  const codexOptions = test.calls.filter(call => call.path.startsWith('/options/codex'))
  assert.equal(codexOptions.length, 2)
  assert.ok(codexOptions.every(call => new URLSearchParams(call.path.split('?')[1]).get('refresh') === 'false'))
  assert.ok(test.calls.findIndex(call => call.path === '/models/codex?refresh=true') < test.calls.indexOf(codexOptions.at(-1)))
  test.unmount()
}

console.log('PASS: Workers settings resources, native rows, scope guards, persistence and navigation')
