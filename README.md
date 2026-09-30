[![ci](https://github.com/CybeRxNinja/harness/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/CybeRxNinja/harness/actions/workflows/ci.yml)
[![release](https://github.com/CybeRxNinja/harness/actions/workflows/release.yml/badge.svg)](https://github.com/CybeRxNinja/harness/releases)
[![tests](https://img.shields.io/badge/tests-195%20passing-brightgreen)](.github/workflows/ci.yml)
[![license](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![release](https://img.shields.io/github/v/tag/CybeRxNinja/harness?filter=plugin-v*&label=plugin)](https://github.com/CybeRxNinja/harness/releases/latest)

# Harness — a plugin for opencode, not a fork

Harness turns **stock opencode** into a harness-style agent: an orchestrator that
delegates, plans, and verifies — plus durable memory, evidence-gated lessons,
risk-based permissions, a bundled skill library, and a native stats sidebar.
One plugin directory, two entrypoints, **no fork, no relay, no custom binary,
no API keys**: models always come from *your* opencode config, and every agent
inherits your default model.

```
you ──> stock opencode ──> harness plugin (server.ts + tui.tsx)
                                ├─ native tools: skills_list / skill_view / memory_recall + todowrite / todoread / wait
                                ├─ compaction hook → durable memory and open todos survive summarization
                                ├─ orchestrator agent + native task workers, todos, waits
                                └─ sidebar panel: Window · Tokens · Models · Todo · Workers · Waits · Skills · Agents · Memory
```

Potato-PC safe: stdlib-only Python core, SQLite state, <150 MB idle, at most
2 parallel workers per wave (orchestrator discipline).

## Proof of work

Everything below is verified in CI (`ci.yml` gates on `HARNESS_MOCK=1 pytest`)
and re-runnable locally (the former live smoke is removed):

| claim | proof |
| --- | --- |
| plugin loads and activates on stock opencode | formerly proven by a live smoke booting `opencode serve` and asserting `status == "active"` → `SMOKE OK` |
| TUI half is real (panel, chip, commands) | the smoke asserted `features.tui` — set only when a `tui.tsx` entrypoint is present |
| 19 bundled skills seeded into opencode's skill store | the smoke counted them against a live server |
| plugin is visible in the Plugins panel | the TUI panel filters on `features.tui`; the directory install ships both entrypoints |
| memory remembers without being asked | `tests/test_memory_auto.py`: durable facts + progress capture; 2-evidence lessons auto-promote to `MEMORY.md`; credentials refused (live sessions capture via the plugin context hook) |
| dangerous commands ask, safe ones don't | `tests/test_risk.py`: 94 ask-rules generated from `harness/risk.py` (`git push`, `rm -rf`, `DROP TABLE`, deploys) — greps, diffs, fetches, tests never prompt |
| worker rows reach a terminal state | `tests/test_rlm.py`: `is_terminal` / `worker_timeout` readers behind `harness doctor` (live routing is opencode's native `task` tool) |
| entrypoints never break a boot | `tests/test_plugin.py` transpiles both files with `Bun.Transpiler`; bad `setup()` returns are pinned by tests |
| the model's plan lands in the harness todo space | verified on a real server: `opencode run "…call todowrite…"` → rows landed in `<project>/.opencode/harness/sessions.db` under the run's session id, painted by the panel off a real pty capture |
| 190 tests, no third-party deps | `HARNESS_MOCK=1 python -m pytest -q` |

Latest release: **[`plugin-v0.15`](https://github.com/CybeRxNinja/harness/releases/tag/plugin-v0.15)**
— `--from-release` assets are byte-identical to the checkout, a path last verified
end-to-end on `plugin-v0.11` (`harness plugin install --from-release latest` → live
smoke passed).

## What the plugin does

Installed into `~/.config/opencode/plugins/harness/` and auto-loaded by
opencode v2:

- **Skills** — 19 bundled skills seeded into opencode's skill store
  (`ctx.skill.transform`): build ladder (incl. **ponytail**, MIT), review,
  audit, spec-driven development, security, and more.
- **Native tools** — `skills_list`, `skill_view`, `memory_recall`,
  `todowrite`/`todoread`, `wait` registered through `ctx.tool.transform` (no
  MCP hop): `todowrite`/`todoread` are the plan tools opencode 2.x dropped,
  backed by the harness todo space (`<project>/.opencode/harness/sessions.db`
  → `todos`) — the same list the sidebar reads and the compaction brief
  carries; `wait` takes `{label, timeout_s, hint?}` and returns an expiry
  nudge to check the task and act. `AGENTS.md` and three bundled
  skills (`planning-and-task-breakdown`, `incremental-implementation`,
  `using-agent-skills`) tell the agents to keep it current on evidence.
- **Memory** — on compaction (`ctx.session.hook("compaction")`) a recalled
  FTS5 brief is appended to the summarization request, so decisions and root
  causes survive a condensed transcript; oversized tool outputs are condensed
  in place (`ctx.tool.hook("execute.after")`).
- **Agents** — `orchestrator/ask/debug/review/plan` plus the specialized
  subagents (`explore`, `librarian`, `plan-consultant`, `plan-reviewer`,
  `code-reviewer`, `test-engineer`, `security-auditor`) merged into
  `opencode.json` by the installer (backed up, never clobbered), no model pins:
  the orchestrator routes by role instead of falling back to the built-in
  `general`.
- **Risk-gated shell policy** — opencode permissions generated from
  `harness/risk.py`, so the model's advice and the host's enforcement are the
  same object: reads/fetches/tests run free, destructive commands ask with a
  reason.
- **Sidebar panel** (`tui.tsx`) — opencode's own row grammar and theme keys:
  Window (last-message context gauge), Tokens (+$), Models (provider + model + steps), Todo
  (auto-expands as the plan changes), Workers (live `task` children: occupancy plus the
  newest rows with age), Waits (pending `wait` countdowns, auto-expands), Skills, Agents,
  Memory; several rows can be expanded at once;
  footer chip `harness · 9.6k tok · $0.00` toggles it. Details and the loader/TUI
  traps this survives: `harness/plugin/README.md`.

## Install

```bash
# one shot (installs the CLI + plugin into stock opencode)
curl -fsSL https://raw.githubusercontent.com/CybeRxNinja/harness/main/install.sh | bash

# or from a checkout
pip install -e .
python -m harness setup            # seed skills, verify model + opencode binary
python -m harness plugin install   # -> ~/.config/opencode/plugins/harness/{server.ts,tui.tsx}
opencode                           # launch stock opencode, or: harness tui
```

Set `"model"` in `opencode.json` first (e.g. `"anthropic/claude-sonnet-4-5"`);
`harness doctor` shows what resolved (it errors when no model is configured).

No-release shortcut — the plugin files are self-contained:

```bash
harness plugin install --from-release latest   # fetch server.ts + tui.tsx from GitHub releases
```

## Install (for agents)

An agent asked to install the plugin should follow [`docs/install-for-agents.md`](docs/install-for-agents.md): preconditions, ordered install, the exact `harness doctor` lines that mean healthy, and a failure branch per symptom.

## What lives where

```
<project>/.opencode/harness/   sessions.db (facts, todos, workers, waits),
                               MEMORY.md, backups
~/.config/opencode/            opencode.json (merged agents + shell policy)
                               + plugins/harness/{server.ts,tui.tsx}
~/.harness/                    global skills, user config
```

## CLI (installer, doctor, and kept operators)

The Python side is what installs and inspects the plugin. The headless
brain (`harness chat`, `plan`, `checkpoint`, `mcp`) was retired (the stubs exit 2): live
sessions run inside opencode, where the plugin provides the same features
natively (task routing, compaction brief, context memory, `wait`):

```
harness tui                  # config + plugin, then launch stock opencode
harness plugin install [--from-release latest]
harness doctor [--verbose]   # plugin, memory, workers, permission policy
harness skills list|view|pending|approve|reject
harness memory search|save|refine|show|prune
harness config get|set|show [--scope user|project]
```

## Repo layout

```
harness/plugin/     the plugin: server + TUI entrypoints (harness.ts, tui.tsx) — the product
harness/            cli, memory, risk, rlm (worker-state readers), store, doctor, ...
harness/data/skills bundled skills (19, incl. ponytail — MIT, see NOTICE.md)
docs/               one page per feature (start at docs/quickstart.md, docs/plugin.md)
.github/workflows/  ci.yml (tests) · release.yml (plugin-v* → assets)
```

## Docs

`docs/quickstart.md` · `docs/plugin.md` · `docs/tui.md` · `docs/memory.md` ·
`docs/rlm.md` · `docs/security.md` · `docs/config.md` · `docs/skills.md` ·
`docs/agents.md` · `docs/mcp.md` · `docs/compression.md` ·
`AGENTS.md` (build discipline) · `MEMORY.md` (auto-promoted lessons)

## License

MIT — bundled ponytail skills keep their upstream notice in
`harness/data/skills/NOTICE.md`.
