// Harness CLI plugin for the opencode TUI.
//
// It fills opencode's EXISTING side panel (the `sidebar.content` slot) with a
// Kilo-style stats/info panel — session header, Context, Token usage, Models,
// Todo, Agents + Skills, Memory. It deliberately does not move anything around:
// no routes, no docked overlay, no replaced slots. The only other contribution
// is a small footer chip (the built-in status row) that shows headline numbers
// and toggles the sidebar.
//
// Contract (opencode v2.0.8), everything probed live rather than assumed:
//   * Lives beside server.ts in ~/.config/opencode/plugins/harness/. The loader
//     probes a plugin DIRECTORY for `server.*` and `tui.*` entrypoints and sets
//     features.tui (the flag the Plugins panel filters on) only when a tui
//     entrypoint exists.
//   * The TUI compiles this .tsx itself (JSX -> OpenTUI elements), so JSX needs
//     no imports. No static imports on purpose: the TUI bundles its own
//     solid-js and a second copy would break reactivity, so state comes from
//     `ctx.storage.memory` instead. Dynamic `import("…")` is fine; `require` and
//     `Bun` are NOT defined in this scope (`node:fs` and `bun:sqlite` resolve).
//   * `ctx.keymap.layer(...)` throws "Keymap.Provider is missing" unless it is
//     called from inside a slot's render component, so commands are registered
//     from the `app` slot (the pattern the CLI-plugin docs use).
//   * Data stores need `sync(location)` before `list(location)` returns
//     anything; session stats come from `data.session.get(sid)` as
//     `{tokens:{input,output,reasoning,cache:{read,write}}, cost, time, ...}`.
//   * Mouse reporting is on (`?1002h ?1003h ?1006h`), so section headers are
//     clickable to collapse/expand.
//   * Every step is guarded: a TUI API drift degrades to a log line or a plain
//     text row, never a broken screen.

type Rec = Record<string, any>

const STATE = "harness.sidebar.state"
const HARNESS_PREFIX = "harness-"
const POLL_MS = 8000

/**
 * opencode's skill store namespaces the seeded skills (`harness-spec-driven-
 * development`), but the sidebar is 46 columns wide and the namespace is the
 * same for every row — so the row shows the skill itself (`spec-driven-
 * development`). Only the display is shortened; ids keep the prefix.
 */
const shortSkill = (id: unknown) => String(id ?? "").replace(/^harness-/, "")
const ROW_LIMIT = 6

const log = (m: string) => console.error(`[harness tui] ${m}`)
const err = (e: unknown) => (e instanceof Error ? e.message : String(e))
const short = (e: unknown) => err(e).slice(0, 140)

/** 12345 -> "12,345" */
function fmt(n: unknown): string {
  const v = Math.round(Number(n ?? 0))
  if (!Number.isFinite(v)) return "0"
  return v.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",")
}

/** 9603 -> "9.6k" (chip-sized) */
function kfmt(n: unknown): string {
  const v = Number(n ?? 0)
  if (!Number.isFinite(v) || v <= 0) return "0"
  if (v < 1000) return String(Math.round(v))
  if (v < 1_000_000) return `${(v / 1000).toFixed(1)}k`
  return `${(v / 1_000_000).toFixed(1)}m`
}

