# TUI-B fork (opencode -> harness-tui)

Upstream: `sst/opencode` pinned in `tui/UPSTREAM_REF` (currently `v1.18.31`).
Only `packages/opencode` + `packages/tui` are built; web UI embed skipped.

## Reskin = plugin + config, not a rewrite
1. `plugins/harness-panels.tsx` — builtin sidebar plugin (Tasks|Agents|Skills|Memory|Models tabs via the `sidebar_content` slot, reads `HARNESS_URL`/`HARNESS_TOKEN`). CI copies it to `packages/tui/src/feature-plugins/sidebar/harness.tsx`.
2. `patches/sidebar-builtins.patch` — 2-line registry entry (generated via `git diff` against the pinned tag, verified with `git apply --check`).
3. `harness/harness-opencode.json` — drop-in user config (shipped as package data, so pip installs work too): `provider.harness` (openai-compat → `http://127.0.0.1:8787/v1`, key `{env:HARNESS_TOKEN}`, models auto-fastest + tag:*), agents `orchestrator/ask/debug/review`, default model `harness/auto-fastest`.

## Scope note (honest)
Via the provider shim the TUI drives the harness **relay** (routing/QoS/failover) with opencode-native sessions/tools. The harness brain (orchestrator spawn, RLM kernel, skills, memory writes) runs in `harness serve` and is operated via CLI + `POST /api/chat`, observed from the sidebar panels. Unifying both loops over the OpenAI protocol is future work, not v0.

## Build (CI only — never on potato PC)
`.github/workflows/build-tui.yml`: checkout upstream → apply `tui/patches/*.patch` → copy plugin → `bun install` → `script/build.ts --single --skip-embed-web-ui` → `harness-tui-linux-x64` artifact/Release. Installer verifies sha256.

## Dev loop without building
Run core + CLI fallback: `harness serve &` then `harness chat`. TUI contract tests hit the same SSE endpoints.
