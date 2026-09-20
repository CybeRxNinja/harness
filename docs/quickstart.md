# Quickstart
1. Install the harness CLI: `pip install -e .` (repo) or
   `curl -fsSL https://raw.githubusercontent.com/CybeRxNinja/harness/main/install.sh | bash`.
   Stdlib-only, no dependencies.
2. Install **stock opencode** (`npm create opencode@latest` or `npx opencode`).
3. Set your model in `opencode.json` (e.g. `"model": "anthropic/claude-sonnet-4-5"`).
   Every harness agent inherits this default.
4. `harness setup` — seeds global skills, verifies your model + opencode binary.
   Then install the plugin: `harness plugin install` (copies
   `harness/plugin/harness.ts` to `~/.config/opencode/plugins/`, auto-loaded by
   opencode v2).
5. `harness doctor` — Python, disk, DB size, resolved user model, plugin file.
6. `harness chat "hi" --mode ask` — works with zero model access (MOCK echo);
   with your model configured it runs for real via `opencode run`.
7. `harness tui` — ensures opencode config + plugin, then launches
   stock opencode. Each directory gets its own `.opencode/harness/` state.

Key files: `AGENTS.md` (rules, highest precedence), `harness.jsonc` layers
(defaults < `~/.harness` < `.opencode/harness.jsonc` < env), budgets enforced.
See `docs/plugin.md` for the opencode plugin install walkthrough.
