# TUI (stock opencode, no fork)
Harness has **no custom TUI binary**. It runs as a single opencode v2 server
plugin (`harness/plugin/harness.ts`) inside **stock opencode** — no patched
TUI, no AppImage, no fork. There is no relay: models come from your opencode
providers. That observability lives in `harness chat` outputs; project state
lives under `.opencode/harness/`.

- `harness tui` ensures opencode config + plugin, then execs `opencode`
  (auto-detected on `PATH`). `--setup-only` only ensures setup and prints
  paths (then run `opencode` yourself).
- `harness plugin path` prints both shipped entrypoints (`harness.ts`, `tui.tsx`);
  `harness plugin install` copies them to `~/.config/opencode/plugins/harness/`
  (auto-loaded by opencode v2). A bare `harness.ts` is server-only and never
  appears in the TUI plugin list, which filters on `features.tui`.
- Agents `orchestrator/ask/debug/review` + native `plan` come from
  `opencode.json` (merged by setup) with no model pins — they inherit your
  configured default model. The orchestrator's `mode: subagent` specialists
  (explore, librarian, plan-consultant, plan-reviewer, code-reviewer,
  test-engineer, security-auditor) come from the same merge.

## Thinking / reasoning

The harness plugin does not touch message rendering — thinking blocks are
stock opencode behavior:

- `/thinking` toggles visibility of the reasoning blocks (collapsed by
  default; run it when you want to see the inner reasoning).
- `ctrl+t` cycles model **variants** — that is what actually turns reasoning
  on for models where it is off. Display only shows blocks the model emits;
  `/thinking` alone does not enable reasoning.
- `ctrl+p` opens the command palette with every view toggle (e.g. `/details`
  for tool execution detail); view settings persist across restarts.
- The sidebar's `reasoning N` line in the Window/Tokens detail shows how many
  reasoning tokens the last message used.
