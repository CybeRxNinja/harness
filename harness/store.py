"""SQLite stores: sessions/messages, tasks(boulder)/todos/workers, mailbox, facts."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path


def db_path(project_root: Path) -> Path:
    from .paths import migrate_legacy, state_dir
    migrate_legacy(project_root)
    p = state_dir(project_root) / "sessions.db"
    return p


SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, title TEXT, mode TEXT, created INTEGER, updated INTEGER);
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT, session TEXT, role TEXT, content TEXT, ts INTEGER);
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(content, session UNINDEXED, tokenize='porter');
CREATE TABLE IF NOT EXISTS todos(id INTEGER PRIMARY KEY AUTOINCREMENT, session TEXT, text TEXT, status TEXT, ts INTEGER);
CREATE TABLE IF NOT EXISTS works(id TEXT PRIMARY KEY, plan TEXT, status TEXT, data TEXT, updated INTEGER);
CREATE TABLE IF NOT EXISTS workers(id TEXT PRIMARY KEY, session TEXT, name TEXT, category TEXT, model TEXT, status TEXT, cost REAL, updated INTEGER);
CREATE TABLE IF NOT EXISTS mailbox(id INTEGER PRIMARY KEY AUTOINCREMENT, sender TEXT, receiver TEXT, receiver_role TEXT, content TEXT, ts INTEGER);
CREATE TABLE IF NOT EXISTS facts(id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT, source TEXT, ts INTEGER);
CREATE TABLE IF NOT EXISTS pending(id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, name TEXT, diff TEXT, gist TEXT, ts INTEGER);
CREATE TABLE IF NOT EXISTS usage(id INTEGER PRIMARY KEY AUTOINCREMENT, session TEXT, model TEXT, inp INTEGER, out INTEGER, ctx INTEGER, ts INTEGER);
"""


def connect(project_root: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path(project_root), timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    return con


def ensure_session(con: sqlite3.Connection, sid: str, mode: str = "code") -> None:
    now = int(time.time())
    con.execute("INSERT OR IGNORE INTO sessions(id,title,mode,created,updated) VALUES(?,?,?, ?,?)",
                (sid, sid[:40], mode, now, now))
    con.execute("UPDATE sessions SET updated=? WHERE id=?", (now, sid))
    con.commit()


def add_message(con: sqlite3.Connection, session: str, role: str, content: str) -> None:
    con.execute("INSERT INTO messages(session,role,content,ts) VALUES(?,?,?,?)",
                (session, role, content[:20000], int(time.time())))
    try:
        con.execute("INSERT INTO messages_fts(content,session) VALUES(?,?)", (content[:8000], session))
    except Exception:
        pass
    con.commit()


def history(con: sqlite3.Connection, session: str, limit: int = 40) -> list[dict]:
    rows = con.execute("SELECT role,content FROM messages WHERE session=? ORDER BY id DESC LIMIT ?",
                       (session, limit)).fetchall()
    return [{"role": r, "content": c} for r, c in reversed(rows)]


def search(con: sqlite3.Connection, q: str, limit: int = 8) -> list[dict]:
    try:
        rows = con.execute("SELECT session,content FROM messages_fts WHERE messages_fts MATCH ? LIMIT ?",
                           (q, limit)).fetchall()
        return [{"session": s, "snippet": c[:400]} for s, c in rows]
    except Exception:
        return []


def estimate_text_tokens(text: str) -> int:
    return max(1, len(text or "") // 4)


def record_usage(con: sqlite3.Connection, session: str, model: str,
                 inp: int, out: int, ctx: int = 0) -> None:
    import time as _t
    con.execute("INSERT INTO usage(session,model,inp,out,ctx,ts) VALUES(?,?,?,?,?,?)",
                (session, model, int(inp), int(out), int(ctx), int(_t.time())))
    con.commit()


def get_usage(con: sqlite3.Connection, session: str = "") -> dict:
    q = "SELECT COALESCE(SUM(inp),0), COALESCE(SUM(out),0), COUNT(*) FROM usage"
    args: tuple = ()
    if session:
        q += " WHERE session=?"
        args = (session,)
    inp, out, calls = con.execute(q, args).fetchone()
    by_model = con.execute(
        "SELECT model, SUM(inp), SUM(out), COUNT(*) FROM usage "
        + ("WHERE session=? " if session else "") + "GROUP BY model ORDER BY 4 DESC LIMIT 8",
        args).fetchall()
    last = con.execute(
        "SELECT model, inp, ctx FROM usage "
        + ("WHERE session=? " if session else "") + "ORDER BY id DESC LIMIT 1",
        args).fetchone()
    ctx_pct = round(100 * last[1] / last[2], 1) if last and last[2] else 0.0
    return {"input": inp, "output": out, "total": inp + out, "calls": calls,
            "ctx_pct": ctx_pct, "last_model": last[0] if last else "",
            "by_model": [{"model": m, "input": i, "output": o, "calls": c}
                         for m, i, o, c in by_model]}
