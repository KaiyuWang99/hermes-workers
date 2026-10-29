import {
  Button,
  Codicon,
  COMPOSER_AREAS,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
  ErrorState,
  ListRow,
  ModelCatalogMenu,
  ModelMenuCloseContext,
  Loader,
  PALETTE_AREA,
  ROUTES_AREA,
  SearchField,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  StatusDot,
  ToggleRow,
  host,
  usePluginI18n,
  useValue
} from '@hermes/plugin-sdk'
import { useEffect, useMemo, useRef, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ROUTE = '/workers'
const SETTINGS_ROUTE = '/settings?tab=plugins&plugin=workers'

function WorkersSettingsRedirect() {
  useEffect(() => {
    if (typeof window !== 'undefined' && typeof window.location.replace === 'function') {
      window.location.replace(`#${SETTINGS_ROUTE}`)
    } else host.navigate(SETTINGS_ROUTE)
  }, [])
  return jsx('div', { className: 'flex h-full items-center justify-center', children: jsx(Loader, { label: 'Workers', type: 'lemniscate-bloom' }) })
}

function scopeIsCurrent(scope) {
  return !scope || (host.state.profile.get() === scope.profile && host.state.connectionId.get() === scope.connectionId)
}

function WorkersScopedPage({ rest, embedded }) {
  const profile = useValue(host.state.profile)
  const connectionId = useValue(host.state.connectionId)
  return jsx(WorkersPage, { rest, embedded, scope: { profile, connectionId } }, JSON.stringify([connectionId, profile]))
}

// The desktop loader evaluates plugin.js as a single ESM blob and rejects
// relative imports, so every locale must stay in this file.
const LOCALES = {
  en: {
    title: 'Workers', subtitle: 'Choose Kanban worker backends and models.',
    routing: 'Worker routing', openCode: 'OpenCode Worker', codex: 'Codex', claudeCode: 'Claude Code',
    openCodeHelp: 'Configured Hermes providers and models, with API keys supplied by the Hermes credential pool.',
    codexHelp: 'Codex CLI models with your existing ChatGPT login.', claudeCodeHelp: 'Uses configured Hermes providers and models. Context inheritance is controlled below; Claude Code keeps its native agent loop.',
    nativeFallback: 'All external workers are off. New Cards use the Hermes Native Worker.',
    provider: 'Provider', model: 'Model', reasoning: 'Reasoning', fast: 'Fast tier', credentials: 'Hermes credentials',
    syncHooks: 'Sync Hermes hooks', syncHooksHelp: 'Project approved Hermes shell hooks into new Claude Code Workers.',
    hookList: 'Hermes shell hooks', noHooks: 'No Hermes shell hooks are configured.', hookUnapproved: 'Not approved in Hermes', hookUnsupported: 'No Claude Code equivalent', hookUnavailable: 'Command unavailable',
    credentialReady: 'Configured and available', credentialCooling: 'Configured, currently cooling down',
    credentialMissing: 'No matching Hermes credential',
    save: 'Save configuration', saving: 'Saving…', refresh: 'Refresh',
    saved: 'Workers configuration updated.', searchModels: 'Search models', noModels: 'No models found', refreshing: 'Refreshing…',
    newWorkersOnly: 'Changes apply to newly spawned workers. Running workers are not restarted.',
    loadError: 'Workers configuration could not be loaded.', openPage: 'Workers: Configure backends and models',
    configurationProfile: 'Configuration profile', defaultProfile: 'Default', scopeChanged: 'The active profile or connection changed. Reopen Workers settings before saving.',
    providerCatalog: 'Hermes provider and model catalog', codexCatalog: 'Codex model catalog', savedCredentials: 'Hermes credentials for saved settings',
    resourceLoading: 'Loading…', resourceReady: 'Loaded', retry: 'Retry',
    inheritanceTitle: 'What new workers inherit', inheritanceScope: 'External workers',
    inheritanceHelp: 'These switches apply to new OpenCode, Codex and Claude Code workers. Native workers use Hermes settings.',
    mandatoryTask: 'Task and Kanban protocol', mandatoryTaskHelp: 'The task, workspace and required Hermes task bridge are always included.', alwaysIncluded: 'Always included',
    inheritProfile: 'Profile context', inheritProfileHelp: 'SOUL, personality, user memory and plugin prompt sections.',
    inheritProject: 'Project rules', inheritProjectHelp: 'Project context and rules loaded by Hermes for the workspace.',
    inheritSkills: 'Preloaded Skills', inheritSkillsHelp: 'The Skill index, profile auto-load and Card-selected Skills. Workers can still discover Skills through their tools.',
    inheritMcp: 'Inherited external MCP servers', inheritMcpHelp: 'External MCP servers configured in Hermes. The required task bridge and Claude Code native MCP remain available.',
    nativeManagement: 'Hermes capabilities', manageSkills: 'Manage Skills', manageMcp: 'Manage MCP',
    environmentTitle: 'Environment checks', environmentHelp: 'Check this form without saving or sending a model task. Authentication and CLI readiness are separate checks.',
    checkEnvironment: 'Check environment', checkingEnvironment: 'Checking…', nativeWorker: 'Hermes Native Worker',
    nativeRuntime: 'Hermes runtime', nativeRuntimeHelp: 'Native workers run inside Hermes; there is no external CLI or credential to check.', notApplicable: 'Not applicable',
    cliReadiness: 'CLI readiness', authentication: 'Authentication', realTask: 'Real task execution',
    checkPassed: 'Passed', checkFailed: 'Failed', checkNotChecked: 'Not checked', taskNotTested: 'No real task has been run',
    diagnosticsChanged: 'Settings changed. Run the check again for the current form.',
    hermesCompatibility: 'Hermes API compatibility', missingApis: 'Missing APIs',
    orphanedHookSelections: 'Orphaned hook selections', orphanedHookSelectionsHelp: 'Saved choices no longer match a configured hook:',

    inheritSoul: 'SOUL identity', inheritPersonality: 'Personality and profile instructions', inheritUser: 'User profile', inheritMemory: 'Long-term memory', inheritExternalMemory: 'External memory context', inheritPluginContext: 'Plugin system instructions',
    inheritSkillIndex: 'Skill index', inheritAutoSkills: 'Profile auto-loaded Skills', inheritCardSkills: 'Card-selected Skills', skillIndexHelp: 'Requires inherited Hermes tools and the profile Skills toolset.',
    advancedInheritance: 'Advanced inheritance and CLI settings', inheritTools: 'Hermes tools', inheritToolsHelp: 'Turn off to keep only the required Kanban tools in the bridge. CLI-native tools remain under their own settings.',
    inheritFallbacks: 'Hermes fallback models', inheritFallbacksHelp: 'Claude Code and OpenCode can use the Hermes fallback chain after the primary model fails. Codex does not use this chain.',
    nativeRules: 'CLI-native project rules', nativeRulesHelp: 'Hermes injection switches do not stop a CLI reading its own CLAUDE.md or AGENTS.md. The supported native configuration controls are below.',
    claudeNativeMcp: 'Claude Code native MCP', claudeNativeSkills: 'Claude Code native Skills and commands', claudeSource: 'Claude Code settings source', claudeSourcesHelp: 'Select user, project and local settings files. Managed settings still apply; an unsupported CLI reports an error rather than ignoring a choice.',
    codexUserConfig: 'Read Codex user configuration', codexUserConfigHelp: 'Controls config.toml; the existing Codex login is kept.',
    opencodeUserConfig: 'Copy OpenCode user settings', opencodeUserConfigHelp: 'Controls the user settings copied into the generated Worker config. OpenCode may still load native global settings itself.',
    opencodeProjectConfig: 'Read OpenCode project configuration', opencodeProjectConfigHelp: 'Controls native project-configuration discovery for new OpenCode workers.',

    reasoningNativeHelp: 'Choose a reasoning level; Default lets the CLI decide.', reasoningDefault: 'Default', reasoningUnsupported: 'This reasoning setting is not supported by this CLI.',
    backendHelp: 'Enable this backend to route new tasks to it. Enabling it turns other external backends off; turning it off returns to Hermes Native.', modelCatalog: 'Model catalog', optionCatalog: 'Model options', variant: 'Variant', optionUnavailable: 'Saved option is unavailable for this model:', optionDefaultHelp: 'Default lets the backend decide.'
  },
  zh: {
    title: 'Workers', subtitle: '配置 Kanban Worker 后端和模型。',
    routing: 'Worker 路由', openCode: 'OpenCode Worker', codex: 'Codex', claudeCode: 'Claude Code',
    openCodeHelp: '与 Claude Code 共用已配置 Hermes 凭证的 provider 和模型，API Key 来自 Hermes credential pool。',
    codexHelp: '使用 Codex CLI 模型和现有 ChatGPT 登录。', claudeCodeHelp: '使用已配置 Hermes 凭证的 provider 和模型；上下文由下方继承选项控制，Claude Code 保留原生 Agent Loop。',
    nativeFallback: '所有外部 Worker 均已关闭，新 Card 将使用 Hermes Native Worker。',
    provider: 'Provider', model: '模型', reasoning: '推理强度', fast: '快速层级', credentials: 'Hermes 凭证',
    syncHooks: '同步 Hermes Hooks', syncHooksHelp: '将已批准的 Hermes shell hook 注入新启动的 Claude Code Worker。',
    hookList: 'Hermes Shell Hooks', noHooks: '当前没有配置 Hermes Shell Hook。', hookUnapproved: '未通过 Hermes 授权', hookUnsupported: '没有 Claude Code 对应事件', hookUnavailable: '命令不可用',
    credentialReady: '已配置且可用', credentialCooling: '已配置，当前冷却中',
    credentialMissing: '没有匹配的 Hermes 凭证',
    save: '保存配置', saving: '保存中…', refresh: '刷新',
    saved: 'Workers 配置已更新。', searchModels: '搜索模型', noModels: '没有匹配模型', refreshing: '刷新中…',
    newWorkersOnly: '修改只影响新启动的 Worker，不会重启正在运行的 Worker。',
    loadError: '无法加载 Workers 配置。', openPage: 'Workers：配置后端和模型',
    configurationProfile: '配置所属 Profile', defaultProfile: '默认', scopeChanged: '当前 Profile 或连接已切换，请重新打开 Workers 设置后再保存。',
    providerCatalog: 'Hermes Provider 与模型目录', codexCatalog: 'Codex 模型目录', savedCredentials: '已保存配置的 Hermes 凭证',
    resourceLoading: '加载中…', resourceReady: '已加载', retry: '重试',
    inheritanceTitle: '新 Worker 继承什么', inheritanceScope: '外部 Worker',
    inheritanceHelp: '这些开关影响新启动的 OpenCode、Codex 和 Claude Code Worker；Native Worker 使用 Hermes 原生设置。',
    mandatoryTask: '任务与 Kanban 协议', mandatoryTaskHelp: '始终包含任务、工作目录和必需的 Hermes 任务桥接。', alwaysIncluded: '始终继承',
    inheritProfile: 'Profile 上下文', inheritProfileHelp: 'SOUL、人格、用户记忆和插件提示词。',
    inheritProject: '项目规则', inheritProjectHelp: 'Hermes 为当前工作目录加载的项目上下文和规则。',
    inheritSkills: '预加载 Skills', inheritSkillsHelp: 'Skill 索引、Profile 自动加载和 Card 指定的 Skills；Worker 仍可通过工具主动发现 Skills。',
    inheritMcp: '继承外部 MCP', inheritMcpHelp: 'Hermes 配置中的外部 MCP 服务；必需的任务桥接和 Claude Code 原生 MCP 保持可用。',
    nativeManagement: 'Hermes 能力管理', manageSkills: '管理 Skills', manageMcp: '管理 MCP',
    environmentTitle: '环境检查', environmentHelp: '本地环境检查；未发送模型请求。检查当前表单，不保存配置；凭证和 CLI 可运行性分别检查。',
    checkEnvironment: '检查环境', checkingEnvironment: '检查中…', nativeWorker: 'Hermes Native Worker',
    nativeRuntime: 'Hermes 运行时', nativeRuntimeHelp: 'Native Worker 直接运行在 Hermes 内，没有外部 CLI 或凭证需要检查。', notApplicable: '不适用',
    cliReadiness: 'CLI 可运行性', authentication: '凭证或登录状态', realTask: '真实任务执行',
    checkPassed: '通过', checkFailed: '未通过', checkNotChecked: '未检查', taskNotTested: '未执行真实任务',
    diagnosticsChanged: '配置已修改，请重新检查当前表单。',
    hermesCompatibility: 'Hermes API 兼容性', missingApis: '缺少接口',
    orphanedHookSelections: '孤立的 Hook 选择', orphanedHookSelectionsHelp: '已保存的选择匹配不到任何 Hook：',

    inheritSoul: '身份（SOUL）', inheritPersonality: '人格与 Profile 系统指令', inheritUser: '用户资料（USER）', inheritMemory: '长期记忆（MEMORY）', inheritExternalMemory: '外部记忆上下文', inheritPluginContext: '插件追加的系统提示词',
    inheritSkillIndex: 'Skills 索引', inheritAutoSkills: 'Profile 自动加载的 Skills', inheritCardSkills: '任务 Card 指定的 Skills', skillIndexHelp: '需要继承 Hermes 工具集，且 Profile 已启用 Skills 工具。',
    advancedInheritance: '高级继承与 CLI 设置', inheritTools: '继承 Hermes 工具集', inheritToolsHelp: '关闭后，任务桥接只保留必需的看板工具；CLI 自身工具按其原生配置运行。',
    inheritFallbacks: '沿用 Hermes 备用模型链', inheritFallbacksHelp: '供 Claude Code、OpenCode 在主模型失败后使用；Codex 不使用这条备用链。',
    nativeRules: 'CLI 自身的项目规则', nativeRulesHelp: 'Hermes 注入开关不阻止 CLI 自行读取 CLAUDE.md、AGENTS.md；下面列出各 CLI 支持的原生配置控制。',
    claudeNativeMcp: '继承 Claude Code 原生 MCP', claudeNativeSkills: '启用 Claude Code 原生 Skills 与命令', claudeSource: 'Claude Code 设置文件来源', claudeSourcesHelp: '分别选择 user、project、local 设置文件；受管理设置仍会生效。CLI 不支持指定选项时会明确报错。',
    codexUserConfig: '读取 Codex 用户配置', codexUserConfigHelp: '控制 config.toml 的加载，继续使用已有 Codex 登录。',
    opencodeUserConfig: '复制 OpenCode 用户设置', opencodeUserConfigHelp: '控制复制到 Worker 生成配置的用户设置；OpenCode 自身仍可能读取原生全局设置。',
    opencodeProjectConfig: '读取 OpenCode 项目配置', opencodeProjectConfigHelp: '控制新 OpenCode Worker 的原生项目配置发现。',

    reasoningNativeHelp: '选择推理强度；默认值由 CLI 决定。', reasoningDefault: '默认', reasoningUnsupported: '当前 CLI 不支持此推理设置。',
    backendHelp: '启用此后端后，新任务会路由到它；启用它会关闭其他外部后端，关闭它将返回 Hermes Native。', modelCatalog: '模型目录', optionCatalog: '模型选项', variant: '变体', optionUnavailable: '此模型不支持已保存的选项：', optionDefaultHelp: '默认值由后端决定。'
  },
  'zh-hant': {
    title: 'Workers', subtitle: '設定 Kanban Worker 後端與模型。',
    routing: 'Worker 路由', openCode: 'OpenCode Worker', codex: 'Codex', claudeCode: 'Claude Code',
    openCodeHelp: '與 Claude Code 共用已設定 Hermes 認證的 provider 和模型，API Key 來自 Hermes credential pool。',
    codexHelp: '使用 Codex CLI 模型與現有 ChatGPT 登入。', claudeCodeHelp: '使用已設定 Hermes 認證的 provider 和模型；上下文由下方繼承選項控制，Claude Code 保留原生 Agent Loop。',
    nativeFallback: '所有外部 Worker 均已關閉，新 Card 將使用 Hermes Native Worker。',
    provider: 'Provider', model: '模型', reasoning: '推理強度', fast: '快速層級', credentials: 'Hermes 憑證',
    syncHooks: '同步 Hermes Hooks', syncHooksHelp: '將已核准的 Hermes shell hook 注入新啟動的 Claude Code Worker。',
    hookList: 'Hermes Shell Hooks', noHooks: '目前沒有設定 Hermes Shell Hook。', hookUnapproved: '未通過 Hermes 授權', hookUnsupported: '沒有 Claude Code 對應事件', hookUnavailable: '命令不可用',
    credentialReady: '已設定且可用', credentialCooling: '已設定，目前冷卻中',
    credentialMissing: '沒有匹配的 Hermes 憑證',
    save: '儲存設定', saving: '儲存中…', refresh: '重新整理',
    saved: 'Workers 設定已更新。', searchModels: '搜尋模型', noModels: '沒有符合的模型', refreshing: '重新整理中…',
    newWorkersOnly: '修改只影響新啟動的 Worker，不會重新啟動執行中的 Worker。',
    loadError: '無法載入 Workers 設定。', openPage: 'Workers：設定後端與模型',
    configurationProfile: '設定所屬 Profile', defaultProfile: '預設', scopeChanged: '目前 Profile 或連線已切換，請重新開啟 Workers 設定後再儲存。',
    providerCatalog: 'Hermes Provider 與模型目錄', codexCatalog: 'Codex 模型目錄', savedCredentials: '已儲存設定的 Hermes 認證',
    resourceLoading: '載入中…', resourceReady: '已載入', retry: '重試',
    inheritanceTitle: '新 Worker 繼承什麼', inheritanceScope: '外部 Worker',
    inheritanceHelp: '這些開關影響新啟動的 OpenCode、Codex 和 Claude Code Worker；Native Worker 使用 Hermes 原生設定。',
    mandatoryTask: '任務與 Kanban 協議', mandatoryTaskHelp: '始終包含任務、工作目錄與必要的 Hermes 任務橋接。', alwaysIncluded: '始終繼承',
    inheritProfile: 'Profile 上下文', inheritProfileHelp: 'SOUL、人格、使用者記憶和外掛提示詞。',
    inheritProject: '專案規則', inheritProjectHelp: 'Hermes 為工作目錄載入的專案上下文與規則。',
    inheritSkills: '預載入 Skills', inheritSkillsHelp: 'Skill 索引、Profile 自動載入與 Card 指定的 Skills；Worker 仍可透過工具探索 Skills。',
    inheritMcp: '繼承外部 MCP', inheritMcpHelp: 'Hermes 設定中的外部 MCP 服務；必要的任務橋接與 Claude Code 原生 MCP 保持可用。',
    nativeManagement: 'Hermes 能力管理', manageSkills: '管理 Skills', manageMcp: '管理 MCP',
    environmentTitle: '環境檢查', environmentHelp: '檢查目前表單，不儲存設定，不傳送模型任務；認證與 CLI 可執行性分別檢查。',
    checkEnvironment: '檢查環境', checkingEnvironment: '檢查中…', nativeWorker: 'Hermes Native Worker',
    nativeRuntime: 'Hermes 執行階段', nativeRuntimeHelp: 'Native Worker 直接執行於 Hermes 內，沒有外部 CLI 或憑證需要檢查。', notApplicable: '不適用',
    cliReadiness: 'CLI 可執行性', authentication: '認證或登入狀態', realTask: '真實任務執行',
    checkPassed: '通過', checkFailed: '未通過', checkNotChecked: '未檢查', taskNotTested: '未執行真實任務',
    diagnosticsChanged: '設定已修改，請重新檢查目前表單。',
    hermesCompatibility: 'Hermes API 相容性', missingApis: '缺少介面',
    orphanedHookSelections: '孤立的 Hook 選擇', orphanedHookSelectionsHelp: '已儲存的選擇已對應不到任何 Hook：',

    inheritSoul: '身分（SOUL）', inheritPersonality: '人格與 Profile 系統指令', inheritUser: '使用者資料（USER）', inheritMemory: '長期記憶（MEMORY）', inheritExternalMemory: '外部記憶上下文', inheritPluginContext: '外掛附加系統提示',
    inheritSkillIndex: 'Skills 索引', inheritAutoSkills: 'Profile 自動載入的 Skills', inheritCardSkills: '任務 Card 指定的 Skills', skillIndexHelp: '需要繼承 Hermes 工具集，且 Profile 已啟用 Skills 工具。',
    advancedInheritance: '進階繼承與 CLI 設定', inheritTools: '繼承 Hermes 工具集', inheritToolsHelp: '關閉後只保留必要的看板工具；CLI 原生工具遵循其設定。',
    inheritFallbacks: '沿用 Hermes 備用模型鏈', inheritFallbacksHelp: '用於 Claude Code、OpenCode 主模型失敗後；Codex 不使用此鏈。', nativeRules: 'CLI 原生專案規則', nativeRulesHelp: 'Hermes 注入開關不會阻止 CLI 自行讀取 CLAUDE.md、AGENTS.md；以下為支援的原生控制。',
    claudeNativeMcp: '繼承 Claude Code 原生 MCP', claudeNativeSkills: '啟用 Claude Code 原生 Skills 與命令', claudeSource: 'Claude Code 設定來源', claudeSourcesHelp: '選取 user、project、local 設定；受管理設定仍生效。不支援的選項會報錯。',
    codexUserConfig: '讀取 Codex 使用者設定', codexUserConfigHelp: '控制 config.toml，保留現有登入。', opencodeUserConfig: '複製 OpenCode 使用者設定', opencodeUserConfigHelp: '控制複製到 Worker 設定的內容；CLI 仍可能自行讀取全域設定。', opencodeProjectConfig: '讀取 OpenCode 專案設定', opencodeProjectConfigHelp: '控制新 Worker 的原生專案設定探索。',

    reasoningNativeHelp: '選擇推理強度；預設值由 CLI 決定。', reasoningDefault: '預設', reasoningUnsupported: '目前 CLI 不支援此推理設定。',
    backendHelp: '啟用此後端後，新任務會路由至此；啟用它會關閉其他外部後端，關閉它將返回 Hermes Native。', modelCatalog: '模型目錄', optionCatalog: '模型選項', variant: '變體', optionUnavailable: '此模型不支援已儲存的選項：', optionDefaultHelp: '預設值由後端決定。'
  },
  ja: {
    title: 'Workers', subtitle: 'Kanban Worker のバックエンドとモデルを設定します。',
    routing: 'Worker ルーティング', openCode: 'OpenCode Worker', codex: 'Codex', claudeCode: 'Claude Code',
    openCodeHelp: 'Claude Code と同じ Hermes 認証情報の provider とモデルを使用します。',
    codexHelp: 'Codex CLI モデルと既存の ChatGPT ログインを使用します。', claudeCodeHelp: '設定済み Hermes provider/model を使用します。コンテキストの継承は下の設定に従い、Claude Code はネイティブ Agent Loop を保持します。',
    nativeFallback: '外部 Worker はすべてオフです。新しい Card は Hermes Native Worker を使用します。',
    provider: 'Provider', model: 'モデル', reasoning: '推論強度', fast: '高速レイヤー', credentials: 'Hermes 認証情報',
    syncHooks: 'Hermes Hooks を同期', syncHooksHelp: '承認済みの Hermes shell hook を新しい Claude Code Worker に投影します。',
    hookList: 'Hermes Shell Hooks', noHooks: 'Hermes Shell Hook は設定されていません。', hookUnapproved: 'Hermes で未承認', hookUnsupported: 'Claude Code 対応イベントなし', hookUnavailable: 'コマンドを利用できません',
    credentialReady: '設定済み・利用可能', credentialCooling: '設定済み・クールダウン中',
    credentialMissing: '対応する Hermes 認証情報がありません',
    save: '設定を保存', saving: '保存中…', refresh: '更新',
    saved: 'Workers 設定を更新しました。', searchModels: 'モデルを検索', noModels: 'モデルがありません', refreshing: '更新中…',
    newWorkersOnly: '変更は新規 Worker に適用され、実行中の Worker は再起動しません。',
    loadError: 'Workers 設定を読み込めませんでした。', openPage: 'Workers：バックエンドとモデルを設定',
    configurationProfile: '設定プロファイル', defaultProfile: '既定', scopeChanged: 'プロファイルまたは接続が変更されました。Workers 設定を開き直してから保存してください。',
    providerCatalog: 'Hermes provider・モデル一覧', codexCatalog: 'Codex モデル一覧', savedCredentials: '保存済み設定の Hermes 認証',
    resourceLoading: '読み込み中…', resourceReady: '読み込み済み', retry: '再試行',
    inheritanceTitle: '新しい Worker が継承するもの', inheritanceScope: '外部 Worker',
    inheritanceHelp: '新規 OpenCode、Codex、Claude Code Worker に適用します。Native Worker は Hermes の設定を使用します。',
    mandatoryTask: 'タスクと Kanban プロトコル', mandatoryTaskHelp: 'タスク、作業ディレクトリ、必須の Hermes タスクブリッジは常に含まれます。', alwaysIncluded: '常に継承',
    inheritProfile: 'プロファイルのコンテキスト', inheritProfileHelp: 'SOUL、人格、ユーザーメモリ、プラグインのプロンプト。',
    inheritProject: 'プロジェクトルール', inheritProjectHelp: 'Hermes が作業ディレクトリから読み込むコンテキストとルール。',
    inheritSkills: '事前読み込み Skills', inheritSkillsHelp: 'Skill 一覧、プロファイルの自動読み込み、Card 指定の Skills。Worker はツールで Skills を探索できます。',
    inheritMcp: '外部 MCP の継承', inheritMcpHelp: 'Hermes に設定された外部 MCP サーバー。必須タスクブリッジと Claude Code ネイティブ MCP は引き続き利用できます。',
    nativeManagement: 'Hermes の機能管理', manageSkills: 'Skills を管理', manageMcp: 'MCP を管理',
    environmentTitle: '環境チェック', environmentHelp: '保存やモデルタスクの送信をせず、現在のフォームを検査します。認証と CLI の動作を別々に確認します。',
    checkEnvironment: '環境をチェック', checkingEnvironment: '確認中…', nativeWorker: 'Hermes Native Worker',
    nativeRuntime: 'Hermes ランタイム', nativeRuntimeHelp: 'Native Worker は Hermes 内で実行されるため、外部 CLI や認証情報の確認はありません。', notApplicable: '該当なし',
    cliReadiness: 'CLI の動作', authentication: '認証状態', realTask: '実際のタスク実行',
    checkPassed: '成功', checkFailed: '失敗', checkNotChecked: '未確認', taskNotTested: '実際のタスクは未実行',
    diagnosticsChanged: '設定が変更されました。現在のフォームを再確認してください。',
    hermesCompatibility: 'Hermes API 互換性', missingApis: '不足している API',
    orphanedHookSelections: '孤立した Hook 選択', orphanedHookSelectionsHelp: '保存された選択が設定済み Hook と一致しません：',

    inheritSoul: 'SOUL のアイデンティティ', inheritPersonality: '人格と Profile の指示', inheritUser: 'ユーザー情報（USER）', inheritMemory: '長期メモリ（MEMORY）', inheritExternalMemory: '外部メモリの文脈', inheritPluginContext: 'プラグインのシステム指示',
    inheritSkillIndex: 'Skills インデックス', inheritAutoSkills: 'Profile 自動ロード Skills', inheritCardSkills: 'Card 指定 Skills', skillIndexHelp: 'Hermes ツールの継承と Profile の Skills ツールが必要です。',
    advancedInheritance: '詳細な継承と CLI 設定', inheritTools: 'Hermes ツールを継承', inheritToolsHelp: 'オフでは必須の Kanban ツールだけを残します。CLI 自身のツールは固有の設定に従います。', inheritFallbacks: 'Hermes フォールバックモデル', inheritFallbacksHelp: 'Claude Code と OpenCode の主モデル失敗時に使用します。Codex は対象外です。',
    nativeRules: 'CLI 固有のプロジェクト規則', nativeRulesHelp: 'Hermes の注入設定は CLI 自身による CLAUDE.md や AGENTS.md の読み込みを止めません。対応する固有設定を以下で選べます。', claudeNativeMcp: 'Claude Code 固有の MCP', claudeNativeSkills: 'Claude Code 固有の Skills とコマンド', claudeSource: 'Claude Code 設定ソース', claudeSourcesHelp: 'user、project、local を選択します。管理設定は適用されます。未対応の選択はエラーになります。',
    codexUserConfig: 'Codex ユーザー設定を読む', codexUserConfigHelp: 'config.toml を制御し、既存のログインを維持します。', opencodeUserConfig: 'OpenCode ユーザー設定をコピー', opencodeUserConfigHelp: 'Worker 設定へのコピーを制御します。CLI はグローバル設定を独自に読む場合があります。', opencodeProjectConfig: 'OpenCode プロジェクト設定を読む', opencodeProjectConfigHelp: '新規 Worker の固有プロジェクト設定の探索を制御します。',

    reasoningNativeHelp: '推論レベルを選択します。既定値は CLI に従います。', reasoningDefault: '既定値', reasoningUnsupported: 'この CLI は選択した推論設定に対応していません。',
    backendHelp: 'このバックエンドを有効にすると新しいタスクがここにルーティングされ、他の外部バックエンドはオフになります。オフにすると Hermes Native に戻ります。', modelCatalog: 'モデル一覧', optionCatalog: 'モデルのオプション', variant: 'バリアント', optionUnavailable: '保存済みオプションはこのモデルで使用できません:', optionDefaultHelp: '既定値はバックエンドに従います。'
  }
}


Object.assign(LOCALES.en, {
  featureUnsupported: 'The installed Claude Code CLI does not support this option.',
  cliFeatures: 'CLI features',
  allSupported: 'All supported', unsupported: 'Unsupported',
  permissionMode: 'Permission mode', permissionModeDefault: 'CLI default',
  permissionModeHelp: 'Leave empty to let the Claude CLI choose.',
  permissionModeDanger: 'bypassPermissions disables permission checks; use only in an isolated sandbox.'
})
Object.assign(LOCALES.zh, {
  featureUnsupported: '当前安装的 Claude Code CLI 不支持此选项。',
  cliFeatures: 'CLI 能力',
  allSupported: '全部支持', unsupported: '不支持',
  permissionMode: '权限模式', permissionModeDefault: 'CLI 默认',
  permissionModeHelp: '留空则由 Claude CLI 决定。',
  permissionModeDanger: 'bypassPermissions 会跳过所有权限检查，只应在隔离沙箱中使用。'
})
Object.assign(LOCALES['zh-hant'], {
  featureUnsupported: '目前安裝的 Claude Code CLI 不支援此選項。',
  cliFeatures: 'CLI 能力',
  allSupported: '全部支援', unsupported: '不支援',
  permissionMode: '權限模式', permissionModeDefault: 'CLI 預設',
  permissionModeHelp: '留空則由 Claude CLI 決定。',
  permissionModeDanger: 'bypassPermissions 會跳過所有權限檢查，只應在隔離沙箱中使用。'
})
Object.assign(LOCALES.ja, {
  featureUnsupported: 'インストールされている Claude Code CLI はこのオプションに対応していません。',
  cliFeatures: 'CLI 機能',
  allSupported: 'すべて対応', unsupported: '未対応',
  permissionMode: '権限モード', permissionModeDefault: 'CLI 既定',
  permissionModeHelp: '空欄の場合は Claude CLI に任せます。',
  permissionModeDanger: 'bypassPermissions は権限チェックを省略します。隔離されたサンドボックスでのみ使用してください。'
})
const INHERITANCE_FIELDS = {
  soul: ['inheritSoul', undefined, 'profile_context'],
  personality: ['inheritPersonality', undefined, 'profile_context'],
  user_profile: ['inheritUser', undefined, 'profile_context'],
  memory: ['inheritMemory', undefined, 'profile_context'],
  external_memory: ['inheritExternalMemory', undefined, 'profile_context'],
  plugin_context: ['inheritPluginContext', undefined, 'profile_context'],
  project_rules: ['inheritProject', 'inheritProjectHelp'],
  skill_index: ['inheritSkillIndex', 'skillIndexHelp', 'skills'],
  auto_skills: ['inheritAutoSkills', undefined, 'skills'],
  card_skills: ['inheritCardSkills', undefined, 'skills'],
  mcp: ['inheritMcp', 'inheritMcpHelp'],
  hermes_tools: ['inheritTools', 'inheritToolsHelp'],
  fallback_models: ['inheritFallbacks', 'inheritFallbacksHelp']
}
const ADVANCED_INHERITANCE = new Set(['hermes_tools', 'fallback_models'])
const LEGACY_INHERITANCE = Object.fromEntries(
  Object.entries(INHERITANCE_FIELDS)
    .filter(([, field]) => field[2])
    .map(([key, field]) => [key, field[2]])
)
function inheritanceRows(configuration) {
  const raw = configuration.inheritance || {}
  const known = Object.keys(INHERITANCE_FIELDS)
  const keys = new Set([
    ...known,
    ...Object.entries(raw)
      .filter(([, value]) => typeof value === 'boolean')
      .map(([key]) => key)
  ])
  return [...keys]
    .filter(key => key !== 'profile_context' && key !== 'skills')
    .sort((left, right) => {
      const leftIndex = known.indexOf(left)
      const rightIndex = known.indexOf(right)
      if (leftIndex !== -1 && rightIndex !== -1) return leftIndex - rightIndex
      if (leftIndex !== -1) return -1
      if (rightIndex !== -1) return 1
      return left.localeCompare(right)
    })
}
function inheritanceEnabled(configuration, key) {
  const raw = configuration.inheritance || {}
  if (typeof raw[key] === 'boolean') return raw[key]
  const legacy = LEGACY_INHERITANCE[key] || null
  return legacy && typeof raw[legacy] === 'boolean' ? raw[legacy] : true
}

function errorText(error) {
  return error?.message || String(error)
}

function useOptionalResource(scope) {
  const [resource, setResource] = useState({ data: [], status: 'idle', error: '' })
  const request = useRef(0)
  const alive = useRef(true)
  useEffect(() => {
    alive.current = true
    return () => { alive.current = false; request.current += 1 }
  }, [])
  const load = loader => {
    if (!alive.current || !scopeIsCurrent(scope)) return
    const current = ++request.current
    const isCurrent = () => alive.current && current === request.current && scopeIsCurrent(scope)
    setResource(previous => ({ ...previous, status: 'loading', error: '' }))
    Promise.resolve().then(() => isCurrent() ? loader() : undefined)
      .then(data => { if (isCurrent()) setResource({ data, status: 'ready', error: '' }) })
      .catch(error => { if (isCurrent()) setResource(previous => ({ ...previous, status: 'error', error: errorText(error) })) })
  }
  return [resource, load]
}

function useBackendResource(scope, resourceKey, enabled, refreshToken, loader) {
  const [resource, setResource] = useState({ data: null, status: 'idle', error: '', key: resourceKey, refreshToken })
  const request = useRef(0)
  const alive = useRef(true)
  const currentKey = useRef(resourceKey)
  const currentRefreshToken = useRef(refreshToken)
  const previousKey = useRef(resourceKey)
  const previousRefreshToken = useRef(refreshToken)
  const loaderRef = useRef(loader)
  currentKey.current = resourceKey
  currentRefreshToken.current = refreshToken
  loaderRef.current = loader
  useEffect(() => {
    alive.current = true
    return () => { alive.current = false; request.current += 1 }
  }, [])
  const load = (refresh = true) => {
    const key = resourceKey
    const token = refreshToken
    if (!enabled || !alive.current || !scopeIsCurrent(scope)) return
    const current = ++request.current
    const isCurrent = () => alive.current && current === request.current && key === currentKey.current && token === currentRefreshToken.current && scopeIsCurrent(scope)
    setResource({ data: null, status: 'loading', error: '', key, refreshToken: token })
    Promise.resolve().then(() => isCurrent() ? loaderRef.current(refresh) : undefined)
      .then(data => { if (isCurrent()) setResource({ data, status: 'ready', error: '', key, refreshToken: token }) })
      .catch(error => { if (isCurrent()) setResource({ data: null, status: 'error', error: errorText(error), key, refreshToken: token }) })
  }
  useEffect(() => {
    if (!enabled) {
      request.current += 1
      setResource({ data: null, status: 'idle', error: '', key: resourceKey, refreshToken })
      return
    }
    const identityChanged = previousKey.current !== resourceKey
    const explicitRefresh = previousRefreshToken.current !== refreshToken
    previousKey.current = resourceKey
    previousRefreshToken.current = refreshToken
    load(identityChanged || explicitRefresh)
  }, [enabled, resourceKey, refreshToken])
  const current = resource.key === resourceKey && resource.refreshToken === refreshToken
    ? resource
    : { data: null, status: enabled ? 'loading' : 'idle', error: '', key: resourceKey, refreshToken }
  return [current, load]
}

function setWorker(draft, backend, patch) {
  return {
    ...draft,
    workers: {
      ...draft.workers,
      [backend]: { ...draft.workers[backend], ...patch }
    }
  }
}

function workerModelParts(value) {
  const [provider = '', ...rest] = String(value || '').split('/')
  return { provider, model: rest.join('/') }
}

function NativeModelPicker({ disabled, worker, onChange, options, scope }) {
  const t = usePluginI18n('workers')
  const [open, setOpen] = useState(false)
  const selected = workerModelParts(worker.model)
  const update = patch => { if (!disabled && scopeIsCurrent(scope)) onChange(patch) }
  const controller = {
    allowSpeed: false,
    current: { provider: selected.provider, model: selected.model, effort: worker.reasoning_effort || '', fast: false },
    presetFor: () => ({}),
    // Selecting a model keeps this worker's effort; it must not adopt or write
    // the chat composer's default/preset behind the user's back.
    applyPreset: () => {},
    select: (model, provider) => {
      if (disabled || !scopeIsCurrent(scope)) return false
      update({ model: `${provider}/${model}` })
      return true
    },
    setOptions: (patch, row) => {
      const hasVariant = patch?.variant !== undefined
      const value = hasVariant ? patch.variant : patch?.effort ?? patch?.reasoning_effort
      if (value === undefined || disabled || !scopeIsCurrent(scope)) return false
      const isDefault = value === '' || value === DEFAULT_OPTION_SENTINEL || (!hasVariant && value === 'default')
      if (!isDefault && !(options || []).includes(value)) {
        host.notify({ kind: 'error', message: t('reasoningUnsupported') })
        return false
      }
      update({ model: `${row.provider}/${row.model}`, reasoning_effort: isDefault ? '' : value })
      return true
    }
  }
  return jsxs(DropdownMenu, {
    open,
    onOpenChange: setOpen,
    children: [
      jsx(DropdownMenuTrigger, { asChild: true, children: jsxs(Button, {
        disabled, variant: 'outline', className: 'w-full justify-between gap-2',
        children: [
          jsx('span', { className: 'min-w-0 truncate', children: selected.model ? `${selected.provider}: ${selected.model}` : t('model') }),
          jsx(Codicon, { name: 'chevron-down', size: '0.75rem' })
        ]
      }) }),
      jsx(DropdownMenuContent, { align: 'start', className: 'w-72 p-0', children:
        jsx(ModelMenuCloseContext.Provider, { value: () => setOpen(false), children: jsx(ModelCatalogMenu, {
          controller,
          profile: scope?.profile || 'default',
          ownerConnectionId: scope?.connectionId,
          request: (method, params) => {
            if (!scopeIsCurrent(scope)) return Promise.reject(new Error(t('scopeChanged')))
            return host.request(method, params)
          }
        }) })
      })
    ]
  })
}

const DEFAULT_OPTION_SENTINEL = '__workers_default_option__'
const PERMISSION_DEFAULT_SENTINEL = '__workers_default_permission__'

function OptionSelect({ disabled, value, options = [], legacyDefault = false, onChange }) {
  const t = usePluginI18n('workers')
  const isDefault = !value || (legacyDefault && value === 'default')
  const selected = isDefault ? DEFAULT_OPTION_SENTINEL : value
  return jsxs(Select, {
    disabled,
    value: selected,
    onValueChange: next => onChange(next === DEFAULT_OPTION_SENTINEL ? '' : next),
    children: [
      jsx(SelectTrigger, { children: jsx(SelectValue, { children: isDefault ? t('reasoningDefault') : value }) }),
      jsx(SelectContent, { children: [
        jsx(SelectItem, { value: DEFAULT_OPTION_SENTINEL, children: t('reasoningDefault') }, DEFAULT_OPTION_SENTINEL),
        ...options.filter(option => option && !(legacyDefault && option === 'default')).map(option => jsx(SelectItem, { value: option, children: option }, option))
      ] })
    ]
  })
}

function ModelPicker({ disabled, models, onChange, placeholder, value }) {
  const t = usePluginI18n('workers')
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const shown = useMemo(() => {
    const query = search.trim().toLowerCase()
    return (models || []).filter(item => !query || `${item.id} ${item.displayName || ''} ${item.description || ''}`.toLowerCase().includes(query))
  }, [models, search])
  const current = (models || []).find(item => item.id === value)
  return jsxs(DropdownMenu, {
    open,
    onOpenChange: next => { setOpen(next); if (!next) setSearch('') },
    children: [
      jsx(DropdownMenuTrigger, {
        asChild: true,
        children: jsx(Button, {
          disabled,
          variant: 'outline',
          children: [
            jsx('span', { className: 'min-w-0 truncate', children: current?.displayName || value || placeholder }),
            jsx(Codicon, { name: 'chevron-down', size: '0.75rem' })
          ]
        })
      }),
      jsxs(DropdownMenuContent, {
        align: 'start',
        className: 'w-96 p-2',
        children: [
          jsx(SearchField, {
            'aria-label': t('searchModels'),
            containerClassName: 'mb-2 w-full',
            onChange: setSearch,
            placeholder: t('searchModels'),
            value: search
          }),
          shown.length === 0
            ? jsx('p', { className: 'px-2 py-4 text-center text-xs text-(--ui-text-tertiary)', children: t('noModels') })
            : jsx('div', {
                className: 'max-h-72 overflow-y-auto',
                children: shown.map(item => jsxs(DropdownMenuItem, {
                  className: 'flex flex-col items-start gap-0.5',
                  onSelect: () => { onChange(item.id); setOpen(false) },
                  children: [
                    jsx('span', { className: 'text-xs text-foreground', children: item.displayName || item.id }),
                    item.description && jsx('span', { className: 'text-[0.6875rem] text-(--ui-text-tertiary)', children: item.description })
                  ]
                }, item.id))
              })
        ]
      })
    ]
  })
}

function BackendPanel({ backend, worker, rest, saving, scope, status, refreshToken, onChange }) {
  const t = usePluginI18n('workers')
  const directoryKey = JSON.stringify([
    backend.id, backend.model_source, worker.binary || '', scope?.profile || '', scope?.connectionId || ''
  ])
  const [modelCatalog, refreshModels] = useBackendResource(
    scope,
    directoryKey,
    Boolean(worker.enabled && backend.model_source === 'cli'),
    refreshToken,
    refresh => rest(`/models/${encodeURIComponent(backend.id)}${refresh ? '?refresh=true' : ''}`)
      .then(value => ({ ...value, models: Array.isArray(value?.models) ? value.models : [] }))
  )
  const optionKey = JSON.stringify([
    backend.id, backend.option_kind, worker.model || '', worker.binary || '', scope?.profile || '', scope?.connectionId || ''
  ])
  const optionSourceReady = backend.model_source !== 'cli' || modelCatalog.status === 'ready'
  const [optionCatalog, refreshOptions] = useBackendResource(
    scope,
    optionKey,
    Boolean(worker.enabled && backend.option_kind && optionSourceReady),
    refreshToken,
    refresh => {
      const shouldRefresh = backend.model_source === 'cli' ? false : refresh
      const query = new URLSearchParams({ model: String(worker.model || ''), refresh: shouldRefresh ? 'true' : 'false' })
      return rest(`/options/${encodeURIComponent(backend.id)}?${query}`)
        .then(value => ({
          ...value,
          backend: value?.backend || backend.id,
          kind: value?.kind || backend.option_kind,
          options: Array.isArray(value?.options) ? value.options.filter(option => typeof option === 'string') : [],
          default: typeof value?.default === 'string' ? value.default : '',
          source: String(value?.source || '')
        }))
    }
  )
  const models = modelCatalog.data?.models || []
  const options = optionSourceReady ? optionCatalog.data?.options || [] : []
  const kind = backend.option_kind
  useEffect(() => {
    if (backend.id !== 'codex' || worker.service_tier !== 'priority' || modelCatalog.status !== 'ready') return
    const row = models.find(item => item.id === worker.model)
    if (row && !row.serviceTiers?.some(item => item.id === 'priority')) onChange({ service_tier: '' })
  }, [backend.id, worker.model, worker.service_tier, modelCatalog.status, models])
  const optionLabel = kind === 'variant' ? t('variant') : t('reasoning')
  const currentModel = String(worker.model || '')
  const legacyDefault = backend.id === 'claude-code'
  const isLegacyDefault = legacyDefault && worker.reasoning_effort === 'default'
  const unsupported = Boolean(worker.reasoning_effort && !isLegacyDefault && optionCatalog.status === 'ready' && optionSourceReady && !options.includes(worker.reasoning_effort))
  const optionDescription = unsupported
    ? `${t('optionUnavailable')} ${worker.reasoning_effort}`
    : optionCatalog.data?.message || t('optionDefaultHelp')
  const selectedCodexModel = models.find(item => item.id === currentModel)
  const credential = status?.[backend.id]?.credential
  const credentialLabel = !credential?.configured
    ? t('credentialMissing')
    : credential.available
      ? t('credentialReady')
      : t('credentialCooling')
  const credentialTone = !credential?.configured ? 'bad' : credential.available ? 'good' : 'warn'

  return worker.enabled && jsxs('section', {
    className: 'mt-3 grid gap-3 border-t border-(--ui-stroke-tertiary) pt-4',
    children: [
      jsx('h2', { className: 'font-semibold text-foreground', children: backend.label }),
      backend.model_source === 'hermes' && jsx(Field, {
        label: t('model'),
        children: jsx(NativeModelPicker, {
          disabled: saving,
          worker,
          options,
          scope,
          onChange
        })
      }),
      backend.model_source === 'cli' && jsx(Field, {
        label: t('model'),
        children: jsx(ModelPicker, {
          disabled: saving,
          models,
          onChange: model => {
            if (backend.id !== 'codex') return onChange({ model })
            const next = models.find(item => item.id === model)
            const supportsPriority = Boolean(next?.serviceTiers?.some(item => item.id === 'priority'))
            const keepTier = worker.service_tier === 'priority' && supportsPriority ? 'priority' : ''
            onChange(keepTier === worker.service_tier ? { model } : { model, service_tier: '' })
          },
          placeholder: t('model'),
          value: currentModel
        })
      }),
      backend.model_source === 'cli' && jsx(ResourceStatus, {
        id: backend.id,
        label: `${backend.label} ${t('modelCatalog')}`,
        resource: modelCatalog,
        onRefresh: () => refreshModels(true)
      }),
      kind && jsx(Field, {
        label: optionLabel,
        description: optionDescription,
        children: jsx(OptionSelect, {
          disabled: saving,
          value: worker.reasoning_effort,
          options,
          legacyDefault,
          onChange: reasoning_effort => onChange({ reasoning_effort })
        })
      }),
      kind && (backend.model_source !== 'cli' || modelCatalog.status === 'ready') && jsx(ResourceStatus, {
        id: `options-${backend.id}`,
        label: `${backend.label} ${t('optionCatalog')}`,
        resource: optionCatalog,
        onRefresh: () => refreshOptions(true)
      }),
      credential && jsx(ListRow, {
        title: t('savedCredentials'),
        action: jsxs('span', { className: 'flex items-center gap-2', children: [jsx(StatusDot, { tone: credentialTone }), credentialLabel] })
      }),
      backend.id === 'codex' && selectedCodexModel?.serviceTiers?.some(item => item.id === 'priority') && jsx(ToggleRow, {
        label: t('fast'),
        checked: worker.service_tier === 'priority',
        disabled: saving,
        onChange: checked => onChange({ service_tier: checked ? 'priority' : '' })
      })
    ]
  })
}

function WorkerToggle({ checked, description, disabled, label, onChange }) {
  return jsx(ToggleRow, { checked, description, disabled, label, onChange })
}

function HookToggle({ hook, checked, disabled, onChange }) {
  const t = usePluginI18n('workers')
  const statusLabel = !hook.syncable
    ? t('hookUnsupported')
    : !hook.approved
      ? t('hookUnapproved')
      : !hook.executable
        ? t('hookUnavailable')
        : ''
  return jsx(ToggleRow, {
    label: hook.display_name || hook.name || hook.event,
    checked,
    disabled: disabled || !hook.syncable || !hook.approved || !hook.executable,
    onChange,
    description: statusLabel,
    hint: [hook.command, hook.matcher && `matcher: ${hook.matcher}`].filter(Boolean).join('\n')
  })
}

function Field({ children, label, description }) {
  return jsx(ListRow, { title: label, description, action: children })
}

function ResourceStatus({ id, label, resource, onRefresh }) {
  const t = usePluginI18n('workers')
  return jsx(ListRow, {
    id: `resource-${id}`,
    title: label,
    description: resource.error || resource.data?.message || (resource.status === 'loading' || resource.status === 'idle' ? t('resourceLoading') : t('resourceReady')),
    action: jsx(Button, {
      'aria-label': `${t('refresh')}: ${label}`,
      disabled: resource.status === 'loading',
      onClick: onRefresh,
      size: 'sm', variant: 'outline',
      children: resource.status === 'loading' ? t('resourceLoading') : resource.status === 'error' ? t('retry') : t('refresh')
    })
  })
}

function DiagnosticResult({ result }) {
  const t = usePluginI18n('workers')
  const status = value => t(value === 'passed' ? 'checkPassed' : value === 'failed' ? 'checkFailed' : value === 'not_applicable' ? 'notApplicable' : 'checkNotChecked')
  const compatibility = result.compatibility || {}
  const missingApis = [...(compatibility.missing_required || []), ...(compatibility.missing_optional || [])]
  if (result.backend === 'native') {
    return jsxs('div', {
      children: [
        jsx(ListRow, { title: t('nativeRuntime'), description: t('nativeRuntimeHelp'), action: status('passed') }),
        missingApis.length > 0 && jsx(ListRow, { title: t('hermesCompatibility'), description: missingApis.join(', '), action: t('missingApis') }),
        jsx(ListRow, { title: t('realTask'), action: t('taskNotTested') })
      ]
    })
  }
  const features = result.cli?.features || {}
  const supported = Object.entries(features).filter(([, value]) => value).map(([key]) => key)
  const unsupported = Object.entries(features).filter(([, value]) => !value).map(([key]) => key)
  const featureSummary = Object.keys(features).length === 0
    ? undefined
    : unsupported.length
      ? `${t('unsupported')}: ${unsupported.join(', ')}`
      : t('allSupported')
  return jsxs('div', {
    children: [
      jsx(ListRow, { title: t('cliReadiness'), description: result.cli?.message, hint: [result.cli?.binary, result.cli?.version].filter(Boolean).join(' · '), action: status(result.cli?.status) }),
      Object.keys(features).length > 0 && jsx(ListRow, { title: t('cliFeatures'), description: featureSummary, hint: supported.join(', ') }),
      jsx(ListRow, { title: t('authentication'), description: result.authentication?.message, hint: result.authentication?.source, action: status(result.authentication?.status) }),
      missingApis.length > 0 && jsx(ListRow, { title: t('hermesCompatibility'), description: missingApis.join(', '), action: t('missingApis') }),
      jsx(ListRow, { title: t('realTask'), action: t('taskNotTested') })
    ]
  })
}

function WorkersPage({ rest, embedded = false, scope }) {
  const t = usePluginI18n('workers')
  const [state, setState] = useState(null)
  const [draft, setDraft] = useState(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [inheritanceOpen, setInheritanceOpen] = useState(false)
  const [diagnostics, setDiagnostics] = useState({})
  const [refreshToken, setRefreshToken] = useState(0)
  const [hooks, requestHooks] = useOptionalResource(scope)
  const configurationRequest = useRef(0)
  const diagnosticRequests = useRef({})
  const alive = useRef(true)
  const isCurrent = () => alive.current && scopeIsCurrent(scope)
  const backends = state?.backends || []
  const workerConfig = draft?.workers || {}
  const hasClaudeBackend = backends.some(backend => backend.id === 'claude-code')
  const claudeCode = workerConfig['claude-code'] || {}
  const opencode = workerConfig.opencode || {}
  const codex = workerConfig.codex || {}
  const claudeNeeded = Boolean(hasClaudeBackend && claudeCode.enabled)
  const claudeCapabilities = state?.claude_capabilities || {}
  const claudeFeatures = claudeCapabilities.features || {}
  const claudeSourceChoices = Array.isArray(claudeCapabilities.choices?.setting_sources) && claudeCapabilities.choices.setting_sources.length
    ? claudeCapabilities.choices.setting_sources
    : null
  const claudeSavedSources = Array.isArray(claudeCode.settings_sources) ? claudeCode.settings_sources : []
  const claudeSettingsSources = [...new Set([...(claudeSourceChoices || ['user', 'project', 'local']), ...claudeSavedSources])]
  const claudePermissionChoices = Array.isArray(claudeCapabilities.choices?.permission_mode) ? claudeCapabilities.choices.permission_mode : []
  const claudePermissionMode = String(claudeCode.permission_mode || '')
  const claudeUnsupported = key => claudeFeatures[key] === false
  const hooksNeeded = Boolean(inheritanceOpen && claudeNeeded && claudeCode.sync_hermes_hooks)
  const loadHooks = () => requestHooks(() => rest('/hooks').then(value => ({
    hooks: Array.isArray(value?.hermes_hooks) ? value.hermes_hooks : [],
    orphaned: Array.isArray(value?.orphaned_selections) ? value.orphaned_selections : []
  })))

  const load = () => {
    if (!isCurrent()) return
    const currentRequest = ++configurationRequest.current
    const requestIsCurrent = () => isCurrent() && currentRequest === configurationRequest.current
    const draftAtStart = draft
    const stateAtStart = state
    const draftWasPristine = !draft || draft === state?.configuration
    setLoading(true)
    setError('')
    rest('/state')
      .then(next => {
        if (!requestIsCurrent()) return
        setState(current => current === stateAtStart ? next : current)
        setDraft(current => draftWasPristine && current === draftAtStart ? next.configuration : current)
      })
      .catch(nextError => { if (requestIsCurrent()) setError(errorText(nextError)) })
      .finally(() => { if (requestIsCurrent()) setLoading(false) })
  }

  useEffect(() => {
    alive.current = true
    return () => { alive.current = false; configurationRequest.current += 1 }
  }, [])
  useEffect(load, [])
  useEffect(() => { if (hooksNeeded && hooks.status === 'idle') loadHooks() }, [hooksNeeded])
  useEffect(() => {
    if (!claudeCode.enabled || Object.keys(claudeFeatures).length === 0) return
    const patch = {}
    if (claudeUnsupported('strict_mcp_config') && claudeCode.strict_mcp_config) patch.strict_mcp_config = false
    if (claudeUnsupported('native_skills') && claudeCode.native_skills === false) patch.native_skills = true
    const saved = Array.isArray(claudeCode.settings_sources) ? claudeCode.settings_sources : null
    const choices = claudeCapabilities.choices?.setting_sources
    if (claudeUnsupported('settings_sources') && saved?.length) patch.settings_sources = []
    else if (Array.isArray(choices) && choices.length && saved) {
      const kept = saved.filter(source => choices.includes(source))
      if (kept.length !== saved.length) patch.settings_sources = kept
    }
    const permissionChoices = claudeCapabilities.choices?.permission_mode
    const savedPermission = String(claudeCode.permission_mode || '')
    if (savedPermission && (claudeUnsupported('permission_mode') || (Array.isArray(permissionChoices) && permissionChoices.length && !permissionChoices.includes(savedPermission)))) {
      patch.permission_mode = ''
    }
    if (Object.keys(patch).length) setDraft(current => setWorker(current, 'claude-code', patch))
  }, [claudeCode.enabled, claudeCode.strict_mcp_config, claudeCode.native_skills, claudeCode.settings_sources, claudeCode.permission_mode, claudeCapabilities])

  const refresh = () => {
    load()
    setRefreshToken(current => current + 1)
  }

  if (loading && !draft) {
    return jsx('div', { className: embedded ? 'flex min-h-32 items-center justify-center' : 'flex h-full items-center justify-center', children: jsx(Loader, { label: t('title'), type: 'lemniscate-bloom' }) })
  }
  if (error && !draft) {
    return jsx('div', {
      className: embedded ? 'flex min-h-32 items-center justify-center' : 'flex h-full items-center justify-center px-8',
      children: jsx(ErrorState, {
        title: t('loadError'),
        description: error,
        children: jsx(Button, { onClick: () => load(true), variant: 'secondary', children: t('refresh') })
      })
    })
  }

  const hermesHooks = hooks.data?.hooks || []
  const orphanedHookSelections = hooks.data?.orphaned || []
  const enabledBackends = backends.filter(backend => Boolean(workerConfig[backend.id]?.enabled))
  const hookSelection = {
    ...Object.fromEntries(hermesHooks.map(hook => [hook.id, Boolean(hook.selected)])),
    ...(claudeCode.hermes_hook_selection || {})
  }
  const inheritanceKeys = inheritanceRows(draft || {})
  const mainInheritanceKeys = inheritanceKeys.filter(key => !ADVANCED_INHERITANCE.has(key))
  const advancedInheritanceKeys = inheritanceKeys.filter(key => ADVANCED_INHERITANCE.has(key))

  const toggleBackend = (backend, checked) => {
    setDraft(current => backends.reduce(
      (next, { id }) => setWorker(next, id, { enabled: checked && id === backend }),
      current
    ))
  }

  const save = () => {
    if (!isCurrent()) {
      setError(t('scopeChanged'))
      host.notify({ kind: 'error', message: t('scopeChanged') })
      return
    }
    setSaving(true)
    setError('')
    // Retired fields stay out of the save payload; the backend also drops them.
    const { config_path, default_worker, ...baseConfiguration } = draft
    const claudeWorker = draft.workers['claude-code']
    const configuration = {
      ...baseConfiguration,
      workers: {
        ...draft.workers,
        ...(hasClaudeBackend && claudeWorker ? { 'claude-code': { ...claudeWorker, hermes_hook_selection: hookSelection } } : {})
      }
    }
    rest('/configuration', { method: 'PUT', body: { configuration } })
      .then(next => {
        if (!isCurrent()) return
        configurationRequest.current += 1
        setLoading(false)
        setState(next)
        setDraft(next.configuration)
        host.notify({ kind: 'success', message: t('saved') })
      })
      .catch(nextError => {
        if (!isCurrent()) return
        const message = errorText(nextError)
        setError(message)
        host.notify({ kind: 'error', message })
      })
      .finally(() => { if (isCurrent()) setSaving(false) })
  }

  const checkEnvironment = backend => {
    if (!isCurrent()) return
    const currentRequest = (diagnosticRequests.current[backend] || 0) + 1
    diagnosticRequests.current[backend] = currentRequest
    const requestIsCurrent = () => isCurrent() && diagnosticRequests.current[backend] === currentRequest
    const checkedDraft = draft
    setDiagnostics(current => ({ ...current, [backend]: { status: 'loading', error: '', draft: checkedDraft } }))
    const { config_path, ...configuration } = draft
    rest('/doctor', { method: 'POST', body: { backend, configuration } })
      .then(value => { if (requestIsCurrent()) setDiagnostics(current => ({ ...current, [backend]: { status: 'ready', result: value.diagnostics, error: '', draft: checkedDraft } })) })
      .catch(nextError => { if (requestIsCurrent()) setDiagnostics(current => ({ ...current, [backend]: { status: 'error', error: errorText(nextError), draft: checkedDraft } })) })
  }

  const openCapabilities = tab => {
    if (!isCurrent()) return
    const query = new URLSearchParams({ tab })
    if (scope) {
      query.set('profile', scope.profile)
      if (scope.connectionId) query.set('connection', scope.connectionId)
    }
    host.navigate(`/capabilities?${query}`)
  }

  return jsxs('div', {
    className: embedded ? 'flex min-w-0 flex-col pb-6' : 'flex h-full min-h-0 flex-col overflow-y-auto px-5 pb-20 pt-2',
    children: [
      jsxs('header', {
        className: 'mb-4 flex flex-col gap-1',
        children: [
          jsx('h1', { className: 'text-base font-semibold text-foreground', children: t('title') }),
          jsx('p', { className: 'text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)', children: t('subtitle') })
        ]
      }),

      scope && jsx(ListRow, { title: t('configurationProfile'), action: scope.profile || t('defaultProfile') }),

      jsxs('section', {
        id: 'workers-routing',
        children: [
          jsx('h2', { className: 'font-semibold text-foreground', children: t('routing') }),
          jsx('p', { className: 'py-3 text-xs leading-5 text-(--ui-text-tertiary)', children: t('backendHelp') }),
          ...backends.map(backend => jsx(WorkerToggle, {
            checked: Boolean(workerConfig[backend.id]?.enabled),
            disabled: saving,
            label: backend.label,
            onChange: checked => toggleBackend(backend.id, checked)
          }, backend.id)),
          enabledBackends.length === 0 && jsx('p', { className: 'py-3 text-xs leading-5 text-(--ui-text-tertiary)', children: t('nativeFallback') })
        ]
      }),
      ...backends.map(backend => jsx(BackendPanel, {
        key: backend.id,
        backend,
        worker: workerConfig[backend.id] || { enabled: false, model: '', reasoning_effort: '' },
        rest, saving, scope, status: state?.status, refreshToken,
        onChange: patch => setDraft(current => setWorker(current, backend.id, patch))
      }, backend.id)),

      jsxs('details', {
        id: 'workers-inheritance',
        className: 'mt-5 border-t border-(--ui-stroke-tertiary) pt-4',
        onToggle: event => setInheritanceOpen(event.currentTarget.open),
        children: [
          jsx('summary', { className: 'cursor-pointer font-semibold text-foreground', children: t('inheritanceTitle') }),
          jsx(ListRow, { title: t('inheritanceScope'), description: t('inheritanceHelp') }),
          jsx(ListRow, { title: t('mandatoryTask'), description: t('mandatoryTaskHelp'), action: t('alwaysIncluded') }),
          ...mainInheritanceKeys.map(key => {
            const [label, description] = INHERITANCE_FIELDS[key] || [key]
            return jsx(ToggleRow, {
              id: `inheritance-${key}`,
              label: t(label), description: description ? t(description) : undefined,
              disabled: saving || (key === 'skill_index' && !inheritanceEnabled(draft, 'hermes_tools')),
              checked: inheritanceEnabled(draft, key),
              onChange: checked => setDraft(current => ({ ...current, inheritance: { ...current.inheritance, [key]: checked } }))
            }, key)
          }),
          jsxs('details', {
            children: [
              jsx('summary', { className: 'cursor-pointer py-3 font-semibold text-foreground', children: t('advancedInheritance') }),
              ...advancedInheritanceKeys.map(key => {
                const [label, description] = INHERITANCE_FIELDS[key] || [key]
                return jsx(ToggleRow, {
                  id: `inheritance-${key}`, label: t(label), description: description ? t(description) : undefined, disabled: saving,
                  checked: inheritanceEnabled(draft, key),
                  onChange: checked => setDraft(current => ({ ...current, inheritance: { ...current.inheritance, [key]: checked } }))
                }, key)
              }),
              jsx(ListRow, { title: t('nativeRules'), description: t('nativeRulesHelp') }),
              claudeCode.enabled && jsx(ToggleRow, {
                id: 'native-claude-mcp', label: t('claudeNativeMcp'),
                description: claudeUnsupported('strict_mcp_config') ? t('featureUnsupported') : undefined,
                disabled: saving || claudeUnsupported('strict_mcp_config'),
                checked: !claudeCode.strict_mcp_config,
                onChange: checked => setDraft(current => setWorker(current, 'claude-code', { strict_mcp_config: !checked }))
              }),
              claudeCode.enabled && jsx(ToggleRow, {
                id: 'native-claude-skills', label: t('claudeNativeSkills'),
                description: claudeUnsupported('native_skills') ? t('featureUnsupported') : undefined,
                disabled: saving || claudeUnsupported('native_skills'),
                checked: claudeCode.native_skills !== false,
                onChange: checked => setDraft(current => setWorker(current, 'claude-code', { native_skills: checked }))
              }),
              claudeCode.enabled && jsx(ListRow, { title: t('claudeSource'), description: t('claudeSourcesHelp') }),
              ...(claudeCode.enabled ? claudeSettingsSources.map(source => {
                const sourceUnsupported = claudeSourceChoices !== null && !claudeSourceChoices.includes(source)
                return jsx(ToggleRow, {
                  id: `native-claude-source-${source}`, label: `${t('claudeSource')}: ${source}`,
                  description: claudeUnsupported('settings_sources') || sourceUnsupported ? t('featureUnsupported') : undefined,
                  disabled: saving || claudeUnsupported('settings_sources') || sourceUnsupported,
                  checked: !Array.isArray(claudeCode.settings_sources) || claudeCode.settings_sources.includes(source),
                  onChange: checked => setDraft(current => {
                    const existing = current.workers['claude-code'].settings_sources
                    const selected = Array.isArray(existing) ? existing : claudeSettingsSources
                    return setWorker(current, 'claude-code', { settings_sources: claudeSettingsSources.filter(item => item === source ? checked : selected.includes(item)) })
                  })
                }, source)
              }) : []),
              claudeCode.enabled && jsx(Field, {
                label: t('permissionMode'),
                description: [
                  claudeUnsupported('permission_mode') ? t('featureUnsupported') : '',
                  t('permissionModeHelp'),
                  claudePermissionMode === 'bypassPermissions' ? t('permissionModeDanger') : ''
                ].filter(Boolean).join(' '),
                children: jsxs(Select, {
                  disabled: saving || claudeUnsupported('permission_mode'),
                  value: claudePermissionMode || PERMISSION_DEFAULT_SENTINEL,
                  onValueChange: next => setDraft(current => setWorker(current, 'claude-code', {
                    permission_mode: next === PERMISSION_DEFAULT_SENTINEL ? '' : next
                  })),
                  children: [
                    jsx(SelectTrigger, { children: jsx(SelectValue, { children: claudePermissionMode || t('permissionModeDefault') }) }),
                    jsx(SelectContent, { children: [
                      jsx(SelectItem, { value: PERMISSION_DEFAULT_SENTINEL, children: t('permissionModeDefault') }, PERMISSION_DEFAULT_SENTINEL),
                      ...claudePermissionChoices.map(mode => jsx(SelectItem, { value: mode, children: mode }, mode))
                    ] })
                  ]
                })
              }),
              codex.enabled && jsx(ToggleRow, {
                id: 'native-codex-user-config', label: t('codexUserConfig'), description: t('codexUserConfigHelp'), disabled: saving,
                checked: codex.ignore_user_config === false,
                onChange: checked => setDraft(current => setWorker(current, 'codex', { ignore_user_config: !checked }))
              }),
              opencode.enabled && jsx(ToggleRow, {
                id: 'native-opencode-user-config', label: t('opencodeUserConfig'), description: t('opencodeUserConfigHelp'), disabled: saving,
                checked: opencode.inherit_user_config !== false,
                onChange: checked => setDraft(current => setWorker(current, 'opencode', { inherit_user_config: checked }))
              }),
              opencode.enabled && jsx(ToggleRow, {
                id: 'native-opencode-project-config', label: t('opencodeProjectConfig'), description: t('opencodeProjectConfigHelp'), disabled: saving,
                checked: opencode.inherit_project_config !== false,
                onChange: checked => setDraft(current => setWorker(current, 'opencode', { inherit_project_config: checked }))
              })
            ]
          }),
          jsx(WorkerToggle, {
            checked: Boolean(claudeCode.sync_hermes_hooks),
            description: t('syncHooksHelp'), disabled: saving || !claudeCode.enabled,
            label: t('syncHooks'),
            onChange: checked => setDraft(current => setWorker(current, 'claude-code', { sync_hermes_hooks: checked }))
          }),
          hooksNeeded && jsx(ResourceStatus, { id: 'hooks', label: t('hookList'), resource: hooks, onRefresh: loadHooks }),
          hooksNeeded && hooks.status === 'ready' && hermesHooks.length === 0 && jsx(ListRow, { title: t('noHooks') }),
          hooksNeeded && orphanedHookSelections.length > 0 && jsx(ListRow, {
            title: t('orphanedHookSelections'),
            description: `${t('orphanedHookSelectionsHelp')} ${orphanedHookSelections.join(', ')}`
          }),
          hooksNeeded && hermesHooks.map(hook => jsx(HookToggle, {
            checked: Object.prototype.hasOwnProperty.call(hookSelection, hook.id) ? Boolean(hookSelection[hook.id]) : Boolean(hook.selected),
            disabled: saving, hook,
            onChange: checked => setDraft(current => {
              const saved = current.workers['claude-code']?.hermes_hook_selection || {}
              const defaults = Object.fromEntries(hermesHooks.map(item => [item.id, Boolean(item.selected)]))
              return setWorker(current, 'claude-code', { hermes_hook_selection: { ...defaults, ...saved, [hook.id]: checked } })
            })
          }, hook.id)),
          jsx(ListRow, {
            title: t('nativeManagement'),
            action: jsxs('div', { className: 'flex flex-wrap gap-2', children: [
              jsx(Button, { onClick: () => openCapabilities('skills'), size: 'sm', variant: 'outline', children: t('manageSkills') }),
              jsx(Button, { onClick: () => openCapabilities('connectors'), size: 'sm', variant: 'outline', children: t('manageMcp') })
            ] })
          })
        ]
      }),

      jsxs('section', {
        className: 'mt-5 border-t border-(--ui-stroke-tertiary) pt-4',
        'aria-label': t('environmentTitle'),
        children: [
          jsx('h2', { className: 'font-semibold text-foreground', children: t('environmentTitle') }),
          jsx('p', { className: 'py-2 text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)', children: t('environmentHelp') }),
          ...(enabledBackends.length ? enabledBackends : [{ id: 'native', label: t('nativeWorker') }]).map(({ id: backend, label }) => {
            const result = diagnostics[backend]
            return jsx(ListRow, {
              id: `doctor-${backend}`, title: label,
              description: result?.draft && result.draft !== draft ? t('diagnosticsChanged') : result?.error || t('taskNotTested'),
              action: jsx(Button, {
                'aria-label': `${t('checkEnvironment')}: ${label}`,
                disabled: saving || (result?.status === 'loading' && result.draft === draft),
                onClick: () => checkEnvironment(backend), size: 'sm', variant: 'outline',
                children: result?.status === 'loading' && result.draft === draft ? t('checkingEnvironment') : t('checkEnvironment')
              }),
              below: result?.result && result.draft === draft ? jsx(DiagnosticResult, { result: result.result }) : undefined
            }, backend)
          })
        ]
      }),

      error && jsx('p', { className: 'mt-4 text-xs text-destructive', children: error }),
      jsxs('div', {
        className: 'mt-5 flex flex-wrap gap-2',
        children: [
          jsx(Button, { disabled: saving, onClick: save, size: 'sm', children: saving ? t('saving') : t('save') }),
          jsx(Button, { id: 'workers-global-refresh', disabled: saving || loading, onClick: refresh, size: 'sm', variant: 'outline', children: loading ? t('refreshing') : t('refresh') })
        ]
      }),
      jsx('p', { className: 'mt-auto pt-8 text-xs leading-5 text-(--ui-text-tertiary)', children: t('newWorkersOnly') })
    ]
  })
}

const plugin = {
  id: 'workers',
  name: 'Workers',
  description: 'Configure Hermes Kanban worker backends and models.',
  defaultEnabled: true,
  register(ctx) {
    ctx.i18n.register(LOCALES)
    const rest = ctx.rest
    const hasSettingsPage = typeof ctx.registerSettingsPage === 'function'
    const hasProfileScope = typeof useValue === 'function' && host.state?.profile && host.state?.connectionId
    const renderPage = embedded => jsx(hasProfileScope ? WorkersScopedPage : WorkersPage, { rest, embedded })
    if (hasSettingsPage) ctx.registerSettingsPage({
      id: 'settings', title: 'Workers', icon: 'server-process',
      render: () => renderPage(true)
    })
    const destination = hasSettingsPage ? SETTINGS_ROUTE : ROUTE
    ctx.registerMany([
      { id: 'page', area: ROUTES_AREA, data: { path: ROUTE }, render: () => hasSettingsPage ? jsx(WorkersSettingsRedirect, {}) : renderPage(false) },
      {
        id: 'open', area: PALETTE_AREA,
        data: { id: 'workers.open', label: ctx.i18n.t('openPage'), keywords: ['workers', 'backend', 'kanban', 'model'], run: () => host.navigate(destination) }
      },
      {
        id: 'open-settings-from-composer',
        area: COMPOSER_AREAS.contextMenu,
        data: {
          items: () => [{
            label: 'Workers',
            onSelect: () => host.navigate(destination)
          }]
        }
      }
    ])
  }
}

export default plugin
