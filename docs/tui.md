# TUI (stock opencode, no fork)
Harness has **no custom TUI binary**. It runs as a single opencode v2 server
plugin (`harness/plugin/harness.ts`) inside **stock opencode** — no patched
TUI, no AppImage, no fork. There is no relay: models come from your opencode
providers. That observability lives in `harness chat` outputs; project state
lives under `.opencode/harness/`.

- `harness tui` ensures opencode config + plugin, then execs `opencode`
  (auto-detected on `PATH`). `--setup-only` only ensures setup and prints
  paths (then run `opencode` yourself).
- `harness plugin path` prints the shipped `harness.ts`; `harness plugin install`
  copies it to `~/.config/opencode/plugins/` (auto-loaded by opencode v2).
- Agents `orchestrator/ask/debug/review` + native `plan` come from
  `opencode.json` (merged by setup) with no model pins — they inherit your
  configured default model.
