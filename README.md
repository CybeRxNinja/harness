# Harness — all-in-one AI coding harness (v0.1 prototype)

Python core + a single opencode v2 **server plugin**. Standard-library-only
core. Potato-PC safe: <150MB idle, SQLite only, 2 parallel workers.

No fork, no custom binary, no relay: Harness runs as a plugin inside
**stock opencode**, seeding built-in skills and recalling durable memory when
opencode compacts a session. Models always come from **your** opencode
providers — every agent inherits your configured default model.

## Install (stock opencode)

```bash
pip install -e .            # or: curl -fsSL https://raw.githubusercontent.com/CybeRxNinja/harness/main/install.sh | bash
python -m harness setup     # seed skills, verify your model + opencode binary
python -m harness plugin install   # -> ~/.config/opencode/plugins/harness/{server.ts,tui.tsx}
opencode                  # stock opencode; set "model" in opencode.json first
```

`harness chat` works with zero model access (MOCK echo); with your model
configured it runs for real via `opencode run`.

First launch from a project dir wires agents (`orchestrator/ask/debug/review/
plan`, no model pins) into `~/.config/opencode/opencode.json` — backed up,
never clobbered. Sessions persist per directory under
`<project>/.opencode/harness/`.

## What lives where

```
<project>/.opencode/harness/   sessions + FTS memory, MEMORY.md, kernel,
                                workers, runs, checkpoints, plans/boulder/ledger,
                                project skills, project config, backups
~/.config/opencode/            opencode.json (merged agents + generated shell
                               policy) + plugins/harness/{server.ts,tui.tsx}
~/.harness/                    global skills/bundles, user config
```

## CLI (same brain, no TUI)

```
harness chat "fix failing tests" --mode orchestrator --model provider/model
harness tui                  # config + plugin, then launch opencode
harness skills list|view|pending|approve|reject
harness memory search|save|refine
harness checkpoint save|restore
harness plan start|next|check
harness config get|set|show [--scope user|project]
harness doctor [--verbose]
```

Modes: `code orchestrator plan ask debug review`. Models: explicit
`provider/model`, else your opencode default (`harness doctor` shows it).

## The plugin (`harness/plugin/harness.ts`)

A single stock-opencode v2 plugin (no fork), default-exported as
`{ id, setup(ctx) }`. Once installed it:

- seeds bundled skills into opencode's skill store (`ctx.skill.transform`);
- exposes `skills_list`/`skill_view`/`memory_recall` as native tools
  (`ctx.tool.transform`);
- condenses oversized tool outputs in place (`ctx.tool.hook("execute.after")`);
- on compaction (`ctx.session.hook("compaction")`), appends a recalled memory
  brief to the summarization request so durable project facts survive a
  condensed transcript.

Harness agents come from `opencode.json` (merged by `harness plugin install`/
`harness tui`); skills surface as native plugin tools, no MCP hop.
See `docs/plugin.md`.

## Repo layout

```
harness/            loop, kernel(RLM), rlm(spawn), models(opencode backend),
                    risk(permission policy), compact, orchestrator, store,
                    memory, skills, mcp, checkpoints, doctor, cli
harness/data/skills bundled skills (ladder/router/review packs), NOTICE.md
harness/plugin/     opencode v2 server + TUI entrypoints (harness.ts, tui.tsx)
docs/               one page per feature (start at docs/quickstart.md, docs/plugin.md)
scripts/            optional external skill-pack installers (Addy / reverse-router)
install.sh          CLI + plugin installer (no binary)
```

## Docs

- `docs/quickstart.md` `agents.md` `rlm.md` `skills.md` `memory.md` `plugin.md`
- `docs/config.md` `security.md` `mcp.md` `tui.md` `compression.md`
- `AGENTS.md` (persona, build discipline, operating rules) · `MEMORY.md` (durable lessons)
