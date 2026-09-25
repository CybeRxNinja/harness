// Harness CLI plugin for the opencode TUI.
//
// It fills opencode's EXISTING side panel (the `sidebar.content` slot) with a
// stats/info panel — Window, Tokens, Models, Todo, Workers, Waits, Skills,
// Agents, Memory — and nothing else moves: no routes, no docked overlay, no replaced
// slots. It is
// built to read as part of opencode rather than bolted onto it:
//
//   * same visual grammar as opencode's own sidebar rows — a label in
//     `theme.text.base` and a value in `theme.text.muted`, on one line, with the
//     label taking `flexGrow` and the value `flexShrink 0` so the value pins to
//     the right edge at any panel width (opencode's own MCP rows do this);
//   * same numbers as opencode's readout (total tokens = in+out+reasoning+
//     cache read+write; window % = LAST assistant message / model.limit.context,
//     opencode's own header rule) — and the usage totals plus the Models steps
//     cover this session's `task` subagent sessions too: a child runs as a
//     SEPARATE Session.Info with its own tokens, discovered via
//     `session.sync(sid, {children:true})` + `session.family(sid)` — the same
//     set opencode's own `session.cost` sums. The Window stays this-session:
//     a context window belongs to one session, never to the family;
//   * no duplicate "Context" section — opencode already renders one — so the
//     panel adds what it doesn't show (breakdown, window size, skills, memory).
//
// Contract (probed live on opencode 2.0.11, never assumed):
//   * Lives beside server.ts in ~/.config/opencode/plugins/harness/. The loader
//     probes a plugin DIRECTORY for `server.*` and `tui.*` entrypoints and sets
//     features.tui (the flag the Plugins panel filters on) only when a tui
//     entrypoint exists.
//   * The TUI compiles this .tsx itself (JSX -> OpenTUI elements), so JSX needs
//     no imports. No static imports on purpose: the TUI bundles its own
//     solid-js and a second copy would break reactivity, so state comes from
//     `ctx.storage.memory` instead. Dynamic `import("…")` is fine; `require` and
//     `Bun` are NOT defined in this scope (`node:fs` and `bun:sqlite` resolve).
//   * `sidebar.content` receives `{sessionID}` — the ACTIVE session id.
//   * `ctx.theme.text` is `{base, muted, action, formfield, feedback{...}}`.
//   * `ctx.data.session.get(sid)` -> `{agent, model:{id,providerID,variant},
//     tokens:{input,output,reasoning,cache:{read,write}}, cost, title, …}`;
//     `ctx.data.session.cost(sid)` is opencode's own cost accessor.
//   * opencode 2.0.14 has no todo tool and writes no todos: the Todo row reads
//     the HARNESS todo space (`<project>/.opencode/harness/sessions.db`, the
//     `todos` table the server half's `todowrite` writes), and falls back to
//     the newest list in that space. Degrades to "none" on schema drift.
//   * The Workers row reads the RLM pool out of the same DB (`workers`), keyed
//     by PROJECT — `rlm.spawn` leaves `workers.session` empty, so a worker is
//     not this session's. It shows the persisted status plus the age of the
//     last update; the authoritative stale verdict stays `harness doctor`'s.
//   * The Waits row reads the `wait` tool's countdowns out of the same DB
//     (`waits`: label + deadline), PROJECT-wide like Workers. It auto-expands
//     while any wait is pending and hides when none is.
//   * `ctx.keymap.layer(...)` throws "Keymap.Provider is missing" unless it is
//     called from inside a slot's render component, so commands are registered
//     from the `app` slot (the pattern the CLI-plugin docs use).
//   * Data stores need `sync(location)` before `list(location)` returns
//     anything, and a sync can be slow (it is the only network-touching step):
//     every scan is therefore time-boxed, so the panel always settles instead
//     of sitting on a placeholder forever.
//
// Every step is guarded: a TUI API drift degrades to a log line, a muted row or
// a "—" value — never a broken screen and never a stuck placeholder.

type Rec = Record<string, any>

const STATE = "harness.sidebar.state"
const HARNESS_PREFIX = "harness-"
const POLL_MS = 8000
/** A store sync can block on the network; past this the panel shows what it has. */
const SCAN_MS = 5000
const ROW_LIMIT = 6
/** Detail rows per section when expanded (the sidebar viewport is short). */
const DETAIL_LIMIT = 5

/**
 * opencode's internal agents run its own plumbing (conversation compaction,
 * title generation). They are not work roles a user can pick, and listing
 * them advertised capabilities nobody could spawn — while inflating the
 * "Agents available" count above the rows actually shown. Filtered at load,
 * so the count and the list can never disagree.
 */
const INTERNAL_AGENTS = new Set(["compaction", "title", "summarize", "summary"])

/** Status glyphs, shared with the server half's todo space. */
const TODO_MARKS: Rec = { completed: "●", in_progress: "◐", cancelled: "✕", pending: "○" }
const todoMark = (status: unknown) => TODO_MARKS[String(status)] ?? "○"

/**
 * Worker lifecycle glyphs (harness/rlm.py). `!` for a timed-out worker and `~`
 * for a stale one are ASCII on purpose — the status word is printed next to the
 * glyph, so the mark only has to group unfinished / failed / finished.
 */
const WORKER_MARKS: Rec = { done: "●", running: "◐", queued: "○", error: "✕", timeout: "!", stale: "~" }
const workerMark = (status: unknown) => WORKER_MARKS[String(status)] ?? "·"
/** Still-working statuses, i.e. the ones whose age is worth showing. */
const WORKER_LIVE = new Set(["queued", "running"])

/**
 * Memory-note glyphs, like TODO_MARKS/WORKER_MARKS above: a progress note
 * reads as its status, not as the "✎" every fact used to get.
 */
const MEMORY_MARKS: Rec = { progress: "◐", done: "●", blocked: "✕", error: "⚠" }
/** "progress [s_123]: did the thing" -> status, session, core message. */
const MEMORY_RE = /^(progress|done|blocked|error) \[(.+?)\]: (.*)$/

/**
 * "how long ago" for a worker row, from its `updated` epoch SECONDS
 * (`int(time.time())` in harness/rlm.py — not milliseconds).
 *
 * Deliberately not a stale verdict: `harness doctor` owns that rule
 * (`budgets.worker_timeout_s` × 2), and a second definition here would drift
 * from it. `◐ build:panel · running 42m` says the same thing honestly.
 */
function age(updated: unknown): string {
  const t = Number(updated ?? 0)
  if (!Number.isFinite(t) || t <= 0) return ""
  const secs = Math.max(0, Math.round((Date.now() - t * 1000) / 1000))
  if (secs < 60) return `${secs}s`
  if (secs < 3600) return `${Math.round(secs / 60)}m`
  return `${Math.round(secs / 3600)}h`
}

