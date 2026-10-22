# Skills

Progressive disclosure: L0 `skills list` → L1 `skills view <name>` → L2
`skills view <name> --subpath references/x.md`. Format: When/Procedure/
Pitfalls/Verification; lessons-not-logs.

## Bundled (`harness/data/skills/<category>/<name>/SKILL.md`)

```
meta/using-agent-skills        route work to the right skill
meta/self-maintenance         maintain/improve harness when asked, or automatic only when natural + genuinely improves UX/efficiency
meta/find-skills              match the task against the loaded set, load only the one it needs; external search is a user-approved fallback
define/spec-driven-development
plan/planning-and-task-breakdown
build/frontend-craft-floor     the craft floor: 4 modes, refuse list, pre-ship checks
build/incremental-implementation
build/ponytail                 the laziness ladder (see below)
build/test-driven-development
build/ui-ux-craft              design-time UI/UX rules by priority: a11y, color, type, layout, forms, charts
verify/debugging-and-error-recovery
review/code-review-and-quality
review/code-simplification
review/security-and-hardening
review/ponytail-review         over-engineering review, one line per finding
review/ponytail-audit          whole-repo version of the same
ship/git-workflow-and-versioning
security/reverse-router        ships DISABLED (sec pack, scope-gate, lawful-use only)
security/apk-reverse           static APK analysis (manifest/certs/code/secrets), lawful-use only
```

Seeded into opencode's store as `harness-<name>` by the plugin
(`ctx.skill.transform`) and into `~/.harness/skills` by `harness setup`.

Two skills carry a `references/` sub-file — `build/ui-ux-craft`
(`references/quick-reference.md`) and `build/frontend-craft-floor`
(`references/craft-floor.md`). Both load L2 only when an L1 row actually bites
(palette/type numbers, the floor checklist); never preemptively, and never as a
second skill at match time — the router picks one of the two (see
`meta/using-agent-skills`).

Resolution order (first root wins on a same-named skill): `HARNESS_SKILLS_DIR`
(if set) → `<project>/.opencode/harness/skills` → package data
(`harness/data/skills`) → `~/.harness/skills`. Missing/unreadable roots are
skipped. The host's own directories are read too, so `skills list`, the
`skill_view` tool and the panel agree with what opencode actually advertises:
`<project>/.opencode/skills` and `~/.config/opencode/skills`. Opencode's own
config stays global by host design (`~/.config/opencode/opencode.json`,
`~/.config/opencode/plugins/harness`) — only harness skills overlay per
project.

## Routing (automatic use)

opencode advertises every skill's id + description at every model step, so a
skill is used automatically the moment it is *placed where the host looks*
(`.opencode/skills`) and has a description. Harness adds the two things that
wear out at scale:

* `skills_list {query}` ranks the list: likely skills first, marked `◆`, named
  up front — the output shape is unchanged, `query` is optional.
* The session `context` hook adds one system line before a generation when a
  skill's name/description overlaps the user's own message (stemmed word
  overlap, same matcher as the CLI). It names at most two, says to load one
  only if it fits, never repeats itself for the same message, and stays silent
  when the message already names the match. Off with `"skills": {"nudge":
  false}` in `.opencode/harness.jsonc` (nearest file wins).
* `harness skills match <task text>` prints the same ranking from the CLI.

Nothing is force-loaded: opencode's `skill` tool decides, and an id the model
picks is honoured either way.

## Getting more skills (`harness skills add`)

Curated sources: `harness skills sources`. Each entry names the repo, the
subdirectory its skills live in and its licence; the five packs named as
starting points are all there (obra/superpowers, addyosmani/agent-skills,
morluto/rea, Shubhamsaboo/awesome-llm-apps, K-Dense-AI/scientific-agent-skills).

```
harness skills sources                      # what is curated, with licences
harness skills add agent-skills             # alias, owner/repo, URL, or a local dir
harness skills add obra/superpowers --only code-review   # a few, not the pack
harness skills add agent-skills --agents    # also import agents/*.md
harness skills add scientific-skills --dry-run           # plan, write nothing
harness skills update                       # re-fetch every imported pack, apply drift
```

Imported packs land in `<project>/.opencode/skills` (host-discovered) or
`~/.config/opencode/skills` with `--global`, each skill carrying
`.harness-source.json` (repo, ref, commit, licence, files). Untrusted text:
only files are copied (never symlinks), credentials/binaries/oversized files
are refused, and `skills update` touches nothing that has no provenance — a
hand-written skill or an approved memory draft is left alone.

`--agents` imports the pack's other half: `agents/*.md` with name +
description frontmatter land in `<project>/.opencode/agents` (opencode's
agent discovery), tracked by one sidecar, `.harness-agents.json`, so
`skills update` re-syncs prompts too. A file that differs from the pack is
never overwritten by a bare re-run: it takes `--update` (refresh) or
`--force` (adopt). `--only` filters skills; agents import whole or not at all.

## Learning a skill from the work (`harness skills draft`)

```
harness skills draft <id> --query "<topic>"   # compose from durable memory
harness skills draft <id> --dry-run           # read the SKILL.md before staging
harness skills pending                        # review
harness skills approve --ids <n>              # -> .opencode/skills/<id>/SKILL.md
```

The draft pulls this project's durable facts and MEMORY.md lessons into
When/Procedure/Pitfalls/Verification, refuses to draft without at least three
evidence-backed lines, and stages as `skill-create` — the same queue lessons
use. Approval writes to the host's project dir (it used to write harness'
private overlay, where nothing could see it) plus its provenance, so
`skills update` knows it came from memory and never looks for it upstream.

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
