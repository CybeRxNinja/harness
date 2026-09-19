"""MCP manager (v0): stdio servers only, lazy spawn, allow/ask/deny. Harness-owned so CLI+TUI+HTTP share."""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

_procs: dict[str, subprocess.Popen] = {}
_last_used: dict[str, float] = {}


def servers(cfg: dict) -> dict:
    return (cfg.get("mcp", {}) or {}).get("servers", {}) or {}


def _allowed(cfg: dict, server: str, tool: str) -> str:
    s = servers(cfg).get(server, {})
    rules = s.get("tools", {})
    return rules.get(tool, rules.get("*", "ask"))


def list_tools(cfg: dict) -> list[dict]:
    out = []
    for name, s in servers(cfg).items():
        out.append({"server": name, "cmd": s.get("cmd", ""), "enabled": s.get("enabled", True)})
    return out


def call(cfg: dict, server: str, tool: str, args: dict, auto_approve: bool = False) -> dict:
    s = servers(cfg).get(server)
    if not s or not s.get("enabled", True):
        raise ValueError(f"mcp server {server!r} not enabled")
    verdict = _allowed(cfg, server, tool)
    if verdict == "deny":
        raise PermissionError(f"mcp {server}.{tool} denied by policy")
    if verdict == "ask" and not auto_approve:
        raise PermissionError(f"mcp {server}.{tool} needs approval (ask). Re-run with --auto or approve in TUI.")
    cmd = s.get("cmd")
    if not cmd:
        raise ValueError(f"mcp server {server} has no cmd")
    # Minimal JSON-RPC stdio call with timeout. Servers must accept {"tool":..,"args":..} line.
    try:
        proc = _procs.get(server)
        if proc is None or proc.poll() is not None:
            proc = subprocess.Popen(cmd, shell=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, text=True)
            _procs[server] = proc
        _last_used[server] = time.time()
        assert proc.stdin and proc.stdout
        proc.stdin.write(json.dumps({"tool": tool, "args": args}) + "\n")
        proc.stdin.flush()
        import select
        # simple blocking read with timeout via communicate-free readline in thread
        import threading
        line: list[str] = []
        def _read():
            try:
                line.append(proc.stdout.readline())
            except Exception:
                pass
        t = threading.Thread(target=_read, daemon=True)
        t.start()
        t.join(timeout=15)
        if not line or not line[0]:
            raise TimeoutError("mcp server timeout (15s)")
        try:
            return json.loads(line[0])
        except Exception:
            return {"output": line[0][:4000]}
    except PermissionError:
        raise
    except Exception as e:
        return {"error": f"mcp degraded: {e}"}


def audit(cfg: dict) -> list[dict]:
    """Check which server cmds exist (no auto-install in v0)."""
    import shutil
    out = []
    for name, s in servers(cfg).items():
        cmd = str(s.get("cmd", ""))
        prog = cmd.split()[0] if cmd else ""
        out.append({"server": name, "cmd": cmd, "found": bool(shutil.which(prog)) if prog else False})
    return out
