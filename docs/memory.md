# Memory

`MEMORY.md` (project root), below `AGENTS.md` in the precedence chain, plus SQLite facts
and FTS5 transcripts under `<project>/.opencode/harness/`. Per project; global
`~/.harness/` holds only global skills and user config.

## It fills itself

Every turn ends with `memory.capture_turn`, so nothing has to be remembered on
purpose:

- **Facts** — the turn's durable lines (decisions, root causes, migrations,
  blockers, "tests passed") are extracted and stored. Lines inside code fences,
  diff hunks and table rows are ignored; so are anything under 20 chars.
  Deduplicated on normalized text, capped by `memory.max_facts` (2000), and
  **credentials are refused** (`sk-…`, `ghp_…`, JWTs, `password: …`, PEM
  blocks) — a memory that leaks a key is worse than a forgetful one.
- **Progress** — a one-line headline per turn with source `progress`, or `done`
  when the turn *verified* itself: a test/lint/typecheck command ran and came
  back clean. `blocked` when a turn reports a blocker.
- **Ledger** — a verified turn also closes the active plan's next box
  (`orchestrator.auto_check`). The project's rule is that the verification
  section is the acceptance for the checkbox, so a turn that merely *says*
  "done" closes nothing. A red test run closes nothing either.

Recall is term-based, not phrase-based: the whole turn is tokenized into up to
five terms and matched with OR, scored by how many terms hit and then by
recency, plus FTS5 over transcripts. A query with no searchable term returns
nothing on purpose — answering an unrelated question with the newest few facts
reads as recall but is only noise the caller would cite.

## Lessons are evidence-gated, not approval-gated

`memory refine` stages one evidence excerpt for a lesson. When a lesson name has
**two distinct excerpts**, `auto_refine` promotes it into `MEMORY.md` on its own
and consumes the staged rows; one excerpt stays staged for review. That is
`/refine`'s "2+ evidence excerpts" rule with the approval step automated by the
evidence rather than by a human. Three identical excerpts still count as one.

`MEMORY.md` is kept inside `memory.cap_lines` (default 200) by the writer:
`memory approve`, `auto_refine` and lesson promotion all go through it, and the
oldest entries are the ones dropped. `memory.file` is `<project>/MEMORY.md` —
the file the precedence chain actually reads (approvals used to land in
`.opencode/harness/MEMORY.md`, which nothing loaded).

```bash
harness memory search "rlm mailbox"   # term recall over facts + transcripts
harness memory save --text "…"        # store one fact by hand
harness memory refine --session S --name L   # stage evidence; 2 promote it
harness memory show                   # the current MEMORY.md
harness memory prune --days 30        # drop old notes, keep decisions/progress
```

`memory prune` retires notes older than `memory.retention_days` while keeping
`lesson`, `decision`, `progress`, `blocked` and `done` — notes decay,
why-decisions and where-the-work-got-to do not. Set `memory.enabled: false` to
turn capture off entirely; recall still works.

Recall reaches the model two ways: the top-1 hint appended to the system prompt
(`recalled memory: … (verify before relying)`), and `memory_recall` as a native
opencode tool (reads the same `sessions.db` off disk) plus the plugin's
compaction brief, so a summary never swallows the durable facts.