function cut(value: unknown, max: number): string {
  const s = String(value ?? "").replace(/\s+/g, " ").trim()
  return s.length <= max ? s : `${s.slice(0, Math.max(1, max - 1))}…`
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

function modelProvider(value: unknown): string {
  if (!value || typeof value === "string") return ""
  return String((value as Rec).providerID ?? "")
}

/** "opencode/muse-spark" -> ["opencode", "muse-spark"] */
function splitModel(value: unknown): [string, string] {
  const id = String(value ?? "")
  const i = id.indexOf("/")
  return i < 0 ? ["", id] : [id.slice(0, i), id.slice(i + 1)]
}

function shortModel(rid: unknown): string {
  const parts = String(rid ?? "").split("/")
  const last = parts[parts.length - 1] || String(rid ?? "")
  return last.length > 22 ? `${last.slice(0, 21)}…` : last
}

/** Normalize whatever a data store hands back into an array. */
function asArray(listed: any): Rec[] {
  for (const c of [listed, listed?.data, listed?.items, listed?.value]) {
    if (Array.isArray(c)) return c
    for (const inner of [c?.data, c?.items]) if (Array.isArray(inner)) return inner
  }
  return []
}

function factDbs(directory: string): string[] {
  const home = process.env?.HOME ?? ""
  return [
    `${directory}/.opencode/harness/sessions.db`,
    `${home}/.opencode/harness/sessions.db`,
  ].filter((p) => p && !p.startsWith("/.opencode"))
}

const HarnessTui = {
  id: "harness",

  setup(ctx: Rec) {
    if (!ctx?.ui || typeof ctx.ui.slot !== "function") {
      log("ui.slot unavailable on this opencode version — TUI views skipped")
      return
    }

    const th = {
      base: ctx?.theme?.text?.base ?? "white",
      muted: ctx?.theme?.text?.muted ?? "gray",
      accent: ctx?.theme?.text?.accent ?? ctx?.theme?.categorical?.[0] ?? "magenta",
      warn: ctx?.theme?.text?.warning ?? "yellow",
    }

    // Reactive view state (section open flags). storage.memory is a reactive
    // [store, update] pair: clicking a header writes to it and the sidebar
    // repaints. Without it the panel still renders, just cannot collapse.
    let view: Rec = { ...defaultSections(), sessionID: "", skills: 0, agents: 0, todos: 0, models: 0, rev: 0, scanning: false }
    let setView: ((fn: (draft: Rec) => void) => void) | null = null
    try {
      const pair = ctx.storage?.memory?.(STATE, { initial: { ...defaultSections() } })
      if (pair?.[0] && typeof pair?.[1] === "function") {
        view = pair[0]
        setView = pair[1]
      }
    } catch (e) {
      log(`storage.memory unavailable (${short(e)}) — sections cannot collapse`)
    }
    const patch = (fn: (draft: Rec) => void) => {
      try {
        setView?.(fn)
      } catch (e) {
        log(`state update failed: ${short(e)}`)
      }
    }
    const isOpen = (name: string) => view.open === name

    // Plain (non-reactive) detail rows: re-read on every load and shown as-is.
    const data: Rec = {
      tokens: null,
      cost: 0,
      time: null,
      agent: "",
      model: "",
      contextLimit: 0,
      steps: [] as Rec[],
      providers: [] as string[],
      agents: [] as string[],
      skills: [] as string[],
      facts: [] as string[],
      todos: [] as string[],
      notes: [] as string[],
    }
    let sqlite: any = null
    let fs: any = null
    let sessionID = ""
    let loading = false
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
            return `${mark} ${cut(r?.content, 44)}`
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
        const out: string[] = []
        for (const dbPath of factDbs(directory)) {
          try {
            if (!fs.existsSync(dbPath)) continue
            const db = new sqlite.Database(dbPath, { readonly: true })
            try {
              for (const r of db.query("SELECT text FROM facts ORDER BY id DESC LIMIT ?").all(ROW_LIMIT) as Rec[]) {
                out.push(`✎ ${cut(r?.text, 44)}`)
              }
            } finally {
              db.close()
            }
          } catch {
            /* unreadable db: try the next candidate */
          }
          if (out.length) break
        }
        return out
      } catch {
        return []
      }
    }

    let scanned = false

    /**
     * The slow half of a scan: the session's assistant messages (Models rows),
     * the lazily-synced location stores (agents/skills/providers/models) and the
     * context-window limit. A `provider.sync()` can block on the network and
     * none of it changes between messages, so it runs when the session changes,
     * on the first load and on `/harness-refresh` — not on the 8s poll, which
     * only needs the counters that move during a turn.
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
        const prov = String(m?.providerID ?? m?.model?.providerID ?? modelProvider(m?.model) ?? "unknown")
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
        Promise.resolve(store?.model?.sync?.(loc)).catch(() => {}),
        Promise.resolve(store?.provider?.sync?.(loc)).catch(() => {}),
        Promise.resolve(store?.agent?.sync?.(loc)).catch(() => {}),
        Promise.resolve(store?.skill?.sync?.(loc)).catch(() => {}),
      ])
      const provs = asArray(store?.provider?.list?.(loc))
      const models = asArray(store?.model?.list?.(loc))
      data.providers = provs.map((p: Rec) => String(p?.id ?? p?.name ?? "")).filter(Boolean).slice(0, ROW_LIMIT)

      const [provId, modelName] = splitModel(data.model)
      const provEntry = provs.find((p: Rec) => String(p?.id ?? p?.name ?? "") === provId)
      const fromProvider = provEntry?.models?.[modelName] ??
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
          steps.push({ row: `  ${name} ${e.steps} ${e.cost ? `$${e.cost.toFixed(4)}` : "–"}`, kind: "row" })
        }
      }
      if (!steps.length) steps.push({ row: "(no steps in this session yet)", kind: "row" })
      data.steps = steps

      data.agents = asArray(store?.agent?.list?.(loc)).map((a: Rec) => String(a?.name ?? a?.id ?? "")).filter(Boolean)
      data.skills = asArray(store?.skill?.list?.(loc))
        .map((s: Rec) => String(s?.id ?? s?.name ?? ""))
        .filter((s: string) => s.startsWith(HARNESS_PREFIX))
    }

    const load = async (full = false) => {
      if (loading) return
      loading = true
      // "scanning…" is driven by a REACTIVE flag, never by the plain `loading`
      // guard: read inside a tracked JSX expression (where it is not reactive),
      // a plain variable reports whatever it happened to hold at that repaint —
      // which is how the placeholder ended up stuck on screen next to fully
      // loaded stats. The flag is cleared in the same state update that bumps
      // `rev`, so the placeholder can never outlive its scan.
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
        data.cost = Number(session?.cost ?? 0)
        data.time = session?.time ?? null
        data.agent = String(session?.agent ?? "")
        data.title = String(session?.title ?? "")
        data.model = modelId(session?.model)

        // Slow sources only when they can have changed: session change, first
        // load, or an explicit refresh (see scanSlow).
        if (full || !scanned) {
          await scanSlow(loc, data_)
          scanned = true
        }

        // The rows below are location-scoped, so they work on
        // the home screen too (no session yet) — that is what keeps the footer
        // chip from claiming "0 skills" before your first message.
        data.todos = sessionID ? await readTodos(sessionID) : []
        data.facts = await readFacts(directory)

        // warm the session store for the next poll (see the note above on why
        // this cannot happen before the reads)
        if (sessionID) {
          void Promise.resolve(data_?.session?.sync?.(sessionID)).catch(() => {})
          void Promise.resolve(data_?.session?.message?.sync?.(sessionID)).catch(() => {})
        }
      } catch (e) {
        notes.push(short(e))
      } finally {
        data.notes = notes
        patch((d) => {
          d.sessionID = sessionID.slice(0, 12)
          d.skills = data.skills.length
          d.agents = data.agents.length
          d.todos = data.todos.length
          d.models = data.steps.filter((s: Rec) => s.kind === "row" && !String(s.row).includes("(")).length
          d.providers = data.providers.length
          // Surface a load failure in the panel itself: cli-side console.error
          // does not reach opencode's log, so a silent catch would leave only
          // an empty-looking panel to debug
          d.note = notes[0] ?? ""
          d.scanning = false
          // The rows above are plain objects, not reactive: bump a counter the
          // render reads so the store notifies and the panel repaints with the
          // freshly loaded stats. Without this the panel keeps the snapshot it
          // painted before the first load finished ("no tokens reported yet")
          // even though the data arrived a moment later.
          d.rev = Number(d.rev ?? 0) + 1
        })
        loading = false
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

    const headline = () => {
      void view.rev // tracked read: repaint when a load lands
      const t = data.tokens
      const total = t ? Number(t.input ?? 0) + Number(t.output ?? 0) + Number(t.reasoning ?? 0) : 0
      if (total > 0) return `harness · ${kfmt(total)} tok · $${Number(data.cost ?? 0).toFixed(2)}`
      // No session yet: the skill/fact stores are location-scoped and empty at
      // the default location, so counting them here would print a misleading
      // "0 skills" next to a plugin that ships 11. Advertise the panel instead.
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
              <text fg={th.muted}>{headline()}</text>
            </box>
          )
        },
      })
    } catch (e) {
      log(`footer chip failed: ${short(e)}`)
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
                        variant: data.notes.length ? "warning" : "info",
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
     * Accordion section, Kilo-style: click the header to open it. Only ONE
     * section shows its detail rows at a time — the sidebar is a short, fixed
     * viewport (it does not scroll), so an all-expanded panel would push the
     * lower sections off-screen and they would look missing. Collapsed sections
     * still show their headline, so every stat stays visible at a glance.
     */
    const Section = (props: { name: string; title: string; summary: () => string; children: any }) => (
      <box flexDirection="column">
        <box
          flexDirection="row"
          onMouseDown={() =>
            patch((d) => {
              d.open = d.open === props.name ? "" : props.name
            })
          }
        >
          <text fg={isOpen(props.name) ? th.accent : th.base}>
            {`${isOpen(props.name) ? "▼" : "▶"} ${props.title}  ${cut(props.summary(), 26)}`}
          </text>
        </box>
        {isOpen(props.name) ? props.children : null}
      </box>
    )

    /**
     * Detail rows. `lines()` is called INSIDE the JSX expression on purpose:
     * Solid re-runs tracked JSX expressions, not the component/render body, so a
     * plain `const rows = lines()` up here would freeze at the first paint and
     * keep showing the pre-load snapshot ("window 9,603 tokens (limit unknown)")
     * no matter what the store did later. The closures read `view.rev`, which is
     * what makes the expression tracked.
     */
    const Rows = (props: { lines: () => string[]; empty: string }) => (
      <box flexDirection="column">
        {(props.lines().length ? props.lines() : [props.empty]).map((l) => (
          <text fg={th.muted}>{cut(l, 46)}</text>
        ))}
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
            // the note on Rows() for why that is required.
            const pct = () => {
              void view.rev
              const t = data.tokens
              return data.contextLimit > 0 && t
                ? Math.min(100, Math.round((100 * Number(t.input ?? 0)) / data.contextLimit))
                : 0
            }
            const ctxLines = (): string[] => {
              void view.rev
              const t = data.tokens
              const cache = t?.cache ?? {}
              if (!t) return ["(no tokens reported yet — send a message)"]
              // Four rows is the budget: header + six section headlines + an
              // open section has to fit the sidebar's fixed viewport (it does
              // not scroll), or the sections below fall off the bottom and look
              // missing. The window/percentage already sits in the summary, so
              // the model + agent identity takes its row instead.
              return [
                `${shortModel(data.model) || "model —"} · agent ${data.agent || "—"}`,
                `input ${fmt(t.input)} · output ${fmt(t.output)}`,
                `reasoning ${fmt(t.reasoning)} · cache ${fmt(cache.read)}/${fmt(cache.write)}`,
                `cost $${Number(data.cost ?? 0).toFixed(4)}`,
              ]
            }
            const usageLines = (): string[] => {
              void view.rev
              const t = data.tokens
              const cache = t?.cache ?? {}
              if (!t) return []
              // paired so the section stays inside the four-row budget (Kilo
              // lists each counter separately; the numbers are identical)
              return [
                `Input ${fmt(t.input)} · Output ${fmt(t.output)}`,
                `Reasoning ${fmt(t.reasoning)}`,
                `Cache read ${fmt(cache.read)} · write ${fmt(cache.write)}`,
                `Cost $${Number(data.cost ?? 0).toFixed(4)}`,
              ]
            }
            const stepRows = (): string[] => {
              void view.rev
              return data.steps.map((s: Rec) => s.row as string)
            }
            const factRows = (): string[] => {
              void view.rev
              return data.facts
            }
            const todoRows = (): string[] => {
              void view.rev
              return data.todos
            }
            const agentRows = (): string[] => {
              void view.rev
              return [
                ...data.skills.slice(0, 5).map((s) => `▪ ${shortSkill(s)}`),
                ...data.agents.slice(0, 3).map((a) => `◦ ${a}`),
              ]
            }
            return (
              <box flexDirection="column">
                {/* one header row: the agent/model identity moved into the
                    Context rows, so the header costs one line instead of two */}
                <text fg={view.note ? th.warn : th.accent}>
                  {view.note ? cut(view.note, 46) : `harness · ${view.sessionID || "no session"}`}
                </text>

                <Section
                  name="context"
                  title="Context"
                  summary={() => {
                    void view.rev
                    const tok = data.tokens
                    if (!tok) return "—"
                    return `${fmt(tok.input)}/${data.contextLimit ? fmt(data.contextLimit) : "?"}${
                      pct() ? ` ${pct()}%` : ""
                    }`
                  }}
                >
                  <Rows lines={ctxLines} empty="(no context data)" />
                </Section>

                <Section
                  name="usage"
                  title="Token usage"
                  summary={() => {
                    void view.rev
                    const tok = data.tokens
                    return tok ? `in ${fmt(tok.input)} · out ${fmt(tok.output)}` : "—"
                  }}
                >
                  <Rows lines={usageLines} empty="(no usage yet)" />
                </Section>

                <Section
                  name="models"
                  title="Models"
                  summary={() => `${view.models ?? 0} used · ${view.providers ?? 0} providers`}
                >
                  <Rows lines={stepRows} empty="(no models)" />
                </Section>

                <Section
                  name="todo"
                  title="Todo"
                  summary={() => `${view.todos ?? 0} item${(view.todos ?? 0) === 1 ? "" : "s"}`}
                >
                  <Rows lines={todoRows} empty="(no todos in this session)" />
                </Section>

                <Section
                  name="agents"
                  title="Agents + Skills"
                  summary={() => `${view.agents ?? 0} / ${view.skills ?? 0}`}
                >
                  <Rows lines={agentRows} empty="(none registered)" />
                </Section>

                <Section
                  name="memory"
                  title="Memory"
                  summary={() => `${data.facts.length} fact${data.facts.length === 1 ? "" : "s"}`}
                >
                  <Rows lines={factRows} empty="(no durable facts yet)" />
                </Section>

                {data.notes.length ? <text fg={th.warn}>{cut(data.notes[0], 46)}</text> : null}
                {view.scanning ? <text fg={th.muted}>scanning skills · memory · todos…</text> : null}
              </box>
            )
          } catch (e) {
            return <text fg={th.warn}>harness panel: {short(e)}</text>
          }
        },
      })
      ctx.ui.slot({
        append: "sidebar.footer",
        render: () => <text fg={th.muted}>harness · /harness · click header</text>,
      })
    } catch (e) {
      log(`sidebar slots failed: ${short(e)}`)
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
 * Initial state: which section is open (accordion — one at a time; the sidebar
 * viewport is short and does not scroll, so the collapsed rows must stay visible
 * as one-line headlines). `rev` is bumped on every load to trigger a repaint.
 */
function defaultSections(): Rec {
  // nothing expanded by default: the sidebar viewport is short, so six
  // one-line headlines are guaranteed to fit where an expanded section would
  // push the last sections off the bottom (nothing scrolls).
  return { open: "", rev: 0, scanning: false }
}

export default HarnessTui
