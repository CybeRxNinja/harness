// Harness CLI plugin for the opencode TUI. Lives beside server.ts in
// ~/.config/opencode/plugins/harness/ — the loader probes a plugin DIRECTORY
// for `server.*` and `tui.*` entrypoints; a bare harness.ts can only ever be a
// server plugin, which is exactly why it used to show under "Server" in the
// TUI's Plugins panel while never appearing in the plugin list (that list
// filters on features.tui, which is only set when a tui entrypoint exists).
//
// Contract (opencode v2.0.8), all of it probed live rather than assumed:
//   * The TUI compiles this .tsx itself (JSX -> OpenTUI elements), so JSX needs
//     no imports. No static imports on purpose: the TUI bundles its own
//     solid-js, and a second copy would break reactivity, so state comes from
//     `ctx.storage` instead. Dynamic `import("…")` is fine — `require` and
//     `Bun` are NOT defined here (`node:fs` and `bun:sqlite` do resolve).
//   * Default export: { id, setup }. setup(ctx) gets the TUI context
//     (location, app, client, data, theme, renderer, ui, keymap, storage).
//   * `ctx.ui.slot({placement, target, render})` adds JSX at a named slot.
//     Placements: prepend|append|before|after|replace. Targets: app,
//     home.footer, home.footer.status, prompt.footer, prompt.footer.status,
//     prompt.footer.file, session.composer.top, sidebar.content,
//     sidebar.footer, session.panel.
//   * `ctx.keymap.layer(...)` throws "Keymap.Provider is missing" unless it is
//     called from inside a slot's render component, so the palette command is
//     registered from the `app` slot (the pattern the CLI-plugin docs use).
//   * `session.panel` + `ctx.ui.panel.open(name)` is the host-owned side panel:
//     the host sizes/focuses it and keeps narrow terminals full-screen. The
//     contribution renders only when `panel.name` is its own name.
//   * `ctx.storage.memory(key, {initial})` returns a reactive [store, update];
//     writing to it repaints the slots (verified live). State lives there so
//     the views update without importing a signal library.
//   * Every step is guarded: a TUI API drift must degrade to a log line or a
//     plain-text fallback, never break the terminal.
//
// What it renders:
//   1. footer chip       `harness · N skills · M facts`, click opens the panel
//   2. side panel        skills + memory views (open with ctrl+g or /harness)
//   3. sidebar content   compact skills/facts summary — the sidebar is empty
//                        without a plugin contribution
//   4. palette command   "Harness: open panel" (+ slash /harness, /harness-refresh)

type Rec = Record<string, any>

const PANEL = "harness.panel"
const STORE = "harness.panel.state"
const SKILL_LIMIT = 12
const FACT_LIMIT = 8

const log = (m: string) => console.error(`[harness tui] ${m}`)
const err = (e: unknown) => (e instanceof Error ? e.message : String(e))
const short = (e: unknown) => err(e).slice(0, 140)

/** Normalize whatever the skill store hands back into an array. */
function asSkills(listed: any): Rec[] {
  for (const c of [listed, listed?.data, listed?.skills, listed?.items]) {
    if (Array.isArray(c)) return c
    for (const inner of [c?.data, c?.skills, c?.items]) if (Array.isArray(inner)) return inner
  }
  return []
}

function cut(value: unknown, max: number): string {
  const s = String(value ?? "").replace(/\s+/g, " ").trim()
  return s.length <= max ? s : `${s.slice(0, max - 1)}…`
}

/** Candidate fact DBs: project-local first (mirrors the server plugin). */
function factDbs(directory: string): string[] {
  const home = process.env?.HOME ?? ""
  return [`${directory}/.opencode/harness/sessions.db`, `${home}/.opencode/harness/sessions.db`].filter(
    (p) => p && !p.startsWith("/.opencode"),
  )
}

