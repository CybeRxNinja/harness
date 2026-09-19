# TUI-B fork (opencode -> harness-tui)

Upstream: `sst/opencode` pinned in `tui/UPSTREAM_REF` (currently `v1.18.31`).
Only `packages/opencode` + `packages/tui` are built; web UI embed skipped.

## Reskin = plugin + config, not a rewrite
1. `plugins/harness-panels.tsx` — builtin sidebar plugin (Todo|Agents|Skills|Memory|Models|Stats tabs via the `sidebar_content` slot, reads `HARNESS_URL`/`HARNESS_TOKEN`). CI copies it to `packages/tui/src/feature-plugins/sidebar/harness.tsx`.
2. `plugins/harness-route.tsx` — footer fragment showing the live resolved route (`→ provider/model`) next to the configured id. CI copies it to `packages/tui/src/component/harness-route.tsx`.
3. `patches/sidebar-builtins.patch` + `patches/footer-route.patch` — registry entry + footer hook (generated via `git diff` against the pinned tag, verified with `git apply --check` / `patch --dry-run`).
4. `harness/harness-opencode.json` — drop-in user config (shipped as package data, so pip installs work too): `provider.harness` (openai-compat → local relay, key `{env:HARNESS_TOKEN}`, models auto-fastest + tag:* + Free Best), agents `orchestrator/ask/debug/review` + native `plan` pinned to reasoning, default model `harness/auto-fastest`.

## Scope note (honest)
Via the provider shim the TUI drives the harness **relay** (routing/QoS/failover) with opencode-native sessions/tools. The harness brain (orchestrator spawn, RLM kernel, skills, memory writes) runs in `harness serve` and is operated via CLI + `POST /api/chat`, observed from the sidebar panels. Unifying both loops over the OpenAI protocol is future work, not v0.

## Build (CI only — never on potato PC)
`.github/workflows/build-tui.yml` (bun pinned to upstream's `packageManager`, currently 1.3.14 — 1.4.x emits broken binaries): checkout upstream at `tui/UPSTREAM_REF` → apply `tui/patches/*.patch` → copy plugins → `bun install` → `script/build.ts --single --skip-embed-web-ui` (version stamped from tag, required for Zen free-tier gating) → `harness-tui-linux-x64` + `Harness_TUI-x86_64.AppImage` artifacts/Release.
`.github/workflows/upstream-sync.yml` checks upstream weekly, verifies patches, opens a ready-to-merge PR (or a manual-merge issue where repo policy blocks Actions PRs).

## AppImage first launch (self-setup, no install mess)
`AppRun` merges config (never clobbers), installs/refreshes the Python CLI from GitHub (PROTO contract), starts the gateway rooted at the launch directory, installs the launcher shortcut, then execs the TUI. Single file in `~/Applications/` is the whole install.

## Dev loop without building
Run core + CLI fallback: `harness serve &` then `harness chat`. TUI contract tests hit the same SSE endpoints.
