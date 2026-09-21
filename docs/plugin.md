# Plugin (install to opencode)
Harness ships as a single stock-opencode v2 **server plugin**, not a fork.
Installing it gives your opencode sessions built-in skills, native
skills/memory tools, and memory that survives session compaction. There is
no relay: models always come from **your** opencode providers.

## Install

```bash
pip install git+https://github.com/CybeRxNinja/harness.git   # harness CLI (stdlib-only)
harness plugin install --from-release latest                 # no repo checkout needed
opencode                                                     # stock opencode; the plugin auto-loads
```

(From a checkout instead: `pip install -e .` then plain `harness plugin install`.)

The plugin lands at `~/.config/opencode/plugins/harness/` as a directory with
two entrypoints — `server.ts` (skills/tools/hooks) and `tui.tsx` (a footer
status chip in the TUI) — and is auto-loaded by opencode v2; no entry in
`opencode.json`'s `plugin` array is required. The directory form matters:
opencode probes a plugin directory for `server.*` and `tui.*` entrypoints, and
the TUI's Plugins panel lists only plugins with a `tui` entrypoint
(`features.tui`); a bare `harness.ts` is a server-only plugin and appears
solely under the panel's Server section. `harness tui` does the above setup and launches opencode for you:

```bash
harness tui             # opencode config + plugin, then exec opencode
harness tui --setup-only  # prep only, then run `opencode` yourself
```

> Models are yours: set `model` in `opencode.json` (or per-agent `model`).
> Harness agents carry no model pins — they inherit your default.

## What the plugin does

All four are registered through the v2 plugin context from `setup(ctx)`:

1. **Skill seeding** — `ctx.skill.transform` registers the bundled skills
   (`harness/data/skills/<category>/<name>/SKILL.md`) into opencode's skill store
   so they're loadable by id without editing `opencode.json`.
2. **Native tools** — `ctx.tool.transform` adds
   `skills_list`/`skill_view`/`memory_recall` as *direct* tools
   (`options: { codemode: false }`; the v2 default is Code-Mode-only, where a
   by-name call fails with "No tool named … is currently available"). They read
   local disk + SQLite directly (no gateway, no extra process).
3. **Output condensing** — the `ctx.tool.hook("execute.after")` hook collapses
   oversized tool results in place (errors pass through untouched).
4. **Compaction memory** — the `ctx.session.hook("compaction")` hook recalls
   project facts locally and appends them to the summarization request, so
   durable facts survive the summary step.

The loader only accepts a default-exported object with a string `id` plus an
`effect` or `setup` function, and `setup` must return a cleanup function or
nothing — any other returned value is called as a cleanup function and kills
plugin activation. The file therefore has no imports and returns nothing; see
`harness/plugin/README.md` for the full contract.

## TUI views (a stats panel in the sidebar)

`tui.tsx` is the plugin's TUI half. It fills **opencode's existing sidebar** with
a Kilo-style stats/info panel and adds a small footer chip — it deliberately does
not rearrange the TUI: no routes, no docked overlay, no replaced slots.

| view | slot | what it shows |
| --- | --- | --- |
| chip | `home.footer.status` | `harness · 9.6k tok · $0.00` — click toggles the sidebar (with no session yet it reads `harness · click for stats`, since the skill/fact stores are location-scoped and empty at the default location) |
| stats panel | `sidebar.content` | the sections below |
| hint | `sidebar.footer` | `harness · /harness · click header` |

```
ctrl+g              toggle the stats sidebar (palette: "Harness: toggle stats sidebar")
/harness            same, from the prompt (/hp is an alias)
/harness-refresh    re-scan everything now (/hr)
click a header      expand that section (one at a time — accordion)
```

The sidebar is a short, fixed viewport that does not scroll — about **11 rows**
in a normal terminal — so the layout is budgeted: one header row, six one-line
headlines, and at most four detail rows for whichever section is open. That is
why **nothing is expanded by default**: with Context open the last sections were
pushed off the bottom and looked missing (they were unreachable, since the
viewport cannot scroll). Click a header to fold/unfold.

