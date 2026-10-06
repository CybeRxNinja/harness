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
//     by PROJECT — `task` workers are project rows, not session rows, so a
//     worker is not this session's. It shows the persisted status plus the
//     last update; a row the server sweep retired to `timeout` keeps its age,
//     so the flip reads as history — while the active count covers only
//     queued/running rows. The authoritative stale verdict stays `harness doctor`'s.
//   * The Waits row reads the `wait` tool's countdowns out of the same DB
//     (`waits`: label + deadline + total_ms), PROJECT-wide like Workers. It auto-expands
//     while any wait is pending and hides when none is. The countdown ticks
//     every second at render time (a 1s `rev` bump over the stored deadlines —
//     no DB read per tick); rows without a total keep the plain text form.
//   * `ctx.keymap.layer(...)` throws "Keymap.Provider is missing" unless it is
//     called from inside a slot's render component, so commands are registered
//     from the `app` slot (the pattern the CLI-plugin docs use).
//   * Data stores need `sync(location)` before `list(location)` returns
//     anything, and a sync can be slow (it is the only network-touching step):
//     every scan is therefore time-boxed, so the panel always settles instead
//     of sitting on a placeholder forever.
//   * Refresh is a CHANGE PROBE, not a poll: one 500ms tick fingerprints
//     everything the panel shows with O(1) local reads (message count + the last
//     assistant row, this session's tokens/family, the state DB's mtime) and
//     does literally nothing when the fingerprint is unchanged, so the panel
//     tracks opencode's own Context rows within a tick and costs ~zero while
//     idle. Every 16th tick (8s) still runs the unconditional pass, which owns
//     the slow location stores, the time-derived rows and anything the probe
//     cannot see. One timer before, one timer after.
//
// Every step is guarded: a TUI API drift degrades to a log line, a muted row or
// a "—" value — never a broken screen and never a stuck placeholder.

type Rec = Record<string, any>

const STATE = "harness.sidebar.state"
const HARNESS_PREFIX = "harness-"
const POLL_MS = 8000
/**
 * Fast-path tick. The panel used to learn about a finished turn from the 8s
 * poll alone, so its Context numbers trailed opencode's own by up to 8s; the
 * fast path closes that to one tick (see the change probe below) and the 8s
 * poll stays as the slow safety net. It is the SAME interval, re-ticked: net
 * timer count is unchanged (one poll + the Waits tick).
 */
const FAST_MS = 500
/** Fast ticks per unconditional net pass — keeps the old 8s cadence exact. */
const NET_EVERY = Math.max(1, Math.round(POLL_MS / FAST_MS))
/** A store sync can block on the network; past this the panel shows what it has. */
const SCAN_MS = 5000
/**
 * How long the slow half keeps re-proving the location stores before it accepts
 * whatever they answered. Server-side skill seeding lands seconds after start
 * (opencode seeds on boot), so the window is wall-clock, not a try count — see
 * `slowUntil` in setup(). Past it the latch settles, which is what keeps a
 * genuinely empty setup from re-syncing forever.
 */
const SLOW_WINDOW_MS = 30_000
/** Floor between two slow RETRIES, so the window above is not spent per tick. */
const SLOW_RETRY_MS = 2000
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
 * Grace past the deadline during which a row still reads as "due": the server
 * half removes the record on expiry, so this only covers the race between the
 * last poll and the delete. Rows older than this are hidden outright (see
 * readProjectState) — a killed wait whose row was never removed can never
 * linger in the panel past this.
 */
const WAIT_GRACE_MS = 5000

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
 * Elapsed/total share of a wait row for its progress bar, reusing bar() (the
 * Window usage-bar precedent): 0% at record time, 100% at deadline. Returns
 * -1 when the row carries no usable total — pre-migration rows or a drifted
 * table — so the caller falls back to the current text form.
 */
function waitPct(row: Rec): number {
  const total = Number(row?.total ?? row?.total_ms ?? 0)
  const deadline = Number(row?.deadline ?? 0)
  if (!Number.isFinite(total) || total <= 0 || !Number.isFinite(deadline) || deadline <= 0) return -1
  const elapsed = total - Math.max(0, deadline - Date.now())
  return Math.max(0, Math.min(100, (elapsed / total) * 100))
}

/**
 * Trailing cell of a wait row. Without a total it is the old ` left` (the
 * fallback is byte-identical); with one the 8-cell bar replaces the word —
 * 5 cells saved toward the 32-col detail budget — reading `◐ demo · 28s ██░░`.
 */
