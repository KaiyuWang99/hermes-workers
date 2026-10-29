/** OpenCode request hook: same model/provider, Hermes classification + credential rotation. */

import { mkdirSync, writeFileSync } from "node:fs"
import { dirname } from "node:path"

const PLUGIN_ID = "workers"
let credentialCache = null

function reportUpstreamStatus(status, decision = null) {
  const task = String(process.env.HERMES_KANBAN_TASK || "").trim()
  const target = String(process.env.WORKERS_OPENCODE_STATUS || "").trim()
  if (!task || !target) return
  const value = { status_code: Number(status) }
  if (decision && typeof decision === "object") {
    value.should_fallback = Boolean(decision.should_fallback)
    value.reason = String(decision.reason || "")
  }
  try {
    mkdirSync(dirname(target), { recursive: true })
    writeFileSync(target, JSON.stringify(value))
  } catch {}
}

function bridgeCommand() {
  try {
    const value = JSON.parse(process.env.WORKERS_OPENCODE_BRIDGE || "[]")
    return Array.isArray(value) && value.length ? value : null
  } catch {}
  return null
}

async function credentialHelper(action, payload, model = "") {
  const base = bridgeCommand()
  if (!base) return null
  const args = [...base, action]
  if (action !== "handle") args.push(String(payload || ""), String(model || ""))
  const proc = Bun.spawn(args, {
    stdin: "pipe",
    stdout: "pipe",
    stderr: "pipe",
    env: process.env,
  })
  if (action === "handle") proc.stdin.write(JSON.stringify(payload || {}))
  proc.stdin.end()
  const [stdout, code] = await Promise.all([
    new Response(proc.stdout).text(),
    proc.exited,
  ])
  if (code !== 0) return null
  try {
    const value = JSON.parse(stdout)
    return value?.ok ? value : null
  } catch {
    return null
  }
}

async function credentialFor(provider, model) {
  const override = String(process.env.WORKERS_OPENCODE_API_KEY || "").trim()
  if (override) return { provider, credential_id: "", key: override }
  if (credentialCache?.provider === provider && credentialCache.model === model && credentialCache.value?.key) {
    return credentialCache.value
  }
  const value = await credentialHelper("select", provider, model)
  if (value?.key) credentialCache = { provider, model, value }
  return value
}

function requestUrl(input) {
  if (typeof input === "string") return input
  if (input instanceof URL) return input.toString()
  return input?.url || ""
}

function requestMethod(input, init) {
  return String(init?.method || input?.method || "GET").toUpperCase()
}

function requestBody(init) {
  if (typeof init?.body !== "string") return null
  try {
    const body = JSON.parse(init.body)
    return body && typeof body === "object" ? body : null
  } catch {
    return null
  }
}

function isModelRequest(url, method, body) {
  if (method !== "POST" || !body || typeof body.model !== "string") return false
  return url.includes("/chat/completions") || url.includes("/responses") || url.includes("/messages")
}

function headersFor(input, init) {
  return new Headers(init?.headers || input?.headers || undefined)
}

function setCredential(headers, apiMode, key) {
  if (apiMode === "anthropic_messages") {
    headers.delete("authorization")
    headers.set("x-api-key", key)
  } else {
    headers.set("authorization", `Bearer ${key}`)
  }
  headers.delete("content-length")
}

function extraHeaders() {
  try {
    const value = JSON.parse(process.env.WORKERS_OPENCODE_EXTRA_HEADERS || "{}")
    return value && typeof value === "object" ? value : {}
  } catch {}
  return {}
}

async function createHooks() {
  const originalFetch = globalThis.fetch
  if (globalThis.__hermesWorkersFetch) return {}
  globalThis.__hermesWorkersFetch = true

  globalThis.fetch = async (input, init) => {
    if (!process.env.HERMES_KANBAN_TASK) return originalFetch(input, init)
    const url = requestUrl(input)
    const method = requestMethod(input, init)
    const body = requestBody(init)
    if (!isModelRequest(url, method, body)) return originalFetch(input, init)

    const provider = String(process.env.WORKERS_OPENCODE_PROVIDER || "").trim()
    const apiMode = String(process.env.WORKERS_OPENCODE_API_MODE || "chat_completions")

    let credential = await credentialFor(provider, body.model)
    if (!credential?.key) {
      return new Response(
        JSON.stringify({ error: { message: `No available Hermes credential for ${provider}` } }),
        { status: 503, headers: { "content-type": "application/json" } },
      )
    }

    let lastResponse
    for (let rotation = 0; rotation < 2; rotation += 1) {
      const headers = headersFor(input, init)
      Object.entries(extraHeaders()).forEach(([key, value]) => headers.set(key, String(value)))
      setCredential(headers, apiMode, credential.key)
      const response = await originalFetch(url, { ...init, headers })
      lastResponse = response
      if (response.ok) {
        reportUpstreamStatus(response.status)
        return response
      }
      let errorBody = {}
      let errorMessage = ""
      try {
        const text = await response.clone().text()
        errorMessage = text
        try { errorBody = JSON.parse(text) } catch {}
      } catch {}
      const decision = await credentialHelper("handle", {
        provider,
        model: body.model,
        credential_id: credential.credential_id,
        api_key: credential.key,
        status_code: response.status,
        error_body: errorBody,
        error_message: errorMessage,
      })
      reportUpstreamStatus(response.status, decision)
      if (!decision?.rotate || !decision.key || decision.key === credential.key || rotation > 0) return response
      try { await response.body?.cancel() } catch {}
      credential = {
        provider,
        credential_id: String(decision.credential_id || ""),
        key: String(decision.key),
      }
      credentialCache = { provider, model: body.model, value: credential }
    }
    return lastResponse
  }
  return {}
}

export { reportUpstreamStatus }

export default {
  id: PLUGIN_ID,
  server: async () => createHooks(),
}
