# Plugin (install to opencode)
Harness ships as a single stock-opencode v2 **server plugin**, not a fork.
Installing it gives your opencode sessions built-in skills, native
skills/memory tools, and memory that survives session compaction. There is
no relay: models always come from **your** opencode providers.

## Install

```bash
pip install git+https://github.com/CybeRxNinja/harness.git   # harness CLI (stdlib-only)
harness plugin install --from-release plugin-v0.2            # no repo checkout needed
opencode                                                     # stock opencode; the plugin auto-loads
```

(From a checkout instead: `pip install -e .` then plain `harness plugin install`.)

The plugin file lands at `~/.config/opencode/plugins/harness.ts` and is
auto-loaded by opencode v2 — no entry in `opencode.json`'s `plugin` array is
required. `harness tui` does the above setup and launches opencode for you:

```bash
harness tui             # opencode config + plugin, then exec opencode
harness tui --setup-only  # prep only, then run `opencode` yourself
```

> Models are yours: set `model` in `opencode.json` (or per-agent `model`).
> Harness agents carry no model pins — they inherit your default.

## What the plugin does

1. **Skill seeding** — registers the bundled skills (under
   `harness/data/skills/`) into opencode's skill store so they're usable without
   editing `opencode.json`.
2. **Native tools** — `skills_list`/`skill_view`/`memory_recall` read local
   disk + SQLite directly (no gateway, no extra process).
3. **Output condensing** — `tool.execute.after` collapses oversized tool
   results in place (errors pass through untouched).
4. **Compaction memory** — on opencode's `experimental.session.compacting` hook,
   it recalls project facts locally and injects them as compaction context, so
   durable facts survive the summary step.

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

## Verify

- `harness doctor` reports your user model, the plugin file, and opencode presence.
- A long chat: when the session compacts, the recalled memory brief is applied.
