---
name: incremental-implementation
description: Thin vertical slices, test, verify, commit. Use for multi-file changes.
---
# Incremental Implementation (vendored subset, MIT: addyosmani/agent-skills)
## When to Use
Any change touching >1 file.
## Procedure
1. One slice. 2. Test. 3. Verify. 4. Commit (~100 lines).
## Verification
Each slice has passing test + atomic commit.
