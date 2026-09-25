"""RLM spawn/mailbox. category XOR subagent_type. max_parallel, max_depth.

The pool IS the concurrency limit, so there is no second semaphore to keep in
sync with it (one was removed for exactly that reason). It is now sized from
`budgets.max_parallel` instead of a hardcoded 2, and grows a new executor when
the configured width changes.

Lifecycle, and why each piece exists:

  queued    -> spawn admitted, row written, work submitted
  running   -> the worker thread picked it up
  done      -> result.md written and the summary posted to the mailbox
  error     -> the backend raised; the message is kept in result.md
  timeout   -> the backend exceeded `budgets.worker_timeout_s`
  stale     -> the row still claims queued/running long after its last update
               (the thread died; nothing will ever finish it)

Workers always talk back through the mailbox and `workers/<id>/result.md` —
never as a return value — so a parent turn never blocks on a child.
"""
from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .paths import state_dir

_POOLS: dict[int, ThreadPoolExecutor] = {}
SUBAGENT_TYPES = ("explore", "librarian", "plan-consultant", "plan-reviewer",
                  "code-reviewer", "test-engineer", "security-auditor")
# Valid `category` intents are config-driven (config.intents) because the
# orchestrator may name its own; the subagent list is fixed by the spawn
# contract in AGENTS.md.
TERMINAL = ("done", "error", "timeout")

# A subagent_type is a ROLE, so each one carries its own brief: one generic
# prompt for every kind is how "spawn a code reviewer" and "spawn a security
# auditor" came back as the same worker. `explore`/`librarian` add READ-ONLY on
# top (see _system_prompt).
SUBAGENT_BRIEFS = {
    "explore": ("Find the files and trace the flow, then answer where/how with file:line "
                "evidence; no edits and no proposed diffs."),
    "librarian": ("Research the outside world: upstream source, official docs and the versions "
                  "in use. Cite every source (path or URL + version) and never guess an API."),
    "plan-consultant": ("Read the plan and the code it touches, then return what the plan must "
                        "decide — interfaces, ordering, file ownership, risks. Recommend, do "
                        "not implement."),
    "plan-reviewer": ("Attack the plan before execution: gaps, wrong assumptions, missing "
                      "verification, overlapping ownership. One verdict per item with evidence."),
    "code-reviewer": ("Review the diff on five axes — correctness, tests, simplicity, security, "
                      "readability — with severity Blocker/Nit and file:line evidence. Static "
                      "review only: never launch a browser, server or test runner (runtime "
                      "checks are the test-engineer's lane) and mark anything unverified at "
                      "runtime as such. On a re-run, review only the hunks changed since your "
                      "last pass: one verdict per prior finding (fixed / still open / regressed) "
                      "— never a fresh full-file audit and never restated closed findings."),
    "test-engineer": ("Write and run the tests that prove the change — failing first, then "
                      "passing. Touch test files only; report the exact command and its result; "
                      "never weaken a test to get green. Use the project's existing runner and "
                      "its ONE canonical command — never add a second test framework; a fix gets "
                      "only the check that covers the touched code, and an already-passing gate "
                      "is not re-run wholesale; browser/e2e only when the repo ships that tooling "
                      "and a browser is connected. Scratch artifacts (scripts, screenshots, "
                      "logs) go under the project's .opencode/harness/tmp/ — never /tmp or a "
                      "system directory; reuse one browser/server session; never install "
                      "packages or tools — report the missing tool instead."),
    "security-auditor": ("Check the change against the OWASP Top-10 and its trust boundaries — "
                         "inputs, auth, secrets (never print one), permissions, path escapes. "
                         "Findings with severity and a concrete fix."),
}


def is_terminal(status: str) -> bool:
    """True when a worker has reached a status nothing will move it out of.

    `stale` is deliberately not terminal: it is a computed verdict for a row
    that still claims to be running.
    """
    return str(status) in TERMINAL


def pool(max_parallel: int | None = None) -> ThreadPoolExecutor:
    """The shared executor, one per configured width.

    `None` means the default (2); an explicit `0` means serial, not "default" —
    a config that says zero workers must not silently run two.
    """
    width = 2 if max_parallel is None else max(1, int(max_parallel))
    ex = _POOLS.get(width)
    if ex is None:
        ex = ThreadPoolExecutor(max_workers=width, thread_name_prefix="harness-worker")
        _POOLS[width] = ex
    return ex


def _budgets(cfg: dict) -> dict:
    return (cfg.get("budgets", {}) or {})


def worker_timeout(cfg: dict) -> int:
    return int(_budgets(cfg).get("worker_timeout_s", 600))


