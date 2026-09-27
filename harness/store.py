"""SQLite stores: facts, todos, workers/waits (live contract), plus legacy tables.

The live sidebar reads the todos/waits/workers tables and the plugin reads
facts straight from disk, so those tables are the contract. Session/message
writers and the history reader were retired with the headless turn loop; the
tables stay in SCHEMA so existing databases keep opening. `search` stays: kept
memory.recall() reads the FTS index through it (degrading to empty when the
retired writer never populated it).
"""
from __future__ import annotations

import sqlite3
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
CREATE TABLE IF NOT EXISTS mailbox(id INTEGER PRIMARY KEY AUTOINCREMENT, sender TEXT, receiver TEXT, receiver_role TEXT, content TEXT, ts INTEGER, delivered INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS facts(id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT, source TEXT, ts INTEGER);
CREATE TABLE IF NOT EXISTS pending(id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, name TEXT, diff TEXT, gist TEXT, ts INTEGER);
"""


def connect(project_root: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path(project_root), timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    _migrate(con)
    return con


def ensure_column(con: sqlite3.Connection, table: str, column: str, decl: str) -> bool:
    """Add a column to a pre-existing table. True when it had to be added.

    `CREATE TABLE IF NOT EXISTS` never touches an existing table, so a new
    column in SCHEMA is invisible to every database created before it.
    """
    try:
        cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return False
    if not cols or column in cols:
        return False
    try:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        return True
    except Exception:
        return False


def _migrate(con: sqlite3.Connection) -> None:
    """Forward-compatible additions to tables that already exist on disk."""
    ensure_column(con, "mailbox", "delivered", "INTEGER DEFAULT 0")


def search(con: sqlite3.Connection, q: str, limit: int = 8) -> list[dict]:
    try:
        rows = con.execute("SELECT session,content FROM messages_fts WHERE messages_fts MATCH ? LIMIT ?",
                           (q, limit)).fetchall()
        return [{"session": s, "snippet": c[:400]} for s, c in rows]
    except Exception:
        return []
