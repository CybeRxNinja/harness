# Quickstart
1. AppImage (recommended): download, `chmod +x`, run from your project dir.
   First launch wires `opencode.json`, installs the CLI, starts the gateway.
2. Or CLI only: `pip install -e .` (repo) — stdlib-only, no deps.
3. `harness doctor` — RAM, disk, DB size, provider keys, router top pick.
4. `harness chat "hi" --mode ask` — works with zero keys (MOCK echo).
5. Go live: `export OPENROUTER_API_KEY=...` (also read: GROQ, CEREBRAS,
   NVIDIA, GOOGLE, KILO, OPENCODE, OLLAMA_BASE_URL for local models).
6. `harness tui` — gateway + config + TUI, rooted at the current directory.
   Each directory gets its own `.opencode/harness/` state; switching
   directories restarts the gateway (sessions persist per directory).

Key files: `AGENTS.md` (rules, highest precedence), `harness.jsonc` layers
(defaults < `~/.harness` < `.opencode/harness.jsonc` < env), budgets enforced.
