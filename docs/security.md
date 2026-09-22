# Security
Lawful-use only. Paths jailed to project root; shell allowlist + 10s timeout;
200k file cap; 8k tool-output truncation with spill to `runs/`. Secrets are
env-only and redacted in logs/trajectories, and **never** written to memory
(`memory.is_secretish` refuses credentials before they reach the facts table).
`/sec` skills need a scope file (`auth` + network profile, no target ACT until
ready), audit-logged. Project skills are scanned (prompt-injection/exfil →
quarantine) and need explicit trust. `--auto` only in trusted checkouts;
Ask/Plan modes are read-only.

## Approval means "this one is dangerous"

Permission prompts are risk-gated, not blanket (`harness/risk.py`). Reads,
greps, diffs, status checks, fetches, searches and test/lint runs never ask.
The destructive and irreversible set does:

- data loss — `rm`/`rmdir`/`shred`/`truncate`/`mkfs`/`dd of=`
- history and remotes — `git push` (force push reported as such),
  `reset --hard`, `clean`, `checkout --`, `branch -D`, `filter-branch`
- host and privilege — `sudo`, `su`, `chown`, `kill`/`pkill`, `reboot`
- outward-facing — `npm publish`, `twine upload`, `docker push`,
  `gh release`, deploys, `terraform apply|destroy`, `kubectl`, cloud CLIs
- unrecoverable data — `DROP TABLE|DATABASE`, `TRUNCATE`, `DELETE FROM`
  with no `WHERE`
- harness's own — `checkpoint restore`, `plugin uninstall`, `config set`,
  `memory forget`

Plus two things a command string cannot express: a write to a **sensitive path**
(`.env*`, `.ssh/`, `.aws/`, `id_rsa`, `.netrc`, `.git/config`, CI workflows) and
a write **outside the project root**, which checkpoints cannot undo
(`external_directory`). Every verdict carries the reason a human needs
("publishes commits to a remote; other people may already have them"), so the
owner decides on facts instead of a generic yes/no.

MCP stays ask-by-default with `audit`.