def spawn(con, cfg: dict, project_root: Path, prompt: str, name: str,
          category: str | None = None, subagent_type: str | None = None,
          load_skills: list[str] | None = None, budget: dict | None = None) -> dict:
    if not name:
        raise ValueError("rlm.spawn requires name")
    if bool(category) == bool(subagent_type):
        raise ValueError("pass exactly one of category|subagent_type")
    if category:
        if "/" in str(category):
            raise ValueError("category takes intent (quick/deep/...), not provider/model")
        from .config import intents
        valid = intents(cfg)
        if str(category) not in valid:
            raise ValueError(f"unknown category: {category} (valid: {', '.join(valid)})")
    if subagent_type and subagent_type not in SUBAGENT_TYPES:
        raise ValueError(f"unknown subagent_type: {subagent_type}")
    if _budgets(cfg).get("max_depth", 1) < 1:
        raise RuntimeError("nested spawn disabled (max_depth)")
    wid = "w_" + uuid.uuid4().hex[:8]
    # Workers inherit the user's configured opencode default model.
    from . import models as _backend
    try:
        model = _backend.resolve_model("", cfg)
    except RuntimeError:
        model = "user-default"
    sdir = state_dir(project_root) / "workers" / wid
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "prompt.md").write_text(f"# {name}\n\n{prompt}\n")
    con.execute("INSERT INTO workers(id,session,name,category,model,status,cost,updated) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (wid, "", name, category or subagent_type, model, "queued", 0.0, int(time.time())))
    con.commit()
    pool(_budgets(cfg).get("max_parallel", 2)).submit(
        _run_worker, str(project_root), wid, prompt, name, category or subagent_type,
        model, load_skills or [], budget or {})
    return {"rlm_child_id": wid, "name": name, "session_dir": str(sdir),
            "model": model, "status": "queued"}


def _mark(con, wid: str, status: str) -> None:
    con.execute("UPDATE workers SET status=?, updated=? WHERE id=?",
                (status, int(time.time()), wid))
    con.commit()


def _run_worker(root: str, wid: str, prompt: str, name: str, kind: str, model: str,
                skills: list[str], budget: dict) -> None:
    """One worker's whole life. Every exit path leaves a terminal status."""
    project_root = Path(root)
    sdir = state_dir(project_root) / "workers" / wid
    from .store import connect
    con = None
    try:
        con = connect(project_root)
        _mark(con, wid, "running")
        from .config import load_config
        cfg, _ = load_config(project_root)
        sys = _system_prompt(project_root, cfg, kind, skills)
        from . import models as _backend
        try:
            msg = _backend.chat([{"role": "system", "content": sys},
                                 {"role": "user", "content": prompt[:6000]}],
                                model=model, cfg=cfg, workdir=project_root)
            content = str(msg.get("content", ""))[:4000]
            status = "done"
        except RuntimeError as e:
            # the backend's own timeout comes back as RuntimeError
            content = f"worker failed: {e}"
            status = "timeout" if "timed out" in str(e) else "error"
        except Exception as e:
            content = f"worker failed: {e}"
            status = "error"
        _write_result(sdir, name, model, status, content)
        _mark(con, wid, status)
        from . import memory as M
        M.save_fact(con, f"{kind} worker {name} {status}: {content[:300]}", "worker")
        con.execute("INSERT INTO mailbox(sender,receiver,receiver_role,content,ts,delivered) "
                    "VALUES(?,?,?,?,?,0)", (name, "parent", "parent", content[:4000], int(time.time())))
        con.commit()
    except Exception as e:
        # connect()/config load failed: without this the row stays `queued`
        _write_result(sdir, name, model, "error", f"worker could not start: {e}")
        if con is not None:
            try:
                _mark(con, wid, "error")
            except Exception:
                pass
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass


def _system_prompt(project_root: Path, cfg: dict, kind: str, skills: list[str]) -> str:
    sys = ("You are a focused subagent. Answer with SUMMARY + DIFF only, <=4k tokens. "
           "Do not ask questions. "
           "Resources: scratch/temp files go under the project's .opencode/harness/tmp/ — "
           "never /tmp or any system directory — and never install packages or tools "
           "(report the missing tool instead). End with a SUMMARY listing every file you "
           "created or changed, with line counts.")
    brief = SUBAGENT_BRIEFS.get(str(kind))
    if brief:
        sys += " " + brief
    if kind in ("explore", "librarian"):
        sys += " READ-ONLY: do not propose writes."
    if not skills:
        return sys
    from . import skills as _S
    parts = []
    for name in skills[:3]:
        try:
            parts.append(_S.view(project_root, cfg, name)[:2500])
        except Exception:
            continue
    if parts:
        sys += ("\n\nRelevant skills (follow them):\n" + "\n---\n".join(parts))[:8000]
    return sys


def _write_result(sdir: Path, name: str, model: str, status: str, content: str) -> None:
    try:
        sdir.mkdir(parents=True, exist_ok=True)
        (sdir / "result.md").write_text(
            f"# {name} [{status}] model={model}\n\n{content}\n")
    except Exception:
        pass


def list_subagents(con, stale_after_s: int | None = None) -> list[dict]:
    """Recent workers, newest first. Rows still claiming to run long after their
    last update are reported as `stale` — a dead thread never finishes its own
    row, and a status that never changes is worse than an honest failure."""
    now = int(time.time())
    rows = con.execute(
        "SELECT id,name,category,model,status,updated,cost FROM workers "
        "ORDER BY rowid DESC LIMIT 20").fetchall()
    out: list[dict] = []
    for wid, name, kind, model, status, updated, cost in rows:
        if status in ("queued", "running") and stale_after_s:
            if updated and now - int(updated) > stale_after_s:
                status = "stale"
        out.append({"id": wid, "name": name, "kind": kind, "model": model,
                    "status": status, "updated": updated, "cost": cost})
    return out


def prune_stale(con, older_than_s: int = 3600) -> int:
    """Persist the stale verdict for workers that stopped reporting."""
    cutoff = int(time.time()) - int(older_than_s)
    cur = con.execute(
        "UPDATE workers SET status='stale' WHERE status IN ('queued','running') "
        "AND updated < ? AND updated > 0", (cutoff,))
    con.commit()
    return int(cur.rowcount or 0)


def result(con, wid: str, limit: int = 4000) -> str:
    """A worker's result file, as text (or why there is not one yet)."""
    row = con.execute("SELECT name,status,updated FROM workers WHERE id=?", (wid,)).fetchone()
    if not row:
        return f"unknown worker {wid}"
    name, status, _updated = row
    # the DB sits at <root>/.opencode/harness/sessions.db, so its parent IS the
    # state dir that holds workers/<id>/result.md
    try:
        state = Path(con.execute("PRAGMA database_list").fetchone()[2]).parent
        return (state / "workers" / wid / "result.md").read_text()[:limit]
    except Exception:
        return f"{name} [{status}]: no result yet (it may still be running)"


def wait(con, wid: str, timeout_s: float = 60.0, interval: float = 0.1) -> dict:
    """Block until a worker reaches a terminal status (or the timeout)."""
    deadline = time.time() + max(0.0, timeout_s)
    while True:
        row = con.execute("SELECT name,status,updated FROM workers WHERE id=?",
                          (wid,)).fetchone()
        if not row:
            return {"id": wid, "status": "unknown"}
        name, status, updated = row
        if status in TERMINAL:
            return {"id": wid, "name": name, "status": status, "updated": updated}
        if time.time() >= deadline:
            return {"id": wid, "name": name, "status": status, "updated": updated,
                    "waited_out": True}
        time.sleep(interval)


def delete_subagent(con, wid: str) -> None:
    """Forget a worker row (its files under workers/<id>/ stay for the log)."""
    con.execute("DELETE FROM workers WHERE id=?", (wid,))
    con.commit()


def send(con, sender: str, content: str, receiver_role: str = "parent", receiver_name: str = "") -> None:
    con.execute("INSERT INTO mailbox(sender,receiver,receiver_role,content,ts,delivered) "
                "VALUES(?,?,?,?,?,0)",
                (sender, receiver_name or receiver_role, receiver_role, content[:4000], int(time.time())))
    con.commit()


def inbox(con, role: str = "parent", limit: int = 5, mark_delivered: bool = True) -> list[dict]:
    """Undelivered messages for `role`, newest first.

    Delivered messages are marked so a parent that flushes its inbox every turn
    does not re-append the same child completion forever (it used to).
    """
    rows = con.execute(
        "SELECT id,sender,content FROM mailbox WHERE receiver_role=? AND delivered=0 "
        "ORDER BY id DESC LIMIT ?", (role, limit)).fetchall()
    out = [{"from": s, "content": c[:1000], "id": i} for i, s, c in rows]
    if out and mark_delivered:
        try:
            con.execute("UPDATE mailbox SET delivered=1 WHERE id IN ("
                        + ",".join("?" * len(rows)) + ")", tuple(i for i, _, _ in rows))
            con.commit()
        except Exception:
            pass
    return out
