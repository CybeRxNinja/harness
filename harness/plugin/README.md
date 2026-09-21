# Harness opencode plugin (v2 API, stock opencode, no fork)

Two sources shipped as one plugin **directory**:

| source | installs as | entrypoint |
| --- | --- | --- |
| `harness.ts` | `plugins/harness/server.ts` | default-exported `{ id, setup }` — skills, tools, hooks |
| `tui.tsx` | `plugins/harness/tui.tsx` | TUI entrypoint — footer chip, side panel, sidebar rows, commands |

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

## TUI contract (tui.tsx, probed against opencode v2.0.8)

| target | what harness contributes |
| --- | --- |
| `home.footer.status` | `harness · N skills · M facts`, click opens the panel |
| `session.panel` | the side panel: skills + memory views (renders only when `panel.name` is ours) |
| `sidebar.content` / `sidebar.footer` | harness summary + `ctrl+g side panel` hint |
| `app` | the `app` slot hosts the `ctx.keymap.layer` call (see below) |

Opened by `ctrl+g`, `/harness` (alias `/hp`), or the palette entry
"Harness: open panel"; the panel's own keys are panel-scoped, so they cannot
hijack the prompt.

Traps found by probing the running TUI — do not "simplify" these away:

- **`ctx.keymap.layer` needs the Provider.** Called from `setup` it throws
  `Keymap.Provider is missing`; it must be called from inside a slot's render
  component, which is why the commands are registered from the `app` slot.
- **No signals import.** State is `ctx.storage.memory(key, {initial})`, a
  reactive `[store, update]` pair whose writes repaint the slots. Importing
  `solid-js` would load a second instance and break reactivity.
- **`require` and `Bun` are undefined** in the TUI plugin scope. File and SQLite
  access go through dynamic `import("node:fs")` / `import("bun:sqlite")`
  (`Bun.file` is *not* available here, unlike in server.ts).
- **`session.panel` is host-owned.** opencode sizes, focuses and full-screens it
  and keeps narrow terminals full-screen, so `toggleFullscreen` is a no-op until
  there is room for a side panel. `panel.open()` outside a session returns false.
- **Every render is guarded** and returns a fallback `<text>`, because a throw
  inside a slot can take the TUI screen with it.

A syntax error in this file is only visible as a WARN in opencode's log
(`plugin operation failed … stage=read`) with nothing in the UI, so
`tests/test_plugin.py::test_plugin_entrypoints_parse` transpiles both
entrypoints with `Bun.Transpiler` in CI.
