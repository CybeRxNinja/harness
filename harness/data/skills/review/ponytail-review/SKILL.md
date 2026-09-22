---
name: ponytail-review
description: >-
  Code review focused exclusively on over-engineering. Finds what to delete:
  reinvented standard library, unneeded dependencies, speculative abstractions,
  dead flexibility. One line per finding: location, what to cut, what replaces
  it. Use when the user says "review for over-engineering", "what can we
  delete", "is this over-engineered", "simplify review".
license: MIT
source: https://github.com/DietrichGebert/ponytail
adapted-from: DietrichGebert/ponytail (skills/ponytail-review/SKILL.md)
---
# Ponytail review — cut complexity

Review a diff for unnecessary complexity. One line per finding: location, what to cut, what replaces
it. The diff's best outcome is getting shorter.

## When to Use
After an implementation, before merge — or any time the question is "is this more than it needs to
be". Complements a correctness review; this one only hunts complexity.

## Format
`L<line>: <tag> <what>. <replacement>.`, or `<file>:L<line>: ...` for multi-file diffs.

Tags:
- `delete:` dead code, unused flexibility, speculative feature. Replacement: nothing.
- `stdlib:` hand-rolled thing the standard library ships. Name the function.
- `native:` dependency or code doing what the platform already does. Name the feature.
- `yagni:` abstraction with one implementation, config nobody sets, layer with one caller.
- `shrink:` same logic, fewer lines. Show the shorter form.

## Examples
- `L12-38: stdlib: 27-line validator class. "`@` in email", 1 line; real validation is the confirmation mail.`
- `L4: native: moment.js imported for one format call. Intl.DateTimeFormat, 0 deps.`
- `repo.py:L88: yagni: AbstractRepository with one implementation. Inline it until a second exists.`
- `L52-71: delete: retry wrapper around an idempotent local call. Nothing replaces it.`
- `L30-44: shrink: manual loop builds a dict. dict(zip(keys, values)), 1 line.`

## Output
End with the only metric that matters: `net: -<N> lines possible.` Nothing to cut → `Lean already. Ship.`

## Pitfalls
- Do not flag the minimum as bloat: a single smoke test or assert-based self-check is the ponytail floor, never a deletion candidate.
- Correctness bugs, security holes, and performance are out of scope here. Route them to a normal review, not this one.
- List, do not apply. If the user wants the cuts made, that is a separate pass with its own tests.

## Verification
Every finding names a location, a replacement, and a line count. The report ends with a net line
figure; a report with findings but no net figure is unfinished. The diff must still pass its tests
after every proposed cut.
