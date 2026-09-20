"""Compaction: summarize old turns, preserve kernel/todos/boulder/registry."""
from __future__ import annotations

from pathlib import Path


def estimate_tokens(text: str) -> int:
    return len(text) // 4 or 1


def should_compact(messages: list[dict], ctx_limit: int = 128000) -> bool:
    total = sum(estimate_tokens(m.get("content", "")) for m in messages)
    return total > int(ctx_limit * 0.7)


def compact(con, cfg: dict, project_root: Path, session: str, kernel, ctx_limit: int = 128000) -> str:
    from .store import history, add_message
    from . import models as backend
    hist = history(con, session, 60)
    total = sum(estimate_tokens(m["content"]) for m in hist)
    if total < int(ctx_limit * 0.7):
        return f"no-op ({total} toks)"
    keep = hist[-10:]
    old = hist[:-10]
    old_text = "\n".join(f"{m['role']}: {m['content'][:400]}" for m in old)[-8000:]
    try:
        msg = backend.chat(
            [{"role": "system", "content": "Summarize the conversation into 10 bullets: decisions, files, todos, blockers."},
             {"role": "user", "content": old_text}],
            model="", cfg=cfg, workdir=project_root)
        summary = str(msg.get("content", ""))[:3000]
    except Exception:
        summary = f"(offline summary) {len(old)} older turns, {total} toks. First: {old_text[:300]}"
    kinfo = {}
    try:
        kinfo = kernel.size_info()
        varnames = [k for k in kernel.ns.keys() if not (k.startswith("__") and k.endswith("__"))][:20]
    except Exception:
        varnames = []
    todos = con.execute("SELECT text,status FROM todos WHERE session=? ORDER BY id", (session,)).fetchall()
    note = (f"[compacted {total}->{estimate_tokens(summary)} toks] {summary}\n"
            f"kernel vars kept: {varnames} {kinfo}\n"
            f"todos kept: {[(t, s) for t, s in todos]}")
    add_message(con, session, "system", note)
    try:
        kernel.save()
    except Exception:
        pass
    return note[:2000]
