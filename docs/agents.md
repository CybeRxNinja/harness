# Agents
Main orchestrates; `spawn(category XOR subagent_type)` — exactly one per call.
Categories are intent labels, never model names: `quick/deep/ultrabrain/visual/
writing/unspecified-low/unspecified-high` (the `categories` config list; a typo is
a refused spawn, not a worker with an unknown kind). Reasoning level and turn
budget come from `budgets`, not from a per-category payload — a knob nothing
reads is not a feature.
Curated read-only: `explore/librarian`; plan-gated: `plan-consultant/
plan-reviewer`; reviewers: `code-reviewer/test-engineer/security-auditor`.
All workers inherit your opencode default model (`harness doctor` shows it).

TUI agents mirror this: `orchestrator` (delegates, never writes directly — it
routes through the `task` tool to the subagents `explore`, `librarian`,
`plan-consultant`, `plan-reviewer`, `code-reviewer`, `test-engineer`,
`security-auditor`, falling back to opencode's built-in `general` only when none
fits), `ask`/`debug`/`review` primaries, native `plan` — none pin a model, so
they all run on your configured opencode default unless you pin per-agent
`model` yourself. Every subagent carries its own prompt, none can spawn further
subagents (depth 1), and all but `test-engineer` (which writes tests only) are
edit-denied.

Flow: `plan start --title T --items "a;b"` → `plan next` → parallel `quick`
bursts (the orchestrator picks the category itself; `rlm.spawn` validates it
against the `categories` config list) → independent review before `plan check`.
`boulder.json` + `ledger.jsonl` resume across sessions, and a turn whose
test/lint run came back clean ticks the next box on its own.
Concurrency `budgets.max_parallel` (2), depth 1, worker timeout 600s. File
ownership lease: only the orchestrator merges; workers return SUMMARY+DIFF
(≤4k).

## The orchestrator asks less

Reads, greps, status checks, test runs and fetches need no permission — the
orchestrator just does them. Before anything destructive or irreversible it
calls `risk_check` once and asks a single question naming what cannot be
undone, then never re-asks for that action in the session. A wave is assessed
as a batch (`risk_check` with `actions`), so a plan needs one question about
its irreversible subset rather than one per step. See `docs/security.md` and
`docs/config.md` (shell permissions are generated from `harness/risk.py`).

Progress records itself: each turn writes a progress fact, and a turn whose
test/lint run came back clean closes the active box in the plan ledger. See
`docs/memory.md`.

## Fast by construction

What a task costs is decided in the prompts: a lean session once burned its
budget re-reading product files it already held summaries for, booted two
headless browsers in parallel, and scattered scratch into `/tmp`. So the
orchestrator learns what a worker did from its SUMMARY (recon goes to
`explore`) and never opens product files or images itself — inspection is
grep first, then narrow <=200-line windows, and findings arrive as text with
file:line. At most one worker runs a browser or server and it is the
`test-engineer`; `code-reviewer` is static-only. Every spawn prompt requires a
SUMMARY listing each file created/changed with line counts. Scratch lives in
the project's `.opencode/harness/tmp/` — never `/tmp` or a system directory —
and no worker installs packages or tools (report the missing tool instead).
Independent workers run in parallel, two per wave. Installs and scratch that
land outside the project prompt for approval (`docs/security.md`).
