---
name: self-maintenance
description: Maintain and improve the harness itself. Use when asked, or automatically only when it is the natural way to genuinely improve UX or a clearly more efficient way to do things.
---
# Self-Maintenance
## When to Use
Owner asks, OR automatic only if BOTH (a) natural next step in current task AND (b) genuinely improves UX or clearly more efficient. Else skip in one line.
Non-Goals: no speculative refactors, no new deps for few-line fixes, ZERO personal data (no names, emails, tokens, home paths) in skills/logs/memory.
## Procedure
1. Recall memory + L0 `skills_list`; load at most ONE skill; `todowrite` if multi-step.
2. Reproduce/localize with grep first + <=200-line reads; root cause, not symptom.
3. Ladder YAGNI -> reuse -> stdlib -> native -> installed dep -> one-liner -> minimum; one file owner.
4. `harness checkpoint save` before risky merge; ~100-line atomic diffs.
5. Verify with ONE canonical test command max once per wave (or parse/syntax + one structural check if no runner); never silence a failing gate.
6. Capture durable decision without PII; tick todos only on evidence.
## Pitfalls
- Speculative refactor or new dep for a few-line fix; installing packages (report missing tool instead).
- Personal data in diff/logs/memory; scratch outside `.opencode/harness/tmp/`.
- Risk-gated asks only for destructive/irreversible actions; max 8 spawns / 4 waves.
## Verification
Triggered only when asked or natural+genuine; failing->passing evidence or waiver+reason; no personal data in diff.
