# Harness — refactor status (forked TUI → stock-opencod plugin)

All changes are in the working tree, **uncommitted**. Nothing has been pushed yet.
`python -m pytest -q` → **44 passed**.

## Done

### 1. The opencod server plugin is complete (`harness/plugin/harness.ts`)
- Named export `HarnessPlugin` (plus `export default` for opencod's loader).
- `setup(ctx)`:
  - bootstraps/starts the relay gateway (`harness serve`, localhost:8787) on first use;
  - seeds bundled skills (`harness/data/skills/`) into opencod's skill store via `ctx.skill.transform`;
  - registers `experimental.session.compacting` (via `ctx.session.on(...)`) which fetches a memory brief from the gateway (`GET /api/memory?q=<sessionID>`, Bearer `HARNESS_TOKEN`) and injects it into `output.context` so durable facts survive session compaction.
- Provider / agents / MCP are NOT duplicated by the plugin — they come from `opencod.json` merged by `harness tui`/`harness setup` (`ensure_opencode_config`), matching `harness/plugin/README.md`.

### 2. Forked TUI is removed; harness now runs on stock opencod
- Deleted `tui/` (patches, AppImage, `UPSTREAM_REF`, sidebar plugins).
- Deleted fork-only CI: `.github/workflows/build-tui.yml`, `.github/workflows/upstream-sync.yml`.
- Added `.github/workflows/ci.yml` (pytest + `harness doctor`).
- `harness tui` now ensures gateway + `opencod.json` + plugin, then execs stock `opencode` (auto-detected on `PATH`). `--setup-only` prints `HARNESS_TOKEN`/`HARNESS_URL` exports. Removed the `harness-tui`/AppImage binary path.
- `harness plugin install|path` ships/installs the plugin to `~/.config/opencode/plugins/` (auto-loaded by opencod v2).
- `install.sh` installs the CLI + plugin (no binary download).
- `harness doctor` reports stock opencod presence (`opencode_binary`).
- `harness/cli.py`: `_find_tui`/`_ensure_tmp` → `_find_opencode`; factored `install_plugin()` shared by `harness plugin install` and `harness tui`.

### 3. Docs + install guide
- New `docs/plugin.md` — the requested "how to install the plugin to opencod" walkthrough.
- `README.md`, `docs/quickstart.md`, `docs/tui.md` rewritten to reflect stock opencod + the single plugin (no fork/AppImage references remain).

### 4. Tests
- `tests/test_plugin.py` (2): `test_plugin_files_exist` (asserts `HarnessPlugin` + `experimental.session.compacting` present), `test_plugin_install_idempotent`.
- `tests/test_tui.py`: `test_tui_config_merge`, `test_relay_baseurl_follows_port`, `test_find_opencode` (replaces the old AppImage test).
- Full suite: **44 passed**.

## Remaining / caveats

1. **Not committed or pushed.** Everything above is staged-ready in the working tree; no `git commit`/`git push` has been run. (A `git commit` was prepared but not executed; push to `origin` needs the repo's GitHub credentials.)
2. **The TUI sidebar panels are gone.** `harness-panels.tsx`/`harness-route.tsx` were fork-coupled (imported `../builtins`); they were removed with the fork. If you want the sidebar (Todo/Models/Memory/Skills panels) back, they'd need to be rewritten as a stock opencod TUI plugin (`@opencode-ai/plugin/tui`, `api.slots.register`) — a separate effort.
3. **`experimental.session.compacting` registration is via `ctx.session.on(...)`.** The published `@opencode-ai/plugin` v2 types don't document `ctx.session` (they also omit `provider`/`mcp`, which the plugin already uses), so this is inferred from the runtime ctx the existing transforms imply. It's wrapped in try/catch so it degrades gracefully if the runtime differs.
4. **`harness tui` requires stock `opencode` on `PATH`.** If not installed, it prints `npm create opencod@latest` instructions and falls back to `harness chat`. The CLI itself needs no opencod.
5. **`sessions.db` at repo root was a stale leftover** (never tracked); removed. Runtime DB lives under `.opencode/harness/` (gitignored).
6. **`build/` and `.opencode/harness/`** are gitignored runtime/test state — not part of the change.

## How to verify
- `python -m pytest -q` → 44 passed
- `python -m harness plugin path` → prints `harness/plugin/harness.ts`
- `python -m harness plugin install` (with `XDG_CONFIG_HOME` set) → copies to `~/.config/opencode/plugins/harness.ts`, prunes legacy specs
- `python -m harness tui --dry-run` → prints the new dry-run line
- `python -m harness doctor` → shows `opencode_binary`
- `python -m harness setup` → mints token, seeds skills, prints env vars