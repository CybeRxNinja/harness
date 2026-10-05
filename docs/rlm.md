# RLM kernel (headless path retired)

> Live sessions route subagents through opencode's native `task` tool (the
> orchestrator agent in `harness-opencode.json`); the Python pool/spawn/mailbox
> below is retired. What remains live: the `workers` table (sidebar +
> `harness doctor` via `rlm.is_terminal` / `rlm.worker_timeout`).


`py` executes in a persistent per-session namespace (pickled vars survive
compaction/resume; unpicklable values summarized). `bash` runs 10s in
foreground, then returns a background handle (`poll/output/tail`, logs in
`.opencode/harness/runs/`). Keys/DB/routing stay in the host, never in `py`
globals. Trust model: user permissions, paths jailed to project root, `socket`
blocked unless `allow_net`, shell allowlist. Durable, not a sandbox.

## Spawning

`rlm.spawn` returns an admission handle `{rlm_child_id, name, session_dir,
model, status}` immediately — results arrive through the mailbox and
`workers/<id>/result.md`, never as return values, so a parent turn never blocks
on a child. Exactly one of `category` (intent: `quick`/`deep`/…, never a
provider/model string) or `subagent_type` (`explore`, `librarian`,
`plan-consultant`, `plan-reviewer`, `code-reviewer`, `test-engineer`,
`security-auditor`) per call.

The thread pool **is** the concurrency limit, sized from
`budgets.max_parallel` (default 2) and reused per width — there is no second
semaphore to drift out of sync. `max_depth: 0` refuses spawns outright.

## Lifecycle

| status | meaning |
|---|---|
| `queued` | admitted, row written, work submitted |
| `running` | the worker thread picked it up |
| `done` | `result.md` written and the summary posted to the mailbox |
| `error` | the backend raised; the message is in `result.md` |
| `timeout` | the backend exceeded `budgets.worker_timeout_s` (default 600s) |
| `stale` | the row still claims queued/running long after its last update |

Every exit path writes a terminal status. What ships today is
`harness/rlm.py` reduced to the read-only helpers `harness doctor` uses to
classify a row (`is_terminal`, `worker_timeout`); the rest of this file
describes the retired path.

## Mailbox

`inbox(role)` returns undelivered messages and marks them delivered, so a
parent that flushes its inbox every turn hands each child summary over exactly
once (it used to re-append the same completions forever). Pass
`mark_delivered=False` to peek. `send(sender, content, receiver_role=…)` posts to
a sibling or the parent.

Workers get `SUMMARY + DIFF` instructions (`≤4k`) and a read-only clause for
`explore`/`librarian`; `load_skills` prepends up to three skill bodies to the
system prompt, which is what makes a worker follow a project procedure.
