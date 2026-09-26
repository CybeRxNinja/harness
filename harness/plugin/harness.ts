// Harness plugin for STOCK opencode v2. Installed by `harness plugin install`
// into ~/.config/opencode/plugins/ (auto-discovered, no config entry needed).
//
// Loader contract (opencode v2.0.8, PluginModule.load + PluginSupervisor):
//   * The default export must be an object shaped { id, setup } (or
//     { id, effect }); anything else fails the load with
//     "Plugin must export a default definition with an id and an effect or
//     setup function."
//   * `setup` runs at activation and may return a cleanup function. Returning
//     any OTHER truthy value makes the host call that value as a cleanup
//     function ("TypeError: … is not a function"), the activation dies, the
//     plugin never appears under /plugins, and session work that waits for
//     plugin activation stalls. v1 hook objects are NOT read in v2: tools,
//     skills and hooks must be registered through the ctx.* APIs below.
//   * No value-level imports: the server compiles plugins in its own registry
//     where bare specifiers do not resolve. This file imports nothing.
//
// What it registers:
//   1. skill seeding      ctx.skill.transform(editor => editor.add(skill))
//   2. native tools       ctx.tool.transform(editor => editor.add(tool))
//      skills_list / skill_view / memory_recall, plus todowrite / todoread —
//      the plan tools opencode 2.x no longer ships, backed by the harness todo
//      space (the same `todos` table the sidebar panel reads) — plus `wait`,
//      a visible countdown sleep backed by the `waits` table the sidebar reads.
//   3. output condensing  ctx.tool.hook("execute.after", fn)
//   4. compaction brief   ctx.session.hook("compaction", fn)
//   5. auto-memory        ctx.session.hook("context", fn) — durable facts +
//      progress notes from live turns (the Python loop path is not what live
//      opencode sessions run through, so nothing else ever stores them)
//
// Provider / agents are NOT duplicated here: they come from opencode.json.

type Rec = Record<string, any>

const CONDENSE_OVER = 4000
const MAX_SKILL_CHARS = 12000
const NO_FACTS = "(nothing recalled — verify from the transcript instead)"
const BRIEF_MARK = "Harness memory brief"
const ERROR_LINE = /Traceback |Error:|Exception:|FAILED|failed|AssertionError|panic:|fatal:/

function short(e: unknown): string {
  const s = e instanceof Error ? e.message : String(e)
  return s.length > 160 ? `${s.slice(0, 160)}…` : s
}

/** Async process helper: never blocks the opencode server's event loop. */
async function run(cmd: string[]): Promise<{ ok: boolean; text: string }> {
  try {
    const proc = Bun.spawn(cmd, { stdout: "pipe", stderr: "ignore" })
    const [out, code] = await Promise.all([new Response(proc.stdout).text(), proc.exited])
    return { ok: code === 0, text: out ?? "" }
  } catch {
    return { ok: false, text: "" }
  }
}

/**
 * `ctx.skill.list()` returns the raw server response, whose shape differs
 * between versions (`SkillInfo[]`, `{data: [...]}`, `{skills: [...]}`).
 * Normalize whatever comes back into an array.
 */
function asSkills(listed: any): Rec[] {
  const candidates = [listed, listed?.data, listed?.skills, listed?.items]
  for (const c of candidates) {
    if (Array.isArray(c)) return c
    for (const inner of [c?.data, c?.skills, c?.items]) if (Array.isArray(inner)) return inner
  }
  return []
}

/** One-line description for the skill listing: collapse whitespace, never cut mid-word. */
function shortenDesc(value: unknown): string {
  const d = String(value ?? "").replace(/\s+/g, " ").trim()
  if (d.length <= 240) return d
  const cut = d.slice(0, 240)
  const space = cut.lastIndexOf(" ")
  return `${(space > 120 ? cut.slice(0, space) : cut).trimEnd()}…`
}

function frontmatter(text: string): Rec {
  const fm: Rec = {}
  const m = text.match(/^---\s*\n([\s\S]*?)\n---/)
  for (const line of (m?.[1] ?? "").split("\n")) {
    const i = line.indexOf(":")
    if (i > 0) fm[line.slice(0, i).trim()] = line.slice(i + 1).trim()
  }
  return fm
}

/** Directories that may hold bundled harness skills, best first. */
async function skillRoots(): Promise<string[]> {
  const roots: string[] = []
  const home = process.env.HOME ?? ""
  if (process.env.HARNESS_SKILLS_DIR) roots.push(process.env.HARNESS_SKILLS_DIR)
  const py = await run([
    "python3", "-c",
    "import harness,os; print(os.path.join(os.path.dirname(harness.__file__),'data','skills'))",
  ])
  if (py.ok && py.text.trim()) roots.push(py.text.trim())
  if (home) roots.push(`${home}/.harness/skills`)
  return [...new Set(roots.filter((r) => r && r.length > 1))]
}

/**
 * Bundled skills in opencode's shape: {id, name, description, path, content}.
 * Seeds live at `<root>/<category>/<skill>/SKILL.md` (see harness/skills.py),
 * so glob two levels down.
 */
