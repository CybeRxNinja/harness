# Harness — all-in-one AI coding harness (v0.1 prototype)

Python core + HTTP relay gateway + a single opencod v2 **server plugin**.
Standard-library-only core. Potato-PC safe: <150MB idle, SQLite only,
2 parallel workers.

No fork, no custom binary: Harness runs as a plugin inside **stock opencod**,
bootstrapping a local relay gateway (so `harness/*` models route through your
provider keys), seeding built-in skills, and recalling durable memory when
opencod compacts a session.

## Install (stock opencod)

```bash
pip install -e .            # or: curl -fsSL https://raw.githubusercontent.com/CybeRxNinja/harness/main/install.sh | bash
python -m harness setup     # mint gateway token, seed skills, show key env vars
python -m harness plugin install   # -> ~/.config/opencode/plugins/harness.ts (auto-loaded by opencod v2)
opencode                  # stock opencod; the plugin auto-starts the gateway
```

Zero keys needed to start (MOCK/offline mode); add one to go live:

```bash
export OPENROUTER_API_KEY=sk-...   # also: GROQ, CEREBRAS, NVIDIA, GOOGLE, KILO, OPENCODE, OLLAMA_BASE_URL
```

First launch from a project dir wires `opencod.json` (provider + agents)
into `~/.config/opencode/opencode.json` — backed up, never clobbered. The
gateway is rooted at the current directory; switching directories restarts it
(sessions persist per directory under `<project>/.opencode/harness/`).

## What lives where

```
<project>/.opencode/harness/   sessions + FTS memory, MEMORY.md, kernel,
                                workers, runs, checkpoints, plans/boulder/ledger,
                                project skills, project config, backups
~/.config/opencode/            opencod.json (merged) + plugins/harness.ts
~/.harness/                    gateway token, router stats + catalog, global
                                skills/bundles, user config
```

## CLI (same brain, no TUI)

```
harness chat "fix failing tests" --mode orchestrator --model tag:coding
harness serve --port 8787      # gateway (opencod plugin + HTTP talk here)
harness serve --stop
harness tui [--port 8787]      # serve + config + plugin, then launch opencod
harness router refresh         # re-discover provider models
harness router catalog
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

## The plugin (`harness/plugin/harness.ts`)

A single stock-opencod v2 plugin (no fork). Once installed it:

- bootstraps/starts the harness HTTP relay gateway on demand and keeps it alive;
- seeds bundled skills into opencod's skill store;
- on `experimental.session.compacting`, injects a recalled memory brief into the
  compaction summary so durable project facts survive a condensed transcript.

Provider definitions and harness agents come from
`opencod.json` (merged by `harness tui`/`harness setup`); skills surface as
native plugin tools, no MCP hop. See `docs/plugin.md`.

## Repo layout

```
harness/            loop, kernel(RLM), rlm(spawn), router(relay+catalog),
                    reasoning, compact, orchestrator, store, memory, skills,
                    mcp, checkpoints, serve, doctor, cli
harness/plugin/     opencod v2 server plugin (harness.ts) + README
docs/               one page per feature (start at docs/quickstart.md, docs/plugin.md)
scripts/            optional external skill-pack installers (Addy / reverse-router)
install.sh          CLI + plugin installer (no binary)
```

## Docs

- `docs/quickstart.md` `routing.md` `agents.md` `rlm.md` `skills.md` `memory.md` `plugin.md`
- `docs/gateway.md` `config.md` `security.md` `mcp.md` `tui.md`
- `AGENTS.md` (agent operating rules), `SOUL.md`, `MEMORY.md`, `CONSTRAINTS.md`
