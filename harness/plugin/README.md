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

Registered tools: `skills_list`, `skill_view`, `memory_recall`, `todowrite`,
`todoread`. The last two are the plan tools opencode 2.x dropped; they persist
into the harness todo space (`<project>/.opencode/harness/sessions.db` → `todos`,
keyed by the opencode session id), which is also what the sidebar's Todo row and
the compaction brief read — and every `todowrite` is **mirrored into opencode's
own `todo` table** (`opencode.db`, resolved via `OPENCODE_DB` / `$XDG_DATA_HOME`)
so opencode's own storage holds the plan too. The mirror never creates a
foreign database and never fails the tool call (see `mirrorOpencodeTodos`).
`execute(input, context)` — `context.sessionID` is the
active session, and a todo tool with no session answers instead of writing.

`input` is a JSON Schema; `execute(input, tool)` returns `{ content }`. Set
`options.codemode: false` or the tool is Code-Mode-only (reachable as
`tools.<name>()` inside `execute`, and "No tool named … is currently available"
when called directly).
Bundled seeds live at `harness/data/skills/<category>/<name>/SKILL.md` (two
levels), so seeding globs `**/SKILL.md`.

Provider / agents are still merged into `opencode.json` by
`ensure_opencode_config` — the plugin does not duplicate them, and agents carry
no model pins: they inherit your opencode default model.

## TUI contract (tui.tsx, probed against opencode 2.0.11)

| target | what harness contributes |
| --- | --- |
| `home.footer.status` | `harness · 12% ctx · $0.03 · 5.9m tok`, click toggles the sidebar (pressure first, lifetime total last). The chip carries `flexGrow=1 flexShrink=1 minWidth=0` on purpose: opencode renders its own **version** text right after this slot with `flexShrink 0`, so a non-shrinking chip would push it off-screen — the chip truncates instead, the version stays |
| `sidebar.content` | the stats rows: Window / Tokens / Models / Todo / Workers / Skills / Agents / Memory, each click-to-expand (several at once). Todo/Workers/Memory hide when empty; any list past five rows ends with `… +N more` so counts always match their list |
| `sidebar.footer` | `harness · click a row` / `harness · N expanded` |
| `app` | the `app` slot hosts the `ctx.keymap.layer` call (see below) |

Rows follow opencode's own geometry (label `flexGrow` + value `flexShrink 0`, so
values pin right at any width), use its own theme keys (`text.base` for labels,
`text.muted` for values) and its own numbers (total tokens = in + out +
reasoning + cache for the session **and its `task` subagent sessions** — each
child is its own `Session.Info`, merged in via `session.sync(sid,{children:true})`
+ `session.family(sid)`; the Window bar = the LAST assistant message /
the model's context limit, opencode's header rule — the session sum only grows
and pinned the bar at 100%), and there is no "Context" row because opencode
already renders one. Opened by `ctrl+g`, `/harness` (alias `/hp`), or the sidebar toggle;
`/harness-refresh` (`/hr`) re-scans.

Traps found by probing the running TUI — do not "simplify" these away:

- **`ctx.keymap.layer` needs the Provider.** Called from `setup` it throws
  `Keymap.Provider is missing`; it must be called from inside a slot's render
  component, which is why the commands are registered from the `app` slot.
- **No signals import.** State is `ctx.storage.memory(key, {initial})`, a
  reactive `[store, update]` pair. Importing `solid-js` would load a second
  instance and break reactivity.
- **A store write does NOT repaint the screen by itself.** Reads track (the slot
  re-renders, `rev` 0 → 4 on a real session) but nothing tells opencode to redraw
  the sidebar, so the panel kept its first paint — all zeros with a `⋯` that never
  cleared. After every state write, call `ctx.renderer.requestRender()` **deferred
  through `setTimeout(…, 0)`**: asking for a render from inside the update a
  render is already applying is how a repaint goes re-entrant. `ctx.theme.text`
  is `{base, muted, action, formfield, feedback}` — `text.default`/`text.subdued`
  are from a different version and resolve to `undefined`, i.e. rows in a fallback
  colour.
- **A store `sync()` can block on the network**, so every scan is time-boxed
  (`settle(p, SCAN_MS)`) and a `full` load requested while one is in flight is
  queued (`pendingFull`) rather than dropped — the session id arrives with the
  first slot render, exactly while the setup-time load is running.
- **There is no todo store, and opencode writes no todos.** `data.session.todo`
  and `data.location.todo` are `undefined`, and nothing past 2.0.13 writes
  opencode's own `todo` table (2.0.14 has no todo tool at all). The row reads the
  HARNESS todo space instead — this session's list from
  `<project>/.opencode/harness/sessions.db`, else the newest list in the project.
- **The Models rows must be built on every load.** `message.list(sid)` returns an
  in-memory store that only fills after `message.sync(sid)`, which runs at the
  end of a load; computing the rows inside the once-per-session slow scan froze
  them at `0 used` for the whole session. The walk runs every pass, with one
  `setTimeout(load, 500)` nudge per session.
- **A provider entry has no `models` map** (`Provider.Info` is
  id/name/activation/package). The Models row therefore shows no catalog counts
  at all ("kilo · 392 models" was inventory trivia); the model store only feeds
  the context-limit lookup.
- **The Workers row is project-wide**, and its count is a `count(*)` query rather
  than `rows.length` of the six displayed rows: `rlm.spawn` leaves `workers.session`
  empty, and a worker that runs for an hour can fall outside the newest rows while
  short ones finish. `workers.updated` is epoch **seconds** (`int(time.time())`),
  and the row prints the age instead of re-deriving `stale` (`doctor`'s rule).
- **Detail lines are budgeted at 34 cells.** The sidebar is a fixed 42 columns;
  one cell more and opencode middle-truncates the line it is already showing.
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