function waitTail(row: Rec): string {
  const pct = waitPct(row)
  if (pct < 0) return " left"
  return ` ${bar(pct, 8)}`
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

/**
 * Time-box a promise that may block: `true` when it settled in time, `false`
 * when the time-box won (the slow path keeps running in the background — the
 * caller just stops waiting). A rejection counts as settled: it finished, it
 * just failed. Callers that only need the wait to end ignore the value.
 *
 * The box timer is released the moment the promise settles. A race leaves its
 * loser pending for the whole window, and one pass boxes four store syncs, so
 * without this the panel kept a handful of 5s timers alive between passes.
 */
function settle(p: any, ms: number): Promise<boolean> {
  let box: any = null
  return Promise.race([
    Promise.resolve(p).then(() => true).catch(() => true),
    new Promise<boolean>((resolve) => {
      box = setTimeout(() => resolve(false), ms)
    }),
  ]).finally(() => {
    try {
      if (box !== null) clearTimeout(box)
    } catch {
      /* already fired */
    }
  })
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
      waitRows: [] as Rec[],
      waitSig: "",
      lastSkill: "",
      lastCompaction: "",
      lastRisk: "",
      files: [] as Rec[],
      vcsKnown: false,
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
    /** Last change-probe signature the fast path acted on (see probe). */
    let seenSig = ""
    /** Fast ticks since the last unconditional net pass. */
    let netTick = 0
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

    /** mtime+size of a state-DB file, "-" when it cannot be stat'ed. */
    const dbStamp = (path: string): string => {
      try {
        const st = fs?.statSync?.(path)
        return st ? `${st.mtimeMs}:${st.size}` : "-"
      } catch {
        return "-"
      }
    }
    /** Last project-DB stamp the probe saw, so it can report "the disk moved". */
    let dbSeen = ""

    /**
     * Change probe: a short signature of everything the panel shows, built from
     * O(1) LOCAL reads — no store sync, no SQLite open, no message walk, no
     * repaint. The fast tick compares it with the last one and does nothing at
     * all when they match, which is what makes an idle panel free instead of
     * "one full load every tick".
     *
     * What it fingerprints, and why that is enough:
     *   * message count + the LAST assistant row's id/output (scanned from the
     *     end, opencode's own header rule) — the Window/Models numbers move
     *     there, and a streaming step edits that same row in place;
     *   * this session's tokens/cost/model/agent + family size — the Tokens
     *     row, the footer chip and a subagent's arrival;
     *   * the state DB's mtime+size, file AND -wal (WAL writes land there), so
     *     a todo/worker/wait/fact write shows up without opening the DB.
     *
     * Deliberately NOT a full substitute for a load: it reads no row bodies, so
     * an edit to an OLDER assistant row (a retried step's cost) can slip past
     * it. That is what the 8s safety net below is for — this path only has to
     * be right about the common case, and the net reconciles the rest.
     */
    const probe = (): Rec => {
      const d = ctx.data
      const sig: string[] = [sessionID]
      let n = 0
      let last = ""
      try {
        const msgs = asArray(d?.session?.message?.list?.(sessionID))
        n = msgs.length
        // Index reads, never a spread/for-of: iterating here is the very walk
        // the gate exists to avoid.
        for (let i = n - 1; i >= 0; i--) {
          const m: Rec = msgs[i]
          if (String(m?.role ?? m?.type) !== "assistant") continue
          last = `${String(m?.id ?? "")}:${Number(m?.tokens?.output ?? 0) > 0 ? totalTokens(m.tokens) : 0}:${m?.summary ? 1 : 0}`
          break
        }
      } catch {
        /* store cold or drifted: the net still loads, the panel still settles */
      }
      sig.push(`${n}~${last}`)
      try {
        const s = d?.session?.get?.(sessionID) ?? null
        sig.push(`${totalTokens(s?.tokens)}:${Number(s?.cost ?? 0) || 0}:${modelId(s?.model)}:${String(s?.agent ?? "")}:${familyIds(d, sessionID).length}`)
      } catch {
        /* info row not loaded yet */
      }
      let db = false
      if (fs) {
        const dir = location()?.directory ?? process.cwd?.() ?? "."
        const path = stateDb(dir)
        const stamp = `${dbStamp(path)}/${dbStamp(`${path}-wal`)}`
        db = stamp !== dbSeen
        dbSeen = stamp
        sig.push(stamp)
      } else {
        // No fs yet (first pass): stay quiet rather than guess a change.
        sig.push("")
      }
      return { sig: sig.join("|"), db }
    }

    /**
     * Everything the panel reads off disk, in ONE read-only open of the project
     * state DB: the todo space, the worker pool, the wait countdowns and the
     * fact store.
     *
     * Todos: this session's list wins; with none, the newest list in the project
     * stands in, so the row shows the plan the project is following rather than a
     * dead "0 items". Workers: PROJECT-wide on purpose — the server half writes
     * a row when the `task` tool starts a worker, so a worker belongs to the
     * project, not to whichever opencode session asked for it.
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
        waitRows: [] as Rec[],
        waitSig: "",
        lastSkill: "",
        lastCompaction: "",
        lastRisk: "",
        files: [] as Rec[],
        vcsKnown: false,
      }
      try {
        if (fs === null) fs = await import("node:fs")
        if (sqlite === null) sqlite = await import("bun:sqlite")
        try {
          const uiPath = `${directory}/.opencode/harness/ui-state.json`
          const current = JSON.parse(fs.readFileSync(uiPath, "utf8"))
          if (current?.last_skill?.id) out.lastSkill = `${shortSkill(current.last_skill.id)}`
          if (current?.last_compaction?.chars) out.lastCompaction = `${current.last_compaction.chars} chars`
          if (current?.last_risk?.risk) out.lastRisk = `${current.last_risk.risk}${current.last_risk.ask ? " · ask" : ""}`
        } catch {
          /* no UI state yet */
        }
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
            out.todos = rows.map((r) => `${todoMark(r?.status)} ${wcut(r?.text, 30)}`)
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
              // A retired row keeps its age (`! name · timeout 21m`): the flip
              // is visible as history, not a disappearance — while the active
              // count above already excludes it.
              const since = WORKER_LIVE.has(status) || status === "timeout" ? ` ${age(r?.updated)}` : ""
              return `${workerMark(status)} ${wcut(r?.name, 15)} · ${status}${since}`
            })
          } catch {
            /* no workers table yet */
          }
          try {
            // The `wait` tool's countdown rows, soonest deadline first. Rows
            // past the deadline plus a small grace are hidden outright: the
            // record is removed on expiry, so anything still past-due here is
            // a killed wait whose delete never ran — it must never linger in
            // the panel (the sweep in the server half removes it on the next
            // touch of the table). The display ticks every second at render time
            // (see liveWaits) but the signature below does not, so a manual
            // collapse survives the countdown.
            // total_ms is new (the server half migrates old DBs best-effort):
            // read it when present, fall back to label+deadline when absent —
            // rows without totals keep the current text form (see waitTail).
            let wrows: Rec[]
            try {
              wrows = (
                db.query("SELECT label, deadline, total_ms AS total FROM waits ORDER BY deadline LIMIT ?").all(
                  ROW_LIMIT,
                ) as Rec[]
              ).filter((r) => Number(r?.deadline ?? 0) > Date.now() - WAIT_GRACE_MS)
            } catch {
              wrows = (
                db.query("SELECT label, deadline FROM waits ORDER BY deadline LIMIT ?").all(ROW_LIMIT) as Rec[]
              ).filter((r) => Number(r?.deadline ?? 0) > Date.now() - WAIT_GRACE_MS)
            }
            out.waitRows = wrows.map((r) => ({
              label: String(r?.label ?? ""),
              deadline: Number(r?.deadline ?? 0),
              total: Number((r as Rec)?.total ?? 0) || 0,
            }))
            out.waitSig = wrows.map((r) => `${String(r?.label ?? "")}~${Number(r?.deadline ?? 0)}`).join("\u0001")
            // Every row opens with the live mark, like the todo/worker marks
            // above: one icon in a stable column, never bare text. The tail
            // is the live countdown plus, when the row carries a total, its
            // elapsed/total bar — without one it is the old text (see waitTail).
            out.waits = wrows.map((r) => `◐ ${wcut(r?.label, 15)} · ${left(r?.deadline)}${waitTail(r)}`)
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
     * When the slow half stops being re-attempted. A cold location store
     * answers empty on the first pass and fills when the server finishes
     * seeding it, so one empty pass must not latch `scanned` (see load) — but a
     * setup with genuinely no skills must still settle, so the re-attempts stop
     * at a wall-clock deadline.
     *
     * The budget is TIME, not a try count, and that is the whole fix: the
     * refresh is re-ticked at FAST_MS (500ms) with the 8s net pass still
     * unconditional, so a count of 5 was 1.7s of retrying where it used to be
     * ~40s. The latch therefore settled on the store while the seeding was
     * still in flight, and the panel froze at "0 skills" for the rest of the
     * session (live: "Skills 0 installed / (none seeded at this location)" with
     * skills on disk). 0 = no attempt yet: the window opens on the first one.
     */
    let slowUntil = 0
    /**
     * Earliest time the next RETRY may run. A full pass (session change, first
     * load, `/harness-refresh`) ignores it — that is the caller's escape hatch.
     * Without a floor the window above would sync the location stores on every
     * 500ms tick that saw a change: 30s of budget at the tick rate instead of
     * the old 5 syncs per 40s.
     */
    let slowNext = 0
    /**
     * Verdict of the last slow pass: did all four store syncs beat the
     * time-box? Written by scanSlow itself (see below) — the outer settle()
     * around it maps ANY settlement to true, so its answer cannot gate the
     * latch: when a pass times out exactly on the time-box edge, the outer
     * race can resolve first and report success for a scan that failed.
     */
    let slowOk = false

    /**
     * The slow half of a scan: the lazily-synced location stores
     * (agents/skills/providers/models) and the context-window limit. A `sync()`
     * can block on the network and none of it changes between messages, so it
     * runs when the session changes, on the first load and on
     * `/harness-refresh` — plus, while the latch is down, on the retry floor
     * (see slowNext). The message walk that fills the Models rows is
     * deliberately NOT in here (see scanModels): it is a local read that has to
     * run every pass, because the message store only fills once its own sync
     * lands.
     *
     * Every sync is individually time-boxed: one unreachable provider registry
     * must not strand the whole panel on its placeholder.
     */
    const scanSlow = async (loc: any, data_: Rec): Promise<boolean> => {
      const store = data_?.location
      // Fresh verdict first (this prefix runs synchronously, so a pass that
      // never finishes still leaves slowOk false — see the declaration).
      slowOk = false

      // Location stores ARE lazy: they need sync first or list() is empty.
      // Each sync reports whether it beat the time-box: one unreachable
      // provider registry must not strand the whole panel on its
      // placeholder, and a pass whose syncs never landed must not latch
      // `scanned` either (see load).
      const synced = await Promise.all([
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
      // The verdict travels on slowOk, not the return: the outer settle()
      // maps even a `false` return to true (see load), so load must not read
      // this call's answer from the race.
      slowOk = synced.every(Boolean)
      return slowOk
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
          // steps right, with a `·` separator so a truncated name can never
          // glue to the count (`…849 steps`). Per-model cost stays out —
          // the Tokens row owns `$`.
          steps.push({
            row: `${cut(name, 18).padEnd(18)} · ${e.steps} step${e.steps === 1 ? "" : "s"}`,
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

    /**
     * Waits detail lines recomputed at RENDER time from the stored deadlines,
     * so the 1s tick below (a bare `rev` bump) shows a live countdown with no
     * DB read. Same shape as the load-time rows in readProjectState — those
     * stay the count source and the no-row fallback (waitRows is always set
     * alongside them, but a drifted shape must still render something).
     */
    const liveWaits = (): string[] => {
      const rows = Array.isArray(data.waitRows) ? data.waitRows : null
      if (!rows) return data.waits
      return rows
        .filter((r: Rec) => Number(r?.deadline ?? 0) > Date.now() - WAIT_GRACE_MS)
        .map((r: Rec) => `◐ ${wcut(r?.label, 15)} · ${left(r?.deadline)}${waitTail(r)}`)
    }

    /**
     * One pass over every source. `full` re-runs the slow location stores; the
     * fast path (see the probe) passes `{sync:false, db}` instead, because the
     * message store is already live from opencode's own event stream and the
     * state DB was just stat'ed — re-syncing and re-opening it there would
     * multiply the two heaviest steps by the tick rate for nothing.
     *
     * Resolves true when a pass actually ran. The fast path uses that to hold
     * its fingerprint back until the pass is accepted, so a change seen while
     * another pass was in flight is retried on the next tick, never swallowed.
     */
    const load = async (full = false, opts: Rec = {}): Promise<boolean> => {
      // A `full` request must never be dropped on the floor: the session id
      // arrives with the first slot render, i.e. while the setup-time load is
      // usually still in flight, and losing that one left the panel showing a
      // session-less, all-zero snapshot forever. Queue it instead.
      if (loading) {
        pendingFull = pendingFull || full
        return false
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
        // load, or an explicit refresh (see scanSlow). An unlatched panel
        // re-enters on a floor, so a cold first pass keeps getting chances —
        // a `full` pass never waits for the floor.
        if (full || (!scanned && Date.now() >= slowNext)) {
          slowNext = Date.now() + SLOW_RETRY_MS
          // The outer time-box is a hang-guard only — its answer is ignored
          // on purpose (see slowOk): it reports true for any settlement,
          // including a pass that timed out exactly on the edge.
          await settle(scanSlow(loc, data_), SCAN_MS)
          // A cold store answers empty on the first pass and fills when the
          // server finishes seeding it, so latching on that first pass froze
          // the panel at "0 skills" forever. Latch only on a pass whose syncs
          // landed AND showed something; the window (slowUntil) is the exit
          // for a setup with genuinely no skills, so it settles instead of
          // syncing forever — and it is what stops the re-attempts, not a try
          // count, which the 500ms tick turned into 1.7s of retrying. ALL slow
          // outputs must read non-empty before the early latch: pass 1 can see
          // agents (local builtins, instant) while the server-side skill
          // seeding has not landed yet, and latching on that one warm store
          // froze Skills at 0 until a manual refresh re-ran the slow half.
          if (slowUntil === 0) slowUntil = Date.now() + SLOW_WINDOW_MS
          const populated = data.skills.length > 0 && data.agents.length > 0
          if ((slowOk && populated) || Date.now() >= slowUntil) scanned = true
        }
        // Cheap, and only correct AFTER the message store has filled — so it
        // runs on every pass, not on the slow half.
        scanModels(data_)

        // One read-only open of the project state DB for the todo space, the
        // worker pool and the fact store (all local, and they work before the
        // first message because they are keyed by directory/session, not by the
        // message store). Skipped by the fast path when the DB has not moved:
        // the rows just read are still the truth, and the open is the single
        // most expensive step in a pass.
        if (opts.db !== false) {
          const state = await readProjectState(directory, sessionID)
          data.todos = state.todos
          data.todosDone = state.todosDone
          data.todosFromProject = state.todosFallback
          data.facts = state.facts
          data.factsCount = state.factsCount
          data.workers = state.workers
          data.workersActive = state.workersActive
          data.waits = state.waits
          data.waitRows = state.waitRows
          data.waitSig = state.waitSig
          data.lastSkill = state.lastSkill
          data.lastCompaction = state.lastCompaction
          data.lastRisk = state.lastRisk
        }
        if (typeof ctx?.vcs?.status === "function") {
          data.vcsKnown = true
          try {
            const rows = await settle(ctx.vcs.status(), SCAN_MS)
            data.files = Array.isArray(rows)
              ? rows.map((r: Rec) => ({
                  file: String(r?.file ?? ""),
                  status: String(r?.status ?? "?"),
                  additions: Number(r?.additions ?? 0) || 0,
                  deletions: Number(r?.deletions ?? 0) || 0,
                }))
              : []
          } catch {
            data.files = []
          }
        } else {
          // Host without ctx.vcs.status: ask git directly (git status --porcelain=v1
          // and git diff --numstat HEAD). Same row shape as the host API so the
          // render path is unchanged; untracked rows have no numstat line,
          // so they report +0/-0.
          try {
            let rows: Rec[] | null = null
            const done = await settle((async () => {
              const run = async (cmd: string[]): Promise<{ ok: boolean; text: string }> => {
                try {
                  const proc = Bun.spawn(cmd, { stdout: "pipe", stderr: "ignore" })
                  const [out, code] = await Promise.all([new Response(proc.stdout).text(), proc.exited])
                  return { ok: code === 0, text: out ?? "" }
                } catch {
                  return { ok: false, text: "" }
                }
              }
              const st = await run(["git", "-C", directory, "status", "--porcelain=v1"])
              const ns = await run(["git", "-C", directory, "diff", "--numstat", "HEAD"])
              if (!st.ok || !ns.ok) return
              const adds = new Map<string, { additions: number; deletions: number }>()
              for (const line of ns.text.split("\n")) {
                const parts = line.split("\t")
                if (parts.length >= 3) adds.set(parts[2], { additions: Number(parts[0]) || 0, deletions: Number(parts[1]) || 0 })
              }
              const merged: Rec[] = []
              for (const line of st.text.split("\n")) {
                if (line.length < 4) continue
                const path = line.slice(3)
                const a = adds.get(path)
                merged.push({ file: path, status: line.slice(0, 2).trim() || "?", additions: a?.additions ?? 0, deletions: a?.deletions ?? 0 })
              }
              rows = merged
            })(), SCAN_MS)
            if (done && rows !== null) {
              data.vcsKnown = true
              data.files = rows
            }
          } catch {
            // git missing or no Bun global: degrade exactly as before —
            // vcsKnown stays false and the row renders unavailable.
          }
        }

        // warm the session store for the next poll (see the note above on why
        // this cannot happen before the reads)
        if (sessionID) {
          // {children:true}: fetch this session AND its subagent sessions in one
          // round trip — a `task` child is a separate Session.Info with its own
          // tokens, and until discovery lands, the Tokens row, the chip's $/tok
          // and opencode's own `session.cost` all miss what the workers spent.
          // Network round trips: the fast path leaves them to the net poll,
          // because the message store is already live off the event stream.
          if (opts.sync !== false) {
            void settle(data_?.session?.sync?.(sessionID, { children: true }), SCAN_MS)
            void settle(data_?.session?.message?.sync?.(sessionID), SCAN_MS)
            // subagent messages feed the Models rows too (see scanModels)
            for (const id of familyIds(data_, sessionID).slice(1)) {
              void settle(data_?.session?.message?.sync?.(id), SCAN_MS)
            }
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
          // Whether the slow location stores have settled: the Skills rows
          // read it to print "syncing…" while cold instead of the settled
          // "none seeded" line.
          d.scanned = scanned
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
        // Reconcile the 1s Waits tick with the rows just read: it starts when
        // a wait is pending and stops (with one cheap reconciling pass) when
        // none is — never a dangling timer past the last deadline.
        ensureWaitTick()
        loading = false
        if (pendingFull) {
          pendingFull = false
          void load(true)
        }
        return true
      }
    }

    const noteSession = (sid: unknown) => {
      const next = String(sid ?? "")
      if (!next || next === sessionID) return
      sessionID = next
      // A new session can mean a new project directory with cold stores
      // again: the slow half must re-prove itself, not inherit the latch.
      scanned = false
      slowUntil = 0
      slowNext = 0
      // The new session's numbers differ from the fingerprint by construction,
      // so the fast path must not treat its first pass as a repeat of the old
      // session's.
      seenSig = ""
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
      // ONE interval, two jobs. The fast path runs the change probe first and
      // does nothing at all when nothing moved, so a turn's end shows up within
      // a tick instead of up to POLL_MS later; every NET_EVERY-th tick is the
      // unconditional pass, unchanged: it re-proves a cold location store,
      // refreshes the time-derived rows (worker ages), reconciles anything the
      // probe cannot see and re-syncs the message store. Same stand-down as
      // before, same single timer, released in the cleanup below.
      timer = setInterval(() => {
        if (lastRender === 0 || Date.now() - lastRender > POLL_MS * 4) return
        netTick += 1
        if (netTick % NET_EVERY !== 0) {
          let p: Rec | null = null
          try {
            p = probe()
          } catch {
            p = null
          }
          // No signature (probe drifted) or no change: no walk, no DB, no
          // repaint — the idle cost is the probe itself and nothing else.
          if (!p?.sig || p.sig === seenSig) return
          // Hold the new signature back until the pass is actually accepted:
          // a change seen mid-pass is retried next tick, never swallowed.
          void load(false, { sync: false, db: p.db })
            .then((ran) => {
              if (ran) seenSig = p!.sig
            })
            .catch((e) => log(`fast pass failed: ${short(e)}`))
          return
        }
        void load()
      }, FAST_MS)
    } catch (e) {
      log(`poll unavailable: ${short(e)}`)
    }

    // The Waits countdown ticks between host repaints: the poll above is what
    // refreshes the rows, so a `28s left` label sat stale until the next pass.
    // This 1s timer only bumps `rev` — the Waits detail lines recompute
    // left() from the stored deadlines on every render (see liveWaits), so a
    // bump is a fresh countdown with no DB read. It runs only while an
    // unexpired wait exists and clears itself when none remains; like the
    // poll it stands down when the panel is off-screen, and it is released
    // with the plugin (see the cleanup below). setInterval is the same
    // primitive the poll above already relies on in this host.
    let tickTimer: any = null
    const waitAlive = (): boolean =>
      (Array.isArray(data.waitRows) ? data.waitRows : []).some(
        (r: Rec) => Number(r?.deadline ?? 0) > Date.now() - WAIT_GRACE_MS,
      )
    const stopWaitTick = () => {
      if (tickTimer !== null) {
        try {
          clearInterval(tickTimer)
        } catch {
          /* already released */
        }
        tickTimer = null
      }
    }
    const ensureWaitTick = () => {
      if (!waitAlive()) {
        // Rows just drained: one cheap pass reconciles the count and leaves
        // `open`, instead of waiting up to POLL_MS with a stale row on screen.
        if (tickTimer !== null) {
          stopWaitTick()
          void load()
        }
        return
      }
      if (tickTimer !== null) return
      try {
        tickTimer = setInterval(() => {
          if (!waitAlive()) {
            stopWaitTick()
            void load()
            return
          }
          if (lastRender === 0 || Date.now() - lastRender > POLL_MS * 4) return
          patch((d) => {
            d.rev = Number(d.rev ?? 0) + 1
          })
        }, 1000)
      } catch (e) {
        log(`wait tick unavailable: ${short(e)}`)
      }
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
      /**
       * Optional detail-row colour per line (strings unchanged — colour only):
       * active items pop in the stronger colour, settled ones recede to muted.
       */
      tone?: (line: string) => string
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
                // Word-boundary cut, like the fact rows: a mid-word cut hid the
                // end of the token it landed on.
                <text fg={props.tone ? props.tone(l) : th.muted} wrapMode="none" truncate>
                  {`  ${wcut(l, 32)}`}
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
              if (!data.tokens) return th.muted
              return pct() >= 80 ? th.warn : th.base
            }
            const windowLines = (): string[] => {
              void view.rev
              if (!data.tokens) return []
              const out: string[] = []
              if (data.contextLimit > 0) {
                out.push(`${numfmt(used())} / ${numfmt(data.contextLimit)} in context`)
              }
              out.push(`${data.model ? shortModel(data.model) : "model —"} · agent ${data.agent || "—"}`)
              if (data.lastCompaction) out.push(`brief: ${data.lastCompaction} retained`)
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
                    either known or simply not shown. The separator is a
                    CHARACTER, not the box's `gap`: opencode did not space these
                    texts, and the header rendered `harnessses_f2900e24` (live,
                    2.0.18). So the brand carries the space and the tail the `·`,
                    as literals — the line then reads the same on a host that
                    honours `gap` and one that drops it. */}
                <box flexDirection="row" minWidth={0}>
                  <text fg={view.note ? th.warn : th.base} flexShrink={0}>
                    {"harness "}
                  </text>
                  <text fg={th.muted} flexGrow={1} wrapMode="none" truncate>
                    {(() => {
                      const tail = view.note
                        ? cut(view.note, 40)
                        : [
                            view.sessionID || "",
                            (view.skills ?? 0) > 0 ? `${view.skills} skills` : "",
                            (view.facts ?? 0) > 0 ? `${view.facts} facts` : "",
                          ]
                            .filter((s) => s.length)
                            .join(" · ")
                      return tail ? `· ${tail}` : ""
                    })()}
                  </text>
                  {view.scanning ? (
                    <text fg={th.muted} flexShrink={0}>
                      {" ⋯"}
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
                <Row
                  id="tokens"
                  label="Tokens"
                  value={tokenValue}
                  valueFg={() => {
                    void view.rev
                    return data.tokens ? th.base : th.muted
                  }}
                  lines={tokenLines}
                  empty="(no usage yet)"
                  tone={(l) => (l.startsWith("+ ") ? th.base : th.muted)}
                />
                <Row
                  id="models"
                  label="Models"
                  value={() => {
                    void view.rev
                    if (!view.models) return "not used"
                    const p = view.providers ?? 0
                    return `${view.models} model${view.models === 1 ? "" : "s"} · ${p} provider${p === 1 ? "" : "s"}`
                  }}
                  valueFg={() => {
                    void view.rev
                    return view.models ? th.base : th.muted
                  }}
                  lines={() => {
                    void view.rev
                    return data.steps.map((s: Rec) => String(s.row))
                  }}
                  empty="(none used in this session)"
                  tone={(l) => (l.endsWith(":") ? th.base : th.muted)}
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
                  valueFg={() => th.base}
                  hide={() => {
                    void view.rev
                    return !(view.todos ?? 0)
                  }}
                  lines={() => {
                    void view.rev
                    return data.todos
                  }}
                  empty="(none in this session)"
                  tone={(l) => (l.startsWith("◐") ? th.accent : l.startsWith("○") ? th.base : th.muted)}
                />
                {/* Project-wide, unlike the rows above: the server half records a
                    worker when the `task` tool starts it, so the pool belongs to
                    the project, not to this session. */}
                <Row
                  id="workers"
                  label="Workers"
                  value={() => (view.workersActive ? `${view.workersActive} active` : "idle")}
                  valueFg={() => {
                    void view.rev
                    return view.workersActive ? th.base : th.muted
                  }}
                  hide={() => {
                    void view.rev
                    return !view.workers
                  }}
                  lines={() => {
                    void view.rev
                    return data.workers
                  }}
                  empty="(no workers in this project)"
                  tone={(l) =>
                    l.startsWith("✕") || l.startsWith("!") || l.startsWith("~")
                      ? th.warn
                      : l.startsWith("◐") || l.startsWith("○")
                        ? th.base
                        : th.muted
                  }
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
                  valueFg={() => th.base}
                  hide={() => {
                    void view.rev
                    return !(view.waits ?? 0)
                  }}
                  lines={() => {
                    void view.rev
                    return liveWaits()
                  }}
                  empty="(no waits pending)"
                  tone={() => th.base}
                />
                <Row
                  id="files"
                  label="Files"
                  value={() => {
                    void view.rev
                    if (!data.vcsKnown) return "unavailable"
                    const changed = data.files.length
                    if (!changed) return "clean"
                    const add = data.files.reduce((sum, r) => sum + Number(r.additions ?? 0), 0)
                    const del = data.files.reduce((sum, r) => sum + Number(r.deletions ?? 0), 0)
                    return `${changed} changed · +${add} -${del}`
                  }}
                  valueFg={() => {
                    void view.rev
                    return data.files.length ? th.base : th.muted
                  }}
                  lines={() => {
                    void view.rev
                    if (!data.vcsKnown) return ["(repository state unavailable)"]
                    if (!data.files.length) return ["(no working-copy changes)"]
                    return data.files.slice(0, 5).map((r) =>
                      `${String(r.status ?? "?")[0].toUpperCase()} ${wcut(String(r.file ?? ""), 22)} +${Number(r.additions ?? 0)} -${Number(r.deletions ?? 0)}`,
                    )
                  }}
                  empty="(clean)"
                  tone={(l) => l.startsWith("(") ? th.muted : th.base}
                />
                <Row
                  id="skills"
                  label="Skills"
                  value={() => `${view.skills ?? 0} installed`}
                  valueFg={() => {
                    void view.rev
                    return (view.skills ?? 0) > 0 ? th.base : th.muted
                  }}
                  lines={() => {
                    void view.rev
                    // Cold stores answer empty on the first pass: while the
                    // slow half has not latched, say syncing instead of the
                    // settled "none seeded" line below.
                    if (!data.skills.length && !view.scanned) return ["syncing…"]
                    const out = [] as string[]
                    if (data.lastSkill) out.push(`● last used ${data.lastSkill}`)
                    return [...out, ...data.skills.map((s) => `▪ ${shortSkill(s)}`)]
                  }}
                  empty="(none seeded at this location)"
                />
                <Row
                  id="agents"
                  label="Agents"
                  value={() => `${view.agents ?? 0} available`}
                  valueFg={() => {
                    void view.rev
                    return (view.agents ?? 0) > 0 ? th.base : th.muted
                  }}
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
                  tone={(l) => (l.startsWith("●") ? th.base : th.muted)}
                />
                <Row
                  id="memory"
                  label="Memory"
                  value={() => `${view.facts ?? 0} fact${(view.facts ?? 0) === 1 ? "" : "s"}`}
                  valueFg={() => th.base}
                  hide={() => {
                    void view.rev
                    return !(view.facts ?? 0)
                  }}
                  lines={() => {
                    void view.rev
                    const state = [] as string[]
                    if (data.lastRisk) state.push(`⚠ last risk: ${data.lastRisk}`)
                    return [...state, ...data.facts]
                  }}
                  empty="(no durable facts yet)"
                  tone={(l) =>
                    l.startsWith("◐") ? th.accent : l.startsWith("✕") || l.startsWith("⚠") ? th.warn : th.muted
                  }
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
          // Calm when collapsed, stronger once rows are open — colour only.
          <text fg={openCount() ? th.base : th.muted} wrapMode="none" truncate>
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
      try {
        // The one-shot nudges (message-store warm, family growth) are timers
        // too: a pending one after release would fire a pass into a plugin
        // opencode has already torn down.
        if (famTimer !== null) {
          clearTimeout(famTimer)
          famTimer = null
        }
      } catch {
        /* already fired */
      }
      stopWaitTick()
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
