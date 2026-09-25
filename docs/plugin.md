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
   `skills_list`/`skill_view`/`memory_recall` plus `todowrite`/`todoread` as
   *direct* tools (`options: { codemode: false }`; the v2 default is
   Code-Mode-only, where a by-name call fails with "No tool named … is currently
   available"). They read local disk + SQLite directly (no gateway, no extra
   process). `todowrite`/`todoread` are the plan tools opencode 2.x no longer
   ships, backed by the harness todo space (below).
3. **Output condensing** — the `ctx.tool.hook("execute.after")` hook collapses
   oversized tool results in place (errors pass through untouched).
4. **Compaction memory** — the `ctx.session.hook("compaction")` hook recalls
   project facts locally and appends them, together with the session's still-open
   todos, to the summarization request — so a plan that only lived in the
   transcript is not what a compaction destroys.

The loader only accepts a default-exported object with a string `id` plus an
`effect` or `setup` function, and `setup` must return a cleanup function or
nothing — any other returned value is called as a cleanup function and kills
plugin activation. The file therefore has no imports and returns nothing; see
`harness/plugin/README.md` for the full contract.

## The todo space (one plan, shared)

opencode 2.0.14 ships **no todo tool**, and its `todo` table is legacy: nothing
past 2.0.13 writes it. A session's plan therefore had nowhere to live, and the
sidebar's Todo row could only ever read `0 items`. Harness gives it one place:

