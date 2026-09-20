# Agents
Main orchestrates; `spawn(category XOR subagent_type)` — exactly one per call.
Categories are intent, never model names: `quick/deep/ultrabrain/visual/writing/
unspecified-low/unspecified-high`, each with reasoning level + turn budget.
Curated read-only: `explore/librarian`; plan-gated: `plan-consultant/
plan-reviewer`; reviewers: `code-reviewer/test-engineer/security-auditor`.
All workers inherit your opencode default model (`harness doctor` shows it).

TUI agents mirror this: `orchestrator` (delegates, never writes directly),
`ask`/`debug`/`review` primaries, native `plan` — none pin a model, so they
all run on your configured opencode default unless you pin per-agent `model`
yourself.

Flow: `ulw` auto, or `plan start --title T --items "a;b"` → `plan next` →
parallel `quick` bursts by `orchestrator.classify` → independent review before
`plan check`. `boulder.json` + `ledger.jsonl` resume across sessions.
Concurrency 2, depth 1, stale 15m/TTL 30m. File-ownership lease: only the
orchestrator merges; workers return SUMMARY+DIFF (≤4k).
