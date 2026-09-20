# Harness — refactor status (forked TUI → stock-opencode plugin)

> **Correction (2026-09-20):** the plugin below is described with a superseded
> API guess. The real stock-opencode v2 contract is a default-exported
> `{ id, setup(ctx) }` where everything is registered through `ctx.*`
> transforms/hooks (`setup` returns a cleanup function or nothing). The old
> `effect`-returning-hooks-object / `import type` shape failed plugin
> activation. See `harness/plugin/README.md` and `harness/plugin/harness.ts`.

All changes are in the working tree, **uncommitted**. Nothing has been pushed yet.
`python -m pytest -q` → **48 passed** (plugin feature matrix now also locks
future-host degradation).

## Done

### 0. Diagnosed: "free tier" error + missing plugin panel entry (2026-09-20)
- **Free-tier error is a zen gate, not a plugin bug.** Replaying captured
  requests against `https://opencode.ai/inference/openai/v1/responses` shows the
  free endpoint requires a tool literally named `shell` in the request
  (build+shell → 200; renamed shell, fake edit, or read-only tool lists →
  `FreeTierError`). The built-in `ask` agent sets `permission.bash: deny`, so it
  never advertises `shell` and fails under ANY zen free model. `build`/
  `plan`/`orchestrator` (shell allowed) work. Fixes: use a shell-capable agent,
  set `"bash": "allow"` on the agent, or use a paid key for read-only agents.
- **The TUI Plugins panel lists only `features.tui` plugins — now satisfied.**
  The flag is set only for plugins with a `tui` entrypoint, so the single-file
  install could never appear in the panel's list (only under Server). The
  install is now a directory (`plugins/harness/{server.ts,tui.tsx}`);
  `tui.tsx` registers a footer chip via `ctx.ui.slot({append:
  "home.footer.status"})`. Verified: `/api/plugin` reports
  `features:{server:true,tui:true}`, `opencode plugin list` shows harness, TUI
  reconciliation loads 13 plugins with no errors. Legacy `harness.ts` installs
  are removed by `harness plugin install`.
- **The "update available" banner is opencode's own updater, unrelated to the
  plugin.** The check runs on every TUI launch against
  `https://opencode.ai/update/api/...` and currently reports
  `current=2.0.8 latest=2.0.11` (2.x lives only on opencode.ai's update
  service; GitHub releases show the older 1.18.x train, hence the confusion).
  A/B proof: with the plugin installed vs removed, the logged check is
  identical; the banner predates the plugin (Sep 17 `2.0.3→2.0.5` vs first
  plugin install Sep 20). harness writes nothing opencode versions: only
  `<project>/.opencode/harness/` seeds, `opencode.json` agent merges, and the
  plugin file itself. Silence with `OPENCODE_DISABLE_AUTOUPDATE=1` (opencode
  honors it), or `opencode upgrade`.

## Done

### 1. The opencode server plugin is complete (`harness/plugin/harness.ts`)
- `export default { id: "harness", setup(ctx) }`, zero imports (the server
  resolves plugin specifiers before Bun strips types, so any static import
  fails the load).
- `setup(ctx)` registers four features, each isolated in its own try/catch so
  one failure can never kill activation. Every `ctx.*` API is presence-checked
  first, so an opencode upgrade that renames/removes one costs only its own
  feature (locked by the degradation cases in `test_plugin_features`):
  - **skill seeding** — `ctx.skill.transform(editor => editor.add(...))` seeds
    the bundled skills (`harness/data/skills/<category>/<name>/SKILL.md`) as
    `harness-<name>` ids (11 seeds).
  - **direct tools** — `ctx.tool.transform` adds
    `skills_list`/`skill_view`/`memory_recall` with
    `options: { codemode: false }` (the v2 default hides a tool in the Code
    Mode catalog, where a by-name call fails with "No tool named …").
  - **output condensing** — `ctx.tool.hook("execute.after")` collapses
    oversized success results in place; errors pass through untouched.
  - **compaction brief** — `ctx.session.hook("compaction")` appends the newest
    durable facts to `event.system`, so they survive the summary step. No
    gateway, no extra process: facts are read straight out of
    `<project>/.opencode/harness/sessions.db`.
- Provider / agents / MCP are NOT duplicated by the plugin — they come from
  `opencode.json` merged by `harness tui`/`harness setup`
  (`ensure_opencode_config`), matching `harness/plugin/README.md`.

### 1b. Feature verification against a live server (2026-09-20)
Every feature was driven through a real session (model `opencode/mimo-v2.5-free`)
with diagnostic hooks logging the true v2 payloads:
- `skills_list` → 13 skills (11 `harness-*` + 2 builtin), `skill_view` on both a
  seeded skill and the builtin `opencode` skill, `memory_recall` → the seeded fact.
