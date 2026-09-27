# Harness — status

`python -m pytest -q` → **155 passed** (`HARNESS_MOCK=1`, as CI runs it).
The headless CLI path was retired (9 modules + 4 commands deleted; live features run in the
plugin) — history lives in `git log`; this file is current state only.

## What it is

Python core + one stock-opencode v2 plugin (`harness/plugin/{harness.ts,tui.tsx}`). No fork, no
relay, no API keys in harness: models come from your opencode config, and every agent inherits your
default. 2.3k lines across 12 modules, stdlib-only, SQLite for state.

## Current state (2026-09-23)

**Todos have a home again, and it is the harness's.** opencode 2.0.14 ships no todo tool and writes
no `todo` rows, so the sidebar's Todo row could only ever read `0 items`. The plugin now registers
`todowrite`/`todoread` as direct tools, persisting into the harness todo space
(`<project>/.opencode/harness/sessions.db` → `todos`, keyed by the **opencode** session id) — the
same table the sidebar reads and the compaction brief carries, so the plan is one list instead of
three private copies. The compaction brief now carries the still-open items too.

Proven on the real thing, not in a stub: `opencode run "…call todowrite…"` → the model's tool call
landed as rows in that table under the run's session id, and the panel painted them off a live pty
capture:

```
▾ Models               1 used · 6 providers      ▾ Todo                          3 items
    opencode · 75 models                           ● probe the live panel paint
    muse-spark-1.3-cont… · 1 step                  ◐ write todos into the harness …
                                                   ○ confirm the Models row populates
harness · 2 expanded
```

**The panel opens several rows at once, and the Todo row opens itself.** `open` became a list of row
ids (verified by clicking a real row over the pty: Models and Todo both `▾`, footer `2 expanded`).
The Todo row auto-expands whenever its list appears or changes; a manual collapse sticks until then.
When the list is the project's rather than this session's, the value says so (`2 items · project`)
rather than passing off another session's plan as this one's.

**Workers are visible.** A `Workers` row reads the `workers` table out of the same `sessions.db`
— project-wide. The headless spawn pool is retired; live workers are opencode `task` children. The value is the live
occupancy (`1 active` / `idle` / `none`), from a `count(*)`, and the details are the newest rows with
their age (`◐ map-auth · running 3m`). It deliberately does not re-derive `stale`: that rule is
`doctor`'s (`budgets.worker_timeout_s` × 2), and a second copy would drift from it.

**Agents keep the plan current.** `AGENTS.md` gained a Todo section (one `in_progress`, tick on
evidence not intent, `todoread` before re-planning) and three bundled skills now say where the list
goes: `planning-and-task-breakdown` writes it with `todowrite`, `incremental-implementation` moves
the box per slice, `using-agent-skills` names the list as the shared plan. Also fixed on the way: that
planning skill still pointed at `.harness/plans/` — a path retired in favour of
`.opencode/harness/plans/`.

**The Window bar is a gauge, not a lifetime odometer — and the orchestrator routes by role.** The
row summed the session's tokens against the context window, so the bar pinned at 100% early in a
session: opencode's own header reads the LAST assistant message (`usage.Output > 0`; a compaction
summary counts output only), and the panel now does the same, while the Tokens row keeps the
lifetime total. And the orchestrator could only spawn opencode's built-in `general` subagent,
because nothing else was registered: `opencode.json` now ships `explore`, `librarian`,
`plan-consultant`, `plan-reviewer`, `code-reviewer`, `test-engineer`, `security-auditor`
(`mode: subagent`, an own prompt each, edit-denied except the test engineer, `task: deny` so depth
stays 1) and the orchestrator prompt routes by `subagent_type` (the retired Python RLM path used to
give each of those kinds its own brief too, instead of one generic worker prompt).

**Models populates now.** The walk was inside the once-per-session slow scan, reading a message
store that is empty until its own `sync()` lands — so `Models` sat at `0 used` for the whole session
unless you changed sessions or ran `/harness-refresh`, and every row claimed `1 step`. It runs on
every load, reports real step counts, falls back to the selected model before anything is sent, and
counts a provider's models from the **model** store (a `Provider.Info` entry has no `models` map, so
`opencode` printed bare).

**CI and release run in seconds, and cannot hang.** The old `opencode-live` job hung for 32 minutes
and was cancelled by hand: the smoke script did a blocking `readline()` on `opencode serve`'s banner,
which never arrives when stdout is not a TTY. It now drains the pipe on a thread, has a boot deadline
(`--boot-timeout`), fails fast with the server's own last output, and the job is push-only with a
6-minute cap. `concurrency` cancels superseded runs. The release workflow no longer runs the test
suite (the tag points at a commit CI already tested) or installs Python at all — it checks the assets
it builds, publishes, and verifies them.

