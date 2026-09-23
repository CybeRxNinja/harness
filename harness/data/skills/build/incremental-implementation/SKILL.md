---
name: incremental-implementation
description: Thin vertical slices, test, verify, commit. Use for multi-file changes.
---
# Incremental Implementation (vendored subset, MIT: addyosmani/agent-skills)
## When to Use
Any change touching >1 file.
## Procedure
1. One slice. 2. Test. 3. Verify. 4. Commit (~100 lines).
5. `todowrite` that slice `completed` and the next one `in_progress` — on the evidence from step 3, never on intent.
## Verification
Each slice has passing test + atomic commit, and `todoread` shows one item `in_progress` at most.
