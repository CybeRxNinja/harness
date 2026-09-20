# TUI (stock opencod, no fork)
Harness has **no custom TUI binary**. It runs as a single opencod v2 server
plugin (`harness/plugin/harness.ts`) inside **stock opencod** — no patched
TUI, no AppImage, no fork. The fork-based sidebar (Todo / Models / Memory /
Skills panels written against the patched opencod builtins) is retired; that
observability now lives in `harness chat` outputs and the relay endpoints
(`/api/tasks`, `/api/usage`, `/api/route`, `/api/skills`, `/api/memory`).

- `harness tui` ensures gateway + opencod config + plugin, then execs `opencode`
  (auto-detected on `PATH`). `--setup-only` only ensures setup and prints
  `HARNESS_TOKEN`/`HARNESS_URL` exports (for sourcing before a manual `opencode`).
- `harness plugin path` prints the shipped `harness.ts`; `harness plugin install`
  copies it to `~/.config/opencode/plugins/` (auto-loaded by opencod v2).
- Relay provider: `provider.harness` (`harness/auto-fastest` + `tag:*`),
  openai-compat at `http://127.0.0.1:8787/v1`, key `{env:HARNESS_TOKEN}`.
- Agents `orchestrator/ask/debug/review` + native `plan` (pinned to
  `harness/tag:reasoning`) come from `opencod.json` (merged by setup).
