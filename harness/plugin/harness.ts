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
//   3b. worker rows        ctx.tool.hook("execute.before", fn) inserts the
//      queued row, and the execute.after hook above marks it done/error
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

/** Directories that may hold bundled harness skills, best first: explicit
 * override, then the project overlay, then package data, then global. */
async function skillRoots(projectDir?: string): Promise<string[]> {
  const roots: string[] = []
  const home = process.env.HOME ?? ""
  if (process.env.HARNESS_SKILLS_DIR) roots.push(process.env.HARNESS_SKILLS_DIR)
  if (projectDir) roots.push(`${projectDir}/.opencode/harness/skills`)
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
async function seedSkills(projectDir?: string): Promise<Rec[]> {
  const out: Rec[] = []
  const seen = new Set<string>()
  const { join, basename, dirname } = await import("node:path")
  for (const root of await skillRoots(projectDir)) {
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
 * Escape a raw term for a LIKE pattern: `%`, `_` and `\` in the query must
 * match literally, never act as wildcards.
 */
function escapeLike(term: string): string {
  return String(term ?? "").replace(/[\\%_]/g, (c) => `\\${c}`)
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
  const where = mode === "recent" ? "1=1" : patterns.map(() => "text LIKE ? ESCAPE '\\'").join(" OR ")
  const params = mode === "recent" ? [] : patterns.map((p) => `%${escapeLike(p)}%`)
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
 * The compaction brief in ONE read-only open: newest facts + this session's
 * open todos. The hook used to call facts() then readTodos() — two opens of
 * the same DB per compaction fire for one text. Same queries, same shapes,
 * same text assembly; each half still guarded alone so a drifted table costs
 * only its own section.
 */
async function compactionBrief(projectDir: string, sessionID: string): Promise<string> {
  const { Database } = await import("bun:sqlite").catch(() => ({}) as any)
  if (!Database) return ""
  try {
    const dbPath = stateDb(projectDir)
    if (!(await Bun.file(dbPath).exists())) return ""
    const db = new Database(dbPath, { readonly: true })
    try {
      let brief = ""
      try {
        const rows = db.query("SELECT text, source FROM facts ORDER BY id DESC LIMIT ?").all(5) as any[]
        brief = rows.map((r) => `- [${r.source}] ${String(r.text).slice(0, 200)}`).join("\n")
      } catch {
        /* drifted facts table: todos may still ride */
      }
      let open: Rec[] = []
      try {
        const todos = db.query("SELECT text, status FROM todos WHERE session = ? ORDER BY id").all(sessionID) as Rec[]
        open = todos.filter((t: Rec) => t.status !== "completed" && t.status !== "cancelled")
      } catch {
        /* no todos table yet */
      }
      if (!brief && !open.length) return ""
      const text = [`${BRIEF_MARK} (durable facts — verify before relying):`]
      if (brief) text.push(brief)
      if (open.length) text.push(`Open todos (harness todo space):\n${open.map(todoLine).join("\n")}`)
      return text.join("\n")
    } finally {
      db.close()
    }
  } catch {
    return ""
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
      // One transaction: a crash between the DELETE and the INSERTs must not
      // leave opencode's own table emptier than it was — the mirror either
      // lands whole or rolls back to the previous list.
      db.run("BEGIN IMMEDIATE")
      try {
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
        db.run("COMMIT")
      } catch (e) {
        try {
          db.run("ROLLBACK")
        } catch {
          /* already rolled back */
        }
        throw e
      }
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
 * row lives only for the sleep and is deleted on expiry. A killed call never
 * reaches the delete, so every touch of the table also sweeps rows whose
 * deadline already passed (sweepExpiredWaits) — and the TUI hides expired
 * rows outright, so a stuck row can never linger in the panel. Best-effort on
 * purpose: a lost countdown must never fail the wait itself.
 */
/**
 * Delete countdown rows whose deadline already passed. A killed `wait` never
 * reaches its own clearWait (the host drops the call), so without this the row
 * would linger until the deadline's owner cleaned it — which is nobody. Every
 * touch of the table sweeps the dead instead. Best-effort like the rest.
 */
function sweepExpiredWaits(db: any): void {
  try {
    db.run("DELETE FROM waits WHERE deadline < ?", Date.now())
  } catch {
    /* table missing or busy */
  }
}

async function recordWait(projectDir: string, id: string, label: string, deadline: number): Promise<void> {
  const db = await todoDb(projectDir, true)
  if (!db) return
  try {
    db.run("CREATE TABLE IF NOT EXISTS waits(id TEXT PRIMARY KEY, label TEXT, deadline INTEGER)")
    db.run("INSERT OR REPLACE INTO waits(id, label, deadline) VALUES(?,?,?)", id, label, deadline)
    sweepExpiredWaits(db)
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
    sweepExpiredWaits(db)
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
const MEM_DEFAULT_CAP_LINES = 200
const MEM_MIN_EVIDENCE = 2
const MEM_MAX_EVIDENCE_LEN = 8000
const MEM_MAX_GIST_LEN = 200
/** History truncation parity with add_message() in harness/store.py. */
const HIST_MAX_CONTENT = 20000
const HIST_MAX_FTS = 8000
const MEM_HEADER = "# MEMORY.md — durable facts (cap {cap} lines, evidence-backed lessons only)"

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

/** message→text parse cache: one context fire walks the same history objects
 * twice (assistant texts, then the history tail) — parse each object once.
 * WeakMap, so entries die with the message objects; no growth concern. */
const memTextCache = new WeakMap<object, string>()

/** per-text parse cache: one fire derives lines+headline for facts and again
 * for lessons over the same fresh texts — parse once. Bounded (oldest first);
 * keys are exact (limit + full text), so hits are never approximate. */
const memParseCache = new Map<string, any>()
function memCachePut(k: string, v: any): void {
  memParseCache.set(k, v)
  if (memParseCache.size > 200) {
    const oldest = memParseCache.keys().next().value
    if (oldest !== undefined) memParseCache.delete(oldest)
  }
}
function memNormalize(text: unknown): string {
  const t = String(text ?? "").replace(/\s+/g, " ").trim()
  return t.replace(/^(?:[-*•]\s+|\d+[.)]\s+)/, "")
}

function memSecret(text: string): boolean {
  return MEM_SECRETS.some((r) => r.test(text))
}

/** The durable lines of one assistant message (mirrors important_lines()). */
function memImportantLines(text: string, limit = MEM_LINES_PER_MSG): string[] {
  const ck = `lines:${limit}:${text}`
  const hit = memParseCache.get(ck)
  if (Array.isArray(hit)) return hit
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
  const res = out.slice(0, limit)
  memCachePut(ck, res)
  return res
}

/** One-line summary of a message, for the progress note (mirrors headline()). */
function memHeadline(text: string, limit = MEM_HEADLINE_LIMIT): string {
  const ck = `head:${limit}:${text}`
  const hit = memParseCache.get(ck)
  if (typeof hit === "string") return hit
  let res = ""
  for (const raw of String(text ?? "").split("\n")) {
    const line = memNormalize(raw)
    if (line.length >= 8) {
      res = line.slice(0, limit)
      break
    }
  }
  memCachePut(ck, res)
  return res
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
    if (m && typeof m === "object") {
      const hit = memTextCache.get(m)
      if (hit !== undefined) return hit
    }
    let out = ""
    const parts = Array.isArray(m?.content) ? m.content : Array.isArray(m?.parts) ? m.parts : null
    if (parts) {
      out = parts
        .map((p: any) => (typeof p === "string" ? p : p?.type === "text" && typeof p?.text === "string" ? p.text : ""))
        .join("\n")
    } else if (typeof m?.content === "string") out = m.content
    else if (typeof m?.text === "string") out = m.text
    if (m && typeof m === "object") memTextCache.set(m, out)
    return out
  } catch {
    /* malformed message */
    return ""
  }
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
 * walking up to $HOME with nearest winning — the same chain load_config() in
 * harness/config.py merges, so a nested-dir project agrees with the CLI —
 * then the user file ($HARNESS_HOME, else ~/.harness) as the base layer.
 * Comment lines are stripped with the same (^\s)// rule the Python loader
 * uses; only enabled/max_facts are picked out, so a half-written file cannot
 * break anything. Fail-open: unreadable means enabled with defaults.
 */
async function memoryConfig(projectDir: string): Promise<{ enabled: boolean; maxFacts: number; capLines: number }> {
  let enabled = true
  let maxFacts = MEM_DEFAULT_MAX_FACTS
  let capLines = MEM_DEFAULT_CAP_LINES
  // Project candidates, farthest-first so the nearest wins — user file first
  // as the base, mirroring load_config's defaults < user < ancestor… < nearest.
  const projectFiles: string[] = []
  try {
    const { resolve, dirname } = await import("node:path")
    const home = String(process.env.HOME ?? "")
    let cur = resolve(projectDir)
    for (;;) {
      projectFiles.push(`${cur}/.opencode/harness.jsonc`)
      const parent = dirname(cur)
      if ((home && cur === home) || parent === cur || cur === "/") break
      cur = parent
    }
  } catch {
    projectFiles.push(`${projectDir}/.opencode/harness.jsonc`)
  }
  const candidates = [
    `${process.env.HARNESS_HOME || `${process.env.HOME}/.harness`}/harness.jsonc`,
    ...projectFiles.reverse(),
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
    // Nearest file wins (later candidates override), mirroring _deep_merge.
    if (/"enabled"\s*:\s*false/.test(mem)) enabled = false
    else if (/"enabled"\s*:\s*true/.test(mem)) enabled = true
    const cap = mem.match(/"max_facts"\s*:\s*(-?\d+)/)?.[1]
    if (cap !== undefined) {
      const n = parseInt(cap, 10)
      if (Number.isFinite(n)) maxFacts = n
    }
    // Same chain as max_facts above; clamped at write time like _write_entries.
    const lines = mem.match(/"cap_lines"\s*:\s*(-?\d+)/)?.[1]
    if (lines !== undefined) {
      const n = parseInt(lines, 10)
      if (Number.isFinite(n)) capLines = n
    }
  }
  return { enabled, maxFacts, capLines }
}

/** Read-write open of the project state DB, creating the live tables in the
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
    db.run(
      "CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT, session TEXT, role TEXT, content TEXT, ts INTEGER)",
    )
    db.run("CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(content, session UNINDEXED, tokenize='porter')")
    db.run("CREATE TABLE IF NOT EXISTS context_mark(session TEXT PRIMARY KEY, seen INTEGER)")
    db.run("CREATE TABLE IF NOT EXISTS pending(id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, name TEXT, diff TEXT, gist TEXT, ts INTEGER)")
    db.run(
      "CREATE TABLE IF NOT EXISTS workers(id TEXT PRIMARY KEY, session TEXT, name TEXT, category TEXT, model TEXT, status TEXT, cost REAL, updated INTEGER)",
    )
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

// ---------------------------------------------------------------------------
// History feed: the retired headless writer (add_message in store.py) stored
// every turn's user/assistant text into messages/messages_fts with 20k/8k
// truncation, and recall()'s snippet half reads that FTS index. Live sessions
// never run through it, so the index sat empty. The context hook already sees
// the full {sessionID, messages} history, so it appends only the unseen tail:
// a persistent per-session high-water-mark (context_mark, survives restarts —
// the in-memory cursor alone would re-append everything after a restart).
// Cost is bounded by the same 20k/8k truncation parity.
// ---------------------------------------------------------------------------

function histRole(m: Rec): string {
  return String(m?.role ?? m?.info?.role ?? "unknown").slice(0, 32)
}

async function captureHistory(db: any, sessionID: string, messages: any): Promise<void> {
  if (!sessionID || !Array.isArray(messages) || !messages.length) return
  try {
    let start = 0
    try {
      const row = db.query("SELECT seen FROM context_mark WHERE session = ?").get(sessionID) as any
      start = Number(row?.seen ?? 0) || 0
    } catch {
      start = 0
    }
    // A rewritten history (e.g. a compaction summary replacing the transcript)
    // sits below the old mark: re-baseline from zero so the summary is captured.
    if (start > messages.length) start = 0
    if (start >= messages.length) return
    const now = Math.floor(Date.now() / 1000)
    for (let i = start; i < messages.length; i++) {
      const raw = memMessageText(messages[i] as Rec)
      if (!memNormalize(raw)) continue
      try {
        db.run(
          "INSERT INTO messages(session,role,content,ts) VALUES(?,?,?,?)",
          sessionID,
          histRole(messages[i] as Rec),
          raw.slice(0, HIST_MAX_CONTENT),
          now,
        )
        try {
          db.run("INSERT INTO messages_fts(content,session) VALUES(?,?)", raw.slice(0, HIST_MAX_FTS), sessionID)
        } catch {
          /* fts is best-effort; the row above is the record */
        }
      } catch {
        /* one bad row never blocks the rest */
      }
    }
    try {
      db.run("INSERT OR REPLACE INTO context_mark(session,seen) VALUES(?,?)", sessionID, messages.length)
    } catch {
      /* high-water-mark best-effort; the next firing retries the tail */
    }
  } catch {
    /* never break generation */
  }
}

// ---------------------------------------------------------------------------
// Lesson loop: ports stage_lesson/auto_refine from harness/memory.py. Each
// durable assistant line (the same memImportantLines vocabulary that feeds the
// facts) stages one evidence excerpt into `pending`; when >=2 DISTINCT
// excerpts share a lesson name the lesson auto-writes to the project's
// MEMORY.md (cap-enforced, secret-refused, dupe-checked — same guarantees as
// add_lesson) and the staged rows are consumed. Junk parity with
// _is_junk_turn plus the secret rules: mock/empty/secret excerpts never stage.
// Trigger is assistant text only — tool output is never staged.
// ---------------------------------------------------------------------------

/** Lesson bucket for one durable line: its leading marker, else "outcome". */
function memLessonName(line: string): string {
  const low = memNormalize(line).toLowerCase()
  for (const m of MEM_MARKERS) {
    if (low.startsWith(m) || low.includes(` ${m}`)) return m.replace(/[:\s]+$/, "").slice(0, 80) || "lesson"
  }
  return "outcome"
}

/** Stage one evidence excerpt (mirrors stage_lesson(): no dedupe here —
// distinctness is the promotion gate, computed in memEvidence). */
function memStageLesson(db: any, name: string, excerpt: string, gist: string): number {
  try {
    db.run("INSERT INTO pending(kind,name,diff,gist,ts) VALUES(?,?,?,?,?)",
      "lesson",
      String(name ?? "lesson").slice(0, 80),
      String(excerpt ?? "").slice(0, MEM_MAX_EVIDENCE_LEN),
      String(gist ?? "").slice(0, MEM_MAX_GIST_LEN),
      Math.floor(Date.now() / 1000),
    )
    return Number(db.query("SELECT last_insert_rowid() AS id").get()?.id ?? 0) || 0
  } catch {
    return 0
  }
}

/** The distinct excerpts staged for a lesson (mirrors evidence()). */
function memEvidence(db: any, name: string): string[] {
  try {
    const rows = db.query("SELECT diff FROM pending WHERE kind='lesson' AND name=? ORDER BY id").all(name) as any[]
    const seen = new Set<string>()
    const out: string[] = []
    for (const r of rows) {
      const n = memNormalize(r?.diff).slice(0, 600)
      if (n && !seen.has(n)) {
        seen.add(n)
        out.push(n)
      }
    }
    return out
  } catch {
    return []
  }
}

/** Append one lesson entry to the project MEMORY.md (mirrors add_lesson():
 * secret refusal, dupe check, line cap). Entries are `- [name] body` lines. */
async function memAddLesson(memoryPath: string, name: string, text: string, capLines: number): Promise<boolean> {
  const body = memNormalize(text)
  if (!body || memSecret(body)) return false
  const entry = `- [${String(name ?? "lesson").slice(0, 60)}] ${body.slice(0, 500)}`
  try {
    let header = ""
    const entries: string[] = []
    if (await Bun.file(memoryPath).exists()) {
      let pastHeader = false
      for (const ln of (await Bun.file(memoryPath).text()).split("\n")) {
        if (ln.startsWith("- ")) pastHeader = true
        if (ln.startsWith("- ")) entries.push(ln)
        else if (!pastHeader) header = header ? `${header}\n${ln}` : ln
      }
      header = header.trim()
    }
    if (entries.includes(entry)) return false
    entries.push(entry)
    const cap = Math.max(1, Math.floor(Number(capLines) || MEM_DEFAULT_CAP_LINES))
    const kept = entries.length > cap ? entries.slice(-cap) : entries
    const head = header || MEM_HEADER.replace("{cap}", String(cap))
    await Bun.write(memoryPath, `${head}\n${kept.length ? `${kept.join("\n")}\n` : ""}`)
    return true
  } catch {
    return false
  }
}

/** Promote lessons with >=2 distinct excerpts (mirrors auto_refine()). */
async function memAutoRefine(db: any, memoryPath: string, capLines: number, minEvidence = MEM_MIN_EVIDENCE): Promise<string[]> {
  const promoted: string[] = []
  let rows: any[] = []
  try {
    rows = db.query(
      "SELECT name, COUNT(DISTINCT diff), COUNT(*) FROM pending WHERE kind='lesson' GROUP BY name",
    ).all() as any[]
  } catch {
    return []
  }
  for (const r of rows) {
    const vals = (Array.isArray(r) ? r : Object.values(r ?? {})) as any[]
    const name = String(vals[0] ?? "")
    if (!name) continue
    const ev = memEvidence(db, name)
    if (ev.length < Math.max(2, minEvidence)) continue
    let gist = name
    try {
      const g = db.query("SELECT gist FROM pending WHERE kind='lesson' AND name=? ORDER BY id DESC LIMIT 1").get(name) as any
      if (String(g?.gist ?? "").trim()) gist = String(g.gist).trim()
    } catch {
      /* keep the name */
    }
    const text = `${gist} :: ${ev.slice(0, 2).map((e) => e.slice(0, 200)).join(" | ")}`
    if (await memAddLesson(memoryPath, name, text, capLines)) promoted.push(name)
    try {
      db.run("DELETE FROM pending WHERE kind='lesson' AND name=?", name)
    } catch {
      /* consume best-effort */
    }
  }
  return promoted
}

async function captureLessons(db: any, projectDir: string, fresh: string[], capLines: number): Promise<string[]> {
  try {
    for (const t of fresh) {
      // Mock-model echoes are the harness talking to itself, never evidence.
      if (memNormalize(memHeadline(t)).startsWith("[mock:")) continue
      for (const line of memImportantLines(t)) {
        // Normalization must never hide a credential: check the staged form.
        if (memSecret(line)) continue
        memStageLesson(db, memLessonName(line), line, line.slice(0, 120))
      }
    }
    return await memAutoRefine(db, `${projectDir}/MEMORY.md`, capLines)
  } catch {
    return []
  }
}

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
  for (const t of fresh) {
    seen.push(memNormalize(t))
    while (seen.length > MEM_SEEN_PER_SESSION) seen.shift()
  }
  const db = await memDb(projectDir)
  if (!db) return
  try {
    // The history tail advances on every role (a user-only turn still grows
    // the transcript), so it runs before the fresh-assistant gate below.
    await captureHistory(db, sessionID, messages)
    if (!fresh.length) return
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
    // Same firing, same guards: one staged lesson excerpt per durable line
    // (auto-promoted at 2 distinct excerpts).
    await captureLessons(db, projectDir, fresh, cfg.capLines)
  } finally {
    try {
      db.close()
    } catch {
      /* already closed */
    }
  }
}

function condenseText(raw: string, command = ""): string {
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
  // caveman_lite port (harness/compress.py): filler-strip with code/URL/JSON/
  // path protection, then blank-run collapse and adjacent-dupe drop. Leading
  // indentation is kept (tool output, not prose — caveman stripped it).
  let out = stripFillers(kept.join("\n")).split("\n")
  // Per-command head/tail budgets (the FILTERS head/tail column); the default
  // window is the old 24/48/20 shape. Elision keeps the head and tail and
  // hoists mid-window ERROR_LINEs — errors are sacred even inside the cut.
  const budget = condenseBudget(command)
  if (out.length > budget.max) {
    const head = out.slice(0, budget.head)
    const tail = budget.tail > 0 ? out.slice(-budget.tail) : []
    const mid = out.slice(budget.head, out.length - tail.length)
    const sacred = mid.filter((ln) => ERROR_LINE.test(ln))
    out = [...head, `... [${mid.length - sacred.length} lines elided] ...`, ...sacred, ...tail]
  }
  const text = out.join("\n")
  if (text.length >= raw.length) return raw
  const pct = Math.round(100 * (1 - text.length / raw.length))
  return `${text}\n  [harness: condensed ${pct}% (${raw.length} → ${text.length} chars). Head and tail kept; re-run a narrower command for the middle.]`
}

/** Filler words the prose condenser strips (port of FILLER_RE). */
const CONDENSE_FILLER = /\b(very|really|quite|rather|basically|essentially|actually|just|simply|in order to|due to the fact that|it is important to note that|as a matter of fact|for all intents and purposes)\b/gi
const CONDENSE_CODE = /```[\s\S]*?```|`[^`\n]+`/g
const CONDENSE_URL = /https?:\/\/\S+/g
const CONDENSE_JSON = /\{[^{}]{20,}\}|\[[^\[\]]{20,}\]/g
const CONDENSE_PATH = /(?:~?\/[A-Za-z0-9_.\-]+)+\/?/g

/** Per-command head/tail budgets (port of the FILTERS head/tail column). */
const CONDENSE_BUDGETS: Array<{ cmds: string[]; head: number; tail: number; max: number }> = [
  { cmds: ["ls", "find", "grep", "rg", "cat", "lsblk", "df", "ps"], head: 30, tail: 20, max: 60 },
  { cmds: ["pytest", "python", "python3", "uv"], head: 8, tail: 10, max: 40 },
  { cmds: ["npm", "npx", "node", "bun", "tsc", "eslint"], head: 6, tail: 8, max: 40 },
  { cmds: ["git"], head: 10, tail: 10, max: 60 },
  { cmds: ["pip", "poetry"], head: 4, tail: 6, max: 25 },
]
const CONDENSE_DEFAULT = { head: 24, tail: 20, max: 48 }

function condenseBudget(command: string): { head: number; tail: number; max: number } {
  const cmd = String(command ?? "").trim().split(/\s+/)[0]?.toLowerCase() ?? ""
  for (const b of CONDENSE_BUDGETS) if (cmd && b.cmds.includes(cmd)) return b
  return CONDENSE_DEFAULT
}

/** Best-effort command name out of an execute.after event (shape differs by host version). */
function condenseCommand(event: Rec): string {
  const cands = [event?.command, event?.tool, event?.toolName, event?.name, event?.input?.command, event?.args?.command]
  for (const c of cands) if (typeof c === "string" && c.trim()) return c
  return ""
}

/** Filler-strip with placeholder protection (port of caveman_lite's vault). */
function stripFillers(text: string): string {
  const vault: string[] = []
  const stash = (m: string): string => {
    vault.push(m)
    return `\x00V${vault.length - 1}\x00`
  }
  let tmp = text
    .replace(CONDENSE_CODE, stash)
    .replace(CONDENSE_URL, stash)
    .replace(CONDENSE_JSON, stash)
    .replace(CONDENSE_PATH, stash)
  tmp = tmp.replace(CONDENSE_FILLER, "")
  tmp = tmp.replace(/[ \t]+/g, " ").replace(/\n{3,}/g, "\n\n")
  const lines: string[] = []
  let prev: string | null = null
  for (const ln of tmp.split("\n")) {
    const line = ln.trimEnd()
    if (line === prev && line.trim()) continue
    prev = line
    lines.push(line)
  }
  return lines
    .join("\n")
    .replace(/\x00V(\d+)\x00/g, (_m, i) => vault[Number(i)] ?? _m)
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
    part.text = condenseText(part.text, condenseCommand(event))
  }
}

// ---------------------------------------------------------------------------
// 6. Worker rows: the subagent tool is registered through the standard
//    tool.transform/add path (opencode.tool.subagent, codemode:false) and every
//    model tool call runs through the one Tool.execute dispatch, which triggers
//    execute.before ({tool, sessionID, id, input}) before the call and
//    execute.after ({tool, id, status, ...}) on completion — verified against
//    the opencode v2.0.18 server bundle (see the item-4 probe notes). So
//    execute.before on a subagent call INSERTs a queued row into the existing
//    `workers` table and execute.after marks it done/error. Both names are
//    matched: `subagent` (live) and `task` (legacy alias). Stale stays computed,
//    never written — the TUI already does that — and only queued/running rows
//    ever transition, so a terminal row is never clobbered.
// ---------------------------------------------------------------------------

/** Tool names that spawn a subagent session (live name + legacy alias). */
const WORKER_TOOLS = new Set(["subagent", "task"])

function workerName(input: Rec): string {
  for (const k of ["description", "prompt", "agent", "subagent_type", "category"]) {
    const v = String(input?.[k] ?? "").replace(/\s+/g, " ").trim()
    if (v) return v.slice(0, 80)
  }
  return "subagent"
}

function workerId(event: Rec): string {
  const id = String(event?.id ?? event?.callID ?? "")
  if (id) return id.slice(0, 80)
  return `${Date.now()}-${Math.floor(Math.random() * 1e6)}`
}

async function workerStarted(projectDir: string, event: Rec): Promise<void> {
  try {
    if (!WORKER_TOOLS.has(String(event?.tool ?? ""))) return
    const input = ((event?.input ?? {}) as Rec) ?? {}
    const db = await memDb(projectDir)
    if (!db) return
    try {
      db.run(
        "INSERT OR IGNORE INTO workers(id,session,name,category,model,status,cost,updated) VALUES(?,?,?,?,?,?,?,?)",
        workerId(event),
        String(event?.sessionID ?? ""),
        workerName(input),
        String(input?.subagent_type ?? input?.category ?? ""),
        String(input?.model ?? ""),
        "queued",
        0,
        Math.floor(Date.now() / 1000),
      )
    } finally {
      try {
        db.close()
      } catch {
        /* already closed */
      }
    }
  } catch {
    /* never break tool execution */
  }
}

async function workerFinished(projectDir: string, event: Rec): Promise<void> {
  try {
    if (!WORKER_TOOLS.has(String(event?.tool ?? ""))) return
    const id = String(event?.id ?? event?.callID ?? "")
    if (!id) return
    const db = await memDb(projectDir)
    if (!db) return
    try {
      db.run(
        "UPDATE workers SET status=?, updated=? WHERE id=? AND status IN ('queued','running')",
        event?.status === "error" ? "error" : "done",
        Math.floor(Date.now() / 1000),
        id,
      )
      // Fusion: a finished subagent is a verified outcome (unlike the context
      // hook, which only ever writes `progress`), so it also drops one memory
      // note through the existing writer — done→done, error→blocked. Same
      // guarantees: opt-out honored, secrets refused, dupe-checked (a repeat
      // fire for the same call writes the identical text, which the writer
      // drops), capped. Never breaks tool execution.
      try {
        const row = db.query("SELECT name, session FROM workers WHERE id=?").get(id) as any
        const wname = String(row?.name ?? event?.input?.description ?? "").replace(/\s+/g, " ").trim().slice(0, 80)
        const wsession = String(row?.session ?? event?.sessionID ?? id).slice(0, 24)
        const status = event?.status === "error" ? "blocked" : "done"
        const note = `${status} [${wsession}]: worker ${wname || "subagent"} ${status === "done" ? "finished" : "errored"}`
        if (note && !memSecret(note)) {
          const cfg = await memoryConfig(projectDir)
          if (cfg.enabled) memSaveFact(db, note, "progress", cfg.maxFacts)
        }
      } catch {
        /* memory note best-effort; the worker row above is the record */
      }
    } finally {
      try {
        db.close()
      } catch {
        /* already closed */
      }
    }
  } catch {
    /* never break tool execution */
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
      const seeds = await seedSkills(directory)
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
            if (!items.length) items = await seedSkills(directory)
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
            if (!items.length) items = await seedSkills(directory)
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

    // 3. Condense oversized tool results in place (errors untouched), and mark
    //    subagent calls done/error in the workers table (see section 6: the
    //    same after-event carries both).
    try {
      // guarded: a host without tool hooks just loses condensing, not activation
      if (typeof ctx?.tool?.hook !== "function") throw new Error("ctx.tool.hook unavailable")
      await ctx.tool.hook("execute.after", async (event: Rec) => {
        try {
          condenseToolResult(event)
        } catch {
          /* never break tool execution */
        }
        try {
          await workerFinished(directory, event)
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
          const text = await compactionBrief(directory, String(event?.sessionID ?? ""))
          if (!text) return
          event.system.push({ type: "text", text })
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

    // 6. Worker rows on the tool "execute.before" hook (see the section above
    //    for why this fires for subagent calls: one dispatch, every tool). Own
    //    guard block so a host that accepts "execute.after" but rejects
    //    "execute.before" loses only the queued rows, never condensing.
    try {
      // guarded: a host without tool hooks just loses worker rows, not activation
      if (typeof ctx?.tool?.hook !== "function") throw new Error("ctx.tool.hook unavailable")
      await ctx.tool.hook("execute.before", async (event: Rec) => {
        try {
          await workerStarted(directory, event)
        } catch {
          /* never break tool execution */
        }
      })
    } catch (e) {
      log(`worker hook failed: ${short(e)}`)
    }

    // Nothing returned on purpose: a returned non-function value is treated as
    // a cleanup function and kills activation.
  },
}

export default HarnessPlugin
