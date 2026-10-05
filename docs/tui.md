# TUI (stock opencode, no fork)
Harness has **no custom TUI binary**. It runs as a single opencode v2 server
plugin (`harness/plugin/harness.ts`) inside **stock opencode** — no patched
TUI, no AppImage, no fork. There is no relay: models come from your opencode
providers. That observability lives in opencode session transcripts; project state
lives under `.opencode/harness/`.

- `harness tui` ensures native `harness-*` agents + plugin, then execs `opencode`
  (auto-detected on `PATH`). `--setup-only` only ensures setup and prints
  paths (then run `opencode` yourself).
- `harness plugin path` prints both shipped entrypoints (`harness.ts`, `tui.tsx`);
  `harness plugin install` copies them to `~/.config/opencode/plugins/harness/`
  (auto-loaded by opencode v2). A bare `harness.ts` is server-only and never
  appears in the TUI plugin list, which filters on `features.tui`.
- Agents `harness-orchestrator/harness-ask/harness-debug/harness-review` +
  native `harness-plan` come from native Markdown files under
  `~/.config/opencode/agents/`, with no model pins — they inherit your
  configured default model. The orchestrator's `mode: subagent` specialists
  (`harness-explore`, `harness-librarian`, `harness-plan-consultant`,
  `harness-plan-reviewer`, `harness-code-reviewer`, `harness-test-engineer`,
  `harness-security-auditor`) come from the same managed files.

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

## Waits

The sidebar's `Waits` row sits after Workers and tracks pending `wait` tool
calls (project-wide, like Workers — a countdown belongs to the project, not
to the session that started it):

- Appears and auto-expands while any wait pends; hides entirely when none
  (a wait that appears or changes opens the row; when the last one clears,
  the row leaves the expanded set too).
- Value is occupancy (`1 waiting` / `N waiting`); each line is
  `label · 2m14s left` — the wait's label plus remaining countdown
  (`Ns` under a minute, `MmSSs` under an hour, `HhMMm` beyond; past-due
  reads `due`).
- Source: the `waits` table (`label` + `deadline`) in the project's
  `sessions.db`, soonest deadline first.