/**
 * "time left" for a wait row, from its `deadline` epoch MILLISECONDS (the
 * server half's `wait` tool stores Date.now() + timeout_s * 1000 — not the
 * workers' epoch seconds above). Past-due reads as "due": the record is
 * removed on expiry, so this only covers the race between the last poll and
 * the delete. Remainders are zero-padded so the column stays put at ~44 wide.
 */
function left(deadline: unknown): string {
  const ms = Number(deadline ?? 0) - Date.now()
  if (!Number.isFinite(ms) || ms <= 0) return "due"
  const s = Math.ceil(ms / 1000)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m${String(s % 60).padStart(2, "0")}s`
  return `${Math.floor(m / 60)}h${String(m % 60).padStart(2, "0")}m`
}

/**
 * opencode's skill store namespaces the seeded skills (`harness-spec-driven-
 * development`), but the panel is ~44 columns wide and the namespace is the
 * same for every row — so the row shows the skill itself (`spec-driven-
 * development`). Only the display is shortened; ids keep the prefix.
 */
const shortSkill = (id: unknown) => String(id ?? "").replace(/^harness-/, "")

const log = (m: string) => console.error(`[harness tui] ${m}`)
const short = (e: unknown) => (e instanceof Error ? e.message : String(e)).slice(0, 140)

/** 12345 -> "12,345" (opencode's own readout uses toLocaleString) */
function fmt(n: unknown): string {
  const v = Math.round(Number(n ?? 0))
  if (!Number.isFinite(v)) return "0"
  return v.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",")
}

/** 9603 -> "9.6k" (row-sized) */
function kfmt(n: unknown): string {
  const v = Number(n ?? 0)
  if (!Number.isFinite(v) || v <= 0) return "0"
  if (v < 1000) return String(Math.round(v))
  if (v < 1_000_000) return `${(v / 1000).toFixed(1)}k`
  return `${(v / 1_000_000).toFixed(1)}m`
}

/** 9603 -> "9,603" — full precision where there is room (detail rows, chip). */
function numfmt(n: unknown): string {
  return Number.isFinite(Number(n)) ? fmt(n) : "0"
}

function cut(value: unknown, max: number): string {
  const s = String(value ?? "").replace(/\s+/g, " ").trim()
  return s.length <= max ? s : `${s.slice(0, Math.max(1, max - 1))}…`
}

/**
 * cut() at a word boundary instead of mid-word: the Memory rows showed
 * "progress [s_5526202c]: [mock:…" — cut at 30 cols mid-token, six times
 * over. Falls back to a hard cut for one long token with no space to break.
 */
function wcut(value: unknown, max: number): string {
  const s = String(value ?? "").replace(/\s+/g, " ").trim()
  if (s.length <= max) return s
  const head = s.slice(0, Math.max(1, max - 1))
  const i = head.lastIndexOf(" ")
  return (i > 0 ? head.slice(0, i) : head) + "…"
}

/**
 * One Memory detail row per fact: a progress note ("done [s_x]: …") renders
 * as its status mark plus the core message, any other fact keeps "✎".
 * Consecutive identical rows collapse with a ×N suffix, so six mock-echo
 * notes read as one line ("◐ echo: hello in mock ×6") until retention
 * prunes them. Shapes only the rows already read — never writes the DB.
 */
function humanizeFacts(rows: Rec[]): string[] {
  const rendered = rows.map((r) => {
    const text = String(r?.text ?? "")
    const m = MEMORY_RE.exec(text)
    if (!m) return "✎ " + wcut(text, 30)
    const mark = MEMORY_MARKS[m[1]] ?? "✎"
    return mark + " " + wcut(m[3], 30)
  })
  const out: string[] = []
  for (const line of rendered) {
    const prev = out.length ? out[out.length - 1] : ""
    const g = /^(.*) ×(\d+)$/.exec(prev)
    const core = g ? g[1] : prev
    if (prev && core === line) {
      out[out.length - 1] = line + " ×" + (g ? Number(g[2]) + 1 : 2)
    } else {
      out.push(line)
    }
  }
  return out
}

/**
 * A usage bar for the context window. Always shows at least one cell for any
 * non-zero usage: rounding 1% of eight cells to zero would read as "unused".
 */
function bar(pct: number, cells = 8): string {
  const clamped = Math.max(0, Math.min(100, Number(pct) || 0))
  let filled = Math.round((clamped / 100) * cells)
  if (clamped > 0 && filled < 1) filled = 1
  return `${"█".repeat(filled)}${"░".repeat(cells - filled)}`
}

/**
 * A session/message `model` is `{id, providerID, variant}` or a bare string.
 * Returns "provider/id" — the shape `shortModel` and the context lookup expect.
 */
function modelId(value: unknown): string {
  if (!value) return ""
  if (typeof value === "string") return value
  const v = value as Rec
  const id = String(v.id ?? v.modelID ?? "")
  const prov = String(v.providerID ?? "")
  if (!id) return prov
  return prov ? `${prov}/${id}` : id
}

/** "opencode/muse-spark" -> ["opencode", "muse-spark"] */
function splitModel(value: unknown): [string, string] {
  const id = String(value ?? "")
  const i = id.indexOf("/")
  return i < 0 ? ["", id] : [id.slice(0, i), id.slice(i + 1)]
}

function shortModel(rid: unknown): string {
  return cut(splitModel(rid)[1], 24)
}

/** Total tokens the way opencode counts them (input+output+reasoning+cache). */
function totalTokens(tokens: Rec | null): number {
  if (!tokens) return 0
  const cache = tokens.cache ?? {}
  return (
    Number(tokens.input ?? 0) +
    Number(tokens.output ?? 0) +
    Number(tokens.reasoning ?? 0) +
    Number(cache.read ?? 0) +
    Number(cache.write ?? 0)
  )
}

/** Normalize whatever a data store hands back into an array. */
function asArray(listed: any): Rec[] {
  for (const c of [listed, listed?.data, listed?.items, listed?.value]) {
    if (Array.isArray(c)) return c
    for (const inner of [c?.data, c?.items]) if (Array.isArray(inner)) return inner
  }
  return []
}

/** Resolve `undefined` after `ms` — used to time-box a promise that may block. */
function settle(p: any, ms: number): Promise<void> {
  return Promise.race([
    Promise.resolve(p).catch(() => {}),
    new Promise<void>((resolve) => setTimeout(resolve, ms)),
  ]).then(() => {})
}

/**
 * This session plus its DIRECT subagent sessions. The `task` tool's children
 * run as SEPARATE opencode sessions with their own `tokens`/`cost` rows, so a
 * readout built from `session.get(sid)` alone dropped everything the workers
 * spent — the "tokens do not account for the sub-agents" bug.
 *
 * `session.family(sid)` is opencode's own accessor for this set (the one its
 * `session.cost` sums for a root row). It stays empty until a
 * `session.sync(sid, {children:true})` has discovered the children, so this
 * always includes the active session and degrades to "just me" while
 * discovery is in flight — never to zero.
 */
function familyIds(data_: Rec, sid: string): string[] {
  if (!sid) return []
  let fam: string[] = []
  try {
    fam = asArray(data_?.session?.family?.(sid)).map((r: Rec) => String(r ?? "")).filter(Boolean)
  } catch {
    /* API drift: fall back to a solo session */
  }
  const kids: string[] = []
  for (const id of fam) {
    if (id === sid) continue
    try {
      // family() answers for the TREE ROOT, so a subagent session opened in
      // the TUI must not inherit its siblings' usage: direct children only
      // (depth is 1 — subagents carry `task: deny`).
      if (String(data_?.session?.get?.(id)?.parentID ?? "") === sid) kids.push(id)
    } catch {
      /* info row not loaded yet */
    }
  }
  return [sid, ...kids]
}

// Project-local only: a $HOME probe here recalled a different project's facts
// (state_dir() in harness/paths.py is always <root>/.opencode/harness).
// One file holds both the fact store and the todo space — it is the DB the
// Python core owns, not the TUI's business to relocate.
function stateDb(directory: string): string {
  return `${directory}/.opencode/harness/sessions.db`
}

const HarnessTui = {
  id: "harness",

  setup(ctx: Rec) {
    if (!ctx?.ui || typeof ctx.ui.slot !== "function") {
      log("ui.slot unavailable on this opencode version — TUI views skipped")
      return
    }

    // opencode's own sidebar palette. `base` is the label colour and `muted`
    // the value colour in its rows; `action` marks an interactive row.
    const th = {
      base: ctx?.theme?.text?.base ?? "white",
      muted: ctx?.theme?.text?.muted ?? "gray",
      accent: ctx?.theme?.text?.action ?? ctx?.theme?.text?.accent ?? ctx?.theme?.categorical?.[0] ?? "magenta",
      warn: ctx?.theme?.text?.feedback?.warning ?? ctx?.theme?.text?.warning ?? "yellow",
    }

    // Reactive view state. storage.memory is a reactive [store, update] pair:
    // clicking a row writes to it and the sidebar repaints. Without it the panel
    // still renders, just cannot expand rows.
    let view: Rec = { ...defaultSections(), sessionID: "", skills: 0, agents: 0, todos: 0, models: 0, waits: 0 }
    let setView: ((fn: (draft: Rec) => void) => void) | null = null
    try {
      const pair = ctx.storage?.memory?.(STATE, { initial: { ...defaultSections() } })
      if (pair?.[0] && typeof pair?.[1] === "function") {
        view = pair[0]
        setView = pair[1]
      }
    } catch (e) {
      log(`storage.memory unavailable (${short(e)}) — rows cannot expand`)
    }
    /**
     * Ask opencode to repaint. The rows below come from a plugin store and from
     * plain reads — neither is a host signal — so opencode has no reason to
     * repaint the sidebar for them. Measured on a real session: the store
     * updates landed (rev 0 -> 4, with the session id and skill count) while the
     * screen kept its first paint, i.e. an all-zero panel with a "⋯" marker that
     * never cleared. The host exposes the repaint hook its own views use, so ask
     * for one. Deferred through a timer on purpose: requesting a render from
     * inside the update a render is already applying is how a repaint becomes
     * re-entrant, and a re-entrant repaint is what a glitching screen looks like.
     */
    const repaint = () => {
      try {
        const r: any = ctx?.renderer
        const fn = r?.requestRender
        if (typeof fn === "function") {
          setTimeout(() => {
            try {
              fn.call(r)
            } catch {
              /* cosmetic: the panel still updates on the next host repaint */
            }
          }, 0)
        }
      } catch {
        /* no renderer on this version: the store still drives repaints */
      }
    }
    const patch = (fn: (draft: Rec) => void) => {
      try {
        setView?.(fn)
      } catch (e) {
        log(`state update failed: ${short(e)}`)
      }
      repaint()
    }
    /** Any number of rows can be open at once — `open` is a list of row ids. */
    const isOpen = (name: string) => (Array.isArray(view.open) ? view.open : []).includes(name)

    // Plain (non-reactive) detail rows: re-read on every load and shown as-is.
    const data: Rec = {
      tokens: null,
      cost: 0,
      // What the SUBAGENT sessions contributed to `tokens`/`cost` above — the
      // Tokens detail row prints it, so a family total stays traceable.
      subCount: 0,
      subTokens: 0,
      subCost: 0,
      agent: "",
      model: "",
      contextLimit: 0,
      contextUsed: 0,
      steps: [] as Rec[],
      provsUsed: 0,
      agents: [] as string[],
      skills: [] as string[],
      facts: [] as string[],
      factsCount: 0,
      todos: [] as string[],
      todosDone: 0,
      todosFromProject: false,
      workers: [] as string[],
      workersActive: 0,
      waits: [] as string[],
      waitSig: "",
    }
    let sqlite: any = null
    let fs: any = null
    let sessionID = ""
    let loading = false
    let pendingFull = false
    /** Session whose message store has already been warmed once (see load). */
    let warmedFor = ""
    /** Family size from the last pass — a change means a subagent spawned or
     *  finished, and one extra pass is nudged (see load). One nudge pending. */
    let famSeen = 0
    let famTimer: any = null
    // Timestamp of the last slot render. This entrypoint is loaded in the
    // long-lived server process as well, where no slot ever renders — an
    // instance that polls forever with nobody watching is pure background
    // churn, so the poll below stands down when nothing has been drawn.
    let lastRender = 0

    const location = () => {
      try {
        return ctx.location ?? ctx.data?.location?.default?.()
      } catch {
        return null
      }
    }

    /**
     * Everything the panel reads off disk, in ONE read-only open of the project
     * state DB: the todo space, the worker pool, the wait countdowns and the
     * fact store.
     *
     * Todos: this session's list wins; with none, the newest list in the project
     * stands in, so the row shows the plan the project is following rather than a
     * dead "0 items". Workers: PROJECT-wide on purpose — `rlm.spawn` writes a row
     * before the pool runs it and leaves `workers.session` empty, so a worker
     * belongs to the project, not to whichever opencode session asked for it.
     * Waits: PROJECT-wide like workers — a countdown belongs to the project, not
     * to the session that started it. `waitSig` is the STABLE identity (label +
     * deadline, not the ticking countdown) so a manual collapse sticks while the
     * same waits are pending, exactly like the todo signature below.
     *
     * Each query is guarded alone: a drifted schema costs that one section.
     */
    const readProjectState = async (directory: string, sid: string) => {
      const out = {
        todos: [] as string[],
        todosDone: 0,
        todosFallback: false,
        facts: [] as string[],
        factsCount: 0,
        workers: [] as string[],
        workersActive: 0,
        waits: [] as string[],
        waitSig: "",
      }
      try {
        if (fs === null) fs = await import("node:fs")
        if (sqlite === null) sqlite = await import("bun:sqlite")
        const dbPath = stateDb(directory)
        if (!fs.existsSync(dbPath)) return out
        const db = new sqlite.Database(dbPath, { readonly: true })
        try {
          try {
            const mine = db.query("SELECT text, status FROM todos WHERE session = ? ORDER BY id").all(sid) as Rec[]
            const rows = mine.length
              ? mine
              : (db.query("SELECT text, status FROM todos ORDER BY id DESC LIMIT ?").all(ROW_LIMIT) as Rec[]).reverse()
            // Say so when the list is the PROJECT's, not this session's: the
            // fallback is useful, but silently passing off another session's
            // plan as this one's is not.
            out.todosFallback = !mine.length && rows.length > 0
            out.todosDone = rows.filter((r) => String(r?.status ?? "") === "completed").length
            out.todos = rows.map((r) => `${todoMark(r?.status)} ${cut(r?.text, 30)}`)
          } catch {
            /* no todos table yet */
          }
          try {
            // The active COUNT comes from SQL, not the window below: a long
            // worker can sit outside the newest rows while short ones finish.
            const active = db.query("SELECT count(*) AS n FROM workers WHERE status IN ('queued','running')").get() as Rec
            out.workersActive = Number(active?.n ?? 0) || 0
            const rows = db
              .query("SELECT name, status, updated FROM workers ORDER BY rowid DESC LIMIT ?")
              .all(ROW_LIMIT) as Rec[]
            out.workers = rows.map((r) => {
              const status = String(r?.status ?? "?")
              const since = WORKER_LIVE.has(status) ? ` ${age(r?.updated)}` : ""
              return `${workerMark(status)} ${cut(r?.name, 15)} · ${status}${since}`
            })
          } catch {
            /* no workers table yet */
          }
          try {
            // The `wait` tool's countdown rows, soonest deadline first. The
            // display ticks every pass but the signature below does not, so a
            // manual collapse survives the countdown.
            const wrows = db
              .query("SELECT label, deadline FROM waits ORDER BY deadline LIMIT ?")
              .all(ROW_LIMIT) as Rec[]
            out.waitSig = wrows.map((r) => `${String(r?.label ?? "")}~${Number(r?.deadline ?? 0)}`).join("\u0001")
            out.waits = wrows.map((r) => `${cut(r?.label, 15)} · ${left(r?.deadline)} left`)
          } catch {
            /* no waits table yet */
          }
          try {
            // The VALUE is a real count, not the displayed window: LIMIT 6
            // rows capped it, and the view state never received it at all, so
            // the header and Memory row printed "0 facts" with facts on disk.
            const fc = db.query("SELECT count(*) AS n FROM facts").get() as Rec
            out.factsCount = Number(fc?.n ?? 0) || 0
            // Progress notes render as status + core message with runs
            // collapsed ("◐ echo: hello in mock ×6"), not raw mid-word cuts.
            out.facts = humanizeFacts(
              db.query("SELECT text FROM facts ORDER BY id DESC LIMIT ?").all(ROW_LIMIT) as Rec[],
            )
          } catch {
            /* no facts table yet */
          }
        } finally {
          db.close()
        }
      } catch {
        /* unreadable db */
      }
      return out
    }

    let scanned = false

    /**
     * The slow half of a scan: the lazily-synced location stores
     * (agents/skills/providers/models) and the context-window limit. A `sync()`
     * can block on the network and none of it changes between messages, so it
     * runs when the session changes, on the first load and on
     * `/harness-refresh` — not on the cheap 8s poll. The message walk that fills
     * the Models rows is deliberately NOT in here (see scanModels): it is a
     * local read that has to run every pass, because the message store only
     * fills once its own sync lands.
     *
     * Every sync is individually time-boxed: one unreachable provider registry
     * must not strand the whole panel on its placeholder.
     */
    const scanSlow = async (loc: any, data_: Rec): Promise<void> => {
      const store = data_?.location

      // Location stores ARE lazy: they need sync first or list() is empty.
      await Promise.all([
        settle(store?.model?.sync?.(loc), SCAN_MS),
        settle(store?.provider?.sync?.(loc), SCAN_MS),
        settle(store?.agent?.sync?.(loc), SCAN_MS),
        settle(store?.skill?.sync?.(loc), SCAN_MS),
      ])
      const provs = asArray(store?.provider?.list?.(loc))
      const models = asArray(store?.model?.list?.(loc))

      const [provId, modelName] = splitModel(data.model)
      const provEntry = provs.find((p: Rec) => String(p?.id ?? p?.name ?? "") === provId)
      const fromProvider =
        provEntry?.models?.[modelName] ??
        Object.values(provEntry?.models ?? {}).find((m: Rec) => String(m?.id ?? "").endsWith(modelName))
      const match = models.find((m: Rec) => {
        const id = String(m?.id ?? m?.name ?? "")
        return id === data.model || id === modelName || (modelName && id.endsWith(modelName))
      })
      const limit = fromProvider?.limit?.context ?? match?.limit?.context ?? match?.limits?.context
      data.contextLimit = Number(limit ?? 0) || 0

      data.agents = asArray(store?.agent?.list?.(loc))
        .map((a: Rec) => String(a?.name ?? a?.id ?? ""))
        .filter(Boolean)
        .filter((a: string) => !INTERNAL_AGENTS.has(a.toLowerCase()))
      data.skills = asArray(store?.skill?.list?.(loc))
        .map((s: Rec) => String(s?.id ?? s?.name ?? ""))
        .filter((s: string) => s.startsWith(HARNESS_PREFIX))
    }

    /**
     * Models rows: assistant messages grouped by provider -> model, with the
     * step count and cost (the shape opencode's own sidebar uses). A plain
     * in-memory read, so it runs on EVERY load.
     *
     * Computing it only on the first load was the bug behind "Models never
     * populates": the message store starts empty, `message.sync()` fills it
     * afterwards, and the first pass had already frozen the rows at zero —
     * nothing recomputed them until a new session or a manual refresh.
     */
    const scanModels = (data_: Rec): void => {
      // THIS session's messages: the Window number below is one context window,
      // and a subagent's window has nothing to do with this session's.
      const mine = sessionID ? asArray(data_?.session?.message?.list?.(sessionID)) : []
      // Steps, though, count the whole family: a subagent's calls went through
      // models this session never touched, and leaving them out made the Models
      // rows disagree with the (now family-wide) Tokens total.
      const msgs = [...mine]
      for (const id of familyIds(data_, sessionID).slice(1)) {
        msgs.push(...asArray(data_?.session?.message?.list?.(id)))
      }
      // Context-window usage the way opencode's own header computes it: the LAST
      // assistant message with output wins, and a compaction summary counts its
      // output only. The session AGGREGATE keeps growing past the window, so
      // summing it pinned the bar at 100% for the rest of the session — that
      // was the wrong percentage, not a rounding slip.
      let ctxUsed = 0
      for (const m of mine) {
        if (String(m?.role ?? m?.type) !== "assistant") continue
        const t = m?.tokens
        if (!t || Number(t.output ?? 0) <= 0) continue
        ctxUsed = m?.summary ? Number(t.output ?? 0) : totalTokens(t)
      }
      data.contextUsed = ctxUsed
      const byProv = new Map<string, Map<string, { steps: number; cost: number }>>()
      for (const m of msgs) {
        if (String(m?.role ?? m?.type) !== "assistant") continue
        const prov = String(m?.providerID ?? m?.model?.providerID ?? "unknown")
        const name = shortModel(modelId(m?.model) || data.model) || "unknown"
        if (!byProv.has(prov)) byProv.set(prov, new Map())
        const inner = byProv.get(prov)!
        const cur = inner.get(name) ?? { steps: 0, cost: 0 }
        cur.steps += 1
        cur.cost += Number(m?.cost ?? 0)
        inner.set(name, cur)
      }

      const steps: Rec[] = []
      for (const [prov, used] of byProv) {
        // A provider line WITHOUT catalog counts: "kilo · 392 models" is
        // inventory trivia — what matters is what this session routed through
        // it, and the available-count lived on a shape that drifts (a
        // Provider.Info row carries no `models` map on 2.0.14).
        steps.push({ row: `${cut(prov, 20)}:`, kind: "provider" })
        for (const [name, e] of used) {
          // Aligned columns inside the 32-cell detail budget: model left,
          // steps right. Per-model cost stays out — the Tokens row owns `$`.
          steps.push({
            row: `${cut(name, 18).padEnd(18)}${e.steps} step${e.steps === 1 ? "" : "s"}`,
            kind: "row",
          })
        }
      }
      // Nothing sent yet: the selected model is still the model in use — a
      // "fallback" row that is deliberately NOT counted as usage ("1 used"
      // before the first message was the old lie).
      if (!steps.length && data.model) {
        steps.push({ row: `${shortModel(data.model)} · selected`, kind: "fallback" })
      }
      data.steps = steps
      data.provsUsed = byProv.size
    }

    const load = async (full = false) => {
      // A `full` request must never be dropped on the floor: the session id
      // arrives with the first slot render, i.e. while the setup-time load is
      // usually still in flight, and losing that one left the panel showing a
      // session-less, all-zero snapshot forever. Queue it instead.
      if (loading) {
        pendingFull = pendingFull || full
        return
      }
      loading = true
      // "scanning…" is driven by a REACTIVE flag, never by the plain `loading`
      // guard: read inside a tracked JSX expression (where it is not reactive),
      // a plain variable reports whatever it happened to hold at that repaint.
      // It is cleared in the same state update that bumps `rev`, and the scan is
      // time-boxed, so the marker can never outlive its scan.
      patch((d) => {
        d.scanning = true
      })
      const notes: string[] = []
      try {
        const loc = location()
        const directory: string = loc?.directory ?? process.cwd?.() ?? "."
        const data_ = ctx.data

        // Read the session FIRST. `session.sync()` invalidates the store and
        // re-populates it asynchronously, so `get()` on the same tick after a
        // sync returns nothing — that silently produced an empty panel. The
        // syncs are therefore only kicked off at the end, to warm the store for
        // the next poll.
        const session = sessionID ? data_?.session?.get?.(sessionID) ?? null : null
        data.agent = String(session?.agent ?? "")
        data.model = modelId(session?.model)
        // Usage = THIS session plus every subagent it spawned. `session.get`
        // returns one row and each `task` child is its own session with its own
        // tokens, so summing the family is what makes the Tokens row, the footer
        // chip and the Models totals account for the workers at all.
        let merged: Rec | null = null
        let subCount = 0
        let subTokens = 0
        let subCost = 0
        for (const id of familyIds(data_, sessionID)) {
          const s = id === sessionID ? session : data_?.session?.get?.(id) ?? null
          const t = s?.tokens ?? null
          if (!t) continue
          if (!merged) merged = { input: 0, output: 0, reasoning: 0, cache: { read: 0, write: 0 } }
          merged.input += Number(t.input ?? 0) || 0
          merged.output += Number(t.output ?? 0) || 0
          merged.reasoning += Number(t.reasoning ?? 0) || 0
          merged.cache.read += Number(t.cache?.read ?? 0) || 0
          merged.cache.write += Number(t.cache?.write ?? 0) || 0
          if (id !== sessionID) {
            subCount += 1
            subTokens += totalTokens(t)
            subCost += Number(s?.cost ?? 0) || 0
          }
        }
        data.tokens = merged
        data.subCount = subCount
        data.subTokens = subTokens
        data.subCost = subCost
        // opencode's own cost accessor: it sums the family for a root row, so
        // the children are already inside it once discovery lands. The fallback
        // hand-sums this row plus the subagents for when that accessor drifts.
        try {
          const c = Number(sessionID ? data_?.session?.cost?.(sessionID) : NaN)
          data.cost = Number.isFinite(c) && c > 0 ? c : Number(session?.cost ?? 0) + subCost
        } catch {
          data.cost = Number(session?.cost ?? 0) + subCost
        }

        // Slow sources only when they can have changed: session change, first
        // load, or an explicit refresh (see scanSlow).
        if (full || !scanned) {
          await settle(scanSlow(loc, data_), SCAN_MS)
          scanned = true
        }
        // Cheap, and only correct AFTER the message store has filled — so it
        // runs on every pass, not on the slow half.
        scanModels(data_)

        // One read-only open of the project state DB for the todo space, the
        // worker pool and the fact store (all local, and they work before the
        // first message because they are keyed by directory/session, not by the
        // message store).
        const state = await readProjectState(directory, sessionID)
        data.todos = state.todos
        data.todosDone = state.todosDone
        data.todosFromProject = state.todosFallback
        data.facts = state.facts
        data.factsCount = state.factsCount
        data.workers = state.workers
        data.workersActive = state.workersActive
        data.waits = state.waits
        data.waitSig = state.waitSig

        // warm the session store for the next poll (see the note above on why
        // this cannot happen before the reads)
        if (sessionID) {
          // {children:true}: fetch this session AND its subagent sessions in one
          // round trip — a `task` child is a separate Session.Info with its own
          // tokens, and until discovery lands, the Tokens row, the chip's $/tok
          // and opencode's own `session.cost` all miss what the workers spent.
          void settle(data_?.session?.sync?.(sessionID, { children: true }), SCAN_MS)
          void settle(data_?.session?.message?.sync?.(sessionID), SCAN_MS)
          // subagent messages feed the Models rows too (see scanModels)
          for (const id of familyIds(data_, sessionID).slice(1)) {
            void settle(data_?.session?.message?.sync?.(id), SCAN_MS)
          }
          // The message store fills asynchronously, so this session's model rows
          // land on the NEXT pass. Nudge one so they appear with the session
          // instead of up to POLL_MS later (once per session — a self-scheduling
          // loop here would be a load storm).
          if (warmedFor !== sessionID) {
            warmedFor = sessionID
            setTimeout(() => void load(), 500)
          }
          // A changed family size means a subagent spawned or finished: nudge
          // ONE more pass so its usage lands in ~0.5s instead of a full poll.
          // The size is read from discovery's result, so the follow-up passes
          // see the new member and then stop: bounded at two nudges per spawn.
          const famCount = familyIds(data_, sessionID).length
          if (famCount !== famSeen) {
            famSeen = famCount
            if (famTimer === null) {
              famTimer = setTimeout(() => {
                famTimer = null
                void load()
              }, 500)
            }
          }
        }
      } catch (e) {
        notes.push(short(e))
      } finally {
        patch((d) => {
          d.sessionID = sessionID.slice(0, 12)
          d.skills = data.skills.length
          d.agents = data.agents.length
          d.todos = data.todos.length
          d.todosDone = data.todosDone
          d.todosProject = data.todosFromProject
          d.workersActive = data.workersActive
          d.workers = data.workers.length
          d.waits = data.waits.length
          d.models = data.steps.filter((s: Rec) => s.kind === "row").length
          // Providers the session actually ROUTED through: the store-wide
          // count ("6 providers" installed, 3 used) read as wrong data.
          d.providers = data.provsUsed
          // facts never reached the view state before this — header and Memory
          // printed 0 even with facts on disk.
          d.facts = data.factsCount
          // A todo list that appears or changes opens its own row: the list is
          // the point of the section, and a collapsed "3 items" hides the plan
          // the model is following. A manual collapse sticks until the list
          // changes again (the signature is what re-opens it).
          const todoSig = data.todos.join("\u0000")
          if (todoSig !== (d.todoSig ?? "")) {
            d.todoSig = todoSig
            const open = new Set<string>(Array.isArray(d.open) ? d.open : [])
            if (todoSig) open.add("todo")
            d.open = [...open]
          }
          // A wait that appears or changes opens its own row while anything
          // is pending; when none is, the row leaves `open` so the footer
          // count stays honest (the row itself hides on `waits`, below).
          const waitSig = String(data.waitSig ?? "")
          if (waitSig !== (d.waitSig ?? "")) {
            d.waitSig = waitSig
            const open = new Set<string>(Array.isArray(d.open) ? d.open : [])
            if (waitSig) open.add("waits")
            else open.delete("waits")
            d.open = [...open]
          }
          // Surface a load failure in the panel itself: cli-side console.error
          // does not reach opencode's log, so a silent catch would leave only
          // an empty-looking panel to debug.
          d.note = notes[0] ?? ""
          d.scanning = false
          // The rows above are plain objects, not reactive: bump a counter the
          // render reads so the store notifies and the panel repaints with the
          // freshly loaded stats.
          d.rev = Number(d.rev ?? 0) + 1
        })
        loading = false
        if (pendingFull) {
          pendingFull = false
          void load(true)
        }
      }
    }

    const noteSession = (sid: unknown) => {
      const next = String(sid ?? "")
      if (!next || next === sessionID) return
      sessionID = next
      void load(true) // a new session invalidates every slow source
    }

    /** Every slot render goes through here: it marks the panel as on-screen. */
    const rendered = (props: Rec) => {
      lastRender = Date.now()
      noteSession(props?.sessionID)
    }

    void load(true) // first load runs once a session id arrives from the slot
    let timer: any = null
    try {
      // Token/cost counters move during a turn; the cheap pass keeps them
      // honest without re-syncing the location stores every few seconds.
      timer = setInterval(() => {
        if (lastRender === 0 || Date.now() - lastRender > POLL_MS * 4) return
        void load()
      }, POLL_MS)
    } catch (e) {
      log(`poll unavailable: ${short(e)}`)
    }

    const toggleSidebar = () => {
      try {
        ctx.keymap?.dispatch?.("session.sidebar.toggle")
      } catch (e) {
        log(`sidebar toggle failed: ${short(e)}`)
      }
    }

    // ---- commands: registered inside a slot (keymap needs the Provider) --
    try {
      ctx.ui.slot({
        append: "app",
        render: (props: Rec) => {
          rendered(props)
          try {
            ctx.keymap?.layer?.(() => ({
              mode: "global",
              priority: 20,
              commands: [
                {
                  id: "harness.sidebar",
                  title: "Harness: toggle stats sidebar",
                  group: "Harness",
                  bind: "ctrl+g",
                  palette: true,
                  slash: { name: "harness", aliases: ["hp"] },
                  run: toggleSidebar,
                },
                {
                  id: "harness.refresh",
                  title: "Harness: refresh stats",
                  group: "Harness",
                  palette: true,
                  slash: { name: "harness-refresh", aliases: ["hr"] },
                  run: async () => {
                    await load(true) // the refresh command re-scans everything
                    try {
                      ctx.ui.toast?.show?.({
                        title: "harness",
                        message: headline(),
                        variant: view.note ? "warning" : "info",
                      })
                    } catch {
                      /* cosmetic */
                    }
                  },
                },
              ],
              bindings: ["harness.sidebar", "harness.refresh"],
            }))
          } catch (e) {
            log(`keymap layer failed: ${short(e)}`)
          }
          return null
        },
      })
    } catch (e) {
      log(`app slot failed: ${short(e)}`)
    }

    /**
     * One stat row: label on the left in the theme's label colour, value pinned
     * to the right edge. Built like opencode's own rows — the label takes
     * `flexGrow` and the value `flexShrink 0`, so the value sits at the right
     * edge whatever width the sidebar is, with no width constant to guess.
     *
     * `lines()` is called INSIDE the JSX expression on purpose: Solid re-runs
     * tracked JSX expressions, not the component/render body, so a plain
     * `const rows = lines()` up here would freeze at the first paint and keep
     * showing the pre-load snapshot no matter what the store did later.
     */
    const Row = (props: {
      id: string
      label: string
      value: () => string
      lines: () => string[]
      empty: string
      /** Hide the whole row when there is nothing to say (empty Todo/Workers/Memory). */
      hide?: () => boolean
      /** Optional value colour — the Window row tints itself by pressure. */
      valueFg?: () => string
    }) => {
      // A section with nothing in it is noise, not honesty: an empty Workers
      // row or "0 facts" Memory is exactly the clutter the panel must drop.
      if (props.hide?.()) return null
      return (
      <box flexDirection="column">
        <box
          flexDirection="row"
          gap={1}
          minWidth={0}
          onMouseDown={() =>
            patch((d) => {
              const open = new Set<string>(Array.isArray(d.open) ? d.open : [])
              if (open.has(props.id)) open.delete(props.id)
              else open.add(props.id)
              d.open = [...open]
            })
          }
        >
          <text fg={isOpen(props.id) ? th.accent : th.base} flexShrink={0}>
            {isOpen(props.id) ? "▾" : "▸"}
          </text>
          <text fg={isOpen(props.id) ? th.accent : th.base} flexGrow={1} wrapMode="none" truncate>
            {props.label}
          </text>
          <text fg={props.valueFg ? props.valueFg() : th.muted} flexShrink={0} wrapMode="none">
            {props.value()}
          </text>
        </box>
        {isOpen(props.id) ? (
          <box flexDirection="column">
            {(() => {
              const all = props.lines()
              const rows: string[] = all.length ? all.slice(0, DETAIL_LIMIT) : [props.empty]
              // A count that outgrows its list ("11 available", 5 shown) is
              // the dishonesty users notice — always say how many more exist.
              if (all.length > DETAIL_LIMIT) rows.push(`… +${all.length - DETAIL_LIMIT} more`)
              return rows.map((l) => (
                // 32 columns + the two-space indent is the sidebar's whole content
                // width (measured off a real 42-column sidebar): one more and
                // opencode middle-truncates the row it is already showing.
                <text fg={th.muted} wrapMode="none" truncate>
                  {`  ${cut(l, 32)}`}
                </text>
              ))
            })()}
          </box>
        ) : null}
      </box>
      )
    }

    // ---- the side panel: opencode's own sidebar, our rows ---------------
    try {
      ctx.ui.slot({
        append: "sidebar.content",
        render: (props: Rec) => {
          rendered(props)
          try {
            // Every closure below starts with a tracked read (view.rev) — see
            // the note on Row() for why that is required.
            const total = (): number => {
              void view.rev
              return totalTokens(data.tokens)
            }
            /** Window occupancy: the last message, never the lifetime total. */
            const used = (): number => {
              void view.rev
              return Number(data.contextUsed ?? 0) || 0
            }
            const pct = (): number => {
              void view.rev
              return data.contextLimit > 0 ? Math.min(100, Math.round((used() / data.contextLimit) * 100)) : 0
            }
            const windowValue = (): string => {
              void view.rev
              if (!data.tokens) return "—"
              return data.contextLimit > 0 ? `${bar(pct())} ${pct()}%` : `${kfmt(used())} tok`
            }
            /** Pressure tint: quiet until the window is actually filling up. */
            const windowFg = (): string => {
              void view.rev
              return pct() >= 80 ? th.warn : th.muted
            }
            const windowLines = (): string[] => {
              void view.rev
              if (!data.tokens) return []
              const out: string[] = []
              if (data.contextLimit > 0) {
                out.push(`${numfmt(used())} / ${numfmt(data.contextLimit)} in context`)
              }
              out.push(`${data.model ? shortModel(data.model) : "model —"} · agent ${data.agent || "—"}`)
              return out
            }
            const tokenValue = (): string => {
              void view.rev
              const t = data.tokens
              if (!t) return "—"
              return `${kfmt(total())} · $${Number(data.cost ?? 0).toFixed(4)}`
            }
            const tokenLines = (): string[] => {
              void view.rev
              const t = data.tokens
              if (!t) return []
              const cache = t.cache ?? {}
              // No cost line: it is the row's value already, and a repeat of
              // the same number was pure noise. Cache write is shown only
              // when there is any (it is usually 0).
              const out = [
                `input ${numfmt(t.input)} · output ${numfmt(t.output)}`,
                `reasoning ${numfmt(t.reasoning)} · cache ${kfmt(cache.read)}`,
              ]
              if (Number(cache.write ?? 0) > 0) out.push(`cache write ${numfmt(cache.write)}`)
              // The value above is session + subagents; say what the workers
              // contributed so the family total is traceable, not mysterious.
              if ((data.subCount ?? 0) > 0) {
                out.push(`+ ${data.subCount} subagent${data.subCount === 1 ? "" : "s"} · ${numfmt(data.subTokens)} tok`)
              }
              return out
            }
            return (
              <box flexDirection="column">
                {/* Header: brand + at-a-glance, in opencode's own one-line
                    label/value grammar. It never says "no session" — the id is
                    either known or simply not shown. */}
                <box flexDirection="row" gap={1} minWidth={0}>
                  <text fg={view.note ? th.warn : th.base} flexShrink={0}>
                    harness
                  </text>
                  <text fg={th.muted} flexGrow={1} wrapMode="none" truncate>
                    {view.note
                      ? cut(view.note, 40)
                      : [
                          view.sessionID || "",
                          (view.skills ?? 0) > 0 ? `${view.skills} skills` : "",
                          (view.facts ?? 0) > 0 ? `${view.facts} facts` : "",
                        ]
                          .filter((s) => s.length)
                          .join(" · ")}
                  </text>
                  {view.scanning ? (
                    <text fg={th.muted} flexShrink={0}>
                      ⋯
                    </text>
                  ) : null}
                </box>

                <Row
                  id="window"
                  label="Window"
                  value={windowValue}
                  valueFg={windowFg}
                  lines={windowLines}
                  empty="(no context data yet)"
                />
                <Row id="tokens" label="Tokens" value={tokenValue} lines={tokenLines} empty="(no usage yet)" />
                <Row
                  id="models"
                  label="Models"
                  value={() => {
                    void view.rev
                    if (!view.models) return "not used"
                    const p = view.providers ?? 0
                    return `${view.models} model${view.models === 1 ? "" : "s"} · ${p} provider${p === 1 ? "" : "s"}`
                  }}
                  lines={() => {
                    void view.rev
                    return data.steps.map((s: Rec) => String(s.row))
                  }}
                  empty="(none used in this session)"
                />
                <Row
                  id="todo"
                  label="Todo"
                  value={() => {
                    void view.rev
                    const n = view.todos ?? 0
                    if (!n) return "—"
                    return `${view.todosDone ?? 0}/${n} done${view.todosProject ? " · project" : ""}`
                  }}
                  hide={() => {
                    void view.rev
                    return !(view.todos ?? 0)
                  }}
                  lines={() => {
                    void view.rev
                    return data.todos
                  }}
                  empty="(none in this session)"
                />
                {/* Project-wide, unlike the rows above: `rlm.spawn` records a
                    worker before the pool runs it and leaves `session` empty, so
                    the pool belongs to the project, not to this session. */}
                <Row
                  id="workers"
                  label="Workers"
                  value={() => (view.workersActive ? `${view.workersActive} active` : "idle")}
                  hide={() => {
                    void view.rev
                    return !view.workers
                  }}
                  lines={() => {
                    void view.rev
                    return data.workers
                  }}
                  empty="(no workers in this project)"
                />
                {/* Project-wide like Workers above: a countdown belongs to the
                    project, not to whichever session started it. Hidden while
                    empty, auto-expanded while anything is pending (see load). */}
                <Row
                  id="waits"
                  label="Waits"
                  value={() => {
                    void view.rev
                    const n = view.waits ?? 0
                    return n === 1 ? "1 waiting" : `${n} waiting`
                  }}
                  hide={() => {
                    void view.rev
                    return !(view.waits ?? 0)
                  }}
                  lines={() => {
                    void view.rev
                    return data.waits
                  }}
                  empty="(no waits pending)"
                />
                <Row
                  id="skills"
                  label="Skills"
                  value={() => `${view.skills ?? 0} installed`}
                  lines={() => {
                    void view.rev
                    return data.skills.map((s) => `▪ ${shortSkill(s)}`)
                  }}
                  empty="(none seeded at this location)"
                />
                <Row
                  id="agents"
                  label="Agents"
                  value={() => `${view.agents ?? 0} available`}
                  lines={() => {
                    void view.rev
                    // Active agent first and marked; internals were filtered
                    // at load, so the count and this list always agree (the
                    // Row prints "+N more" when they outgrow the budget).
                    const cur = data.agent
                    return [...data.agents]
                      .sort((a, b) => Number(b === cur) - Number(a === cur))
                      .map((a) => `${a === cur ? "●" : "◦"} ${a}`)
                  }}
                  empty="(none registered)"
                />
                <Row
                  id="memory"
                  label="Memory"
                  value={() => `${view.facts ?? 0} fact${(view.facts ?? 0) === 1 ? "" : "s"}`}
                  hide={() => {
                    void view.rev
                    return !(view.facts ?? 0)
                  }}
                  lines={() => {
                    void view.rev
                    return data.facts
                  }}
                  empty="(no durable facts yet)"
                />
              </box>
            )
          } catch (e) {
            return <text fg={th.warn}>harness panel: {short(e)}</text>
          }
        },
      })
      const openCount = () => (Array.isArray(view.open) ? view.open.length : 0)
      ctx.ui.slot({
        append: "sidebar.footer",
        render: () => (
          <text fg={th.muted} wrapMode="none" truncate>
            {`harness · ${openCount() ? `${openCount()} expanded` : "click a row"}`}
          </text>
        ),
      })
    } catch (e) {
      log(`sidebar slots failed: ${short(e)}`)
    }

    const headline = () => {
      void view.rev // tracked read: repaint when a load lands
      const t = data.tokens
      const totalTok = totalTokens(t)
      if (totalTok > 0) {
        // What you want at a glance: window pressure first, spend second, and
        // the lifetime total demoted — it only ever grows, so leading with it
        // made the chip read as "always the same big number".
        const p =
          data.contextLimit > 0
            ? Math.min(100, Math.round((Number(data.contextUsed ?? 0) / data.contextLimit) * 100))
            : 0
        const ctx = data.contextLimit > 0 ? `${p}% ctx · ` : ""
        return `harness · ${ctx}$${Number(data.cost ?? 0).toFixed(2)} · ${kfmt(totalTok)} tok`
      }
      // No session yet: the skill/fact stores are location-scoped and empty at
      // the default location, so counting them here would print a misleading
      // "0 skills" next to a plugin that ships a skill pack.
      if (!view.sessionID) return "harness · click for stats"
      const bits = [
        (view.skills ?? 0) > 0 ? `${view.skills} skills` : "",
        (view.facts ?? 0) > 0 ? `${view.facts} facts` : "",
      ].filter((s) => s)
      return bits.length ? `harness · ${bits.join(" · ")}` : "harness · click for stats"
    }

    // ---- footer chip: headline stats, click toggles the sidebar ----------
    try {
      ctx.ui.slot({
        append: "home.footer.status",
        render: (props: Rec) => {
          rendered(props)
          return (
            // flexShrink 1 + minWidth 0 are the whole point: opencode's own
            // home footer renders its VERSION text right after this slot with
            // flexShrink 0, so a chip that refuses to shrink pushes that text
            // off the right edge — the app stops showing its own version.
            // The chip yields instead: it truncates, the version stays.
            <box flexGrow={1} flexShrink={1} minWidth={0} onMouseDown={toggleSidebar}>
              <text fg={th.muted} wrapMode="none" truncate>
                {headline()}
              </text>
            </box>
          )
        },
      })
    } catch (e) {
      log(`footer chip failed: ${short(e)}`)
    }

    return () => {
      try {
        if (timer) clearInterval(timer)
      } catch {
        /* nothing to release */
      }
    }
  },
}

/**
 * Initial view state: which rows are expanded (`open`, a list — several at
 * once) plus the counters the rows render. `rev` is bumped on every load to
 * trigger a repaint; `scanning` is the placeholder flag (reactive, never a
 * plain var); `todoSig` is the todo list the auto-expand last reacted to,
 * `waitSig` the wait set it last reacted to.
 */
function defaultSections(): Rec {
  return { open: [] as string[], rev: 0, scanning: false }
}

export default HarnessTui
