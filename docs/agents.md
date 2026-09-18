# Agents
Main orchestrates; `spawn(category XOR subagent_type)`. Categories map intent->model chain with fallback (see config). Curated read-only: explore/librarian; plan-gated: plan-consultant/plan-reviewer; reviewers: code-reviewer/test-engineer/security-auditor.
Flow: `ulw` auto or `plan start --title T --items "a;b"`, `plan next`, `spawn` waves by `orchestrator.classify`, `plan check` after independent review. `boulder.json`+`ledger.jsonl` resume. Concurrency 2, depth 1, stale 15m/TTL 30m.
