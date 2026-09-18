# TUI-B fork (opencode -> harness-tui)

Upstream: `sst/opencode` (pinned — see workflow `TUI_REF`). Only these paths are kept:
`packages/opencode/src/cli`, `packages/opencode/src/tui` (opentui), provider loader.
Deleted in fork patch: `packages/web`, `packages/console`, `desktop`, `sdks/*`.

## Reskin deltas (`tui/patches/`)
1. `provider-harness.patch` — add `provider.harness {baseURL:http://127.0.0.1:8787/v1, apiKey:$HARNESS_TOKEN}`; all completions go through harness relay (`auto-fastest/tag:*`). Hide native model keys UI.
2. `sidebar-tabs.patch` — tabs Sessions|Tasks|Agents|Memory|Skills|Models|MCP hitting `GET /api/*`; center Chat|Diff|Plan|Logs; palette adds `/spec/plan/build/test/review/ship/sec/skills/memory/compress/refine/usage/checkpoint/model/goal/stop`.
3. `agents-modes.patch` — Tab cycles Code/Plan/Ask/Debug/Review/Orchestrator with tool allowlists from `docs/agents.md`; `@general` calls `POST /api/spawn`.
4. `slim.patch` — remove web preview, built-in LSP, auto-update check.

## Build (CI only — never on potato PC)
`.github/workflows/build-tui.yml` runs `bun install` + `bun build --compile` for linux-x64 (arm64 next), uploads `harness-tui-<arch>` to Releases. Installer verifies sha256.

## Dev loop without building
Run core + CLI fallback: `harness serve &` then `harness chat`. TUI contract tests hit the same SSE endpoints.
