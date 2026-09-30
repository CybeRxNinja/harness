---
name: debugging-and-error-recovery
description: Reproduce, localize, reduce, fix, guard. Use when tests fail.
---
# Debugging (vendored subset, MIT: addyosmani/agent-skills)
## Procedure
1. Reproduce. 2. Localize. 3. Reduce. 4. Fix. 5. Guard (regression test).
6. Repair receipt: a non-zero exit is never success — read the failure's receipt/diagnostics, fix the connected neighbourhood, re-run the COMPLETE command (not a narrowed slice), and never claim a verification you did not perform.
## Verification
Repro command + before/after output + guard test.
