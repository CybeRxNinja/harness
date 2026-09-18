# Quickstart
1. `pip install -e .` 2. `harness doctor` 3. `harness chat "hi" --mode ask` (MOCK ok).
4. `export OPENROUTER_API_KEY=...` to go live. 5. `harness serve --port 8787 &` then TUI binary or curl with `~/.harness/token`.
Key files: `AGENTS.md` (rules), `harness.jsonc` layers (defaults < ~/.harness < .harness < env), budgets enforced.
