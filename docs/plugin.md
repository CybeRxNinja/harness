# Plugin (install to opencod)
Harness ships as a single stock-opencod v2 **server plugin**, not a fork.
Installing it gives your opencod sessions a local relay gateway, built-in
skills, and memory that survives session compaction.

## Install

```bash
pip install -e .                       # harness CLI (stdlib-only)
harness setup                          # gateway token + seed skills
harness plugin install                 # copies harness.ts -> ~/.config/opencode/plugins/
opencode                               # stock opencod; the plugin auto-starts the gateway
```

The plugin file lands at `~/.config/opencode/plugins/harness.ts` and is
auto-loaded by opencod v2 — no entry in `opencod.json`'s `plugin` array is
required (`harness plugin install` only prunes any legacy specs left from an
older fork-based setup). `harness tui` does the above setup and launches
opencod for you:

```bash
harness tui             # gateway + opencod config + plugin, then exec opencode
eval "$(harness tui --setup-only)"   # prep a shell, then run `opencode`
```

> No API keys are needed to start (MOCK/offline relay). Add `OPENROUTER_API_KEY`
> (or GROQ/CEREBRAS/NVIDIA/GOOGLE/KILO/OPENCODE/OLLAMA) to go live.

## What the plugin does

1. **Gateway bootstrap** — starts `harness serve` (localhost:8787) on first use
   and keeps it alive for the session; installs/refreshes the Python CLI from
   GitHub when the installed `PROTO` is too old (contract v6).
2. **Skill seeding** — registers the bundled skills (under
   `harness/data/skills/`) into opencod's skill store so they're usable without
   editing `opencod.json`.
3. **Compaction memory** — on opencod's `experimental.session.compacting` hook,
   it recalls project facts via the gateway (`GET /api/memory?q=<sessionID>`,
   Bearer `HARNESS_TOKEN`) and injects them as compaction context, so durable
   facts survive the summary step.

## What the plugin does NOT do (those come from opencod.json)

Provider/agents are merged into `~/.config/opencode/opencode.json` by
`harness tui`/`harness setup` (`ensure_opencode_config`), not by the plugin:

- `provider.harness` — the relay (`harness/auto-fastest`, `tag:coding`,
  `tag:reasoning`, `tag:general`, `tag:fast`, `tag:free`); openai-compat at
  `http://127.0.0.1:8787/v1`, key `{env:HARNESS_TOKEN}`.
- `agent` — `orchestrator/ask/debug/review` + native `plan` (pinned to
  `harness/tag:reasoning`).

Skills need no MCP hop: the plugin exposes `skills_list`/`skill_view`/
`memory_recall` as native opencode tools. The stdio server
(`harness mcp`) remains for non-opencode MCP clients only.

## Uninstall

```bash
harness plugin uninstall   # removes plugin file + provider/agents/model (user keys untouched)
harness serve --stop       # stop the gateway (optional)
pip uninstall harness      # remove the CLI (optional; AppImage reinstalls on next launch)
```

Uninstall only removes harness-owned entries (agents whose model points at
`harness/*`, the default model if it is `harness/*`). Anything you customized
beyond that is left alone.

## Verify

- `opencode` offers `harness/*` models when you pick a model.
- MCP → `harness-skills` exposes the skills/memory tools.
- A long chat: when the session compacts, the recalled memory brief is applied.
- `harness doctor` reports the gateway, router top pick, and opencod presence.
