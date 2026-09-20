#!/usr/bin/env python3
"""Live smoke test: the plugin must activate on a real opencode server.

Boots `opencode serve` (no model calls — free-tier models and API keys are not
needed), forces plugin activation by creating a session, then asserts through
the server's own API that:

  1. the `harness` plugin is listed with state.status == "active" and
     source.path pointing at the file this script installed
  2. the bundled harness-* skills were seeded into the skill store

Direct-tool visibility (codemode:false) is locked by the bun feature tests in
tests/test_plugin.py; the server's per-session tool registry is not a stable
public endpoint, so this script does not assert on it (it would break on
unrelated opencode upgrades).

Exit code 0 = plugin behaves on this opencode version. Any assertion failure
prints the diff between expected and actual so a payload/API drift is obvious.

Usage: python3 scripts/opencode_smoke.py [--port 4199] [--keep]
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLUGIN_SRC = REPO / "harness" / "plugin" / "harness.ts"


def install_plugin() -> Path:
    """Copy the plugin into opencode's auto-loaded plugins dir (idempotent)."""
    dest = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "opencode" / "plugins" / "harness.ts"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(PLUGIN_SRC.read_text())
    return dest


class Server:
    def __init__(self, port: int):
        self.port = port
        self.base = f"http://127.0.0.1:{port}"
        self.proc: subprocess.Popen | None = None
        self.auth: str = ""

    def __enter__(self) -> "Server":
        self.proc = subprocess.Popen(
            ["opencode", "serve", "--hostname", "127.0.0.1", "--port", str(self.port)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        deadline = time.time() + 60
        while time.time() < deadline:
            line = self.proc.stdout.readline() if self.proc.stdout else ""
            m = re.search(r"server password (\S+)", line)
            if m:
                token = base64.b64encode(f"opencode:{m.group(1)}".encode()).decode()
                self.auth = f"Basic {token}"
            if self.auth and self.get("/api/plugin") is not None:
                return self
        raise RuntimeError("opencode serve did not report a password + healthy API in 60s")

    def __exit__(self, *exc) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def get(self, path: str):
        req = urllib.request.Request(self.base + path, headers={"Authorization": self.auth})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read())
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError):
            return None


def wait_for_activation(srv: Server, timeout: float = 45.0) -> dict:
    """Create a session (forces lazy plugin activation) then poll /api/plugin."""
    req = urllib.request.Request(
        srv.base + "/api/session",
        method="POST",
        headers={"Authorization": srv.auth, "Content-Type": "application/json"},
        data=b"{}",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        json.loads(r.read())
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        plugins = (srv.get("/api/plugin") or {}).get("data", [])
        last = next((p for p in plugins if p.get("id") == "harness"), {})
        if last.get("state", {}).get("status") == "active":
            return last
        time.sleep(1)
    return last


def list_skills(srv: Server) -> list[dict]:
    """Unwrap the skill listing (shape varies across versions)."""
    raw = srv.get("/api/skill")
    data = raw.get("data", raw) if isinstance(raw, dict) else raw
    if isinstance(data, dict):
        data = data.get("skills", data.get("data", []))
    return [s for s in data if isinstance(s, dict)] if isinstance(data, list) else []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=4199)
    args = ap.parse_args()

    if not PLUGIN_SRC.exists():
        print(f"FAIL: {PLUGIN_SRC} missing")
        return 2
    dest = install_plugin()

    failures: list[str] = []
    with Server(args.port) as srv:
        harness = wait_for_activation(srv)
        status = harness.get("state", {}).get("status")
        src_path = harness.get("source", {}).get("path")
        print(f"plugin: id={harness.get('id')} status={status} path={src_path}")
        if status != "active":
            failures.append(f"plugin not active: {json.dumps(harness)}")
        elif src_path and Path(src_path).resolve() != dest.resolve():
            failures.append(f"active plugin is not the installed file: {src_path}")

        # 2. skills seeded
        skills = list_skills(srv)
        ids = {str(s.get("id")) for s in skills}
        seeded = sorted(i for i in ids if i.startswith("harness-"))
        print(f"skills: total={len(ids)} harness-seeded={len(seeded)}")
        if not seeded:
            failures.append(f"no harness-* skills seeded (ids: {sorted(ids)[:10]}…)")

    if failures:
        print("\nSMOKE FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"\nSMOKE OK — plugin active, {len(seeded)} skills seeded (installed at {dest})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