- `tool:execute.after` fires with `{tool, sessionID, agent, messageID, id, input,
  status, result}`; a 10.7 KB `shell` result was condensed 96% with head and tail
  (including the shell's `saved to:` path) preserved, and the model still answered
  the question correctly.
- `session:compaction` fires with `{sessionID, model, system, messages, options,
  agent, tools}` and the appended brief was the last `system` entry.

Defects found and fixed in this pass:
1. `memory_recall` with a query containing no searchable term fell back to
   `WHERE 1=1`, i.e. it returned the newest facts regardless of the query.
2. The compaction brief searched facts for the literal words `session <id>`,
   which matches nothing (a session id appears in no fact), so no brief was ever
   injected. It now injects the newest facts.
3. `skill_view` on a builtin skill raised `ENOENT … /builtin/opencode.md` (the
   body lives in the payload's inline `content`, not on disk).
4. Silent failures: the compaction hook swallowed errors; both hooks now log.

### 2. Forked TUI is removed; harness now runs on stock opencode
- Deleted `tui/` (patches, AppImage, `UPSTREAM_REF`, sidebar plugins).
- Deleted fork-only CI: `.github/workflows/build-tui.yml`, `.github/workflows/upstream-sync.yml`.
- Added `.github/workflows/ci.yml` (pytest + `harness doctor`).
- `harness tui` now ensures gateway + `opencode.json` + plugin, then execs stock `opencode` (auto-detected on `PATH`). `--setup-only` prints `HARNESS_TOKEN`/`HARNESS_URL` exports. Removed the `harness-tui`/AppImage binary path.
- `harness plugin install|path` ships/installs the plugin to `~/.config/opencode/plugins/` (auto-loaded by opencode v2).
- `install.sh` installs the CLI + plugin (no binary download).
- `harness doctor` reports stock opencode presence (`opencode_binary`).
- `harness/cli.py`: `_find_tui`/`_ensure_tmp` → `_find_opencode`; factored `install_plugin()` shared by `harness plugin install` and `harness tui`.

### 3. Docs + install guide
- New `docs/plugin.md` — the requested "how to install the plugin to opencod" walkthrough.
- `README.md`, `docs/quickstart.md`, `docs/tui.md` rewritten to reflect stock opencod + the single plugin (no fork/AppImage references remain).

### 4. Tests
- `tests/test_plugin.py` (4): `test_plugin_files_exist` (locks the loader
  contract: default export, `id`, `setup`, no static imports, `codemode: false`,
  hook names), `test_plugin_setup_registers_through_ctx` (bun smoke: registers
  through ctx, executors answer, condensing shrinks success and keeps errors),
  `test_plugin_features` (bun feature matrix: skill seeding/list/view incl.
  builtins and traversal, memory recall incl. no-searchable-term queries, condense
  pass-throughs, compaction brief content/idempotence/tolerance),
  `test_plugin_install_idempotent`.
- `tests/test_tui.py`: `test_tui_config_merge`, `test_relay_baseurl_follows_port`, `test_find_opencode` (replaces the old AppImage test).
- Full suite: **48 passed**.

## Remaining / caveats

1. **Not committed or pushed.** Everything above is staged-ready in the working tree; no `git commit`/`git push` has been run. (A `git commit` was prepared but not executed; push to `origin` needs the repo's GitHub credentials.)
2. **The TUI sidebar panels are gone.** `harness-panels.tsx`/`harness-route.tsx` were fork-coupled (imported `../builtins`); they were removed with the fork. If you want the sidebar (Todo/Models/Memory/Skills panels) back, they'd need to be rewritten as a stock opencod TUI plugin (`@opencode-ai/plugin/tui`, `api.slots.register`) — a separate effort. Related: the Plugins panel only lists `features.tui` plugins, so harness (server-only) does not appear there by design; see section 0.
3. **Hook payloads are runtime facts, not documented types.** The published
   `@opencode-ai/plugin` v2 types don't document `ctx.session`/`ctx.tool.hook`
   payloads. Verified against the running server (v2.0.8): the compaction hook
   receives `{sessionID, model, system, messages, options, agent, tools}` and an
   append to `system` reaches the summarization request; `execute.after`
   receives `{tool, sessionID, agent, messageID, id, input, status, result}` and
   mutating `result.content[].text` is what the model sees. Both are wrapped in
   try/catch so a payload change degrades to a no-op instead of a broken turn.
4. **`harness tui` requires stock `opencode` on `PATH`.** If not installed, it prints `npm create opencode@latest` instructions and falls back to `harness chat`. The CLI itself needs no opencode.
5. **`sessions.db` at repo root was a stale leftover** (never tracked); removed. Runtime DB lives under `.opencode/harness/` (gitignored).
6. **`build/` and `.opencode/harness/`** are gitignored runtime/test state — not part of the change.

## How to verify
- `python -m pytest -q` → 48 passed
- `python scripts/opencode_smoke.py` → boots a real `opencode serve`, forces
  activation, asserts the harness plugin is active + 11 skills seeded (CI: the
  `opencode-live` job installs stock opencode and runs this on every push)
- `python -m harness plugin path` → prints `harness/plugin/harness.ts`
- `python -m harness plugin install` (with `XDG_CONFIG_HOME` set) → copies to `~/.config/opencode/plugins/harness.ts`, prunes legacy specs
- `python -m harness tui --dry-run` → prints the new dry-run line
- `python -m harness doctor` → shows `opencode_binary`
- `python -m harness setup` → mints token, seeds skills, prints env vars