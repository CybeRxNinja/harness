import { createEffect, createSignal, Show } from "solid-js"

// Footer fragment: shows the live resolved route for the current harness
// model, e.g. `→ openrouter/deepseek-v3.2`. Silent when the gateway is
// unreachable or has no route yet for this model id.
export function HarnessRoute(props: { model: string; fg: any }) {
  const [route, setRoute] = createSignal("")
  createEffect(() => {
    const m = props.model
    setRoute("")
    if (!m) return
    const base = process.env.HARNESS_URL ?? "http://127.0.0.1:8787"
    const token = process.env.HARNESS_TOKEN ?? ""
    if (!token) return
    fetch(`${base}/api/route`, { headers: { Authorization: `Bearer ${token}` } })
      .then((r) => (r.ok ? r.json() : []))
      .then((d: any[]) => {
        const hit = Array.isArray(d) ? [...d].reverse().find((r) => r.requested === m) : undefined
        if (hit) setRoute(`→ ${hit.provider}/${hit.model}`)
      })
      .catch(() => {})
  })
  return (
    <Show when={route()}>
      <text fg={props.fg}>{route()}</text>
    </Show>
  )
}