| section | rows | source |
| --- | --- | --- |
| header | session id (the agent · model moved into Context) | `data.session.get(sid)` |
| Context | `model · agent`, in/out, reasoning · cache, cost | `session.tokens` + the provider's `models[id].limit.context` |
| Token usage | Input · Output, Reasoning, Cache read · write, Cost | `session.tokens` |
| Models | per provider: available model count, then `model steps cost` | assistant messages grouped by provider/model |
| Todo | `○ ◐ ●` + text, this session only | opencode's `todo` table (read-only) |
| Agents + Skills | the 11 harness skills, then agents | `location.skill` / `location.agent` after `sync()` |
| Memory | durable facts for the project | the same `sessions.db` the server half uses |

Skill rows drop the shared namespace: the store names them
`harness-spec-driven-development` (that is their id), and the row shows
`spec-driven-development` because the sidebar is 46 columns wide and every row
would repeat `harness-`.

**"The side panel is empty"** — the sidebar renders *only plugin
contributions*, so with nothing claiming `sidebar.content` it is genuinely
empty; harness now fills it (re-run `harness plugin install` on an older
install). If your opencode hides the sidebar, the palette has `Show sidebar`
(`ctrl+x` then `b`). Note opencode puts its own context block at the top of the
sidebar — ours sits underneath it.

Implementation notes worth keeping (all probed against opencode v2.0.8):

- `ctx.keymap.layer` throws `Keymap.Provider is missing` unless called from
  inside a slot's render component, so commands register from the `app` slot.
- State lives in `ctx.storage.memory` (a reactive store). Importing `solid-js`
  would load a second instance and break reactivity.
- **Solid re-runs tracked JSX expressions, not the render body.** Detail rows are
  therefore built inside the JSX expression and each closure reads a tracked
  value; computing the array in the body froze it at the first paint and showed
  stale stats (`window … (limit unknown)`) forever.
- **`session.sync()` invalidates the store** and repopulates it asynchronously,
  so `session.get(sid)` on the same tick returns nothing — read first, sync at
  the end (that produced a panel of empty sections at first).
- `require` and `Bun` are **not** defined in the TUI plugin scope; file and
  SQLite access use dynamic `import("node:fs")` / `import("bun:sqlite")`.
- Counts are harness-only for skills (the store also holds opencode's builtins,
  so “13 skills” above 11 listed rows reads like a bug), and a failed load is
  shown in the panel because cli-side `console.error` never reaches the log.
- **The in-flight placeholder reads a reactive flag, never the `loading` guard.**
  `loading` is a plain variable, so a tracked expression reports whatever it held
  at that repaint — that is how `scanning…` stayed on screen next to fully
  loaded stats. `view.scanning` is set when a scan starts and cleared in the same
  state update that bumps `rev`, so the placeholder cannot outlive its scan.
- **The 8s poll stands down when nothing is on screen.** opencode loads this
  entrypoint in the long-lived server process too, where no slot ever renders;
  polling there meant a session message walk + four store syncs + two SQLite
  reads every 8s with nobody watching. Every slot render stamps `lastRender`,
  and the poll skips once nothing has rendered for ~32s (it resumes the moment
  the panel is drawn again). What a scan reads: session tokens/cost/agent/model,
  the session's assistant messages (Models rows), the lazily-synced location
  stores (skill/agent/model/provider), opencode's `todo` table and the harness
  facts DB — measured at ~4ms warm, which is why the placeholder is the thing
  worth watching, not the cost.

## What the plugin does NOT do (those come from opencode.json)

Agents are merged into `~/.config/opencode/opencode.json` by
`harness plugin install`/`harness tui` (`ensure_opencode_config`), not by the
plugin:

