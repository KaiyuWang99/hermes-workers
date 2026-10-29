import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'

const source = fs.readFileSync(new URL('../desktop/plugin.js', import.meta.url), 'utf8')
const imported = source.match(/import\s*\{([\s\S]*?)\}\s*from '@hermes\/plugin-sdk'/)[1]
const sdk = Object.fromEntries(imported.split(',').map(name => name.trim()).filter(Boolean).map(name => [name.trim(), name.trim()]))
Object.assign(sdk, {
  PALETTE_AREA: 'palette',
  ROUTES_AREA: 'routes',
  COMPOSER_AREAS: { contextMenu: 'composer.contextMenu' },
  host: { navigate() {} },
  usePluginI18n: () => key => key,
  jsx: (type, props) => ({ type, props }),
  jsxs: (type, props) => ({ type, props })
})
const context = vm.createContext(sdk)
const executable = source
  .replace(/^import[\s\S]*?from ['"][^'"]+['"]\s*$/gm, '')
  .replace('export default plugin', 'globalThis.plugin = plugin')
vm.runInContext(executable, context)

const contributions = []
const settingsPages = []
context.plugin.register({
  i18n: { register() {}, t: key => key },
  registerMany: items => contributions.push(...items),
  registerSettingsPage: page => settingsPages.push(page),
  rest() { throw new Error('registration must not call the backend') }
})
assert.equal(context.plugin.id, 'workers')
assert.ok(contributions.some(item => item.id === 'page' && item.area === 'routes' && item.data.path === '/workers'))
assert.ok(contributions.every(item => !item.area.startsWith('titleBar.')))
assert.ok(contributions.some(item => item.id === 'open' && item.area === 'palette'))
assert.equal(settingsPages.length, 1)
assert.equal(settingsPages[0].title, 'Workers')
assert.equal(settingsPages[0].render().props.embedded, true)
console.log('PASS: desktop plugin registers official Workers settings and compatible native SDK entries')