**The sidebar panel reads like part of opencode.** Driving the real TUI (opencode 2.0.11, pty +
terminal emulator) showed the panel frozen on `harness · no session` with every stat at zero and a
`scanning…` that never cleared — while a live probe proved the sidebar slot receives the active
session id and the data was loaded. Two real defects, both fixed and both pinned by tests:

1. **A store write does not repaint the screen.** The slot re-rendered (`rev` 0 → 4, session id,
   14 skills) but nothing told opencode to redraw, so the first all-zero paint stayed. After every
   state write the panel now asks for a repaint (`ctx.renderer.requestRender`, deferred through a
   timer so it cannot re-enter the render that is applying it).
2. **A `full` load could be dropped.** The session id arrives with the first slot render — while the
   setup-time load is still in flight — and the early return there left the session-less snapshot in
   place for good. It is queued now, and every scan is time-boxed, so a store `sync()` blocking on the
   network can no longer strand the panel.

Painted text read back from the live pty stream confirms `Tokens 11.8k · $0.0000`,
`Agents 11 available` and `Window █░░░░░░░ 1%` (the bar always shows a cell for non-zero usage). The
rows use opencode's own geometry and theme keys, with the label left and the value pinned right, and
there is no "Context" row — opencode already renders one, and the duplicate is what made the panel
look like a foreign overlay rather than part of the app.

**Memory works by itself.** Recall was matching a turn as one phrase, so it almost never returned
anything, and nothing wrote facts in the first place. Now every live turn captures its durable
lines (decisions, root causes, blockers, outcomes) into deduped, capped facts plus a progress
note, via the plugin context hook; recall is term-based with an FTS5 pass over transcripts;
credentials are refused at the door. A lesson with two distinct evidence excerpts auto-promotes
into `MEMORY.md` (the author's `/refine` rule with the approval step automated by the evidence).
(The retired headless ledger used to close a plan box on a verified turn; todos are the plan now.)
See `docs/memory.md`.

**Approval means "this is dangerous".** Shell permissions are generated from `harness/risk.py`, so a
grep, a diff or a test run never prompts and `git push`/`rm -rf`/`DROP TABLE`/deploys always do, each
with a reason a human can act on. Applied to the live
`~/.config/opencode/opencode.json` (94 ask rules behind a permissive default). See
`docs/security.md`, `docs/config.md`.

**RLM retired.** Workers used to run in a headless pool sized by `budgets.max_parallel`, reaching a
terminal status on every path (`done|error|timeout|stale`), posting once to a delivered-once
mailbox and writing `workers/<id>/result.md` (verified live and hermetically end to end at the
time). Live sessions route subagents through opencode's native `task` tool instead; the `workers`
table and its terminal-state readers remain behind `harness doctor` and the sidebar. See
`docs/rlm.md`.

**Ponytail is bundled and active.** The laziness ladder now lives in `AGENTS.md` (the one instruction
file, which absorbed `SOUL.md` and `CONSTRAINTS.md`) and as three seeded skills: `ponytail`,
`ponytail-review`, `ponytail-audit` (MIT, attributed in `harness/data/skills/NOTICE.md`).

**Dead weight removed this pass:** `reasoning.py` (nothing imported it), the whole `usage` table and
its three helpers plus `orchestrator.classify` (test-only callers), and the config keys nothing read
(`agents`, `tui`, `mcp.max_servers`, `security.allow_remote_sec`, per-category payloads). `categories`
became the validated intent list, so `spawn(category="quickk")` was refused instead of producing
a worker with an unknown kind (the headless spawn path has since been retired).

## Caveats

1. **Live sessions need a model.** `opencode.json` on this machine has no `model` key, so
   `resolve_model` reports "missing" and `doctor` exits non-zero. Models are picked in the opencode
   TUI, or set `"model"` / `HARNESS_MODEL` explicitly.
2. **The todo space is written by the plugin, not by opencode.** With the plugin removed, no tool
   writes it; the rows already there stay readable.
3. **`stale` is computed, never persisted.** The headless `rlm.prune_stale` writer is retired;
   readers (`harness doctor`, the sidebar) derive it from status plus age, so a dead worker reads
   `◐ … · running 42m` rather than a verdict the panel invented.
4. **Hook payloads are runtime facts, not documented types.** Compaction and `execute.after` payloads
   were verified against opencode 2.0.14 and are wrapped in try/catch, so a payload change degrades
   to a no-op instead of a broken turn.
5. **`sessions.db` in a repo is runtime state**, gitignored, under `<project>/.opencode/harness/`.
6. **Nothing here is pushed.** The working tree on top of the last commit is uncommitted.

## Verify

```bash
HARNESS_MOCK=1 python -m pytest -q          # 155
sqlite3 .opencode/harness/sessions.db "select session,status,text from todos order by id desc limit 5"
python -m harness doctor --verbose          # plugin, memory, workers, permission policy
python -m harness memory show               # current MEMORY.md
python -m harness skills list | grep ponytail
```
