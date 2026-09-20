# Memory
`CONTEXT.md` (project) + `MEMORY.md`/`SOUL.md` (global, 200-line cap) +
SQLite FTS5 transcripts, all under `.opencode/harness/` per project.
`memory search/save/refine`: refine stages a lesson from trajectory evidence
(needs substance), approve moves it to `MEMORY.md`. Kibitzer-lite: top-1
recall injected as `recalled memory: … (verify before relying)`.
Retention 30d (`doctor` shows DB size). Global `~/.harness/` holds global
skills + user config — shared across projects.
