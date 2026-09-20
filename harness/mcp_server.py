"""Harness as an MCP stdio server: exposes skills + memory to MCP clients
(opencode TUI). JSON-RPC 2.0 over stdio, newline-delimited. No token needed:
reads local project files; runs with the client's cwd as project root."""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROTOCOL = "2024-11-05"

TOOLS = [
    {"name": "skills_list",
     "description": "List available harness skills (L0 index: name + when-to-use). Call first to discover skills, then skill_view.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "skill_view",
     "description": "Load a harness skill: full SKILL.md (L1), or a references/ file (L2). Use before doing the task the skill covers.",
     "inputSchema": {"type": "object",
                     "properties": {"name": {"type": "string"},
                                    "path": {"type": "string", "description": "optional references/ file"}},
                     "required": ["name"]}},
    {"name": "memory_recall",
     "description": "Recall durable harness facts/memories relevant to a query. Verify before relying.",
     "inputSchema": {"type": "object",
                     "properties": {"query": {"type": "string"}},
                     "required": ["query"]}},
]


def _text(s: str) -> dict:
    return {"content": [{"type": "text", "text": s[:12000]}]}


def dispatch(method: str, params: dict, root: Path, cfg: dict):
    from . import skills as S
    from . import memory as M
    from .store import connect
    if method == "initialize":
        return {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}},
                "serverInfo": {"name": "harness-skills", "version": "0.1.0"}}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        name = (params or {}).get("name", "")
        args = (params or {}).get("arguments", {})
        if name == "skills_list":
            items = S.scan(root, cfg)
            return _text("\n".join(f"- {s['name']}: {s['description']}" for s in items) or "(no skills)")
        if name == "skill_view":
            try:
                return _text(S.view(root, cfg, args.get("name", ""), args.get("path", "") or ""))
            except Exception as e:
                return _text(f"skill error: {e}")
        if name == "memory_recall":
            con = connect(root)
            try:
                hits = M.recall(con, args.get("query", ""), 5)
            finally:
                con.close()
            return _text("\n".join(f"- [{h.get('source')}] {h['text'][:300]}" for h in hits) or "(nothing recalled)")
        raise ValueError(f"unknown tool {name!r}")
    raise ValueError(f"unknown method {method!r}")


def serve_stdio(root: str | Path = ".") -> int:
    from .config import load_config
    import os as _o
    root = Path(root).resolve()
    cfg, _ = load_config(root)
    trace = None
    if _o.environ.get("HARNESS_MCP_TRACE"):
        trace = open(_o.environ["HARNESS_MCP_TRACE"], "a")
    def log(*a):
        if trace:
            trace.write(" ".join(str(x) for x in a) + "\n")
            trace.flush()
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer

    def handle(raw: bytes) -> None:
        line = raw.strip()
        if not line:
            return
        log("recv:", line[:200])
        try:
            msg = json.loads(line.decode())
        except Exception:
            return
        mid, method, params = msg.get("id"), msg.get("method", ""), msg.get("params", {})
        if method.startswith("notifications/"):
            return
        try:
            result = dispatch(method, params, root, cfg)
            resp: dict = {"jsonrpc": "2.0", "id": mid, "result": result}
        except Exception as e:
            resp = {"jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32603, "message": str(e)[:500]}}
        log("send id:", mid, method)
        stdout.write((json.dumps(resp) + "\n").encode())
        stdout.flush()
    # NOTE: readline(), never read(n): read(n) blocks for a full buffer or EOF,
    # but live MCP clients hold stdin open between messages -> instant deadlock.
    while True:
        try:
            line = stdin.readline()
        except Exception:
            break
        if not line:
            break
        handle(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(serve_stdio())
