import type { TuiPlugin, TuiPluginApi } from "@opencode-ai/plugin/tui"
import type { BuiltinTuiPlugin } from "../builtins"
import { createEffect, createSignal, For, Show } from "solid-js"

const id = "harness:sidebar-panels"

const base = () => process.env.HARNESS_URL ?? "http://127.0.0.1:8787"
const token = () => process.env.HARNESS_TOKEN ?? ""

async function get(path: string): Promise<any> {
  const res = await fetch(base() + path, {
    headers: { Authorization: `Bearer ${token()}` },
  })
  if (!res.ok) throw new Error(`http ${res.status}`)
  return res.json()
}

function fmt(n: number): string {
  const s = Math.round(n ?? 0).toString()
  return s.replace(/\B(?=(\d{3})+(?!\d))/g, ",")
}

function shortModel(rid: string): string {
  const parts = String(rid ?? "").split("/")
  const last = parts[parts.length - 1] || String(rid ?? "")
  return last.length > 26 ? `${last.slice(0, 25)}…` : last
}

function Section(props: {
  title: string
  open: boolean
  onToggle: () => void
  theme: any
  children: any
}) {
  return (
    <box>
      <box flexDirection="row" gap={1} onMouseDown={props.onToggle}>
        <text selectable={false} fg={props.theme.text}>{props.open ? "▼" : "▶"}</text>
        <text selectable={false} fg={props.theme.text}>
          <b>{props.title}</b>
        </text>
      </box>
      <Show when={props.open}>{props.children}</Show>
    </box>
  )
}

type ProvGroup = { provider: string; total: number; open: boolean; rows: { name: string; steps: number; cost: string }[] }

function useHarness(sessionID: string) {
  const [started] = createSignal(new Date().toISOString())
  const [todo, setTodo] = createSignal<string[]>([])
  const [agents, setAgents] = createSignal<string[]>([])
  const [skills, setSkills] = createSignal<string[]>([])
  const [memory, setMemory] = createSignal<string[]>([])
  const [groups, setGroups] = createSignal<ProvGroup[]>([])
  const [ctx, setCtx] = createSignal<string[]>([])
  const [usage, setUsage] = createSignal<string[]>([])
  const [err, setErr] = createSignal("")
  const [tick, setTick] = createSignal(0)
  createEffect(() => {
    tick()
    setErr("")
    const run = async () => {
      try {
        const [t, a, u, r] = await Promise.all([
          get(`/api/tasks`),
          get("/api/agents"),
          get(`/api/usage`),
          get("/api/route"),
        ])
        setTodo([
          ...(t.todos ?? []).map((x: any) => `${x.status === "done" ? "●" : x.status === "doing" ? "◐" : "○"} ${x.text}`.slice(0, 64)),
          ...(t.inbox ?? []).map((x: any) => `◈ ${x.from}: ${String(x.content).slice(0, 60)}`),
        ].slice(0, 10))
        setAgents((a ?? []).slice(0, 10).map((w: any) =>
          `${w.status === "running" ? "●" : "○"} ${w.name} [${w.kind}]`.slice(0, 64)))
        setCtx([
          `${fmt(u.ctx_tokens ?? 0)} tokens`,
          `${u.ctx_pct ?? 0}% used`,
          `${u.spent ?? "$0.00"} spent`,
        ])
        setUsage([
          `Input ${fmt(u.input ?? 0)}`,
          `Output ${fmt(u.output ?? 0)}`,
          `Reasoning –`,
          `Cache read –`,
          `Cache write –`,
          `Cache rate –`,
          `Generation speed ${u.speed ?? 0} t/s`,
          `Cost ${u.spent ?? "$0.00"}`,
        ])
        const byProv = new Map<string, { steps: number; free: boolean; models: Map<string, number> }>()
        for (const x of (r ?? []).slice(-60)) {
          const prov = String(x.provider ?? "relay")
          const name = shortModel(x.model ?? "")
          let e = byProv.get(prov)
          if (!e) { e = { steps: 0, free: true, models: new Map() }; byProv.set(prov, e) }
          e.steps += 1
          if (!(x.model ?? "").endsWith(":free")) e.free = false
          e.models.set(name, (e.models.get(name) ?? 0) + 1)
        }
        setGroups([...byProv.entries()].slice(0, 6).map(([provider, e]) => ({
          provider,
          total: e.steps,
          open: true,
          rows: [...e.models.entries()].map(([name, steps]) => ({
            name, steps, cost: e.free ? "$0.00" : "n/a",
          })),
        })))
        const [s, m] = await Promise.all([
          get("/api/skills").catch(() => []),
          get("/api/memory?q=project").catch(() => []),
        ])
        setSkills((s ?? []).slice(0, 8).map((x: any) => `▪ ${x.name}`.slice(0, 64)))
        setMemory((m ?? []).slice(0, 6).map((x: any) => `✎ ${String(x.text).slice(0, 60)}`))
      } catch (e) {
        setErr("harness offline? start `harness serve`")
      }
    }
    void run()
  })
  return { started, todo, agents, skills, memory, groups, ctx, usage, err, refresh: () => setTick((x) => x + 1) }
}

