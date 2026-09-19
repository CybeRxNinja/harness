"""SQLite stores: sessions/messages, tasks(boulder)/todos/workers, mailbox, facts."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path


def db_path(project_root: Path) -> Path:
    p = project_root.resolve() / ".harness" / "sessions.db"
    p.parent.mkdir(parents=True, exist_ok=True)
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
