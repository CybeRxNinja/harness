# Harness opencode plugin (v2 API, stock opencode, no fork)

Single file `harness.ts`, named export `HarnessPlugin`. Installed by
`harness plugin install` into `~/.config/opencode/plugins/` (auto-loaded).

Does: gateway auto-start, seed-skill registration via `skill.transform`,
memory brief injection on `experimental.session.compacting`.
Provider/agents/MCP still come from `opencode.json` merge
(`ensure_opencode_config`) — the plugin does not duplicate them.
