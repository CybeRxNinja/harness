# Config (self-managed)
Layers: defaults < `~/.harness/harness.jsonc` < `.opencode/harness.jsonc`
< env (`HARNESS_MODEL`). JSONC. Agent-mutable: model_profile|router|budgets|
categories|agents|skills|memory|mcp|tui. Denied: secrets|token|
trusted_project_dirs|mcp_env_allowlist (+ any api_key path — use `setup`/env).

`config get/set/show --scope user|project`: validate → diff preview → backup
(`backups/`) → atomic write → re-validate; corrupt files boot from backup.
`model_profile`: `capable|simple|deep|provider/model`. The relay block in
`opencode.json` is managed (baseURL follows the live gateway); edit
`HARNESS_PORT`/`HARNESS_URL` instead.