async function seedSkills(): Promise<Rec[]> {
  const out: Rec[] = []
  const seen = new Set<string>()
  const { join, basename, dirname } = await import("node:path")
  for (const root of await skillRoots()) {
    const rels: string[] = []
    try {
      const glob = new Bun.Glob("**/SKILL.md")
      for await (const rel of glob.scan({ cwd: root, absolute: false })) rels.push(rel)
    } catch {
      continue // missing/unreadable root
    }
    for (const rel of rels.slice(0, 200)) {
      try {
        const full = join(root, rel)
        const text = await Bun.file(full).text()
        const fm = frontmatter(text)
        const name = fm.name || basename(dirname(full))
        if (seen.has(name)) continue
        seen.add(name)
        out.push({
          id: `harness-${name}`,
          name,
          description: (fm.description || "Harness skill").slice(0, 500),
          path: full,
          content: text,
        })
      } catch {
        /* skip unreadable seed */
      }
    }
  }
  return out
}

/**
 * Read a skill body (SKILL.md or a references/ file) by id or name.
 *
 * Never throws: built-in skills report a virtual path (`/builtin/opencode.md`)
 * that is not a real file — their body only exists in the inline `content`
 * field. A missing file must come back as readable text, not a raw ENOENT that
 * surfaces to the model as a broken tool call.
 */
async function readSkill(skill: Rec, subpath?: string): Promise<string> {
  const label = String(skill.id ?? skill.name ?? "skill")
  const inline = typeof skill.content === "string" ? skill.content : ""
  const skillPath = String(skill.path ?? "")

  const readFile = async (file: string): Promise<string | null> => {
    try {
      if (!(await Bun.file(file).exists())) return null
      const text = await Bun.file(file).text()
      if (text.length <= MAX_SKILL_CHARS) return text
      return `${text.slice(0, MAX_SKILL_CHARS)}\n\n[harness: truncated at ${MAX_SKILL_CHARS} chars — read ${file} for the rest]`
    } catch {
      return null
    }
  }

  if (subpath) {
    const { join, dirname } = await import("node:path")
    if (!skillPath) return `not found: ${subpath} (the ${label} skill has no directory to read from)`
    const base = dirname(skillPath)
    const target = join(base, subpath)
    if (!base || !target.startsWith(base)) return `cannot read ${subpath}: path escapes the ${label} skill directory`
    const text = await readFile(target)
    if (text !== null) return text
    return `not found: ${subpath} (the ${label} skill has no such file under ${base})`
  }

  const text = await readFile(skillPath)
  if (text !== null) return text
  if (inline) return inline.slice(0, MAX_SKILL_CHARS)
  return `skill body unavailable for ${label} (no readable path: ${skillPath || "none"})`
}

// Project-local only: a $HOME probe here recalled a different project's facts
// (state_dir() in harness/paths.py is always <root>/.opencode/harness).
function stateDb(projectDir: string): string {
  return `${projectDir}/.opencode/harness/sessions.db`
}

/**
 * Durable facts from the session DB, straight off disk.
 *
 * Two modes, and the distinction matters:
 *   search — only rows matching one of `patterns`. An empty pattern list means
 *     the query had no searchable terms, and we return NOTHING: answering an
 *     unrelated query with the newest handful of facts reads as recall but is
 *     just noise the model would cite.
 *   recent — the newest rows, regardless of text. Used for the compaction
 *     brief, where there is no query to match (a session id appears in no fact).
 */
async function facts(
  projectDir: string,
  patterns: string[] = [],
  limit = 5,
  mode: "search" | "recent" = "search",
): Promise<string[]> {
  if (mode === "search" && !patterns.length) return []
  const { Database } = await import("bun:sqlite").catch(() => ({}) as any)
  if (!Database) return []
  const hits: string[] = []
  const where = mode === "recent" ? "1=1" : patterns.map(() => "text LIKE ?").join(" OR ")
  const params = mode === "recent" ? [] : patterns.map((p) => `%${p}%`)
  try {
    const dbPath = stateDb(projectDir)
    if (!(await Bun.file(dbPath).exists())) return []
    const db = new Database(dbPath, { readonly: true })
    try {
      const rows = db
        .query(`SELECT text, source FROM facts WHERE ${where} ORDER BY id DESC LIMIT ?`)
        .all(...params, limit) as any[]
      for (const r of rows) hits.push(`- [${r.source}] ${String(r.text).slice(0, 200)}`)
    } finally {
      db.close()
    }
  } catch {
    /* unreadable db */
  }
  return hits
}

/**
 * The project's TODO SPACE — the `todos` table the Python core keeps in
 * <project>/.opencode/harness/sessions.db.
 *
 * opencode 2.0.14 ships no todo tool at all, so a session's plan had nowhere to
 * live: the sidebar's Todo row could only ever read opencode's own `todo`
 * table, which no version past 2.0.13 writes. One space instead of a
 * tool-private copy — the model writes it through `todowrite`, the sidebar
 * reads the same table, and the compaction brief carries it across a
 * compaction. The list is ALSO written through to opencode's own `todo` table
 * (see mirrorOpencodeTodos) so opencode's own storage holds it too.
 */
const TODO_MARKS: Rec = { completed: "●", in_progress: "◐", cancelled: "✕", pending: "○" }
const todoLine = (r: Rec) => `${TODO_MARKS[String(r?.status)] ?? "○"} ${String(r?.text ?? "")}`