function View(props: { api: TuiPluginApi; session_id: string }) {
  const theme = () => props.api.theme.current
  const h = useHarness(props.session_id)
  const [open, setOpen] = createSignal<Record<string, boolean>>({
    Context: true, Usage: true, Models: true, Todo: true,
    Agents: false, Memory: false, Skills: false,
  })
  const [provOpen, setProvOpen] = createSignal<Record<string, boolean>>({})
  const toggle = (k: string) => setOpen((o) => ({ ...o, [k]: !o[k] }))
  const toggleProv = (k: string) => setProvOpen((o) => ({ ...o, [k]: !(o[k] ?? true) }))
  const isProvOpen = (k: string) => provOpen()[k] ?? true
  const rows = (list: () => string[]) => (
    <For each={list()}>{(r) => <text fg={theme().textMuted}>{r}</text>}</For>
  )
  const ready = () => token().length > 0
  const nModels = () => h.groups().reduce((n, g) => n + g.rows.length, 0)

  return (
    <box>
      <box flexDirection="row" gap={1} onMouseDown={() => h.refresh()}>
        <text selectable={false} fg={theme().text}>
          <b>Session {props.session_id.slice(0, 8)} - {h.started()}</b>
        </text>
      </box>
      <Show when={ready()} fallback={
        <text fg={theme().textMuted}>set HARNESS_TOKEN (`cat ~/.harness/token`)</text>
      }>
        <Show when={h.err()}><text fg={theme().textMuted}>{h.err()}</text></Show>
        <Section title="Context" theme={theme()} open={!!open().Context} onToggle={() => toggle("Context")}>
          {rows(h.ctx)}
        </Section>
        <Section title="Token Usage" theme={theme()} open={!!open().Usage} onToggle={() => toggle("Usage")}>
          {rows(h.usage)}
        </Section>
        <Section title={`Models (${nModels()})`} theme={theme()} open={!!open().Models} onToggle={() => toggle("Models")}>
          <For each={h.groups()}>{(g) => (
            <box>
              <box flexDirection="row" gap={1} onMouseDown={() => toggleProv(g.provider)}>
                <text selectable={false} fg={theme().text}>{isProvOpen(g.provider) ? "▼" : "▶"}</text>
                <text selectable={false} fg={theme().text}>{g.provider}</text>
              </box>
              <Show when={isProvOpen(g.provider)}>
                <text selectable={false} fg={theme().textMuted}>Model Steps Cost</text>
                <For each={g.rows}>{(m) => (
                  <text fg={theme().textMuted}>{`${m.name} ${m.steps} ${m.cost}`.slice(0, 64)}</text>
                )}</For>
              </Show>
            </box>
          )}</For>
        </Section>
        <Section title="Todo" theme={theme()} open={!!open().Todo} onToggle={() => toggle("Todo")}>
          {rows(h.todo)}
        </Section>
        <Section title="Agents" theme={theme()} open={!!open().Agents} onToggle={() => toggle("Agents")}>
          {rows(h.agents)}
        </Section>
        <Section title="Memory" theme={theme()} open={!!open().Memory} onToggle={() => toggle("Memory")}>
          {rows(h.memory)}
        </Section>
        <Section title="Skills" theme={theme()} open={!!open().Skills} onToggle={() => toggle("Skills")}>
          {rows(h.skills)}
        </Section>
      </Show>
    </box>
  )
}

const tui: TuiPlugin = async (api) => {
  api.slots.register({
    order: 350,
    slots: {
      sidebar_content(_ctx, props) {
        return <View api={api} session_id={props.session_id} />
      },
    },
  })
}

const plugin: BuiltinTuiPlugin = {
  id,
  tui,
}

export default plugin
