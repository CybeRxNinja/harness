# MCP: native tools first, opencode owns servers

## 1. Native opencode tools (primary — no extra process)

The stock-opencode plugin (`harness/plugin/harness.ts`) exposes
`skills_list` / `skill_view` / `memory_recall` / `todowrite` / `todoread` /
`wait` as **native tools** — no MCP hop, no stdio framing to break (reads
`~/.harness/skills/` + local SQLite directly). This is what opencode agents
use. No `mcp.*` block is needed for them. `wait` takes
`{label, timeout_s 1..600, hint?}` and returns an expiry nudge telling you to
check the task's status and act on it.

## 2. MCP servers (`harness mcp`)

MCP servers are configured by opencode itself (`opencode mcp add|list|auth`),
so harness does not keep a second copy of that config. `harness mcp` is the
stable surface for agents and scripts:

```bash
harness mcp list [--json]                  # project + global, project wins
harness mcp add github --command "npx -y @modelcontextprotocol/server-github" \
  --env GITHUB_TOKEN=...                   # local server
harness mcp add docs --url https://example.com/mcp \
  --header "Authorization=Bearer ..."      # remote server
harness mcp remove github [--global]
```

- **list** reads `<root>/opencode.json` and `~/.config/opencode/opencode.json`
  directly (a config written a second ago is visible immediately), prints the
  transport and which scope it came from, and reports env/header **keys** only
  — never values.
- **add** delegates to `opencode mcp add` (so opencode validates and writes its
  own shape), accepts `--command` (shell-quoted) or `--url`, repeatable
  `--env`/`--header`, and `--global` for the user config. It then runs a
  best-effort `opencode reload`; `--no-reload` skips it.
- **remove** exists because opencode has no remove subcommand. Harness deletes
  only that server's entry from the `mcp` block in place, so comments, the
  `model` key and every other setting keep their exact bytes. It writes a
  `opencode.json.bak` first and refuses to touch a block it cannot parse.

Adding a server starts a process on the host: an agent should do it only when
the owner asked. Never put a token in the command line or a chat message — use
`--env`/`--header` with a value from the environment, or configure the server
with `opencode mcp` yourself. OAuth-capable remote servers: `opencode mcp auth
NAME`. A restart of opencode is always safe when a reload is not picked up.

## 3. Stdio server (retired)

`harness mcp` used to speak JSON-RPC 2.0 over stdio with the same three
skill/memory tools for non-opencode MCP clients (it was verified against the
official `@modelcontextprotocol/sdk` client). It was retired with the headless
path: live sessions use the native tools above, which need no extra process.
The `harness mcp` command now manages opencode's MCP servers instead.
