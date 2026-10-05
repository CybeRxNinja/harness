# Config (self-managed)
Layers: defaults < `~/.harness/harness.jsonc` < `.opencode/harness.jsonc`
< env (`HARNESS_MODEL`, a full `provider/model` id). JSONC. Agent-mutable:
model_profile|budgets|categories|skills|memory|mcp|compress.
Denied: secrets|token|trusted_project_dirs|mcp_env_allowlist|security (+ any
api_key path — models live in opencode, never here; there is no `router`).

Every key in the defaults is type-validated on load. Some are read by nothing;
those are marked **accepted, not enforced**. The full surface:

| key | effect |
|---|---|
| `model_profile` | only a `provider/model` id does anything (the `HARNESS_MODEL` path); every agent otherwise inherits your opencode default |
| `budgets.max_turns` / `max_tokens` / `max_cost_usd` | **accepted, not enforced** (type-checked only; enforcement would live in `harness/rlm.py`, a read-only stub) |
| `budgets.max_parallel` | **accepted, not enforced** — same stub |
| `budgets.max_depth` | **accepted, not enforced** — same stub |
| `budgets.worker_timeout_s` | a worker's backend call bound (default 600) |
| `categories` | the valid `spawn` intents; rlm.spawn refuses anything else |
| `compress.enabled` / `threshold` / `intensity` | **accepted, not enforced** — condensing is a fixed 4000-char threshold (`harness/plugin/harness.ts:37`) |
| `skills.write_approval` / `disabled` / `external_dirs` / `create_dir` | skill store behavior |
| `memory.enabled` / `cap_lines` / `max_facts` / `retention_days` | what is remembered and for how long |
| `mcp.servers` | **accepted, not enforced** — MCP servers come from opencode's own config, so no `mcp.*` block is needed (see `docs/mcp.md`) |
| `security.shell_allowlist` | **accepted, not enforced** — the generated permission map comes from `risk.bash_permission_map()` (`harness/risk.py:346`), which never sees the allowlist |

`categories` is a **list** (the legacy `{"quick": {"reasoning": …}}` dict still loads, its keys are
used as the intent list).

`config get/set/show --scope user|project`: validate → diff preview → backup
(`.opencode/harness/backups/`) → atomic write → re-validate. The backup is a
pre-write copy for humans; a corrupt file raises, it does not boot from it.

`lsp: true` in opencode's own config turns on opencode's **built-in language
servers** (typescript, pyright, gopls, clangd, …) which spawn per project as
their files open — harness never installs a server itself.
`harness tui` additionally exports
`OPENCODE_EXPERIMENTAL_LSP_TOOL=true` before exec'ing opencode so agents keep
the `lsp` tool (definitions / references / symbols) on builds that gate it
behind that flag; on 2.0.x the tool ships ungated and the variable is inert.
Workers are told to prefer it over grep-spaghetti when writing code in an
unfamiliar codebase, and `harness doctor` reports the effective state as
`lsp: enabled / overridden / disabled / unset`.

## Shell permissions are generated, not shipped

`harness.plugin install` renders `permission.bash`/shell permissions for every native `harness-*` agent from `harness/risk.py`, so the policy the model is told about and the rule opencode enforces are derived from the same generator:

```yaml
permissions:
  - {action: "read", resource: "*", effect: "allow"}
  - {action: "glob", resource: "*", effect: "allow"}
  - {action: "grep", resource: "*", effect: "allow"}
  - {action: "webfetch", resource: "*", effect: "allow"}
  - {action: "websearch", resource: "*", effect: "allow"}
  - {action: "subagent", resource: "*", effect: "allow"}
  - {action: "external_directory", resource: "*", effect: "ask"}
  - {action: "shell", resource: "*", effect: "allow"}
  - {action: "shell", resource: "rm *", effect: "ask"}
  - {action: "shell", resource: "git push *", effect: "ask"}
```

A blanket `"ask"` prompted about a grep exactly as loudly as about
`git push --force`, which trains the owner to approve reflexively and makes the
prompt meaningless. The generated map is permissive by default and asks **only**
for the destructive/irreversible set (deletes, `git push`/`reset --hard`,
`sudo`, publishes, deploys, `terraform apply`, `DROP TABLE`, remote-script
pipes, config writes). opencode evaluates patterns
last-match-wins, so the catch-all is written first and the asks after it, in
both a bare and an argument form (`rm` and `rm *`).

`deny` floors survive auto-approve by design — `edit: deny` on the read-only
agents stays denied instead of becoming a question a "yes" could unlock. A
permission object you wrote yourself is never replaced; only a bare string (a
former harness default) is regenerated, and the migration says so on stderr.

Ask the classifier directly instead of guessing:

```python
from harness import risk
risk.assess("git push --force", "auto", "", root)   # {risk, irreversible, ask, reason, rule}
risk.assess_many(["read a.py", "rm -rf x"], root)   # plan-wide verdict
```

It returns `{risk, irreversible, ask, reason, rule}` for one action, or a
plan-wide verdict (`assess_many`) naming the irreversible subset. The classifier
is read-only by design (it never acts): classifying an
action is read-only, and without it the only thing a read-only agent can do
about an unfamiliar command is ask the human.
