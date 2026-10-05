# Install (agent-executable)

You are an agent asked to install the harness plugin into the user's opencode. Run
the steps in order; each states its check and its failure branch. Report command
output verbatim — never summarize away an error.

## 0. Preconditions

| check | command | pass condition |
| --- | --- | --- |
| Python | `python3 --version` | `>= 3.11` (`pyproject.toml: requires-python = ">=3.11"`) |
| opencode | `opencode --version` | stock opencode **2.x** — the v2 plugin API; `docs/plugin.md` records verification against `2.0.14` |
| harness | `harness --help` | lists `{doctor,config,skills,memory,setup,tui,plugin}` |

- `opencode` absent: stop and ask. The user installs it (`npm create opencode@latest` or `npx opencode`, `cli.py:132`) — you do not.
- `opencode --version` on 1.x: stop. The plugin targets the v2 plugin API (`harness.ts:4-11`: default export `{ id, setup }`; v1 hook objects are not read in v2; registration is via ctx.*), so on a 1.x host it is either never discovered or throws `ctx.skill.transform unavailable` (`harness.ts:1552`). Symptom: no `skills_list`, no sidebar chip, while `harness doctor` still prints `opencode_version` (e.g. `opencode v1.18.x`). Tell the user to run `opencode upgrade`. No fork, no AppImage, no binary patch — ever.
- Paths are XDG-based on Linux and macOS: `~/.config/opencode/` is `${XDG_CONFIG_HOME:-$HOME/.config}/opencode` (`harness/paths.py:52-60`). Harness is a plugin, not a fork: no model relay, no API key — models come from the user's own opencode providers.

## 1. Install

The Python CLI installs and inspects; the plugin does the work at runtime. Both steps are required and are not interchangeable.

### 1a. Recommended — from a checkout

```bash
pip install -e .                  # harness CLI, stdlib-only, no dependencies
harness setup                     # seeds the 17 bundled skills, reports model + binary
harness plugin install            # -> ~/.config/opencode/plugins/harness/{server.ts,tui.tsx}
                                 #   + ~/.config/opencode/agents/harness-*.md
```

- `harness setup` (`cli.py:120`) is the **only** step that copies bundled skills to the user-global layer `~/.harness/skills/<category>/<name>/SKILL.md` (`skills.py:107-123`); `skills approve` writes into that same layer.
- `harness plugin install` (`install_plugin()` at `cli.py:310-352`, dispatched by `cmd_plugin()` at `cli.py:381-403`) copies the two entrypoints and renders the 12 harness agents as native `~/.config/opencode/agents/harness-*.md` files. It does **not** seed skills or merge into `opencode.json`.
- Skipping `harness setup` still leaves `skills_list` working — the plugin reads the bundled skills from the installed package at activation (`harness.ts:94-108`) — but the user-global skill layer is never created.

### 1b. Shortcut — no checkout

```bash
pip install git+https://github.com/CybeRxNinja/harness.git
harness setup                     # still required: nothing else seeds the global layer
harness plugin install --from-release latest
```

