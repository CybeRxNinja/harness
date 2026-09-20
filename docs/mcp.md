# MCP: two surfaces, one system

## 1. Native opencode tools (primary — no extra process)
The stock-opencode plugin (`harness/plugin/harness.ts`) exposes
`skills_list` / `skill_view` / `memory_recall` as **native tools** — no MCP
hop, no stdio framing to break, works even with the gateway down (reads
`~/.harness/skills/` + local SQLite directly). This is what opencode agents
use. No `mcp.*` config block is needed or added.

## 2. Stdio server (for NON-opencode MCP clients)
`harness mcp` speaks JSON-RPC 2.0 over stdio (newline-delimited; `readline`,
never `read(n)` — the latter deadlocks live clients holding the pipe open)
with the same three tools. Use it from any MCP-compatible host:

```bash
python3 -m harness mcp   # add as a stdio server in your MCP client
```

Verified against the official `@modelcontextprotocol/sdk` client
(connect → list → call). Bearer token: not needed — local files only.
