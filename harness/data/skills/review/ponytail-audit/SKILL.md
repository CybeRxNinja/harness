---
name: ponytail-audit
description: >-
  Whole-repo audit for over-engineering. Like ponytail-review, but scans the
  entire codebase instead of a diff: a ranked list of what to delete, simplify,
  or replace with stdlib/native equivalents. Use when the user says "audit this
  codebase", "audit for over-engineering", "what can I delete from this repo",
  "find bloat", "make this project lean". One-shot report, applies no fixes.
license: MIT
source: https://github.com/DietrichGebert/ponytail
adapted-from: DietrichGebert/ponytail (skills/ponytail-audit/SKILL.md)
---
# Ponytail audit — whole repo

ponytail-review, repo-wide. Scan the whole tree instead of a diff. Rank findings biggest cut first.

## When to Use
A general "refine / streamline / clean up this project" request, or before a release. Not for a
single diff (use ponytail-review).

## Hunt
Dependencies the stdlib or platform already ships; single-implementation interfaces; factories with
one product; wrappers that only delegate; files exporting one thing; dead flags and config keys
nothing reads; hand-rolled stdlib; duplicated helpers across modules; docs that contradict the code.

How to prove a finding, not guess it: grep each exported symbol for callers outside its own module
and its tests. A function reached by nobody but a test is dead until something calls it. A config key
is dead when no `cfg.get("…")` reads it. Report the grep, not an impression.

## Tags
Same as ponytail-review:
- `delete:` dead code, unused flexibility, speculative feature. Replacement: nothing.
- `stdlib:` hand-rolled thing the standard library ships. Name the function.
- `native:` dependency or code doing what the platform already does. Name the feature.
- `yagni:` abstraction with one implementation, config nobody sets, layer with one caller.
- `shrink:` same logic, fewer lines. Show the shorter form.

## Output
One line per finding, ranked: `<tag> <what to cut>. <replacement>. [path]`.
End with `net: -<N> lines, -<M> deps possible.` Nothing to cut → `Lean already. Ship.`

Add a `stale:` tag for docs or comments that contradict the code (test counts, "uncommitted" on a
committed tree, "removed" features that exist), since those cost every future reader.

## Pitfalls
- Do not propose deleting a feature the user asked for; YAGNI applies to speculation, not requests.
- Correctness bugs, security holes, and performance are out of scope. Route them to a normal review.
- Stub the public API before deleting anything reachable from a documented entry point (CLI subcommand, plugin tool, skill name).

## Verification
Every finding is backed by a grep or a file:line reading, and the report ends with a net figure.
Apply the cuts in a second pass, tests green after each one; never delete and fix in the same step.