/** Open the todo space. `create` is false for reads: a read must not invent state. */
async function todoDb(projectDir: string, create = false): Promise<any> {
  const dbPath = stateDb(projectDir)
  try {
    const { Database } = await import("bun:sqlite").catch(() => ({}) as any)
    if (!Database) return null
    if (!create && !(await Bun.file(dbPath).exists())) return null
    if (create) {
      const { mkdirSync } = await import("node:fs")
      mkdirSync(`${projectDir}/.opencode/harness`, { recursive: true })
    }
    const db = new Database(dbPath)
    db.run("PRAGMA busy_timeout=3000")
    if (create) {
      db.run(
        "CREATE TABLE IF NOT EXISTS todos(id INTEGER PRIMARY KEY AUTOINCREMENT, session TEXT, text TEXT, status TEXT, ts INTEGER)",
      )
    }
    return db
  } catch {
    return null
  }
}

async function readTodos(projectDir: string, sessionID: string): Promise<Rec[]> {
  const db = await todoDb(projectDir)
  if (!db) return []
  try {
    return db.query("SELECT text, status FROM todos WHERE session = ? ORDER BY id").all(sessionID) as Rec[]
  } catch {
    return []
  } finally {
    db.close()
  }
}

/**
 * Write-through to opencode's OWN todo store: the `todo` table in opencode's
 * data DB (`session_id, content, status, priority, position, time_*`) — the
 * storage opencode's own todo tool wrote before 2.0.14, and what anything
 * reading opencode's database still expects. The harness space stays the
 * source of truth (the sidebar and the Python core read it); this mirror is
 * best-effort on purpose: a busy or missing foreign DB is a log line, never a
 * failed tool call. That is the deep-integration ask — the plan lives in
 * opencode's own store as well as the panel, instead of only a private copy.
 */
async function mirrorOpencodeTodos(sessionID: string, rows: Rec[]): Promise<void> {
  if (!sessionID) return
  try {
    const env = String(process.env.OPENCODE_DB ?? "")
    const dataHome = process.env.XDG_DATA_HOME || `${process.env.HOME}/.local/share`
    const dbPath =
      env && env !== ":memory:" && (env.includes("/") || env.endsWith(".db"))
        ? env
        : `${dataHome}/opencode/opencode.db`
    const { Database } = await import("bun:sqlite").catch(() => ({}) as any)
    if (!Database) return
    // Never CREATE a foreign app's database: if opencode has never run there
    // is nothing to mirror into, and nothing may be invented on disk.
    if (!(await Bun.file(dbPath).exists())) return
    const db = new Database(dbPath)
    try {
      db.run("PRAGMA busy_timeout=3000")
      const now = Date.now()
      db.run("DELETE FROM todo WHERE session_id = ?", sessionID)
      rows.forEach((t: Rec, i: number) =>
        db.run(
          "INSERT INTO todo(session_id, content, status, priority, position, time_created, time_updated) VALUES(?,?,?,?,?,?,?)",
          sessionID,
          t.text,
          t.status,
          t.priority,
          i,
          now,
          now,
        ),
      )
    } finally {
      db.close()
    }
  } catch (e) {
    console.error(`[harness] todo mirror skipped: ${short(e)}`)
  }
}

/** Replace the session's list (the tool's whole contract) and answer with it. */
async function writeTodos(projectDir: string, sessionID: string, todos: any): Promise<Rec[]> {
  const db = await todoDb(projectDir, true)
  if (!db) return []
  try {
    const rows = (Array.isArray(todos) ? todos : [])
      .map((t: Rec) => ({
        text: String(t?.content ?? "").replace(/\s+/g, " ").trim().slice(0, 500),
        status: String(t?.status ?? "pending"),
        // opencode-native priority (high/medium/low); only the mirror uses it —
        // the harness space has no priority column and does not need one.
        priority: ["high", "medium", "low"].includes(String(t?.priority ?? ""))
          ? String(t.priority)
          : "medium",
      }))
      .filter((t: Rec) => t.text)
    const now = Date.now()
    db.run("DELETE FROM todos WHERE session = ?", sessionID)
    rows.forEach((t: Rec, i: number) =>
      db.run("INSERT INTO todos(session, text, status, ts) VALUES(?,?,?,?)", sessionID, t.text, t.status, now + i),
    )
    // best-effort: local write is the contract; the foreign mirror never fails it
    await mirrorOpencodeTodos(sessionID, rows)
    return rows
  } catch {
    return []
  } finally {
    db.close()
  }
}

/** Cap on one `wait` call: a sidebar countdown, not a parking lot. */
const MAX_WAIT_S = 600

/**
 * Sleep without blocking the opencode server's event loop (same async rule
 * as `run` above: a timer, never a spin).
 */
function sleep(ms: number): Promise<void> {
  return new Promise<void>((resolve) => setTimeout(resolve, ms))
}

/**
 * The `wait` tool's countdown rows: `waits(id, label, deadline)` in the same
 * project state DB as the todo space, so the TUI reads it in its one
 * read-only open. `deadline` is epoch MILLISECONDS (Date.now() based); the
 * row lives only for the sleep and is deleted on expiry. Best-effort on
 * purpose: a lost countdown must never fail the wait itself.
 */
