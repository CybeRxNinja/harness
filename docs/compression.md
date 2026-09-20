# Compression (lite RTK + Caveman)

Deterministic, stdlib-only, no LLM involved. Two engines, always stacked
`rtk -> caveman`, applied to **tool outputs only** — never to user/assistant
messages.

## RTK-lite (`harness/compress.py:rtk_lite`)
Command-aware filters (pytest/tracebacks, npm/bun/tsc, git, pip/uv, shell
listings + a generic fallback): strip ANSI → keep/drop/collapse line patterns
→ dedupe runs ≥3 → head/tail truncation by intensity. Errors, failures, stack
traces and changed-file lines are **never dropped** at any intensity.

## Caveman-lite (`caveman_lite`)
Protects code blocks, URLs, JSON and paths behind placeholders, then strips a
curated filler list, collapses whitespace/repeats, and restores blocks.

## Config (`compress` section)
`enabled` (default true), `threshold` chars (default 4000),
`intensity`: `minimal` (collapse only) / `standard` (default, truncate 120
lines) / `aggressive` (truncate 60). Raw output spills to
`.opencode/harness/runs/tool-<name>-<ts>.log`; the model sees compressed text
plus a stats trailer naming engines, filter and raw filename.

## Plugin hook
The opencode plugin's `ctx.tool.hook("execute.after")` handler runs the same
policy on opencode-side tool results (>4000 chars, errors pass through
untouched). Errors can never be hidden by either path.
