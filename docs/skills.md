# Skills

Progressive disclosure: L0 `skills list` → L1 `skills view <name>` → L2
`skills view <name> --subpath references/x.md`. Format: When/Procedure/
Pitfalls/Verification; lessons-not-logs.

## Bundled (`harness/data/skills/<category>/<name>/SKILL.md`)

```
meta/using-agent-skills        route work to the right skill
meta/self-maintenance         maintain/improve harness when asked, or automatic only when natural + genuinely improves UX/efficiency
define/spec-driven-development
plan/planning-and-task-breakdown
build/incremental-implementation
build/ponytail                 the laziness ladder (see below)
build/test-driven-development
verify/debugging-and-error-recovery
review/code-review-and-quality
review/code-simplification
review/security-and-hardening
review/ponytail-review         over-engineering review, one line per finding
review/ponytail-audit          whole-repo version of the same
ship/git-workflow-and-versioning
security/reverse-router        ships DISABLED (sec pack, scope-gate, lawful-use only)
```

Seeded into opencode's store as `harness-<name>` by the plugin
(`ctx.skill.transform`) and into `~/.harness/skills` by `harness setup`.

Resolution order (first root wins on a same-named skill): `HARNESS_SKILLS_DIR`
(if set) → `<project>/.opencode/harness/skills` → package data
(`harness/data/skills`) → `~/.harness/skills`. Missing/unreadable roots are
skipped. Opencode's own paths stay global by host design
(`~/.config/opencode/opencode.json`, `~/.config/opencode/plugins/harness`) —
only harness skills overlay per project.

## ponytail (MIT, attributed — see `harness/data/skills/NOTICE.md`)

The build ladder: (1) does this need to exist, (2) is it already here, (3) stdlib,
(4) native platform feature, (5) an installed dependency, (6) one line, (7) only then the minimum that
works. Delete over add; mark a deliberate corner `# ponytail: <ceiling>, <upgrade path>`; leave one
runnable check behind for non-trivial logic. Never simplify away input validation, error handling that
prevents data loss, security, or accessibility.

`AGENTS.md` carries the short form so it is active every prompt; `skill_view ponytail` loads the full
ruleset. Use `ponytail-review` on a diff and `ponytail-audit` across the tree to find deletions — both
report `net: -<N> lines` and apply nothing.

## Managing (retired)

Headless skill authoring (`skill_manage` create/patch/write_file/delete, the
`bundles` loader, the standalone exfil scan and the `scripts/install-*.sh`
pack installers) was retired with the headless path; review now happens in
live review subagents. Kept: progressive disclosure (`skills list|view`) and
the pending queue (`skills pending|approve|reject`).
Precedence `project > local > external_dirs`; missing dirs skipped.
