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

TUI agents mirror this: `orchestrator` (delegates, never writes directly),
`ask`/`debug`/`review` primaries, native `plan` — none pin a model, so they
all run on your configured opencode default unless you pin per-agent `model`
yourself.

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
