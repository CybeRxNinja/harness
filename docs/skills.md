# Skills
Progressive disclosure: L0 `skills list` → L1 `skills view <name>` → L2
`skills view <name> --subpath references/x.md`. Format: When/Procedure/
Pitfalls/Verification; lessons-not-logs. Seed 10 (Addy subset, MIT,
attributed) + `using-agent-skills` router; `reverse-router` ships disabled
(sec pack, scope-gate required, lawful-use only).

`skill_manage` (create/patch/write_file/delete) stages under approval by
default (`write_approval:true`): review via `skills pending|approve|reject`.
Project skills need trust + pass a prompt-injection/exfil scan (quarantined on
hit). Precedence `project > local > external_dirs`; missing dirs skipped.
Bundles (`backend-dev: [review, tdd, pr]`) load skill sets as one slash
command. Full packs: `scripts/install-addy-skills.sh`,
`scripts/install-reverse-router.sh`.
