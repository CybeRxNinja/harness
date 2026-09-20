# Harness opencode plugin (v2 API, stock opencode, no fork)

Single file `harness.ts`, default-exported `{id, effect}` (only `effect` is
invoked by the v2.0.8 loader). Installed by `harness plugin install` into
`~/.config/opencode/plugins/` (auto-loaded). Zero runtime imports.

Does: seed-skill registration via `skill.transform`, native
`skills_list`/`skill_view`/`memory_recall` tools (local disk + SQLite, no
gateway), oversized-tool-output condensing (`tool.execute.after`), memory
brief injection on `experimental.session.compacting` (local recall).
Provider/agents still come from `opencode.json` merge
(`ensure_opencode_config`) — the plugin does not duplicate them. Agents carry
no model pins: they inherit your opencode default model.
