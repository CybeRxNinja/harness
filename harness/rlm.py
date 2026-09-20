"""RLM spawn/mailbox. category XOR subagent_type. max_parallel=2, depth=1 (v0)."""
from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .paths import state_dir

_POOL = ThreadPoolExecutor(max_workers=2)
_LOCK = threading.Semaphore(2)

READ_ONLY = {"write": False, "edit": False, "shell": False}


def spawn(con, cfg: dict, project_root: Path, prompt: str, name: str,
          category: str | None = None, subagent_type: str | None = None,
          load_skills: list[str] | None = None, budget: dict | None = None) -> dict:
    if not name:
        raise ValueError("rlm.spawn requires name")
    if bool(category) == bool(subagent_type):
        raise ValueError("pass exactly one of category|subagent_type")
    if category and "/" in str(category):
        raise ValueError("category takes intent (quick/deep/...), not provider/model")
    if subagent_type and subagent_type not in (
            "explore", "librarian", "plan-consultant", "plan-reviewer",
            "code-reviewer", "test-engineer", "security-auditor"):
        raise ValueError(f"unknown subagent_type: {subagent_type}")
    max_depth = (cfg.get("budgets", {}) or {}).get("max_depth", 1)
    if max_depth < 1:
        raise RuntimeError("nested spawn disabled (max_depth)")
    wid = "w_" + uuid.uuid4().hex[:8]
    # resolve model chain
    if category:
        chain = ((cfg.get("categories", {}) or {}).get(category, {}) or {}).get("models", ["auto-fastest"])
    else:
        chain = ((cfg.get("agents", {}) or {}).get(subagent_type, {}) or {}).get("models", ["auto-fastest"])
    model = chain[0] if chain else "auto-fastest"
    sdir = state_dir(project_root) / "workers" / wid
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "prompt.md").write_text(f"# {name}\n\n{prompt}\n")
    con.execute("INSERT INTO workers(id,session,name,category,model,status,cost,updated) VALUES(?,?,?,?,?,?,?,?)",
                (wid, "", name, category or subagent_type, model, "queued", 0.0, int(time.time())))
    con.commit()
    _POOL.submit(_run_worker, str(project_root), wid, prompt, name, category or subagent_type, model,
                 load_skills or [], budget or {})
    return {"rlm_child_id": wid, "name": name, "session_dir": str(sdir), "model": model, "status": "queued"}


def _run_worker(root: str, wid: str, prompt: str, name: str, kind: str, model: str,
                skills: list[str], budget: dict) -> None:
    from .store import connect
    from . import router
    from .config import load_config
    project_root = Path(root)
    con = connect(project_root)
    cfg, _ = load_config(project_root)
    if not _LOCK.acquire(blocking=False):
        con.execute("UPDATE workers SET status=? WHERE id=?", ("queued", wid))
        con.commit()
        _LOCK.acquire()
    try:
        con.execute("UPDATE workers SET status=? WHERE id=?", ("running", wid))
        con.commit()
        # Read-only kinds get a constrained prompt; workers return summary+diff only.
        sys = ("You are a focused subagent. Answer with SUMMARY + DIFF only, <=4k tokens. "
               "Do not ask questions.")
        if kind in ("explore", "librarian"):
            sys += " READ-ONLY: do not propose writes."
        skill_text = ""
        if skills:
            from . import skills as _S
            parts = []
            for name in skills[:3]:
                try:
                    parts.append(_S.view(project_root, cfg, name)[:2500])
                except Exception:
                    continue
            if parts:
                skill_text = "\n\nRelevant skills (follow them):\n" + "\n---\n".join(parts)
                sys += skill_text[:8000]
        try:
            msg = router.chat([{"role": "system", "content": sys},
                               {"role": "user", "content": prompt[:6000]}],
                              model=model, cfg=cfg, extra={"reasoning": "low"})
            content = str(msg.get("content", ""))[:4000]
        except Exception as e:
            content = f"worker failed: {e}"
        (state_dir(project_root) / "workers" / wid / "result.md").write_text(content)
        con.execute("UPDATE workers SET status=? WHERE id=?", ("done", wid))
        con.execute("INSERT INTO mailbox(sender,receiver,receiver_role,content,ts) VALUES(?,?,?,?,?)",
                    (name, "parent", "parent", content[:4000], int(time.time())))
        con.commit()
    finally:
        try:
            _LOCK.release()
        except Exception:
            pass
        con.close()


def list_subagents(con) -> list[dict]:
    rows = con.execute("SELECT id,name,category,model,status FROM workers ORDER BY rowid DESC LIMIT 20").fetchall()
    return [{"id": r[0], "name": r[1], "kind": r[2], "model": r[3], "status": r[4]} for r in rows]


def delete_subagent(con, project_root: Path, wid: str) -> None:
    con.execute("DELETE FROM workers WHERE id=?", (wid,))
    con.commit()


def send(con, sender: str, content: str, receiver_role: str = "parent", receiver_name: str = "") -> None:
    con.execute("INSERT INTO mailbox(sender,receiver,receiver_role,content,ts) VALUES(?,?,?,?,?)",
                (sender, receiver_name or receiver_role, receiver_role, content[:4000], int(time.time())))
    con.commit()


def inbox(con, role: str = "parent", limit: int = 5) -> list[dict]:
    rows = con.execute("SELECT sender,content FROM mailbox WHERE receiver_role=? ORDER BY id DESC LIMIT ?",
                       (role, limit)).fetchall()
    return [{"from": s, "content": c[:1000]} for s, c in rows]
