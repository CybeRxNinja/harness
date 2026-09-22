# Config (self-managed)
Layers: defaults < `~/.harness/harness.jsonc` < `.opencode/harness.jsonc`
< env (`HARNESS_MODEL`, a full `provider/model` id). JSONC. Agent-mutable:
model_profile|budgets|categories|skills|memory|mcp|compress.
Denied: secrets|token|trusted_project_dirs|mcp_env_allowlist|security (+ any
api_key path — models live in opencode, never here; there is no `router`).

Every key in the defaults is read by code; keys nothing read are deleted rather than left as a knob
that quietly does nothing. The full surface:

| key | effect |
|---|---|
| `model_profile` | only a `provider/model` id does anything (the `HARNESS_MODEL` path); every agent otherwise inherits your opencode default |
| `budgets.max_turns` / `max_tokens` / `max_cost_usd` | one turn's ceilings |
| `budgets.max_parallel` | worker pool width (default 2) |
| `budgets.max_depth` | `0` refuses spawn entirely |
| `budgets.worker_timeout_s` | a worker's backend call bound (default 600) |
| `categories` | the valid `spawn` intents; rlm.spawn refuses anything else |
| `compress.enabled` / `threshold` / `intensity` | oversized tool output handling |
| `skills.write_approval` / `disabled` / `external_dirs` / `create_dir` | skill store behavior |
| `memory.enabled` / `cap_lines` / `max_facts` / `retention_days` | what is remembered and for how long |
| `mcp.servers` | stdio MCP servers and their per-tool allow/ask/deny |
| `security.shell_allowlist` | `shell` tool command allowlist |

`categories` is a **list** (the legacy `{"quick": {"reasoning": …}}` dict still loads, its keys are
used as the intent list).

`config get/set/show --scope user|project`: validate → diff preview → backup
(`backups/`) → atomic write → re-validate; corrupt files boot from backup.

LSP is enabled unless already decided (`lsp: true` merged only when the key
is absent; explicit `false`/object always respected).

## Shell permissions are generated, not shipped

`ensure_opencode_config` writes `permission.bash` for every harness agent from
`harness/risk.py`, so the policy the model is told about and the rule opencode
enforces are the same object:

```json
"permission": {
  "read": "allow", "glob": "allow", "grep": "allow",
  "webfetch": "allow", "websearch": "allow", "task": "allow",
  "external_directory": "ask",
  "bash": { "*": "allow", "rm *": "ask", "git push *": "ask", ... }
}
```

A blanket `"ask"` prompted about a grep exactly as loudly as about
`git push --force`, which trains the owner to approve reflexively and makes the
prompt meaningless. The generated map is permissive by default and asks **only**
for the destructive/irreversible set (deletes, `git push`/`reset --hard`,
`sudo`, publishes, deploys, `terraform apply`, `DROP TABLE`, remote-script
pipes, `checkpoint restore`, config writes). opencode evaluates patterns
last-match-wins, so the catch-all is written first and the asks after it, in
both a bare and an argument form (`rm` and `rm *`).

`deny` floors survive auto-approve by design — `edit: deny` on the read-only
agents stays denied instead of becoming a question a "yes" could unlock. A
permission object you wrote yourself is never replaced; only a bare string (a
former harness default) is regenerated, and the migration says so on stderr.

Ask the classifier directly instead of guessing:

```bash
harness chat '[[tool:risk_check {"action": "git push --force"}]]'
```

It returns `{risk, irreversible, ask, reason, rule}` for one action, or a
plan-wide verdict (`assess_many`) naming the irreversible subset. `risk_check`
is allowed in **every** mode, including `plan`/`ask`/`review`: classifying an
action is read-only, and without it the only thing a read-only agent can do
about an unfamiliar command is ask the human.