- `agent` — `orchestrator/ask/debug/review` + native `plan`, with modern
  `permission` maps (auto-approve compatible). No `model` keys: every agent
  runs on your configured default unless you pin one yourself.

Skills need no MCP hop. The stdio server (`harness mcp`) remains for
non-opencode MCP clients only.

## Uninstall

```bash
harness plugin uninstall   # removes plugin file + harness-merged agents (user keys untouched)
pip uninstall harness      # remove the CLI (optional)
```

Uninstall only removes harness-owned entries (agents without a user model pin,
legacy `provider.harness` / `harness/*` leftovers). Anything you customized
beyond that is left alone.

## Troubleshooting

**"OpenCode's free tier can only be used from within OpenCode"** — zen's
server-side gate, not a plugin bug. The free-tier endpoint accepts a request
only if its tool list advertises a tool literally named `shell` (verified by
replaying captured requests: `shell` present → 200, renamed/fake tool or only
read tools → `FreeTierError`). opencode only advertises `shell` when the
agent's `permission.bash` is not `deny`, so **any** agent that denies bash —
including a read-only one — makes every free model 403.

Harness used to ship its read-only agents (`ask`, `review`) with
`"bash": "deny"`, which is why free models failed under them. They now use
`"bash": "ask"`: the shell tool is advertised (so free models work) while
commands still need per-command approval, and `edit: deny` keeps writes off.
`harness plugin install` migrates an existing install (the old `deny` value is
rewritten to `ask` and reported on stderr); an agent of your own is never
touched. If a custom agent still trips this, use a shell-capable agent, set
`"bash": "ask"` on it, or use a paid key.

**The TUI Plugins panel lists only `features.tui` plugins** — that flag is set
only when the plugin has a `tui` entrypoint. Harness ships one
(`plugins/harness/tui.tsx`, a small footer chip on the home screen), so after
`harness plugin install` (or any install from this version on) harness appears
in the panel's plugin list as well as the Server section. If you installed an
older release that dropped a bare `harness.ts`, re-run `harness plugin install`
— it replaces the single file with the directory layout and removes the stale
file. Server-side activation can always be verified via `opencode api get
/api/plugin` (or `GET /api/plugin` with basic auth against `opencode serve`).

Releases publish both entrypoints as assets — `server.ts` (same file as
`harness.ts`, under its installed name), `harness.ts` (kept for older CLIs) and
`tui.tsx` — added by `.github/workflows/release.yml` on any `plugin-v*` tag.
`--from-release latest` resolves the newest `plugin-v*` release rather than
GitHub's `releases/latest`, which is just the most recently published release
(the repo also publishes TUI binary releases with no plugin assets). Releases
before this one shipped `harness.ts` only: the install then has no `tui.tsx`,
and the CLI says so on stderr instead of failing silently.

**"Update available" even though the GitHub releases page shows nothing newer** —
opencode's own updater, not the plugin. The update check runs on every TUI
launch and compares the installed binary against opencode.ai's update service
(`https://opencode.ai/update/api/...`), which serves the 2.x line (e.g.
`current=2.0.8 latest=2.0.11`). The GitHub releases page only lists the older
1.x train (`v1.18.x`), so a real 2.x update can look "unofficial" there. The
harness plugin has zero influence on this: it is a single auto-loaded `.ts`
file — the binary, `cli.json`, `service.json`, `auth.json`, the npm cache and
all update/version state are never touched (verified by an A/B: the logged
check is byte-identical with the plugin installed vs removed, and the banner
predates the plugin's first install). Silence it with
`OPENCODE_DISABLE_AUTOUPDATE=1` (checked by opencode itself), or take the
update with `opencode upgrade`.

## Verify

- `harness doctor` reports your user model, the plugin file, and opencode presence.
- A long chat: when the session compacts, the recalled memory brief is applied.
- `python scripts/opencode_smoke.py` (CI runs this too): boots a real
  `opencode serve`, forces activation, asserts the plugin is `active` with the
  11 bundled skills seeded.