async function recordWait(projectDir: string, id: string, label: string, deadline: number): Promise<void> {
  const db = await todoDb(projectDir, true)
  if (!db) return
  try {
    db.run("CREATE TABLE IF NOT EXISTS waits(id TEXT PRIMARY KEY, label TEXT, deadline INTEGER)")
    db.run("INSERT OR REPLACE INTO waits(id, label, deadline) VALUES(?,?,?)", id, label, deadline)
  } catch {
    /* sidebar countdown best-effort */
  } finally {
    try {
      db.close()
    } catch {
      /* already closed */
    }
  }
}

async function clearWait(projectDir: string, id: string): Promise<void> {
  const db = await todoDb(projectDir)
  if (!db) return
  try {
    db.run("DELETE FROM waits WHERE id = ?", id)
  } catch {
    /* already gone */
  } finally {
    try {
      db.close()
    } catch {
      /* already closed */
    }
  }
}

/** Words long enough to be worth matching; falls back to the phrase itself. */
function terms(query: string): string[] {
  const ws = String(query ?? "")
    .split(/\s+/)
    .filter((w) => w.length > 2)
  return [...new Set(ws)].slice(0, 5)
}

/** Keyword recall for the memory_recall tool. "" means nothing relevant. */
async function recallFacts(query: string, projectDir: string): Promise<string> {
  const ws = terms(query)
  if (ws.length) return (await facts(projectDir, ws)).join("\n")
  const whole = String(query ?? "").trim()
  if (whole.length > 2) return (await facts(projectDir, [whole])).join("\n")
  return ""
}

// ---------------------------------------------------------------------------
// 5. Auto-memory: live turns persist durable facts with zero model effort.
//
// The Python loop path (run_turn -> capture_turn in harness/memory.py) is
// healthy, but live opencode sessions never run through it — the plugin only
// ever READ facts (memory_recall, compaction brief), so nothing was stored
// and recall + the Memory panel sat on stale mock junk.
//
// There is NO post-turn hook in opencode 2.x to hang this on. Evidence: the
// server binary's hook-name literals are compaction|context|generate|title|
// model.request|http.request|http.response|execute.before|after|sdk|language
// (+ experimental.ws.handshake) — none fires after a turn carrying its text —
// and ctx.event.subscribe needs the Effect runtime this import-free file
// cannot use. The session "context" hook is the closest automatic point: it
// fires before each generation with the full message history ({sessionID,
// agent, messages} — the same payload the plan-mode plugin splices reminders
// into), so the previous assistant message is always visible. Two honest
// costs of that placement: the turn's FINAL message is captured on the NEXT
// context build (next turn, or compaction), and progress granularity is
// per-assistant-message deduped by text, not per-turn. The Python loop still
// owns exact per-turn done/blocked marking — that needs a verified signal
// (a test run that actually came back clean) this hook cannot see, so notes
// written here are always `progress`, never `done`.
//
// Row parity with harness/memory.py (same facts(text,source,ts-seconds)
// shape the TUI humanizer and recall already parse — no new schema):
// durable lines go in with source "turn" (what remember() writes), progress
// notes as `progress [ses]: headline` with source "progress" (what
// progress() writes), with _is_junk_turn parity (empty, or a mock echo next
// to real facts, gets no note), secret refusal, dupe check and the
// writer-side fact cap ("cap by the writer, retention by prune()").
// Opt-out: memory.enabled=false in <project>/.opencode/harness.jsonc or the
// user harness.jsonc (fail-open; no parent-dir walk-up — one line below says
// so). The title/compaction plumbing agents generate no user-visible turns
// and are skipped. Never throws into generation; a host without session
// hooks just loses auto-memory, same degrade rule as the other hooks.
// ---------------------------------------------------------------------------

const MEM_MAX_FACT_LEN = 1000
const MEM_HEADLINE_LIMIT = 160
const MEM_LINES_PER_MSG = 3
const MEM_SEEN_PER_SESSION = 200
const MEM_DEFAULT_MAX_FACTS = 2000

/** Same durable-line vocabulary as IMPORTANT_MARKERS in harness/memory.py. */
const MEM_MARKERS = [
  "decision:", "decided", "root cause", "fixed:", "fix:", "fixed ", "added:",
  "verified:", "verified ", "lesson:", "migration", "breaking", "blocked",
  "blocker:", "next step", "todo:", "note:", "important:", "gotcha",
  "remember:", "constraint:", "requirement:", "must ", "never ", "always ",
]
const MEM_OUTCOME =
  /\b(passed|failing|failed|regression|tests? (pass|fail)|reproduced|timeout|permission denied|not found|traceback)\b/i
/** Same credential shapes as SECRET_RES in harness/memory.py. */
const MEM_SECRETS = [
  /\b(sk|xoxb|ghp|gho|aiza|AKIA)[-_A-Za-z0-9]{12,}/,
  /\b(api[_-]?key|password|passwd|secret|token)\b\s*[:=]\s*\S{8,}/i,
  /-----BEGIN [A-Z ]*PRIVATE KEY-----/,
  /\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}/,
]

/** One-line form for storage and duplicate detection (mirrors normalize()). */
function memNormalize(text: unknown): string {
  const t = String(text ?? "").replace(/\s+/g, " ").trim()
  return t.replace(/^(?:[-*•]\s+|\d+[.)]\s+)/, "")
}

function memSecret(text: string): boolean {
  return MEM_SECRETS.some((r) => r.test(text))
}

