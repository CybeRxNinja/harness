---
name: find-skills
description: Match the task against already-loaded harness skills, then load only the one it needs. Use when starting work and the right playbook is unclear, or when nothing loaded matches.
license: MIT
source: https://github.com/vercel-labs/skills
adapted-from: vercel-labs/skills (skills/find-skills/SKILL.md)
---
# Find Skills
## When to Use
Starting a task whose playbook is unclear, or when the user wants capability beyond what is loaded.
## Procedure
1. `skills_list` first — the L0 index; its description lines are the match surface.
2. Pick the one skill whose description covers the task and `skill_view` it. One. Add a second only when the task genuinely spans two domains (bug fix + release, spec + migration) — and then say why.
3. Follow it, and name in the reply which skill was loaded and what it covers.
4. Nothing matches → do the work directly. No match is a normal outcome, not a blocker.
5. External discovery is the last resort, and only when the user asks to extend capabilities: search the ecosystem (`npx skills search <query>`, or browse the source repo) and PROPOSE what turned up. Installing is a user-run action — the agent never installs.
## Pitfalls
- Loading every skill, or a second one "to be safe": context bloat, and overlapping playbooks blur each other.
- Running `npx skills add` (or any install) on your own initiative. Never, without the user.
- Loading a skill whose name merely sounds close to the task.
- Treating "no match" as a reason to go shopping externally.
## Verification
The reply names the chosen skill (or `none`, plus the work done directly) and why; exactly one `skill_view` unless a second is justified by two real domains.
