# RLM kernel
`py` executes in a persistent per-session namespace (pickled vars survive
compaction/resume; unpicklable values summarized). `bash` runs 10s in
foreground, then returns a background handle (`poll/output/tail`, logs in
`.opencode/harness/runs/`). `rlm.spawn` returns an admission handle
`{id,name,session_dir,model}` immediately — results arrive via mailbox/files,
never as return values. `list/delete_subagents`, follow-ups via agent messages.

Skills are both docs (`skill_view`) and importable callables when `scripts/`
exist. Keys/DB/routing stay in the host, never in `py` globals. Trust model:
user permissions, paths jailed to project root, `socket` blocked unless
`allow_net`, shell allowlist. Durable, not a sandbox.
