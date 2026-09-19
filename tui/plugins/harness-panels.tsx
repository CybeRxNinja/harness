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

function Section(props: {
  title: string
  extra?: string
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
        <Show when={props.extra}>
          <text fg={props.theme.textMuted}>{props.extra}</text>
        </Show>
      </box>
      <Show when={props.open}>{props.children}</Show>
    </box>
  )
}

function useHarness(sessionID: string) {
  const [todo, setTodo] = createSignal<string[]>([])
  const [agents, setAgents] = createSignal<string[]>([])
  const [skills, setSkills] = createSignal<string[]>([])
  const [memory, setMemory] = createSignal<string[]>([])
  const [models, setModels] = createSignal<string[]>([])
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
        const byProv = new Map<string, { steps: number; free: boolean }>()
        for (const x of (r ?? []).slice(-30)) {
          const k = `${x.provider} / ${(x.model ?? "").split("/").pop()}`
          const e = byProv.get(k) ?? { steps: 0, free: true }
          e.steps += 1
          if (!(x.model ?? "").endsWith(":free")) e.free = false
          byProv.set(k, e)
        }
        setModels([...byProv.entries()].slice(0, 8).map(([k, v]) =>
          `${k} · ${v.steps} step${v.steps === 1 ? "" : "s"} · ${v.free ? "$0.00" : "n/a"}`.slice(0, 64)))
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
  return { todo, agents, skills, memory, models, ctx, usage: usage, err, refresh: () => setTick((x) => x + 1) }
}

function View(props: { api: TuiPluginApi; session_id: string }) {
  const theme = () => props.api.theme.current
  const h = useHarness(props.session_id)
  const [open, setOpen] = createSignal<Record<string, boolean>>({
    Context: true, Usage: true, Models: true, Todo: true,
    Agents: false, Memory: false, Skills: false,
  })
  const toggle = (k: string) => setOpen((o) => ({ ...o, [k]: !o[k] }))
  const rows = (list: () => string[]) => (
    <For each={list()}>{(r) => <text fg={theme().textMuted}>{r}</text>}</For>
  )
  const ready = () => token().length > 0

  return (
    <box>
      <box flexDirection="row" gap={1} onMouseDown={() => h.refresh()}>
        <text selectable={false} fg={theme().text}>
          <b>Harness</b>
        </text>
        <text selectable={false} fg={theme().textMuted}>· tap to refresh</text>
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
        <Section title={`Models (${h.models().length})`} theme={theme()} open={!!open().Models} onToggle={() => toggle("Models")}>
          {rows(h.models)}
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
