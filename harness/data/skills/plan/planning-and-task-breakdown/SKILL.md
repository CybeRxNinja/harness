---
name: planning-and-task-breakdown
description: Decompose specs into small verifiable tasks. Use with a spec.
---
# Planning and Task Breakdown (vendored subset, MIT: addyosmani/agent-skills)
## When to Use
Spec exists, need implementable units.
## Procedure
1. Atomic tasks with acceptance + deps. 2. Smallest first.
3. Write the same list with `todowrite` — the plan is the owner's sidebar row and it rides into the compaction brief, so a plan that lives only in prose is a plan that disappears.
4. Mark the first task `in_progress` when you start it, not when you finish it.
## Verification
`.opencode/harness/plans/<slug>.md` has `- [ ] N. title + acceptance + Recommended category:` rows, and `todoread` lists the same open items.