/** The durable lines of one assistant message (mirrors important_lines()). */
function memImportantLines(text: string, limit = MEM_LINES_PER_MSG): string[] {
  const out: string[] = []
  let fenced = false
  for (const raw of String(text ?? "").split("\n")) {
    const stripped = raw.trim()
    if (stripped.startsWith("```") || stripped.startsWith("~~~")) {
      fenced = !fenced
      continue
    }
    if (fenced) continue
    const line = memNormalize(raw)
    if (line.length < 20 || line.length > 400) continue
    if (/^[|+>$/#!]/.test(line) || line.startsWith("/")) continue
    if (/^(\.\.\.|\d+\s*[|:])/.test(line)) continue
    const low = line.toLowerCase()
    if (!(MEM_MARKERS.some((m) => low.startsWith(m) || low.includes(` ${m}`)) || MEM_OUTCOME.test(line))) continue
    if (!out.includes(line)) out.push(line)
  }
  return out.slice(0, limit)
}

/** One-line summary of a message, for the progress note (mirrors headline()). */
function memHeadline(text: string, limit = MEM_HEADLINE_LIMIT): string {
  for (const raw of String(text ?? "").split("\n")) {
    const line = memNormalize(raw)
    if (line.length >= 8) return line.slice(0, limit)
  }
  return ""
}

/** True when a message deserves no progress note (mirrors _is_junk_turn()). */
function memIsJunk(headlineText: string, hasFacts = false): boolean {
  const h = memNormalize(headlineText)
  return !h || (h.startsWith("[mock:") && hasFacts)
}

/** Assistant text out of a session message, tolerating both known shapes
 * ({role, content} and {info, parts}; content/parts as text parts, strings,
 * or one string). Unknown shapes yield "" rather than a throw. */
function memMessageText(m: Rec): string {
  try {
    const parts = Array.isArray(m?.content) ? m.content : Array.isArray(m?.parts) ? m.parts : null
    if (parts) {
      return parts
        .map((p: any) => (typeof p === "string" ? p : p?.type === "text" && typeof p?.text === "string" ? p.text : ""))
        .join("\n")
    }
    if (typeof m?.content === "string") return m.content
    if (typeof m?.text === "string") return m.text
  } catch {
    /* malformed message */
  }
  return ""
}

function memAssistantTexts(messages: any): string[] {
  if (!Array.isArray(messages)) return []
  const out: string[] = []
  for (const m of messages) {
    const role = String(m?.role ?? m?.info?.role ?? "")
    if (role !== "assistant") continue
    const t = memMessageText(m)
    if (t && memNormalize(t)) out.push(t)
  }
  return out
}

/**
 * The memory block of the config layer, read cheaply: project
 * <root>/.opencode/harness.jsonc (project_config_file in harness/paths.py)
 * then the user file ($HARNESS_HOME, else ~/.harness). Comment lines are
 * stripped with the same (^|\\s)// rule the Python loader uses; only
 * enabled/max_facts are picked out, so a half-written file cannot break
 * anything. Fail-open: unreadable means enabled with defaults. No walk-up
 * to parent project dirs — the one place this is cheaper than load_config.
 */
async function memoryConfig(projectDir: string): Promise<{ enabled: boolean; maxFacts: number }> {
  let enabled = true
  let maxFacts = MEM_DEFAULT_MAX_FACTS
  const candidates = [
    `${projectDir}/.opencode/harness.jsonc`,
    `${process.env.HARNESS_HOME || `${process.env.HOME}/.harness`}/harness.jsonc`,
  ]
  for (const file of candidates) {
    let raw = ""
    try {
      if (!(await Bun.file(file).exists())) continue
      raw = await Bun.file(file).text()
    } catch {
      continue
    }
    const code = raw
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/(^|\s)\/\/.*$/gm, "$1")
    const mem = code.match(/"memory"\s*:\s*\{([^}]*)\}/)?.[1] ?? ""
    if (/"enabled"\s*:\s*false/.test(mem)) enabled = false
    const cap = mem.match(/"max_facts"\s*:\s*(-?\d+)/)?.[1]
    if (cap !== undefined) {
      const n = parseInt(cap, 10)
      if (Number.isFinite(n)) maxFacts = n
    }
  }
  return { enabled, maxFacts }
}

/** Read-write open of the project state DB, creating the facts table in the
 * store.py shape when this project has never run the harness CLI (the same
 * create-on-write precedent as the todos table). Null when unusable. */
async function memDb(projectDir: string): Promise<any> {
  try {
    const { Database } = await import("bun:sqlite").catch(() => ({}) as any)
    if (!Database) return null
    const { mkdirSync } = await import("node:fs")
    mkdirSync(`${projectDir}/.opencode/harness`, { recursive: true })
    const db = new Database(stateDb(projectDir))
    db.run("PRAGMA busy_timeout=3000")
    db.run("CREATE TABLE IF NOT EXISTS facts(id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT, source TEXT, ts INTEGER)")
    return db
  } catch {
    return null
  }
}

/** Store one durable fact (mirrors save_fact(): length floor, secret
 * refusal on both forms, case-insensitive dupe check, writer-side cap). */
