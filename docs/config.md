# Config (self-managed)
Layers: defaults < `~/.harness/harness.jsonc` < `.opencode/harness.jsonc`
< env (`HARNESS_MODEL`, a full `provider/model` id). JSONC. Agent-mutable:
model_profile|budgets|categories|agents|skills|memory|mcp|tui|compress.
Denied: secrets|token|trusted_project_dirs|mcp_env_allowlist (+ any api_key
path — models live in opencode, never here; there is no `router` section).

`config get/set/show --scope user|project`: validate → diff preview → backup
(`backups/`) → atomic write → re-validate; corrupt files boot from backup.
`model_profile`: `capable|simple|deep`, or a `provider/model` id to force one
(HARNESS_MODEL flows through here). Otherwise every agent inherits the model
you set in `opencode.json` (`harness doctor` shows the resolved default).

LSP is enabled unless already decided (`lsp: true` merged only when the key
is absent; explicit `false`/object always respected). Agent permission maps
use modern `permission` keys, so auto-approve (`--auto` / TUI toggle) is
respected: `ask` rules auto-approve, `deny` floors (e.g. read-only agents)
stay denied by design.
