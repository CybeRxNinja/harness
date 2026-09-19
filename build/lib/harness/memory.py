"""Memory: facts + recall (FTS5) + staged lessons (/refine)."""
from __future__ import annotations

import time


def save_fact(con, text: str, source: str = "chat") -> int:
    cur = con.execute("INSERT INTO facts(text,source,ts) VALUES(?,?,?)",
                      (text[:1000], source, int(time.time())))
    con.commit()
    return cur.lastrowid


def recall(con, query: str, limit: int = 3) -> list[dict]:
    """Kibitzer-lite: keyword FTS over messages + facts. No sidecar in v0."""
    out: list[dict] = []
    try:
        for (t, s) in con.execute(
                "SELECT text,source FROM facts WHERE text LIKE ? ORDER BY id DESC LIMIT ?",
                (f"%{query[:40]}%", limit)).fetchall():
            out.append({"text": t, "source": s})
    except Exception:
        pass
    try:
        from .store import search
        for h in search(con, query, limit):
            out.append({"text": h["snippet"], "source": f"session:{h['session']}"})
    except Exception:
        pass
    return out[:limit]


def stage_lesson(con, name: str, diff: str, gist: str) -> int:
    cur = con.execute("INSERT INTO pending(kind,name,diff,gist,ts) VALUES(?,?,?,?,?)",
                      ("lesson", name[:80], diff[:8000], gist[:200], int(time.time())))
    con.commit()
    return cur.lastrowid


def list_pending(con) -> list[dict]:
    return [{"id": r[0], "kind": r[1], "name": r[2], "gist": r[3]}
            for r in con.execute("SELECT id,kind,name,gist FROM pending ORDER BY id").fetchall()]


def approve(con, pid: int, memory_file) -> bool:
    row = con.execute("SELECT kind,name,diff FROM pending WHERE id=?", (pid,)).fetchone()
    if not row:
        return False
    kind, name, diff = row
    if kind == "lesson":
        with open(memory_file, "a") as f:
            f.write(f"\n- [{name}] {diff[:500]}\n")
    con.execute("DELETE FROM pending WHERE id=?", (pid,))
    con.commit()
    return True


def forget(con, pid_or_text: str) -> int:
    try:
        pid = int(pid_or_text)
        cur = con.execute("DELETE FROM pending WHERE id=?", (pid,))
        con.commit()
        return cur.rowcount
    except ValueError:
        cur = con.execute("DELETE FROM facts WHERE text LIKE ?", (f"%{pid_or_text[:60]}%",))
        con.commit()
        return cur.rowcount
