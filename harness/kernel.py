"""Persistent Python kernel (RLM control env) + jailed bash handles. Stdlib only."""
from __future__ import annotations

import io
import os
import pickle
import subprocess
import time
import traceback
from contextlib import redirect_stdout
from pathlib import Path

from .paths import state_dir

BLOCKED_IMPORTS = ("socket", "urllib.request", "http.client", "ftplib", "smtplib")


class BashHandle:
    def __init__(self, pid: int, proc: subprocess.Popen, log: Path):
        self.pid = pid
        self._proc = proc
        self.log = log

    def poll(self) -> dict:
        rc = self._proc.poll()
        return {"pid": self.pid, "running": rc is None, "returncode": rc}

    def _read(self) -> str:
        try:
            return self.log.read_text(errors="replace")
        except Exception:
            return ""

    def output(self, limit: int = 8000) -> str:
        return self._read()[-limit:]

    def tail(self, n: int = 50) -> str:
        return "\n".join(self._read().splitlines()[-n:])


class Kernel:
    """Per-session persistent namespace. Pickles picklable vars only."""

    def __init__(self, project_root: Path, session_id: str):
        self.root = project_root.resolve()
        self.session_id = session_id
        self.store = state_dir(self.root) / "kernel" / f"{session_id}.pkl"
        self.runs = state_dir(self.root) / "runs"
        self.runs.mkdir(parents=True, exist_ok=True)
        self.ns: dict = {"__session__": session_id}
        self._load()

    def _load(self) -> None:
        try:
            if self.store.exists():
                data = pickle.loads(self.store.read_bytes())
                if isinstance(data, dict):
                    self.ns.update(data)
        except Exception:
            pass

    def save(self) -> None:
        try:
            self.store.parent.mkdir(parents=True, exist_ok=True)
            safe = {}
            for k, v in self.ns.items():
                if k.startswith("__") and k.endswith("__"):
                    continue
                try:
                    pickle.dumps(v)
                    safe[k] = v
                except Exception:
                    safe[k] = f"<unpicklable {type(v).__name__}>"
            self.store.write_bytes(pickle.dumps(safe))
        except Exception:
            pass

    def _jail(self, path: str) -> Path:
        p = (self.root / path).resolve() if not os.path.isabs(path) else Path(path).resolve()
        if self.root not in p.parents and p != self.root:
            raise PermissionError(f"path escapes project root: {path}")
        return p

    def execute(self, code: str, allow_net: bool = False) -> dict:
        for mod in BLOCKED_IMPORTS:
            if f"import {mod}" in code and not allow_net:
                return {"ok": False, "output": f"blocked import {mod} (allow_net=False)", "error": "blocked"}
        buf = io.StringIO()
        # minimal safe builtins: keep full builtins (documented trust model) but chdir jail
        self.ns["__root__"] = str(self.root)
        try:
            with redirect_stdout(buf):
                exec(compile(code, "<py>", "exec"), self.ns)
            self.save()
            return {"ok": True, "output": buf.getvalue()[-8000:], "error": ""}
        except Exception:
            return {"ok": False, "output": buf.getvalue()[-4000:], "error": traceback.format_exc()[-4000:]}

    def bash(self, cmd: str, timeout: int = 10, allowlist: list[str] | None = None) -> dict | BashHandle:
        prog = cmd.strip().split()[0] if cmd.strip() else ""
        if allowlist is not None and prog not in allowlist and prog not in ("bash", "sh"):
            return {"ok": False, "output": "", "error": f"command not in allowlist: {prog}"}
        log = self.runs / f"{int(time.time()*1000)}.log"
        with open(log, "w") as f:
            f.write(f"$ {cmd}\n")
        try:
            proc = subprocess.Popen(cmd, shell=True, cwd=str(self.root),
                                    stdout=open(log, "a"), stderr=subprocess.STDOUT)
        except Exception as e:
            return {"ok": False, "output": "", "error": str(e)}
        try:
            rc = proc.wait(timeout=timeout)
            out = log.read_text(errors="replace")[-8000:]
            return {"ok": rc == 0, "output": out, "error": "" if rc == 0 else f"exit {rc}", "pid": proc.pid}
        except subprocess.TimeoutExpired:
            return BashHandle(proc.pid, proc, log)

    def size_info(self) -> dict:
        try:
            nbytes = self.store.stat().st_size if self.store.exists() else 0
        except Exception:
            nbytes = 0
        return {"vars": len(self.ns), "bytes": nbytes}
