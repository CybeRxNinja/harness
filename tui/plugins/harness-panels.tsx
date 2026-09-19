import type { TuiPlugin, TuiPluginApi } from "@opencode-ai/plugin/tui"
import type { BuiltinTuiPlugin } from "../builtins"
import { createEffect, createSignal, For, Show } from "solid-js"

const id = "harness:sidebar-panels"

const TABS = ["Todo", "Agents", "Skills", "Memory", "Models", "Stats"] as const
type Tab = (typeof TABS)[number]

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
  if (n >= 1000000) return `${(n / 1000000).toFixed(1)}M`
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`
  return `${n}`
}

function useTabData(tab: () => Tab, sessionID: string) {
  const [rows, setRows] = createSignal<string[]>([])
  const [err, setErr] = createSignal("")
  createEffect(() => {
    const t = tab()
    setErr("")
    setRows(["…"])
    const run = async () => {
      try {
        if (t === "Todo") {
          const d = await get(`/api/tasks?session=${encodeURIComponent(sessionID)}`)
          const todos = (d.todos ?? []).map((x: any) => `${x.status === "done" ? "●" : x.status === "doing" ? "◐" : "○"} ${x.text}`.slice(0, 60))
          const inbox = (d.inbox ?? []).map((x: any) => `◈ ${x.from}: ${String(x.content).slice(0, 60)}`)
          setRows([...todos, ...inbox].slice(0, 12))
        } else if (t === "Agents") {
          const d: any[] = await get("/api/agents")
          setRows(d.slice(0, 12).map((w) => `${w.status === "running" ? "●" : "○"} ${w.name} [${w.kind}]`.slice(0, 60)))
        } else if (t === "Skills") {
          const d: any[] = await get("/api/skills")
          setRows(d.slice(0, 12).map((s) => `▪ ${s.name}`.slice(0, 60)))
        } else if (t === "Memory") {
          const d: any[] = await get("/api/memory?q=project")
          setRows(d.slice(0, 8).map((m) => `✎ ${String(m.text).slice(0, 56)}`))
        } else if (t === "Stats") {
          const u: any = await get(`/api/usage?session=${encodeURIComponent(sessionID)}`)
          const top = (u.by_model ?? []).slice(0, 4)
            .map((m: any) => `▪ ${m.model}: ${fmt(m.input)}+${fmt(m.output)}`.slice(0, 60))
          setRows([
            `in ${fmt(u.input ?? 0)} · out ${fmt(u.output ?? 0)} · ${u.calls ?? 0} calls`,
            `context ${u.ctx_pct ?? 0}%${u.last_model ? ` · ${u.last_model}` : ""}`.slice(0, 60),
            ...top,
          ])
        } else {
          const d: any[] = await get("/api/route")
          const seen = new Map<string, any>()
          for (const r of d) seen.set(r.requested, r)
          const live = [...seen.values()].reverse().slice(0, 6)
            .map((r) => `▸ ${r.requested} → ${r.provider}/${r.model} (${r.ms}ms)`.slice(0, 60))
          const m = await get("/v1/models")
          const ids = ((m.data ?? []) as any[]).slice(0, 6).map((x) => `· ${x.id}`.slice(0, 60))
          setRows([...live, ...ids])
        }
        setRows((r) => (r.length ? r : ["(empty)"]))
      } catch (e) {
        setErr("harness offline? start `harness serve`")
        setRows([])
      }
    }
    void run()
  })
  return { rows, err }
}

function View(props: { api: TuiPluginApi; session_id: string }) {
  const [open, setOpen] = createSignal(true)
  const [tab, setTab] = createSignal<Tab>("Todo")
  const theme = () => props.api.theme.current
  const { rows, err } = useTabData(tab, props.session_id)
  const ready = () => token().length > 0

  return (
    <box>
      <box flexDirection="row" gap={1} onMouseDown={() => setOpen((x) => !x)}>
        <text selectable={false} fg={theme().text}>{open() ? "▼" : "▶"}</text>
        <text selectable={false} fg={theme().text}>
          <b>Harness</b>
        </text>
      </box>
      <Show when={open()}>
        <Show
          when={ready()}
          fallback={
            <text fg={theme().textMuted}>set HARNESS_TOKEN (`cat ~/.harness/token`)</text>
          }
        >
          <box flexDirection="row" gap={1}>
            <For each={TABS}>
              {(t) => (
                <text selectable={false} fg={t === tab() ? theme().text : theme().textMuted} onMouseDown={() => setTab(t)}>
                  {t === tab() ? `[${t}]` : t}
                </text>
              )}
            </For>
          </box>
          <Show when={err()}>
            <text fg={theme().textMuted}>{err()}</text>
          </Show>
          <For each={rows()}>{(r) => <text fg={theme().textMuted}>{r}</text>}</For>
        </Show>
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