| who | how |
| --- | --- |
| the model | `todowrite` (replaces this session's list) and `todoread` |
| the sidebar panel | reads the same table — this session's list, else the newest in the project |
| the compaction brief | carries the still-open items into the summary |
| `harness compact` | already read it (`compact.py` keeps todos across a compaction) |

The table is the one the Python core keeps —
`<project>/.opencode/harness/sessions.db` → `todos(id, session, text, status,
ts)` — keyed by the **opencode** session id, so the model's plan, the panel and
the Python core read one list instead of three private copies. The server half
creates the table on first write, so the plugin works on a project where the
`harness` CLI has never run.

**Write-through to opencode's own store.** Every `todowrite` is *also* mirrored
into opencode's own `todo` table (`session_id, content, status, priority,
position, time_*`) in opencode's data DB — the storage opencode's own todo tool
wrote before 2.0.14 and what anything reading `opencode.db` still expects. The
harness space stays the source of truth (the panel, the Python core and the
compaction brief read it); the mirror is best-effort by design: it resolves the
DB the way opencode does (`OPENCODE_DB`, else `$XDG_DATA_HOME/opencode/`),
never creates a foreign database, and a busy or missing one is a log line —
never a failed tool call. `priority` (high/medium/low) is accepted in the tool
schema and travels with the mirror.

Verified end to end on a real 2.0.14 server: `opencode run "…call todowrite…"`
→ the tool call lands as rows in that table under the run's session id, and the
panel paints them.

```bash
# what the space holds for this project
sqlite3 .opencode/harness/sessions.db "select session, status, text from todos order by id desc limit 10"
```

## TUI views (a stats panel in the sidebar)

`tui.tsx` is the plugin's TUI half. It fills **opencode's existing sidebar** with
a stats/info panel and adds a small footer chip — it deliberately does not
rearrange the TUI: no routes, no docked overlay, no replaced slots.

| view | slot | what it shows |
| --- | --- | --- |
| chip | `home.footer.status` | `harness · 12% ctx · $0.03 · 5.9m tok` — window pressure first, spend second, the (ever-growing) lifetime total demoted; click toggles the sidebar (with no session yet it reads `harness · click for stats`, since the skill/fact stores are location-scoped and empty at the default location) |
| stats panel | `sidebar.content` | the rows below |
| hint | `sidebar.footer` | `harness · click a row` |

```
ctrl+g              toggle the stats sidebar (palette: "Harness: toggle stats sidebar")
/harness            same, from the prompt (/hp is an alias)
/harness-refresh    re-scan everything now (/hr)
click a row         expand/collapse that row's detail lines (several at once)
```

The layout is built out of opencode's own row grammar — a label in
`theme.text.base` on the left, its value in `theme.text.muted` pinned to the
right edge (`flexGrow` on the label, `flexShrink 0` on the value, the same
geometry opencode's MCP rows use). That keeps the numbers aligned at any panel
width, with no width constant to guess:

```
harness ses_f368b7d9 · 14 skills
▾ Window                    █░░░░░░░ 12%
    128,451 / 1,048,576 in context
    muse-spark-1.3-cont… · agent orchestrator
▸ Tokens                 5.9m · $0.0300
▾ Models              2 models · 2 providers
    opencode:
      muse-spark-1.3-c    39 steps
    kilo:
      kilo-auto-free      12 steps
▾ Todo                    1/4 done · project
    ● read the panel contract
    ◐ fix the Models row
    ○ add the Workers row
▾ Workers                      2 active
    ◐ map-auth · running 3m
    ○ docs-pass · queued 4s
▸ Skills                      14 installed
    ▪ using-agent-skills
    ▪ planning-and-task-breakdown
    … +9 more
▸ Agents                     11 available
    ● orchestrator
    ◦ general
    … +6 more

harness · 3 expanded
```

A row with nothing in it **does not render**: Todo, Workers and Memory hide
entirely when empty (a permanent `Workers none` / `0 facts` was clutter, not
state), and any list that outgrows the five-row detail budget ends with
`… +N more`, so a count can never advertise more than the list shows.

Any number of rows can be open at once. The **Todo row opens itself** whenever
its list appears or changes (the list is the point of the section; a collapsed
`1/4 done` hides the plan the model is following), and a manual collapse sticks
until the list changes again.

Every value is read the same way opencode reads it, so the panel agrees with the
app's own readout: total tokens = in + out + reasoning + cache read + write,
cost from `session.cost(sid)`. Usage spans the `task` tool's **subagent
sessions** too — a child is a separate `Session.Info` with its own tokens, so
the panel discovers the family (`session.sync(sid, {children:true})` +
`session.family(sid)`, the same set opencode's `session.cost` sums) and merges
its rows; the Tokens detail then names what the workers contributed
(`+ 2 subagents · 95,412 tok`). The **Window stays this session's own**: one
count belongs to one context window. There is deliberately **no "Context" row** —
opencode's sidebar already renders one at the top, and a second copy is what made
the panel look like an overlay bolted onto the app instead of part of it.

| row | rows when expanded | source |
| --- | --- | --- |
| header | — | session id, non-zero skill/fact counts (`data.session.get(sid)`; a zero count is omitted, never printed as `0 facts`) |
| Window | `used / limit in context`, model · agent — value tinted `feedback.warning` at ≥ 80% | `session.tokens` + the provider's `models[id].limit.context`; `used` is the **last assistant message** (opencode's header rule), not the lifetime sum |
| Tokens | `input · output`, `reasoning · cache` (a `cache write` line only when non-zero; a `+ N subagents · M tok` line when subagents spent anything) — no cost line: the value already carries it | `session.get(sid).tokens` **merged over `session.family(sid)`** (this session + its `task` children — the single-row read was the "not counting sub-agents" bug), cost from `session.cost(sid)` (already family-summing) with a hand-summed fallback |
| Models | per **used** provider a bare `name:` header (no catalog counts), then aligned `model … N steps`; value = models/providers this session actually routed through (`not used` before the first message, whose `· selected` fallback row is not counted as usage) | assistant messages of this session **and of its subagent sessions** grouped by provider/model — re-read on **every** pass, so it fills as the message store does |
| Todo | value is progress, `1/4 done` (+ `· project` for a fallback list); rows are `● completed ◐ in_progress ○ pending ✕ cancelled` + text — this session's list, else the project's newest — **opens itself** when the list changes; **hidden when empty** | the harness todo space, read-only |
| Workers | `◐ running ○ queued ● done ✕ error ! timeout ~ stale` + worker name + age of its last update, newest first; value is live occupancy (`2 active` / `idle`); **hidden when empty** | the `workers` table in the same `sessions.db` — **project-wide, not session-scoped**: `rlm.spawn` records the row and leaves `workers.session` empty, so a worker belongs to the project, not to the opencode session that asked for it. The active count comes from SQL, not the six displayed rows (a long worker can sit outside the newest ones). Deliberately no `stale` verdict here — `harness doctor` owns that rule (`budgets.worker_timeout_s` × 2) and a second copy would drift; the age is printed instead (`◐ map-auth · running 42m`) |
| Skills | the bundled harness skills, `… +N more` past five | `location.skill` after `sync()` |
| Agents | the registered agents — opencode's internal `compaction`/`title` plumbing agents are filtered out, the active agent is listed first with `●`, `… +N more` past five | `location.agent` after `sync()` |
| Memory | durable facts for the project — value is a real `count(*)`; **hidden at zero** | the same `sessions.db` the server half uses |

A usage bar always shows at least one cell for non-zero usage: 1% of eight cells
rounds to zero, and an empty bar next to `1%` reads as a broken panel. The
in-flight marker is a single `⋯` in the header, never a sentence.

Skill rows drop the shared namespace: the store names them
`harness-spec-driven-development` (that is their id), and the row shows
`spec-driven-development` because the sidebar is 42 columns wide and every row
would repeat `harness-`. Detail lines are kept to 34 cells for the same reason:
one more and opencode middle-truncates the row it is already showing
(`◐ write todos i...the harness space`).

**"The side panel is empty"** — the sidebar renders *only plugin
contributions*, so with nothing claiming `sidebar.content` it is genuinely
empty; harness now fills it (re-run `harness plugin install` on an older
install). If your opencode hides the sidebar, the palette has `Show sidebar`
(`ctrl+x` then `b`). Note opencode puts its own context block at the top of the
sidebar — ours sits underneath it.

Implementation notes worth keeping (all probed against opencode 2.0.11):

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
  so a store-wide count above the harness rows listed reads like a bug), and a failed load is
  shown in the panel because cli-side `console.error` never reaches the log.
- **The in-flight placeholder reads a reactive flag, never the `loading` guard.**
  `loading` is a plain variable, so a tracked expression reports whatever it held
  at that repaint — that is how `scanning…` stayed on screen next to fully
  loaded stats. `view.scanning` is set when a scan starts and cleared in the same
  state update that bumps `rev`, so the placeholder cannot outlive its scan.
  Scans are additionally time-boxed (`settle(p, SCAN_MS)`): a store `sync()` can
  block on the network, and one unreachable provider registry must not strand the
  panel on its placeholder.
- **A state write must ask for a repaint (`ctx.renderer.requestRender`).** The
  rows come from a plugin store and from plain reads — neither is a host signal —
  so nothing tells opencode to redraw the sidebar for them. Measured on a real
  session: the store updates landed (`rev` 0 → 4, with the session id and 14
  skills) while the screen kept its first paint — an all-zero panel with a `⋯`
  that never cleared. The request is deferred through `setTimeout(…, 0)`: asking
  for a render from inside the update a render is already applying is how a
  repaint goes re-entrant. Verified by reading the painted text back out of the
  pty stream (which is the only reliable witness — a screen-scraper that models
  the cell grid lies once opencode does a full repaint).
- **A `full` load is queued, never dropped.** The session id arrives with the
  first slot render, i.e. while the setup-time load (no session id yet) is still
  in flight; returning early there left the panel on its session-less,
  all-zero snapshot for good. `pendingFull` re-runs it when the current load
  ends.
- **The Models rows are built on every load, not once per session.** The message
  store starts empty and is filled by `message.sync()`, which runs at the end of
  a load (see below) — so a walk that only ran inside the once-per-session slow
  scan froze `Models` at `0 used` for the whole session. The walk is a local
  in-memory read, so it runs on every pass, and one nudge per session
  (`setTimeout(load, 500)`) picks the rows up with the session instead of up to
  `POLL_MS` later.
- **The 8s poll stands down when nothing is on screen.** opencode loads this
  entrypoint in the long-lived server process too, where no slot ever renders;
  polling there meant a session message walk + four store syncs + two SQLite
  reads every 8s with nobody watching. Every slot render stamps `lastRender`,
  and the poll skips once nothing has rendered for ~32s (it resumes the moment
  the panel is drawn again). What a scan reads: session tokens/cost/agent/model,
  the session's assistant messages (Models rows), the lazily-synced location
  stores (skill/agent/model/provider), the harness todo space and the harness
  facts DB — measured at ~4ms warm, which is why the placeholder is the thing
  worth watching, not the cost.

## What the plugin does NOT do (those come from opencode.json)

Agents are merged into `~/.config/opencode/opencode.json` by
`harness plugin install`/`harness tui` (`ensure_opencode_config`), not by the
plugin:

- `agent` — `orchestrator/ask/debug/review` + native `plan`, plus the
  `mode: subagent` specialists the orchestrator routes to (`explore`,
  `librarian`, `plan-consultant`, `plan-reviewer`, `code-reviewer`,
  `test-engineer`, `security-auditor`), with modern `permission` maps
  (auto-approve compatible). No `model` keys: every agent
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

**`[user_blocked] Your access has been restricted due to repeated policy
violations`** — your *account* was restricted by the model provider; opencode
only relays it (`Upstream request failed`). It is not a harness error and
nothing in the plugin can lift it — it is a known failure on free-tier models
where the provider's output filter flags repeated generations (security- or
crypto-heavy tasks hit it often). Every subagent inherits your default model,
so each spawn fails the same way until you switch: `/models` (`ctrl+x m`) for
a different model or `/connect` for a different provider/key, or contact the
provider about the restriction.

**The sidebar Window row shows 100% with more tokens than the context limit**
(e.g. `5,927,538 / 1,048,576 of window`) — an old plugin build: it divided
the session's *lifetime* tokens by the context limit, which saturates forever
after the first exchanges. Re-run `harness plugin install`, restart opencode,
and confirm with `harness doctor` (no `STALE` line). Fixed builds show the
**last assistant message's** tokens — opencode's own context readout — so the
number rises and falls with the conversation.

**"OpenCode's free tier can only be used from within OpenCode"** — zen's
server-side gate, not a plugin bug. The free-tier endpoint accepts a request
only if its tool list advertises a tool literally named `shell` (verified by
replaying captured requests: `shell` present → 200, renamed/fake tool or only
read tools → `FreeTierError`). opencode only advertises `shell` when the
agent's `permission.bash` is not `deny`, so **any** agent that denies bash —
including a read-only one — makes every free model 403.

Harness used to ship its read-only agents (`ask`, `review`) with
`"bash": "deny"`, which is why free models failed under them. Bash is now
always allowed at some level — the shell tool stays advertised — while the
*granular rule map* decides what actually prompts: `harness plugin install`
writes `permission.bash` from `harness/risk.py` (safe commands run, destructive
ones ask), so reads and test runs never interrupt and `edit: deny` still keeps
writes off. An existing install is migrated from the old bare `"deny"`/`"ask"`
string and the change is reported on stderr; a permission object you wrote
yourself is never touched. If a custom agent still trips this, give it
shell-capable permissions and let the generated map gate it, or use a paid key.

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

**opencode's version not visible in the footer?** — that one *was* the plugin,
and it is fixed. opencode's home footer renders its own version text to the
RIGHT of the `home.footer.status` slot with `flexShrink: 0`; the harness chip
inside that slot did not shrink, so past a certain headline width it pushed
opencode's version off the right edge — the app stopped showing its own actual
version while the chip stayed. The chip now carries
`flexGrow=1 flexShrink=1 minWidth=0`: it truncates and the version keeps its
place. Harness itself renders no version anywhere and writes no `version` key
into `opencode.json` (pinned by a test), and `harness doctor` reports what
opencode's own binary says (`opencode_version: opencode v2.0.14`) so any
mismatch you see elsewhere is instantly attributable.

## Verify

- `harness doctor` reports your user model, plugin freshness (installed vs
  shipped bytes), the harness agents in `opencode.json`, opencode's own
  version straight from the binary, and the effective LSP state (`enabled /
  overridden / disabled / unset` for this project's config).
- A long chat: when the session compacts, the recalled memory brief is applied.
- `python scripts/opencode_smoke.py` (CI runs this too): boots a real
  `opencode serve`, forces activation, asserts the plugin is `active` with the
  the bundled skills seeded.
