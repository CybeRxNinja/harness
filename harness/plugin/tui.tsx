// Harness CLI plugin for the opencode TUI.
//
// It fills opencode's EXISTING side panel (the `sidebar.content` slot) with a
// stats/info panel — Window, Tokens, Models, Todo, Skills, Agents, Memory — and
// nothing else moves: no routes, no docked overlay, no replaced slots. It is
// built to read as part of opencode rather than bolted onto it:
//
//   * same visual grammar as opencode's own sidebar rows — a label in
//     `theme.text.base` and a value in `theme.text.muted`, on one line, with the
//     label taking `flexGrow` and the value `flexShrink 0` so the value pins to
//     the right edge at any panel width (opencode's own MCP rows do this);
//   * same numbers as opencode's readout (total tokens = in+out+reasoning+
//     cache read+write; context % = total / model.limit.context);
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
//   * There is NO todo store (`data.session.todo` / `data.location.todo` are
//     undefined), so the session todo list is read read-only from opencode's
//     own SQLite db and degrades to "none" if that schema ever changes.
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

// Project-local only: a $HOME probe here recalled a different project's facts
// (state_dir() in harness/paths.py is always <root>/.opencode/harness).
function factDb(directory: string): string {
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
    let view: Rec = { ...defaultSections(), sessionID: "", skills: 0, agents: 0, todos: 0, models: 0 }
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
    const isOpen = (name: string) => view.open === name

    // Plain (non-reactive) detail rows: re-read on every load and shown as-is.
    const data: Rec = {
      tokens: null,
      cost: 0,
      agent: "",
      model: "",
      contextLimit: 0,
      steps: [] as Rec[],
      providers: [] as string[],
      agents: [] as string[],
      skills: [] as string[],
      facts: [] as string[],
      todos: [] as string[],
    }
    let sqlite: any = null
    let fs: any = null
    let sessionID = ""
    let loading = false
    let pendingFull = false
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

    /** Best-effort: opencode's own DB (read-only) for the session todo list. */
    const readTodos = async (sid: string): Promise<string[]> => {
      try {
        if (fs === null) fs = await import("node:fs")
        if (sqlite === null) sqlite = await import("bun:sqlite")
        const dbPath = `${process.env?.HOME}/.local/share/opencode/opencode.db`
        if (!fs.existsSync(dbPath)) return []
        const db = new sqlite.Database(dbPath, { readonly: true })
        try {
          const rows = db
            .query("SELECT content, status FROM todo WHERE session_id = ? ORDER BY position LIMIT ?")
            .all(sid, ROW_LIMIT) as Rec[]
          return rows.map((r) => {
            const mark = r?.status === "completed" ? "●" : r?.status === "in_progress" ? "◐" : "○"
            return `${mark} ${cut(r?.content, 40)}`
          })
        } finally {
          db.close()
        }
      } catch {
        return []
      }
    }

    const readFacts = async (directory: string): Promise<string[]> => {
      try {
        if (fs === null) fs = await import("node:fs")
        if (sqlite === null) sqlite = await import("bun:sqlite")
        const dbPath = factDb(directory)
        if (!fs.existsSync(dbPath)) return []
        const db = new sqlite.Database(dbPath, { readonly: true })
        try {
          return (db.query("SELECT text FROM facts ORDER BY id DESC LIMIT ?").all(ROW_LIMIT) as Rec[]).map(
            (r) => `✎ ${cut(r?.text, 40)}`,
          )
        } finally {
          db.close()
        }
      } catch {
        return []
      }
    }

    let scanned = false

    /**
     * The slow half of a scan: the session's assistant messages (Models rows),
     * the lazily-synced location stores (agents/skills/providers/models) and the
     * context-window limit. A `sync()` can block on the network and none of it
     * changes between messages, so it runs when the session changes, on the
     * first load and on `/harness-refresh` — not on the cheap 8s poll.
     *
     * Every sync is individually time-boxed: one unreachable provider registry
     * must not strand the whole panel on its placeholder.
     */
    const scanSlow = async (loc: any, data_: Rec): Promise<void> => {
      const store = data_?.location

      // Model rows: assistant messages grouped by provider -> model, with the
      // step count (Kilo shows Model / Steps / Cost per provider group).
      const msgs = sessionID ? asArray(data_?.session?.message?.list?.(sessionID)) : []
      const byProv = new Map<string, Map<string, { steps: number; cost: number }>>()
      for (const m of msgs) {
        const role = m?.role ?? m?.type
        if (role !== "assistant") continue
        const prov = String(m?.providerID ?? m?.model?.providerID ?? "unknown")
        const name = shortModel(modelId(m?.model) || data.model)
        if (!byProv.has(prov)) byProv.set(prov, new Map())
        const inner = byProv.get(prov)!
        const cur = inner.get(name) ?? { steps: 0, cost: 0 }
        cur.steps += 1
        cur.cost += Number(m?.cost ?? 0)
        inner.set(name, cur)
      }

      // Location stores ARE lazy: they need sync first or list() is empty.
      await Promise.all([
        settle(store?.model?.sync?.(loc), SCAN_MS),
        settle(store?.provider?.sync?.(loc), SCAN_MS),
        settle(store?.agent?.sync?.(loc), SCAN_MS),
        settle(store?.skill?.sync?.(loc), SCAN_MS),
      ])
      const provs = asArray(store?.provider?.list?.(loc))
      const models = asArray(store?.model?.list?.(loc))
      data.providers = provs.map((p: Rec) => String(p?.id ?? p?.name ?? "")).filter(Boolean).slice(0, ROW_LIMIT)

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

      const steps: Rec[] = []
      for (const [prov, used] of byProv) {
        const available = Object.keys(provs.find((p: Rec) => String(p?.id ?? p?.name ?? "") === prov)?.models ?? {}).length
        steps.push({ row: available ? `${prov} · ${available} models` : prov, kind: "provider" })
        for (const [name, e] of used) {
          steps.push({ row: `${name} · 1 step`, kind: "row", cost: e.cost })
        }
      }
      data.steps = steps

      data.agents = asArray(store?.agent?.list?.(loc))
        .map((a: Rec) => String(a?.name ?? a?.id ?? ""))
        .filter(Boolean)
      data.skills = asArray(store?.skill?.list?.(loc))
        .map((s: Rec) => String(s?.id ?? s?.name ?? ""))
        .filter((s: string) => s.startsWith(HARNESS_PREFIX))
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
        data.tokens = session?.tokens ?? null
        data.agent = String(session?.agent ?? "")
        data.model = modelId(session?.model)
        // opencode's own cost accessor (the session row's `cost` works too).
        try {
          const c = sessionID ? data_?.session?.cost?.(sessionID) : undefined
          data.cost = Number(c ?? session?.cost ?? 0) || 0
        } catch {
          data.cost = Number(session?.cost ?? 0) || 0
        }

        // Slow sources only when they can have changed: session change, first
        // load, or an explicit refresh (see scanSlow).
        if (full || !scanned) {
          await settle(scanSlow(loc, data_), SCAN_MS)
          scanned = true
        }

        // Location-scoped, so these also work before the first message.
        data.todos = sessionID ? await readTodos(sessionID) : []
        data.facts = await readFacts(directory)

        // warm the session store for the next poll (see the note above on why
        // this cannot happen before the reads)
        if (sessionID) {
          void settle(data_?.session?.sync?.(sessionID), SCAN_MS)
          void settle(data_?.session?.message?.sync?.(sessionID), SCAN_MS)
        }
      } catch (e) {
        notes.push(short(e))
      } finally {
        patch((d) => {
          d.sessionID = sessionID.slice(0, 12)
          d.skills = data.skills.length
          d.agents = data.agents.length
          d.todos = data.todos.length
          d.models = data.steps.filter((s: Rec) => s.kind === "row").length
          d.providers = data.providers.length
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
    const Row = (props: { id: string; label: string; value: () => string; lines: () => string[]; empty: string }) => (
      <box flexDirection="column">
        <box
          flexDirection="row"
          gap={1}
          minWidth={0}
          onMouseDown={() =>
            patch((d) => {
              d.open = d.open === props.id ? "" : props.id
            })
          }
        >
          <text fg={isOpen(props.id) ? th.accent : th.base} flexShrink={0}>
            {isOpen(props.id) ? "▾" : "▸"}
          </text>
          <text fg={isOpen(props.id) ? th.accent : th.base} flexGrow={1} wrapMode="none" truncate>
            {props.label}
          </text>
          <text fg={th.muted} flexShrink={0} wrapMode="none">
            {props.value()}
          </text>
        </box>
        {isOpen(props.id) ? (
          <box flexDirection="column">
            {(props.lines().length ? props.lines() : [props.empty]).slice(0, DETAIL_LIMIT).map((l) => (
              <text fg={th.muted} wrapMode="none" truncate>
                {`  ${cut(l, 42)}`}
              </text>
            ))}
          </box>
        ) : null}
      </box>
    )

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
            const pct = (): number => {
              void view.rev
              return data.contextLimit > 0 ? Math.min(100, Math.round((total() / data.contextLimit) * 100)) : 0
            }
            const windowValue = (): string => {
              void view.rev
              if (!data.tokens) return "—"
              return data.contextLimit > 0 ? `${bar(pct())} ${pct()}%` : `${kfmt(total())} tok`
            }
            const windowLines = (): string[] => {
              void view.rev
              if (!data.tokens) return []
              const out: string[] = []
              if (data.contextLimit > 0) {
                out.push(`${numfmt(total())} / ${numfmt(data.contextLimit)} of window`)
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
              return [
                `input ${numfmt(t.input)} · output ${numfmt(t.output)}`,
                `reasoning ${numfmt(t.reasoning)}`,
                `cache read ${numfmt(cache.read)} · write ${numfmt(cache.write)}`,
                `cost $${Number(data.cost ?? 0).toFixed(4)}`,
              ]
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
                      : [view.sessionID || "", `${view.skills ?? 0} skills`, `${view.facts ?? 0} facts`]
                          .filter((s) => String(s).length && s !== "0 skills")
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
                  lines={windowLines}
                  empty="(no context data yet)"
                />
                <Row id="tokens" label="Tokens" value={tokenValue} lines={tokenLines} empty="(no usage yet)" />
                <Row
                  id="models"
                  label="Models"
                  value={() => `${view.models ?? 0} used · ${view.providers ?? 0} providers`}
                  lines={() => {
                    void view.rev
                    return data.steps.map((s: Rec) => String(s.row))
                  }}
                  empty="(none used in this session)"
                />
                <Row
                  id="todo"
                  label="Todo"
                  value={() => `${view.todos ?? 0} item${(view.todos ?? 0) === 1 ? "" : "s"}`}
                  lines={() => {
                    void view.rev
                    return data.todos
                  }}
                  empty="(none in this session)"
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
                    return data.agents.map((a) => `◦ ${a}`)
                  }}
                  empty="(none registered)"
                />
                <Row
                  id="memory"
                  label="Memory"
                  value={() => `${view.facts ?? 0} fact${(view.facts ?? 0) === 1 ? "" : "s"}`}
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
      ctx.ui.slot({
        append: "sidebar.footer",
        render: () => (
          <text fg={th.muted} wrapMode="none" truncate>
            {`harness · ${view.open ? "row open" : "click a row"}`}
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
      if (totalTok > 0) return `harness · ${kfmt(totalTok)} tok · $${Number(data.cost ?? 0).toFixed(2)}`
      // No session yet: the skill/fact stores are location-scoped and empty at
      // the default location, so counting them here would print a misleading
      // "0 skills" next to a plugin that ships a skill pack.
      if (!view.sessionID) return "harness · click for stats"
      return `harness · ${view.skills ?? 0} skills · ${view.facts ?? 0} facts`
    }

    // ---- footer chip: headline stats, click toggles the sidebar ----------
    try {
      ctx.ui.slot({
        append: "home.footer.status",
        render: (props: Rec) => {
          rendered(props)
          return (
            <box onMouseDown={toggleSidebar}>
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
 * Initial view state: which row is expanded (one at a time — the sidebar is a
 * short viewport, so the collapsed rows must stay visible as one-line stats)
 * plus the counters the rows render. `rev` is bumped on every load to trigger a
 * repaint; `scanning` is the placeholder flag (reactive, never a plain var).
 */
function defaultSections(): Rec {
  return { open: "", rev: 0, scanning: false }
}

export default HarnessTui
