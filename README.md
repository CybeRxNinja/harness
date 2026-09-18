# Harness — all-in-one AI coding harness (v0.1 prototype)

Python core + HTTP gateway + forked opencode TUI (CI-built) + CLI fallback.
Stdlib-only core. Potato-PC safe.

## 30-second start (no keys needed — MOCK mode)

```bash
pip install -e .
harness doctor
harness chat "explain this repo" --mode ask
harness serve --port 8787 &
curl -H "Authorization: Bearer $(cat ~/.harness/token)" -H 'Content-Type: application/json' \
  -d '{"session_id":"demo","mode":"ask","message":"list files"}' localhost:8787/api/chat
```

Add one key to go live: `export OPENROUTER_API_KEY=...` (or GROQ/CEREBRAS/NVIDIA/OPENAI/GOOGLE, or run Ollama locally).

## Go live

```bash
harness setup
export OPENROUTER_API_KEY=sk-...
harness chat "fix failing tests" --mode orchestrator --model tag:coding
```

## TUI (TUI-B, CI-built)

One command does everything (starts `serve`, wires `opencode.json`, launches the TUI):

```bash
harness tui
```

No binary yet / SSH / scripts: `harness chat ...` is the guaranteed fallback (same loop).

## Layout

```
harness/       core: loop, kernel(RLM), rlm(spawn), router(builtin relay),
               reasoning, compact, orchestrator, tasks, store, memory,
               skills, mcp, checkpoints, serve, doctor, cli
.harness/skills/  seed skills (Addy subset) + reverse-router (disabled default)
docs/          one page per feature
tui/           fork notes + patches + reskin spec
```

## Docs

- `docs/quickstart.md` `routing.md` `agents.md` `rlm.md` `skills.md` `memory.md`
- `docs/tui.md` `gateway.md` `config.md` `security.md` `mcp.md`
- `AGENTS.md` (agent operating rules), `SOUL.md`, `MEMORY.md`, `CONSTRAINTS.md`
