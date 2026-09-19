# Security
Lawful-use only. Paths jailed to project root; shell allowlist + 10s timeout;
200k file cap; 8k tool-output truncation with spill to `runs/`. Secrets are
env-only and redacted in logs/trajectories. `/sec` skills need a scope file
(`auth` + network profile, no target ACT until ready), audit-logged, denied
over remote gateway by default. MCP is ask-by-default with `audit`. Project
skills are scanned (prompt-injection/exfil → quarantine) and need explicit
trust. `--auto` only in trusted checkouts; Ask/Plan modes are read-only.
Gateway is localhost-only with bearer token; never expose without review.