function memSaveFact(db: any, text: string, source: string, maxFacts: number): number {
  try {
    const norm = memNormalize(text).slice(0, MEM_MAX_FACT_LEN)
    if (norm.length < 8 || memSecret(norm) || memSecret(String(text ?? ""))) return 0
    const dupe = db.query("SELECT 1 FROM facts WHERE lower(text)=lower(?) LIMIT 1").get(norm) as any
    if (dupe) return 0
    db.run("INSERT INTO facts(text,source,ts) VALUES(?,?,?)", norm, source, Math.floor(Date.now() / 1000))
    const id = Number(db.query("SELECT last_insert_rowid() AS id").get()?.id ?? 0)
    if (maxFacts > 0) {
      const n = Number((db.query("SELECT COUNT(*) AS n FROM facts").get() as any)?.n ?? 0)
      if (n > maxFacts) {
        db.run("DELETE FROM facts WHERE id NOT IN (SELECT id FROM facts ORDER BY id DESC LIMIT ?)", maxFacts)
      }
    }
    return id || 0
  } catch {
    return 0
  }
}

/** Texts of this session already captured (in-process cursor: the hook fires
 * once per generation, often with the same history twice). Survives nothing
 * by design — the DB dupe check is the cross-restart backstop. */
const memSeen = new Map<string, string[]>()

/**
 * Capture one context-hook firing into the state DB: durable lines of each
 * new assistant message (source "turn") plus one progress note from the
 * newest message's headline. Best-effort and silent: never throws.
 */
async function captureMemory(projectDir: string, sessionID: string, agent: unknown, messages: any): Promise<void> {
  if (!sessionID || !Array.isArray(messages) || !messages.length) return
  if (/^(title|compaction)$/i.test(String(agent ?? ""))) return
  const cfg = await memoryConfig(projectDir)
  if (!cfg.enabled) return
  const texts = memAssistantTexts(messages)
  if (!texts.length) return
  let seen = memSeen.get(sessionID)
  if (!seen) {
    seen = []
    memSeen.set(sessionID, seen)
  }
  const fresh = texts.filter((t) => {
    const n = memNormalize(t)
    return n !== "" && !seen.includes(n)
  })
  if (!fresh.length) return
  for (const t of fresh) {
    seen.push(memNormalize(t))
    while (seen.length > MEM_SEEN_PER_SESSION) seen.shift()
  }
  const db = await memDb(projectDir)
  if (!db) return
  try {
    let hasFacts = false
    for (const t of fresh) {
      for (const line of memImportantLines(t)) {
        if (memSaveFact(db, line, "turn", cfg.maxFacts)) hasFacts = true
      }
    }
    const head = memHeadline(fresh[fresh.length - 1])
    if (!memIsJunk(head, hasFacts)) {
      memSaveFact(db, `progress [${sessionID.slice(0, 24)}]: ${head}`.slice(0, MEM_MAX_FACT_LEN), "progress", cfg.maxFacts)
    }
  } finally {
    try {
      db.close()
    } catch {
      /* already closed */
    }
  }
}

