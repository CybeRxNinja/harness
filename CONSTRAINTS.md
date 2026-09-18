# CONSTRAINTS.md — standing quality bar (Addy constraint-driven, enforced everywhere)
- Tests are proof: no DONE without failing-then-passing test or explicit waiver with reason.
- Change sizing ~100 lines per commit; atomic commits; `harness checkpoint save` before risky merges.
- No secrets in code/logs/trajectories. Env-only keys.
- Measure before optimize (perf), OWASP Top-10 for inputs/auth, keyboard+a11y for UI.
- Agents never silence checks to get green; failing gate = stop, report, fix.
