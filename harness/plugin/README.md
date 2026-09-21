# Harness opencode plugin (v2 API, stock opencode, no fork)

Two sources shipped as one plugin **directory**:

| source | installs as | entrypoint |
| --- | --- | --- |
| `harness.ts` | `plugins/harness/server.ts` | default-exported `{ id, setup }` — skills, tools, hooks |
| `tui.tsx` | `plugins/harness/tui.tsx` | TUI entrypoint (`setup(ctx)` → `ctx.ui.slot`) — the footer chip |

Installed by `harness plugin install` into `~/.config/opencode/plugins/`
(auto-discovered by opencode v2 — no `opencode.json` entry needed). Zero
imports in either file.

The directory form is not cosmetic: opencode probes a plugin *directory* for
`server.*` and `tui.*` entrypoints, and it sets `features.tui` (the flag the
TUI's Plugins panel filters on) only when a `tui` entrypoint exists. A bare
`harness.ts` is server-only and shows solely under the panel's Server section.
Release assets must therefore include **both** `server.ts` and `tui.tsx`; see
`.github/workflows/release.yml`.

## Loader contract (verified against opencode v2.0.8)

`PluginModule.load` decodes the module and requires a default-exported **object**
with a string `id` plus either an `effect` or a `setup` function. Two traps:

- `setup(ctx)` may return a **cleanup function or nothing**. The host calls any
  *other* returned value as a cleanup function, so returning anything else dies
  with `TypeError: … is not a function`, which kills plugin activation: the
  plugin never appears under `/plugins` and work that waits for plugin
  activation stalls.
- The server resolves plugin specifiers before Bun strips types, so a static
  `import` — even `import type … from "@opencode-ai/plugin"` — fails the load
  with `ResolveMessage`. Use no imports (dynamic `import("node:path")` is fine).

v1 hook objects (`{ tool: {…}, "tool.execute.after": … }`) are **not** read in
v2. Everything is registered through `ctx`:

| feature | registration |
| --- | --- |
| skill seeding | `ctx.skill.transform(editor => editor.add({id, name, description, path, content}))` |
| native tools | `ctx.tool.transform(editor => editor.add({name, description, input, execute, options: {codemode: false}}))` |
| output condensing | `ctx.tool.hook("execute.after", fn)` |
| compaction brief | `ctx.session.hook("compaction", fn)` → append to `event.system` |

`input` is a JSON Schema; `execute(input, tool)` returns `{ content }`. Set
`options.codemode: false` or the tool is Code-Mode-only (reachable as
`tools.<name>()` inside `execute`, and "No tool named … is currently available"
when called directly).
Bundled seeds live at `harness/data/skills/<category>/<name>/SKILL.md` (two
levels), so seeding globs `**/SKILL.md`.

Provider / agents are still merged into `opencode.json` by
`ensure_opencode_config` — the plugin does not duplicate them, and agents carry
no model pins: they inherit your opencode default model.
