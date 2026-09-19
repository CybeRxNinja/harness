# MCP
Harness-owned (shared by CLI/TUI/HTTP). v0: stdio servers only, lazy spawn,
15s timeout. Config: `mcp.servers.{name:{cmd,enabled,tools:{tool:
allow|ask|deny}}}`. `audit` reports missing commands (no auto-install).
Skill-embedded servers spawn scoped to one `spawn()` call, then torn down.
TUI MCP tab toggles + audit. Servers that crash report `degraded` and fall
back to skills declaring `fallback_for_toolsets`.
