# MCP: two surfaces, one system

## 1. Native opencode tools (primary — no extra process)
The stock-opencode plugin (`harness/plugin/harness.ts`) exposes
`skills_list` / `skill_view` / `memory_recall` / `todowrite` / `todoread` /
`wait` as **native tools** — no MCP
hop, no stdio framing to break (reads
`~/.harness/skills/` + local SQLite directly). This is what opencode agents
use. No `mcp.*` config block is needed or added. `wait` takes
`{label, timeout_s 1..600, hint?}` and returns an expiry nudge telling you to
check the task's status and act on it.

## 2. Stdio server (retired)
`harness mcp` used to speak JSON-RPC 2.0 over stdio with the same three
skill/memory tools for non-opencode MCP clients (it was verified against the
official `@modelcontextprotocol/sdk` client). It was retired with the headless
path: live sessions use the native tools above, which need no extra process.
