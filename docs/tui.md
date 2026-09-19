# TUI (AppImage + forked opencode, CI-built)
`Harness_TUI-x86_64.AppImage` (releases): one file, first launch self-setups
(config merge, CLI install, gateway, shortcut). Or `harness tui` with a local
binary. Textual fallback dropped; `harness chat` is the guaranteed CLI runner.

Sidebar (Harness panel): Todo (todos + subagent inbox) | Agents (live workers,
kill/peek/follow-up) | Memory (facts + pending) | Skills (L0 + bundles) |
Models (live `requested → provider/model` routes + catalog) | Stats
(input/output/calls/context %/per-model). Footer shows the resolved route
(`→ provider/model`) next to the configured id. Tab labels are
non-selectable; data rows stay copyable.

Provider shim: `provider.harness` (openai-compat → local relay, key
`{env:HARNESS_TOKEN}`, models auto-fastest + tag:* + Free Best). Agents
`orchestrator/ask/debug/review` + native `plan` pinned to reasoning.
The TUI drives the relay with opencode-native sessions; the harness brain
(spawn/kernel/skills) runs in `serve`, observed from the panels.