const HarnessTui = {
  id: "harness",

  setup(ctx: Rec) {
    if (!ctx?.ui || typeof ctx.ui.slot !== "function") {
      log("ui.slot unavailable on this opencode version — harness TUI views skipped")
      return
    }

    const th = {
      base: ctx?.theme?.text?.base ?? "white",
      muted: ctx?.theme?.text?.muted ?? "gray",
      accent: ctx?.theme?.text?.accent ?? ctx?.theme?.categorical?.[0] ?? "magenta",
      warn: ctx?.theme?.text?.warning ?? "yellow",
    }

    // Reactive view state. storage.memory is a reactive [store, update] pair;
    // writing to it repaints the slots. Falls back to a plain object if the
    // API is missing (views then render but do not live-update).
    let view: Rec = { tab: "skills", skills: 0, facts: 0, ready: false, note: "" }
    let setView: ((fn: (draft: Rec) => void) => void) | null = null
    try {
      const pair = ctx.storage?.memory?.(STORE, {
        initial: { tab: "skills", skills: 0, facts: 0, ready: false, note: "" },
      })
      if (pair?.[0] && typeof pair?.[1] === "function") {
        view = pair[0]
        setView = pair[1]
      }
    } catch (e) {
      log(`storage.memory unavailable (${short(e)}) — panel state will not repaint`)
    }
    const patch = (fn: (draft: Rec) => void) => {
      try {
        setView?.(fn)
      } catch (e) {
        log(`state update failed: ${short(e)}`)
      }
    }

    // Detail data (only read when a view mounts). Counts live in `view` so the
    // chip and sidebar repaint when the refresh lands.
    const data: Rec = { skills: [] as Rec[], facts: [] as string[] }
    let sqlite: any = null
    let fs: any = null

    const location = () => {
      try {
        return ctx.location ?? ctx.data?.location?.default?.()
      } catch {
        return null
      }
    }

    const oid = (s: Rec) => String(s?.id ?? s?.name ?? "")
    const isHarness = (s: Rec) => oid(s).startsWith("harness-")

    const refresh = async () => {
      const loc = location()
      const directory: string = loc?.directory ?? process.cwd?.() ?? "."
      let note = ""
      // skills: the server plugin seeds harness-* skills into opencode's store,
      // so read them from there rather than rescanning the filesystem.
      try {
        await Promise.resolve(ctx.data?.location?.skill?.sync?.(loc))
        const listed = ctx.data?.location?.skill?.list?.(loc)
        data.skills = asSkills(listed).map((s) => ({
          id: String(s?.id ?? s?.name ?? ""),
          description: cut(s?.description, 90),
        }))
      } catch (e) {
        note = `skills: ${short(e)}`
      }
      // memory: durable facts straight off disk (the DB the server plugin uses)
      try {
        if (fs === null) fs = await import("node:fs")
        if (sqlite === null) sqlite = await import("bun:sqlite")
        const found: string[] = []
        for (const dbPath of factDbs(directory)) {
          try {
            if (!fs.existsSync(dbPath)) continue
            const db = new sqlite.Database(dbPath, { readonly: true })
            try {
              for (const r of db.query("SELECT text, source FROM facts ORDER BY id DESC LIMIT ?").all(FACT_LIMIT)) {
                found.push(`- [${r?.source ?? "?"}] ${cut(r?.text, 100)}`)
              }
            } finally {
              db.close()
            }
          } catch {
            /* unreadable DB: keep going with the next candidate */
          }
          if (found.length >= FACT_LIMIT) break
        }
        data.facts = found
      } catch (e) {
        if (!note) note = `memory: ${short(e)}`
      }
      patch((d) => {
        // count the harness-* skills, not every skill in the store: the panel
        // lists only harness ones, and a chip reading "13 skills" next to 11
        // listed rows reads like a bug
        d.skills = data.skills.filter(isHarness).length
        d.facts = data.facts.length
        d.ready = true
        d.note = note
      })
    }
    void refresh()
    let timer: any = null
    try {
      // facts are written by the server plugin during a session; a slow poll
      // keeps the counts honest without any server push channel
      timer = setInterval(() => void refresh(), 15000)
    } catch (e) {
      log(`poll unavailable: ${short(e)}`)
    }

    const harnessSkills = () => data.skills.filter(isHarness)
    // Counts are patched in asynchronously (skill store sync + disk read), so
    // say we are still looking rather than showing a wrong "0 skills".
    const counts = () =>
      view.ready ? `${String(view.skills ?? 0)} skills · ${String(view.facts ?? 0)} facts` : "scanning skills + memory…"

    const openPanel = () => {
      try {
        const ok = ctx.ui.panel?.open?.(PANEL)
        if (ok === false) ctx.ui.toast?.show?.({ title: "harness", message: "open a session first", variant: "info" })
      } catch (e) {
        log(`panel.open failed: ${short(e)}`)
      }
    }

    // ---- 1. footer chip (clickable) -------------------------------------
    try {
      ctx.ui.slot({
        append: "home.footer.status",
        render: () => (
          <box onMouseDown={openPanel}>
            <text fg={th.muted}>{"harness · " + counts()}</text>
          </box>
        ),
      })
    } catch (e) {
      log(`footer chip failed: ${short(e)}`)
    }

    // ---- 2. commands (registered inside a slot: keymap needs the Provider) --
    try {
      ctx.ui.slot({
        append: "app",
        render: () => {
          try {
            ctx.keymap?.layer?.(() => ({
              mode: "global",
              priority: 20,
              commands: [
                {
                  id: "harness.panel",
                  title: "Harness: open panel",
                  group: "Harness",
                  bind: "ctrl+g",
                  palette: true,
                  slash: { name: "harness", aliases: ["hp"] },
                  suggested: true,
                  run: openPanel,
                },
                {
                  id: "harness.refresh",
                  title: "Harness: refresh skills + memory",
                  group: "Harness",
                  palette: true,
                  slash: { name: "harness-refresh", aliases: ["hr"] },
                  run: async () => {
                    await refresh()
                    try {
                      ctx.ui.toast?.show?.({
                        title: "harness",
                        message: `${data.skills.length} skills · ${data.facts.length} facts`,
                        variant: "info",
                      })
                    } catch {
                      /* cosmetic */
                    }
                  },
                },
              ],
              bindings: ["harness.panel", "harness.refresh"],
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

    // ---- 3. side panel (skills + memory) --------------------------------
    try {
      ctx.ui.slot({
        append: "session.panel",
        render: (panel: Rec) => {
          if (panel?.name !== PANEL) return null
          try {
            // panel-scoped keys: active only while the panel owns input
            ctx.keymap?.layer?.(() => ({
              commands: [
                { id: "harness.panel.skills", title: "Skills", bind: "s", run: () => patch((d) => (d.tab = "skills")) },
                { id: "harness.panel.memory", title: "Memory", bind: "m", run: () => patch((d) => (d.tab = "memory")) },
                {
                  id: "harness.panel.fullscreen",
                  title: "Toggle fullscreen",
                  bind: "f",
                  run: () => {
                    try {
                      panel?.toggleFullscreen?.()
                    } catch (e) {
                      log(`toggleFullscreen failed: ${short(e)}`)
                    }
                  },
                },
                {
                  id: "harness.panel.close",
                  title: "Close panel",
                  bind: "escape",
                  run: () => {
                    try {
                      panel?.close?.()
                    } catch (e) {
                      log(`panel.close failed: ${short(e)}`)
                    }
                  },
                },
              ],
              bindings: ["harness.panel.skills", "harness.panel.memory", "harness.panel.fullscreen", "harness.panel.close"],
            }))
            const tab = String(view.tab ?? "skills")
            const rows =
              tab === "memory"
                ? data.facts.length
                  ? data.facts
                  : ["(no durable facts yet — the harness server plugin writes them"]
                    .concat([" as sessions run; memory_recall reads them back)"])
                : harnessSkills().length
                  ? harnessSkills()
                      .slice(0, SKILL_LIMIT)
                      .map((s) => `- ${oid(s)}: ${s.description || "(no description)"}`)
                  : view.ready
                    ? ["(no harness skills registered — run `harness plugin install`,"].concat([
                        " then restart opencode so server.ts can seed them)",
                      ])
                    : ["(loading…)"]
            const head = "harness   " + counts() + (view.note ? `  ⚠ ${cut(view.note, 60)}` : "")
            return (
              <box flexDirection="column">
                <text fg={th.accent}>{head}</text>
                <text fg={th.muted}>
                  {tab === "skills" ? "[skills]  memory" : " skills  [memory]"}
                </text>
                {rows.slice(0, SKILL_LIMIT + FACT_LIMIT).map((row) => (
                  <text fg={th.base}>{cut(row, 120)}</text>
                ))}
                <text fg={th.muted}>{"s skills · m memory · f fullscreen · esc close"}</text>
              </box>
            )
          } catch (e) {
            return <text fg={th.warn}>harness panel unavailable: {short(e)}</text>
          }
        },
      })
    } catch (e) {
      log(`session panel failed: ${short(e)}`)
    }

    // ---- 4. sidebar content (otherwise the sidebar has no plugin rows) ----
    try {
      ctx.ui.slot({
        append: "sidebar.content",
        render: () => (
          <box flexDirection="column">
            <text fg={th.accent}>harness</text>
            <text fg={th.muted}>{counts()}</text>
            {data.facts.slice(0, 2).map((f) => (
              <text fg={th.base}>{cut(f, 60)}</text>
            ))}
            <text fg={th.muted}>ctrl+g side panel</text>
          </box>
        ),
      })
      ctx.ui.slot({
        append: "sidebar.footer",
        render: () => <text fg={th.muted}>harness · /harness</text>,
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

export default HarnessTui
