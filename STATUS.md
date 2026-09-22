# Harness — status

`python -m pytest -q` → **197 passed** (`HARNESS_MOCK=1`, as CI runs it).
`python scripts/opencode_smoke.py` → **SMOKE OK**: plugin active with `features.tui`, 14 bundled
skills seeded on a real opencode server. History lives in `git log`; this file is current state only.

## What it is

Python core + one stock-opencode v2 plugin (`harness/plugin/{harness.ts,tui.tsx}`). No fork, no
relay, no API keys in harness: models come from your opencode config, and every agent inherits your
default. 3.9k lines across 18 modules, stdlib-only, SQLite for state.

## Current state (2026-09-22)

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
anything, and nothing wrote facts in the first place. Now every turn extracts its durable lines
(decisions, root causes, blockers, outcomes) into deduped, capped facts plus a progress note; recall
is term-based with an FTS5 pass over transcripts; credentials are refused at the door. A lesson with
two distinct evidence excerpts auto-promotes into `MEMORY.md` (the author's `/refine` rule with the
approval step automated by the evidence), and a verified turn closes the active plan box. See
`docs/memory.md`.

**Approval means "this is dangerous".** Shell permissions are generated from `harness/risk.py`, so a
grep, a diff or a test run never prompts and `git push`/`rm -rf`/`DROP TABLE`/deploys always do, each
with a reason a human can act on. `risk_check` is allowed in every mode. Applied to the live
`~/.config/opencode/opencode.json` (94 ask rules behind a permissive default). See
`docs/security.md`, `docs/config.md`.

**RLM confirmed.** Workers run in a pool sized by `budgets.max_parallel`, reach a terminal status on
every path (`done|error|timeout|stale`), post once to a delivered-once mailbox, write
`workers/<id>/result.md`, and record what they did as a fact. Verified live: spawn → running → error
with the backend's real message recorded (the model id in my test env does not exist on this box);
hermetically verified end to end including a 4-wide wave. See `docs/rlm.md`.

**Ponytail is bundled and active.** The laziness ladder now lives in `AGENTS.md` (the one instruction
file, which absorbed `SOUL.md` and `CONSTRAINTS.md`) and as three seeded skills: `ponytail`,
`ponytail-review`, `ponytail-audit` (MIT, attributed in `harness/data/skills/NOTICE.md`).

**Dead weight removed this pass:** `reasoning.py` (nothing imported it), the whole `usage` table and
its three helpers plus `orchestrator.classify` (test-only callers), and the config keys nothing read
(`agents`, `tui`, `mcp.max_servers`, `security.allow_remote_sec`, per-category payloads). `categories`
is now the validated intent list, so `spawn(category="quickk")` is refused instead of producing a
worker with an unknown kind.

## Caveats

1. **`harness chat` needs a model.** `opencode.json` on this machine has no `model` key, so
   `resolve_model` reports "missing" and `doctor` exits non-zero. Models are picked in the opencode
   TUI, or set `"model"` / `HARNESS_MODEL` explicitly. `HARNESS_MOCK=1` needs no model.
2. **Nothing here is pushed.** Four local commits predate this pass; the working tree on top of them
   is uncommitted.
3. **Hook payloads are runtime facts, not documented types.** Compaction and `execute.after` payloads
   were verified against opencode 2.0.11 and are wrapped in try/catch, so a payload change degrades
   to a no-op instead of a broken turn.
4. **`sessions.db` in a repo is runtime state**, gitignored, under `<project>/.opencode/harness/`.

## Verify

```bash
HARNESS_MOCK=1 python -m pytest -q          # 197
python scripts/opencode_smoke.py            # plugin active, skills seeded
python -m harness doctor --verbose          # plugin, memory, workers, permission policy
python -m harness memory show               # current MEMORY.md
python -m harness skills list | grep ponytail
```
