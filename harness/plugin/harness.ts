// Harness plugin for STOCK opencode v2. No fork, no custom binary.
// Install: `harness plugin install` copies this file to
// ~/.config/opencode/plugins/ (auto-loaded, no config edit needed).
import type { Plugin } from "@opencode-ai/plugin"

const NEED_PROTO = 6
const RELAY_PORT = process.env.HARNESS_PORT ?? "8787"
const RELAY_URL = process.env.HARNESS_URL ?? `http://127.0.0.1:${RELAY_PORT}`

async function sh(cmd: string[]): Promise<{ ok: boolean; text: string }> {
  try {
    const proc = Bun.spawnSync(cmd)
    const out = Buffer.isBuffer(proc.stdout) ? proc.stdout.toString() : String(proc.stdout ?? "")
    return { ok: proc.exitCode === 0, text: out }
  } catch {
    return { ok: false, text: "" }
  }
}

async function gatewayHealthy(): Promise<boolean> {
  try {
    return (await fetch(`${RELAY_URL}/health`)).ok
  } catch {
    return false
  }
}

async function ensureCli(log: (m: string) => void): Promise<boolean> {
  const v = await sh(["python3", "-c", "from harness import PROTO; print(PROTO)"])
  if (v.ok && (parseInt(v.text.trim()) || 0) >= NEED_PROTO) return true
  log("harness: installing Python CLI from GitHub")
  let r = await sh(["python3", "-m", "pip", "install", "--no-cache-dir", "--break-system-packages",
    "git+https://github.com/CybeRxNinja/harness.git"])
  if (!r.ok) {
    r = await sh(["python3", "-m", "pip", "install", "--no-cache-dir",
      "git+https://github.com/CybeRxNinja/harness.git"])
  }
  if (!r.ok) {
    log("harness: CLI install failed; continuing degraded")
    return false
  }
  return true
}

async function ensureGateway(cwd: string, log: (m: string) => void): Promise<void> {
  if (await gatewayHealthy()) return
  if (!(await ensureCli())) return
  try {
    const proc = Bun.spawn(["python3", "-m", "harness", "--root", cwd, "serve", "--port", RELAY_PORT], {
      cwd,
      stdout: "ignore",
      stderr: "ignore",
    })
    proc.unref()
    for (let i = 0; i < 25 && !(await gatewayHealthy()); i++) {
      await new Promise((r) => setTimeout(r, 400))
    }
  } catch (e) {
    log(`harness: gateway start failed (${String(e).slice(0, 120)})`)
  }
}

async function seedSkills(): Promise<any[]> {
  const out: any[] = []
  const r = await sh(["python3", "-c",
    "import harness,os; print(os.path.join(os.path.dirname(harness.__file__),'data','skills'))"])
  const root = r.ok ? r.text.trim() : ""
  if (!root) return out
  const glob = new Bun.Glob("*/SKILL.md")
  for await (const rel of glob.scan({ cwd: root, absolute: false })) {
    try {
      const { join, basename, dirname } = await import("node:path")
      const full = join(root, rel)
      const text = await Bun.file(full).text()
      const m = text.match(/^---\s*\n([\s\S]*?)\n---/)
      const fm: Record<string, string> = {}
      for (const line of (m?.[1] ?? "").split("\n")) {
        const i = line.indexOf(":")
        if (i > 0) fm[line.slice(0, i).trim()] = line.slice(i + 1).trim()
      }
      const dirName = basename(dirname(full))
      out.push({
        id: `harness-${dirName}`,
        name: fm.name || dirName,
        description: (fm.description || "Harness skill").slice(0, 500),
        path: full,
        content: text,
      })
    } catch {
      /* skip unreadable seeds */
    }
  }
  return out
}

// Fetch a short memory brief from the gateway so durable project facts
// survive session compaction. Returns "" when there is nothing worth keeping
// (no token, gateway down, or no matching facts).
async function gatewayToken(): Promise<string> {
  if (process.env.HARNESS_TOKEN) return process.env.HARNESS_TOKEN
  try {
    const home = process.env.HOME ?? ""
    const t = (await Bun.file(`${home}/.harness/token`).text()).trim()
    return t
  } catch {
    return ""
  }
}

async function memoryBrief(sessionID: string, log: (m: string) => void): Promise<string> {
  const token = await gatewayToken()
  if (!token) return ""
  const q = encodeURIComponent(sessionID.slice(0, 40))
  try {
    const res = await fetch(`${RELAY_URL}/api/memory?q=${q}`,
      { headers: { Authorization: `Bearer ${token}` } })
    if (!res.ok) return ""
    const items: Array<{ text: string; source: string }> = await res.json()
    if (!items.length) return ""
    const lines = items.slice(0, 5).map((it) => `- ${String(it.text).slice(0, 200)}`)
    return `Harness memory brief (session ${sessionID.slice(0, 8)}):\n${lines.join("\n")}`
  } catch (e) {
    log(`memory brief failed: ${String(e).slice(0, 120)}`)
    return ""
  }
}

// Plain object literal: no helper imports needed (Bun erases types;
// a value-level import would fail when the resolved plugin package differs).
const HarnessPlugin = {
  id: "harness",
  setup: async (ctx: any) => {
    const log = (m: string) => console.error(`[harness] ${m}`)
    const cwd = process.cwd()
    await ensureGateway(cwd)

    // Seed bundled skills into the opencode skill store so they are usable
    // without a hand-edited config. Provider / agents / MCP are NOT duplicated
    // here — they are merged into opencod.json by `harness tui` setup
    // (ensure_opencode_config), see harness/harness-opencode.json.
    try {
      const seeds = await seedSkills()
      if (seeds.length) {
        await ctx.skill.transform((editor: any) => {
          const existing = new Set(editor.list().map((s: any) => s.id ?? s.name))
          for (const s of seeds) {
            if (!existing.has(s.id)) {
              try {
                editor.add(s)
              } catch (e) {
                log(`skill skipped ${s.id}: ${String(e).slice(0, 120)}`)
              }
            }
          }
        })
      }
    } catch (e) {
      log(`skill seeding failed: ${String(e).slice(0, 120)}`)
    }

    // Preserve durable project facts across session compaction. v2 API:
    // ctx.session.hook("compaction") mutates input.system before the
    // summary LLM runs. Anything else (old .on/event names) is ignored.
    try {
      await ctx.session.hook("compaction", async (input: any) => {
        const brief = await memoryBrief(String(input?.sessionID ?? ""), log)
        if (brief && Array.isArray(input?.system)) {
          input.system.push({ type: "text", text: brief })
        }
      })
    } catch (e) {
      log(`compaction hook skipped: ${String(e).slice(0, 150)}`)
    }
  },
}

export { HarnessPlugin }
export default HarnessPlugin
