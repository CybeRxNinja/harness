"""HTTP gateway (v0). Stdlib http.server. Localhost-only unless --expose.

Routes:
 POST /api/chat {session_id?,mode?,message,model?} -> {content} or SSE if ?stream=1
 GET  /api/sessions | /api/agents | /api/tasks?session= | /health
 POST /api/spawn {prompt,name,category|subagent_type} | /api/stop {id}
 GET  /v1/models | POST /v1/chat/completions (OpenAI-compat for TUI-B relay use)
"""
from __future__ import annotations

import json
import secrets
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def token_path() -> Path:
    from .config import user_dir
    return user_dir() / "token"


def ensure_token() -> str:
    p = token_path()
    try:
        if p.exists():
            return p.read_text().strip()
    except Exception:
        pass
    t = secrets.token_hex(24)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(t)
        try:
            p.chmod(0o600)
        except Exception:
            pass
    except Exception:
        pass
    return t


class Handler(BaseHTTPRequestHandler):
    root: Path = Path(".")
    token: str = ""
    server_version = "harness/0.1"

    def log_message(self, *a):
        pass

    def _auth(self) -> bool:
        if self.path in ("/health",):
            return True
        h = self.headers.get("Authorization", "")
        return h == f"Bearer {self.token}"

    def _send(self, code: int, obj, sse: bool = False) -> None:
        body = obj.encode() if isinstance(obj, str) else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/event-stream" if sse else "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(n).decode() or "{}")
        except Exception:
            return {}

    def do_GET(self):
        from .config import load_config
        from .store import connect
        from . import rlm as R
        from .router import list_models
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/health":
            return self._send(200, {"ok": True, "version": "0.1.0"})
        if not self._auth():
            return self._send(401, {"error": "unauthorized"})
        cfg, _ = load_config(self.root)
        if parsed.path == "/v1/models":
            return self._send(200, {"object": "list", "data": list_models(cfg)})
        if parsed.path == "/api/sessions":
            con = connect(self.root)
            rows = con.execute("SELECT id,mode,updated FROM sessions ORDER BY updated DESC LIMIT 30").fetchall()
            con.close()
            return self._send(200, [{"id": r[0], "mode": r[1], "updated": r[2]} for r in rows])
        if parsed.path == "/api/agents":
            con = connect(self.root)
            out = R.list_subagents(con)
            con.close()
            return self._send(200, out)
        if parsed.path == "/api/tasks":
            qs = urllib.parse.parse_qs(parsed.query)
            sess = (qs.get("session") or [""])[0]
            con = connect(self.root)
            todos = [{"text": r[0], "status": r[1]} for r in
                     con.execute("SELECT text,status FROM todos WHERE session=? ORDER BY id", (sess,)).fetchall()]
            inbox = R.inbox(con) if sess else []
            con.close()
            return self._send(200, {"todos": todos, "inbox": inbox})
        if parsed.path == "/api/skills":
            from . import skills as S
            return self._send(200, S.scan(self.root, cfg))
        if parsed.path == "/api/memory":
            from . import memory as M
            qs = urllib.parse.parse_qs(parsed.query)
            q = (qs.get("q") or [""])[0]
            con = connect(self.root)
            out = M.recall(con, q) if q else []
            con.close()
            return self._send(200, out)
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        from .config import load_config
        from .store import connect, ensure_session
        from .loop import run_turn
        from . import rlm as R
        from .router import chat, parse_model
        if not self.path.startswith("/health") and not self._auth():
            return self._send(401, {"error": "unauthorized"})
        body = self._body()
        cfg, _ = load_config(self.root)
        if self.path.startswith("/api/chat"):
            sid = body.get("session_id") or "default"
            mode = body.get("mode", "code")
            msg = body.get("message", "")
            stream = "stream=1" in self.path or body.get("stream")
            con = connect(self.root)
            ensure_session(con, sid, mode)
            con.close()
            try:
                out = run_turn(self.root, sid, msg, mode, body.get("model", ""), cfg,
                               auto_approve=bool(body.get("auto")))
            except Exception as e:
                return self._send(500, {"error": str(e)[:500]})
            if stream:
                return self._send(200, f"data: {json.dumps({'delta': out['content'][:6000]})}\n\ndata: [DONE]\n\n", sse=True)
            return self._send(200, {"session_id": sid, "content": out["content"], "touched": out.get("touched", [])})
        if self.path == "/api/spawn":
            con = connect(self.root)
            try:
                h = R.spawn(con, cfg, self.root, body.get("prompt", ""), body.get("name", "worker"),
                            category=body.get("category"), subagent_type=body.get("subagent_type"))
            except Exception as e:
                con.close()
                return self._send(400, {"error": str(e)[:500]})
            con.close()
            return self._send(200, h)
        if self.path == "/api/stop":
            return self._send(200, {"ok": True})
        if self.path == "/v1/chat/completions":
            msgs = body.get("messages", [])
            model = body.get("model", "auto-fastest")
            try:
                m = chat(msgs, model=model, cfg=cfg)
                return self._send(200, {"id": "chatcmpl-harness", "object": "chat.completion",
                                        "choices": [{"index": 0, "message": {k: v for k, v in m.items() if not k.startswith("_")}, "finish_reason": "stop"}]})
            except Exception as e:
                return self._send(502, {"error": str(e)[:500]})
        return self._send(404, {"error": "not found"})


def serve(root: Path, port: int = 8787, host: str = "127.0.0.1") -> None:
    Handler.root = root.resolve()
    Handler.token = ensure_token()
    print(f"harness serve on http://{host}:{port} (token in ~/.harness/token)")
    ThreadingHTTPServer((host, port), Handler).serve_forever()