function condenseText(raw: string): string {
  const lines = raw.replace(/\x1b\[[0-9;?]*[a-zA-Z]/g, "").split("\n")
  const kept: string[] = []
  let run: string[] = []
  const flush = () => {
    if (run.length >= 3) kept.push(run[0], `... [${run.length - 1} repeated lines] ...`)
    else kept.push(...run)
    run = []
  }
  for (const ln of lines) {
    if (run.length && ln === run[0]) run.push(ln)
    else {
      flush()
      run = [ln]
    }
  }
  flush()
  let out = kept
  if (out.length > 48) out = [...out.slice(0, 24), `... [${out.length - 44} lines elided] ...`, ...out.slice(-20)]
  const text = out.join("\n")
  if (text.length >= raw.length) return raw
  const pct = Math.round(100 * (1 - text.length / raw.length))
  return `${text}\n  [harness: condensed ${pct}% (${raw.length} → ${text.length} chars). Head and tail kept; re-run a narrower command for the middle.]`
}

/** Shrink oversized tool output in place; errors pass through untouched. */
function condenseToolResult(event: Rec): void {
  if (!event || event.status === "error") return
  const result = event.result
  if (!result || typeof result !== "object") return
  const content = result.content
  if (!Array.isArray(content)) return
  for (const part of content) {
    if (!part || part.type !== "text" || typeof part.text !== "string") continue
    if (part.text.length <= CONDENSE_OVER) continue
    if (ERROR_LINE.test(part.text)) continue
    part.text = condenseText(part.text)
  }
}

const HarnessPlugin = {
  // the loader requires a non-empty string id
  id: "harness",

  async setup(ctx: Rec) {
    const log = (m: string) => console.error(`[harness] ${m}`)
    const directory: string = ctx?.location?.directory || process.cwd()

    // 1. Seed the bundled skills into opencode's skill store so they are
    //    loadable (by id) without hand-editing opencode.json.
    try {
      // guarded: a future host that renames/removes this API degrades to a log
      if (typeof ctx?.skill?.transform !== "function") throw new Error("ctx.skill.transform unavailable")
      const seeds = await seedSkills()
      if (seeds.length) {
        await ctx.skill.transform((editor: Rec) => {
          const current = editor?.list?.()
          const known = new Set((Array.isArray(current) ? current : []).map((s: Rec) => String(s?.id)))
          for (const s of seeds) {
            if (known.has(s.id)) continue
            try {
              editor.add(s)
            } catch (e) {
              log(`skill skipped ${s.id}: ${short(e)}`)
            }
          }
        })
      }
    } catch (e) {
      log(`skill seeding failed: ${short(e)}`)
    }

    // 2. Native tools (no MCP hop): skills + memory read straight off disk.
    //    codemode:false is what makes a tool DIRECT (visible in the model's
    //    tool list); the v2 default puts a tool in the Code Mode catalog only,
    //    where it is reachable as `tools.<name>()` inside `execute` and calls
    //    by name fail with "No tool named … is currently available".
    const direct = { codemode: false }
    try {
      // guarded: one failing editor.add skips only that tool, not all three
      if (typeof ctx?.tool?.transform !== "function") throw new Error("ctx.tool.transform unavailable")
      await ctx.tool.transform((editor: Rec) => {
        const add = (t: Rec) => {
          try {
            editor.add(t)
          } catch (e) {
            log(`tool skipped ${t.name}: ${short(e)}`)
          }
        }
        add({
          name: "skills_list",
          description: "List the skills available in this session (id + when-to-use). Call before skill_view.",
          input: { type: "object", properties: {}, additionalProperties: false },
          options: direct,
          execute: async () => {
            let listed: any = null
            let items: Rec[] = []
            try {
              listed = await ctx.skill.list()
              items = asSkills(listed)
            } catch {
              items = []
            }
            if (!items.length) items = await seedSkills()
            const text = items
              .map((s: Rec) => `- ${s.id ?? s.name}: ${shortenDesc(s.description)}`)
              .join("\n")
            if (text) return { content: `${text}\n(${items.length} skills — call skill_view with an id to load one)` }
            const shape = listed === null ? "unavailable" : `${typeof listed} keys=${Object.keys(listed ?? {}).join(",") || "none"}`
            return { content: `(no skills installed; skill.list returned ${shape})` }
          },
        })

        add({
          name: "skill_view",
          description:
            "Load a skill's instructions (or a file under its directory, e.g. references/x.md). Use before doing the task the skill covers.",
          input: {
            type: "object",
            properties: {
              id: { type: "string", description: "Skill id or name" },
              path: { type: "string", description: "Optional file inside the skill directory" },
            },
            required: ["id"],
            additionalProperties: false,
          },
          options: direct,
          execute: async (input: Rec) => {
            const want = String(input?.id ?? "").trim()
            if (!want) return { content: "skill_view needs an id — call skills_list first and pass one of the listed ids" }
            let items: Rec[] = []
            try {
              items = asSkills(await ctx.skill.list())
            } catch {
              items = []
            }
            if (!items.length) items = await seedSkills()
            const skill = items.find((s: Rec) => String(s.id) === want || String(s.name) === want)
            if (!skill) return { content: `skill not found: ${want}` }
            return { content: await readSkill(skill, input?.path ? String(input.path) : undefined) }
          },
        })

        add({
          name: "memory_recall",
          description: "Recall durable harness facts relevant to a query. Verify before relying on them.",
          input: {
            type: "object",
            properties: { query: { type: "string" } },
            required: ["query"],
            additionalProperties: false,
          },
          options: direct,
          execute: async (input: Rec) => ({
            content: (await recallFacts(String(input?.query ?? ""), directory)) || NO_FACTS,
          }),
        })

        // The plan tool opencode 2.x no longer has, backed by the harness todo
        // space. Extra keys a model sends (priority, id, …) are ignored rather
        // than rejected: a strict schema turns a familiar call into a failed tool.
        add({
          name: "todowrite",
          description:
            "Replace this session's todo list in the harness todo space (the sidebar's Todo row reads it, " +
            "and it is mirrored into opencode's own todo table). " +
            "Use it for multi-step work: one item in_progress at a time, completed as you finish.",
          input: {
            type: "object",
            properties: {
              todos: {
                type: "array",
                description: "The full list, in order. It replaces the previous list.",
                items: {
                  type: "object",
                  properties: {
                    content: { type: "string", description: "The task" },
                    status: { type: "string", enum: ["pending", "in_progress", "completed", "cancelled"] },
                    priority: {
                      type: "string",
                      enum: ["high", "medium", "low"],
                      description: "Optional opencode-native priority (kept when mirroring to opencode's todo table)",
                    },
                  },
                  required: ["content", "status"],
                },
              },
            },
            required: ["todos"],
            additionalProperties: false,
          },
          options: direct,
          execute: async (input: Rec, context: Rec) => {
            const sid = String(context?.sessionID ?? "")
            if (!sid) return { content: "todowrite needs an active session (no sessionID in the tool context)" }
            const rows = await writeTodos(directory, sid, input?.todos)
            const summary = rows.map(todoLine).join("\n")
            return { content: `${rows.length} todo${rows.length === 1 ? "" : "s"} in this session:\n${summary}` }
          },
        })

        add({
          name: "todoread",
          description: "Read this session's todo list from the harness todo space.",
          input: { type: "object", properties: {}, additionalProperties: false },
          options: direct,
          execute: async (_input: Rec, context: Rec) => {
            const sid = String(context?.sessionID ?? "")
            const rows = sid ? await readTodos(directory, sid) : []
            if (!rows.length) return { content: "(no todos in this session — call todowrite to set them)" }
            return { content: rows.map(todoLine).join("\n") }
          },
        })

        // A visible countdown sleep: the sidebar's Waits row counts the stored
        // deadline down while this sleeps, then this answers with a nudge to
        // check the task and act. Validation answers immediately (no record, no
        // sleep) so a bad call is a cheap correction, not a stuck wait.
        add({
          name: "wait",
          description:
            "Wait for something with a visible sidebar countdown, then check it. " +
            "Records the wait in project state (the sidebar's Waits row counts it down), " +
            "sleeps until the deadline without blocking the server, removes the record, " +
            "and returns a nudge telling you to check the task's status and act on it.",
          input: {
            type: "object",
            properties: {
              label: { type: "string", description: "What is being waited on (shown in the sidebar)" },
              timeout_s: { type: "number", description: "Seconds to wait (1..600)" },
              hint: { type: "string", description: "What to check when the wait expires" },
            },
            required: ["label", "timeout_s"],
            additionalProperties: false,
          },
          options: direct,
          execute: async (input: Rec) => {
            const label = String(input?.label ?? "").replace(/\s+/g, " ").trim().slice(0, 80)
            if (!label) return { content: "wait needs a label — pass {label, timeout_s} with timeout_s in seconds" }
            const raw = Number(input?.timeout_s)
            if (!Number.isFinite(raw) || raw <= 0)
              return { content: `wait needs timeout_s in seconds (1..${MAX_WAIT_S}) — got ${String(input?.timeout_s ?? "none")}` }
            const secs = Math.min(MAX_WAIT_S, Math.max(1, Math.floor(raw)))
            const hint = String(input?.hint ?? "").replace(/\s+/g, " ").trim().slice(0, 200)
            const deadline = Date.now() + secs * 1000
            const id = `${Date.now()}-${Math.floor(Math.random() * 1e6)}`
            await recordWait(directory, id, label, deadline)
            try {
              await sleep(secs * 1000)
            } finally {
              await clearWait(directory, id)
            }
            const check = hint ? hint : `the status of "${label}"`
            return { content: `Wait "${label}" expired after ${secs}s — check ${check} and act on what you find.` }
          },
        })
      })
    } catch (e) {
      log(`tool registration failed: ${short(e)}`)
    }

    // 3. Condense oversized tool results in place (errors untouched).
    try {
      // guarded: a host without tool hooks just loses condensing, not activation
      if (typeof ctx?.tool?.hook !== "function") throw new Error("ctx.tool.hook unavailable")
      await ctx.tool.hook("execute.after", (event: Rec) => {
        try {
          condenseToolResult(event)
        } catch {
          /* never break tool execution */
        }
      })
    } catch (e) {
      log(`tool hook failed: ${short(e)}`)
    }

    // 4. Compaction brief: the compaction run's system array is built here, so
    //    the newest durable facts AND the open todos ride along into the summary
    //    instead of being summarized away — a plan that only lives in the
    //    transcript is exactly what a compaction destroys. Verified payload
    //    (v2): the compaction hook receives
    //    {sessionID, model, system, messages, options, agent, tools}.
    try {
      // guarded: a host without session hooks just loses the brief, not activation
      if (typeof ctx?.session?.hook !== "function") throw new Error("ctx.session.hook unavailable")
      await ctx.session.hook("compaction", async (event: Rec) => {
        try {
          if (!Array.isArray(event?.system)) return
          // a retried compaction would otherwise stack duplicate briefs
          const already = event.system.some((s: Rec) => String(s?.text ?? s).includes(BRIEF_MARK))
          if (already) return
          const brief = (await facts(directory, [], 5, "recent")).join("\n")
          const todos = await readTodos(directory, String(event?.sessionID ?? ""))
          const open = todos.filter((t: Rec) => t.status !== "completed" && t.status !== "cancelled")
          if (!brief && !open.length) return
          const text = [`${BRIEF_MARK} (durable facts — verify before relying):`]
          if (brief) text.push(brief)
          if (open.length) text.push(`Open todos (harness todo space):\n${open.map(todoLine).join("\n")}`)
          event.system.push({ type: "text", text: text.join("\n") })
        } catch (e) {
          // never break compaction, but never fail silently either
          log(`compaction brief failed: ${short(e)}`)
        }
      })
    } catch (e) {
      log(`compaction hook failed: ${short(e)}`)
    }

    // 5. Auto-memory on the session "context" hook (see the section above for
    //    why this hook: no post-turn hook exists in opencode 2.x). Own guard
    //    block so a host that accepts "compaction" but rejects "context"
    //    loses only auto-memory, never the brief.
    try {
      // guarded: a host without session hooks just loses auto-memory, not activation
      if (typeof ctx?.session?.hook !== "function") throw new Error("ctx.session.hook unavailable")
      await ctx.session.hook("context", async (event: Rec) => {
        try {
          await captureMemory(directory, String(event?.sessionID ?? ""), event?.agent, event?.messages)
        } catch (e) {
          // never break generation, but never fail silently either
          log(`auto-memory capture failed: ${short(e)}`)
        }
      })
    } catch (e) {
      log(`auto-memory hook failed: ${short(e)}`)
    }

    // Nothing returned on purpose: a returned non-function value is treated as
    // a cleanup function and kills activation.
  },
}

export default HarnessPlugin
