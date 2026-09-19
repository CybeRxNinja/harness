# Harness — all-in-one AI coding harness (v0.1 prototype)

Python core + HTTP gateway + forked opencode TUI (CI-built) + CLI fallback.
Stdlib-only core. Potato-PC safe: <150MB idle, SQLite only, 2 parallel workers.

## Run it (recommended: zero-install AppImage)

Download `Harness_TUI-x86_64.AppImage` from
[releases](https://github.com/CybeRxNinja/harness/releases), then:

```bash
chmod +x Harness_TUI-x86_64.AppImage
cd /your/project
./Harness_TUI-x86_64.AppImage   # or: harness tui
```

First launch self-setups: merges the harness provider into `opencode.json`
(backed up, never clobbered), installs the Python CLI from GitHub, starts the
gateway rooted at your project, installs a launcher shortcut. Later launches
just run. No keys needed to start (MOCK/offline mode); add one to go live:

```bash
export OPENROUTER_API_KEY=sk-...   # or GROQ/CEREBRAS/NVIDIA/GOOGLE/KILO/OPENCODE
```

## What lives where

```
<project>/.opencode/harness/   sessions + FTS memory, MEMORY.md, kernel,
                               workers, runs, checkpoints, plans/boulder/ledger,
                               project skills, project config, backups
~/.harness/                    gateway token, router stats + catalog, global
                               skills/bundles, user config
```

## CLI (same brain, no TUI)

```bash
harness chat "fix failing tests" --mode orchestrator --model tag:coding
harness serve --port 8787      # gateway (TUI + HTTP talk here)
harness serve --stop
harness tui [--port 8787]      # serve + config + launch TUI, per-directory
harness router refresh         # re-discover provider models
harness router catalog         # params / benchmark / verdict per model
harness router approve <provider/model>
harness skills list|view|pending|approve|reject
harness memory search|save|refine
harness checkpoint save|restore
harness plan start|next|check
harness config get|set|show [--scope user|project]
harness doctor [--verbose]
```

Modes: `code orchestrator plan ask debug review`. Model strings: `auto-fastest`,
`<group>`, `tag:<name>[+min_ctx:32k]`, `provider/model`.

## Repo layout

```
harness/       loop, kernel(RLM), rlm(spawn), router(relay+catalog),
               reasoning, compact, orchestrator, store, memory, skills,
               mcp, checkpoints, serve, doctor, cli
.opencode/harness/skills/  seed skills (Addy subset) + reverse-router (opt-in)
docs/          one page per feature (start at docs/quickstart.md)
tui/           fork notes, plugins, patches, AppImage packaging
```

## Docs

- `docs/quickstart.md` `routing.md` `agents.md` `rlm.md` `skills.md` `memory.md`
- `docs/tui.md` `gateway.md` `config.md` `security.md` `mcp.md`
- `AGENTS.md` (agent operating rules), `SOUL.md`, `MEMORY.md`, `CONSTRAINTS.md`
