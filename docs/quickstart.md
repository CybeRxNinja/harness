# Quickstart
1. Install the harness CLI: `pip install -e .` (repo) or
   `curl -fsSL https://raw.githubusercontent.com/CybeRxNinja/harness/main/install.sh | bash`.
   Stdlib-only, no dependencies.
2. Install **stock opencod** (`npm create opencode@latest` or `npx opencode`).
3. `harness setup` — mints the gateway token, seeds global skills, prints key
   env vars. Then install the plugin: `harness plugin install` (copies
   `harness/plugin/harness.ts` to `~/.config/opencode/plugins/`, auto-loaded by
   opencod v2).
4. `harness doctor` — RAM, disk, DB size, provider keys, router top pick.
5. `harness chat "hi" --mode ask` — works with zero keys (MOCK echo).
6. Go live: `export OPENROUTER_API_KEY=...` (also GROQ, CEREBRAS, NVIDIA,
   GOOGLE, KILO, OPENCODE, OLLAMA_BASE_URL).
7. `harness tui` — ensures gateway + opencod config + plugin, then launches
   stock opencod. Each directory gets its own `.opencode/harness/` state;
   switching directories restarts the gateway (sessions persist per directory).

Key files: `AGENTS.md` (rules, highest precedence), `harness.jsonc` layers
(defaults < `~/.harness` < `.opencode/harness.jsonc` < env), budgets enforced.
See `docs/plugin.md` for the opencod plugin install walkthrough.