`--from-release latest` resolves the newest `plugin-v*` release (not GitHub's `releases/latest`, which may be a TUI release with no plugin assets) and writes plugin files only — it never calls `ensure_seed_skills`, so `harness setup` is not optional here. It also accepts `0.3`, `v0.3`, `plugin-v0.3` to pin a release. Prefer 1a: release bytes can drift from the pip package, and `harness doctor` reports that drift as `STALE`.

`harness tui` is the one-shot form (seed, merge config, install plugin, exec opencode). Use it only if the user asked to launch opencode; add `--setup-only` to prep without launching.

## 2. Verify

Run it from the project root (project state is cwd-relative; `--root` is a global flag that must precede the subcommand: `harness --root <path> doctor`). `harness doctor` always exits 0 — read the `ok` field and the check lines, not the exit code. Healthy means all of:

- `"ok": true`
- `"plugin": "<...>/plugins/harness (server + tui)"` — the phrase `(server + tui)` present, the word `STALE` absent
- `"agents": "<...>/opencode/agents (12 harness agents)"`
- `"user_model": "provider/model"` — not `missing: ...`
- `"memory"`: object with `file`, `lines`, `facts` (a fresh project legitimately shows `"lines": 0, "facts": 0`; the key must exist). `"workers"`: object with `total`, `unfinished`, `stale`.
- Functional, inside an opencode session after restart: `skills_list` returns seeded ids such as `using-agent-skills` and `ponytail` (an empty list means the plugin never activated); the footer chip reads `harness · <ctx>% · $<cost> · <tok> tok` (or `harness · click for stats` with no session) and clicking it opens the panel (Window, Tokens, Models, Todo, Workers, Waits, Skills, Agents, Memory).
- `harness plugin path` prints the two entrypoints this build ships; diff those against the installed files if doctor says `STALE`.

## 3. Failure branches

| symptom (verbatim from `harness doctor`) | cause | fix |
| --- | --- | --- |
| `"plugin": "... — STALE: server.ts, tui.tsx differ from this build ..."` | installed bytes differ from the entrypoints the installed package ships (`doctor.py:47-63`) | `harness plugin install`, or `--from-release latest` to match the release. Keep CLI and plugin from one build. |
| `"plugin": "missing (run: harness plugin install)"` | nothing installed | `harness plugin install` |
| `"plugin": "... (server.ts) — missing tui.tsx ..."` | single-entrypoint release, or a pre-directory install | `harness plugin install --from-release latest` (the TUI plugin list only shows plugins with a `tui` entrypoint) |
| `"plugin": "... — legacy server-only layout ..."` | a pre-2.x single-file install | `harness plugin install` supersedes it |
| `"agents": "... missing N harness agent(s): <names> — re-run: harness plugin install"` | `~/.config/opencode/agents` is missing managed files | `harness plugin install` (renders native agent files, never clobbers foreign files) |
| `"agents": "... not found"` | installer never ran, or `XDG_CONFIG_HOME` differs from the launching shell | `harness plugin install`, then confirm `XDG_CONFIG_HOME` matches the shell that launches opencode |
| `"user_model": "missing: no model configured: ..."` | no `model` key in `opencode.json` | ask the user to set `"model": "provider/model"` there, or `export HARNESS_MODEL=provider/model`. Never invent a model id. |
| `"opencode_version": "missing"`, or a 1.x string | see §0 | `opencode upgrade`, then re-verify |
| `harness: unknown command 'chat'` / `'plan'` / `'checkpoint'` / `'mcp'` (exit 2); `harness serve/router was retired with the relay` (exit 2) | retired headless commands | not an error to fix: live sessions run inside opencode |

## 4. Uninstall

```bash
harness plugin uninstall           # plugin dir + generated harness-* agent files
pip uninstall harness              # optional: removes the CLI
```

`harness plugin uninstall` (`cli.py:478-505`) removes `<config>/opencode/plugins/harness/`, any legacy `<config>/opencode/plugins/harness.ts`, and the native `harness-*.md` agent files. It no longer rewrites `opencode.json`. Left behind, removable only with explicit consent: `~/.harness/` (global `skills/`, `harness.jsonc`); `<project>/.opencode/harness/` (`sessions.db` with facts, todos, workers, waits; plus `workers/`, `runs/`, `tmp/`, `shadow/`); and any older `opencode.json.bak-<timestamp>.json` backups from earlier merges.

Full wipe = `harness plugin uninstall`, `pip uninstall harness`, then `rm -rf ~/.harness` and `rm -rf <project>/.opencode/harness`. Those deletes are irreversible: name them to the user and get a yes first. Never delete `<project>/.opencode/` wholesale — other opencode state lives there.

## 5. Rules for you

- Never install system packages, edit shell profiles, or mutate global interpreter state without asking. `pip install -e .` into a virtualenv you create is fine; `pip install --break-system-packages` is not.
- Never hand-edit `~/.config/opencode/opencode.json`: treat it as the user's private config. Harness installs native files beside it and does not merge agents or shell policy into it. Use `harness config set` for harness layers (`~/.harness/harness.jsonc`, `.opencode/harness.jsonc`).
- Never force-push, tag, or cut a release. `--from-release` only downloads assets that already exist.
- Paste `harness doctor` output verbatim to the user, failing lines included, and tell them to restart opencode after any install, uninstall, or config merge: the plugin loads at process start.
- If a check fails and its failure branch does not resolve it, stop and report. Do not hand-patch installed files to make doctor go green.
